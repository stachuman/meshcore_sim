# DETERMINISM.md — Path to Reproducible Simulations

This document analyses every source of non-determinism in the current
simulator, then proposes two concrete implementation plans: a full
Discrete-Event Simulation (DES) architecture and a lighter middle path.
The goal stated for both plans is: **given the same scenario seed, two
runs on any machine produce bit-identical metrics and trace output.**

---

## 1. Current sources of non-determinism

### 1a. `SimRNG` reads `/dev/urandom` — delay VALUES are not reproducible

**File:** `node_agent/SimRNG.cpp`

```cpp
void SimRNG::random(uint8_t* dest, size_t sz) {
    FILE* f = fopen("/dev/urandom", "rb");
    fread(dest, 1, sz, f);
    ...
}
```

`SimRNG` is a CSPRNG backed by the OS entropy pool.  Every call returns
unpredictable bytes.  It is shared by all C++ code as the single `mesh::RNG`
instance, and is used for two purposes:

**A — Node identity generation.**  When `NodeConfig.prv_key` is `None`
(the default in every topology helper and the `grid_topo_config` factory),
`main.cpp` generates a fresh identity at startup:

```cpp
node.self_id = mesh::LocalIdentity(&rng);   // calls SimRNG
```

This means each run gives every node a different public key.  Encrypted
packet bytes therefore differ between runs.  Within a single run, all relay
copies of a flood packet carry the same bytes, so the tracer's fingerprint
correlation still works correctly.  Across runs, fingerprints differ — stored
trace JSON files from two runs of the same scenario are not byte-for-byte
comparable.

**B — Retransmit delay values in privatemesh binaries.**
`privatemesh/adaptive_delay/SimNode.cpp` explicitly calls the RNG to generate
the delay fraction:

```cpp
uint8_t buf[4];
getRNG()->random(buf, 4);                    // reads /dev/urandom
uint32_t r = ((uint32_t)buf[0] << 24) | ...;
float frac  = (float)r * (1.0f / 4294967296.0f);
return (uint32_t)(frac * max_ms);
```

The delay **value** is therefore truly random — not derived from the Python
scenario seed or from any seeded PRNG.  Two runs of `adaptive_agent` on the
same scenario will use different delay fractions, leading to different
collision outcomes.

The **base `node_agent`** overrides `getRetransmitDelay` to return 0
unconditionally (`SimNode.h`), so it never calls `SimRNG` for routing
decisions.  The base binary is unaffected by this issue.

**Impact:** HIGH for `adaptive_agent` and any future privatemesh binary that
draws delay from the RNG; LOW for `node_agent`.

---

### 1b. `SimClock` reads the host wall clock — delay TIMING is not reproducible

**File:** `node_agent/SimClock.cpp` / `SimClock.h`

```cpp
// SimClock uses std::chrono::steady_clock:
unsigned long SimClock::getMillis() {
    auto elapsed = Clock::now() - _start;   // real wall-clock
    return (unsigned long)
        std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
}
```

MeshCore's `Dispatcher` (the base class of `Mesh`) uses `getMillis()` for its
internal retransmit scheduler:

1. On receiving a packet, it stores `_tx_target = getMillis() + getRetransmitDelay()`.
2. Every `loop()` call checks `getMillis() >= _tx_target` and fires the TX if
   true.

The C++ main loop in `main.cpp` drives this by polling every 1 ms via a
`select()` timeout:

```cpp
struct timeval tv { .tv_sec = 0, .tv_usec = 1000 };   // 1 ms
int ret = select(STDIN_FILENO + 1, &rfds, nullptr, nullptr, &tv);
...
node.loop();
```

Sources of wall-clock jitter in this loop:

- `select()` returns after *at least* 1 ms; under OS scheduling pressure it
  may return after 5–20 ms.
- Between `select()` returning and `loop()` executing, the process may be
  preempted.
- On a multi-node simulation, all node subprocesses compete for CPU cores.
  With 9 nodes (3×3 grid), all running simultaneously, each individual node
  may be scheduled erratically on a 4-core machine.

**Concrete consequence for the contention model:**  the orchestrator records
`tx_start = asyncio.get_event_loop().time()` when it receives the `tx` event
from a node.  This timestamp is wall-clock.  If a node's retransmit delay is
1650 ms but wall-clock jitter adds 40 ms, `tx_start` is 40 ms later than
expected.  Whether this packet's window `[tx_start, tx_end]` overlaps with a
neighbour's window is therefore load-dependent.

For `node_agent` (delay = 0), the TX fires on the very first `loop()` call
after the `rx` command is processed.  This means the jitter is bounded by the
`select()` timeout (≈ 1 ms) rather than the retransmit delay.  This is small
compared to the 533 ms airtime windows in contention scenarios, so in practice
`node_agent` is nearly deterministic.

For `adaptive_agent` with delays of 500–2500 ms, the 1 ms polling resolution
and OS scheduling jitter are both small relative to the delay magnitude — but
the combination of unpredictable delay VALUE (§1a) and unpredictable timing
makes results genuinely irreproducible.

**Impact:** HIGH for privatemesh binaries with non-zero delays; LOW for
`node_agent`.

---

### 1c. asyncio event ordering at equal simulated times

**File:** `orchestrator/router.py`

When the orchestrator receives a `tx` event from a node, it creates one
asyncio delivery task per neighbour, all launched simultaneously:

```python
tx_start = asyncio.get_event_loop().time()          # wall-clock now
tx_end   = tx_start + airtime_ms / 1000.0

for link in self._topology.neighbours(sender_name):
    asyncio.create_task(
        self._deliver_to(sender_name, link, hex_data, ...
                         tx_start=tx_start, tx_end=tx_end),
    )
```

Each `_deliver_to` coroutine sleeps for `max(tx_end + latency_ms/1000 - now, 0)`.
Since all links from one sender typically share the same airtime, all tasks
target the same wall-clock wake-up moment.  The asyncio event loop processes
timer completions in whatever order the OS timer subsystem delivers them —
nominally FIFO within one resolution quantum, but not guaranteed across runs
or machines.

**Consequence:** if nodes A and B are both neighbours of a common upstream
node C, they both receive C's flood packet at "the same time".  Which of A
and B calls `loop()` and potentially generates a new `tx` event first depends
on asyncio scheduling.  In a collision-sensitive scenario, A transmitting 1 ms
before B vs. B transmitting 1 ms before A can change whether they collide at
a shared downstream node D.

The same ordering issue arises in `traffic.py`:

```python
tasks = [
    self._delayed_advert(agent, self._rng.uniform(0.0, stagger_secs))
    for agent in self._agents.values()
]
await asyncio.gather(*tasks)
```

The stagger VALUES are drawn from the Python PRNG (seeded, deterministic).
But if two agents receive very close stagger values (e.g. 8.4402 s and
8.4403 s), whether they complete in declaration order or in some other order
depends on the asyncio scheduler's timer granularity.

**Impact:** MEDIUM.  Most significant in contention scenarios; present in all
scenarios wherever two deliveries share the same propagation delay.

---

### 1d. `MetricsCollector` latency uses wall-clock time

**File:** `orchestrator/metrics.py`

```python
def record_send_attempt(self, sender, dest_pub, text):
    self._pending[text] = SendRecord(
        ...
        sent_at=time.monotonic(),          # wall-clock seconds
    )

async def on_event(self, node_name, event):
    if etype == "recv_text":
        rec.received_at = time.monotonic() # wall-clock seconds
```

Reported latency values reflect wall-clock elapsed time including any event
loop stalls.  On a loaded machine, a message with 1115 ms simulated latency
might be reported as 1400 ms.  This is a **metrics accuracy** issue, not a
routing correctness issue — delivery counts and witness counts are unaffected.

**Impact:** LOW for routing; MEDIUM for latency benchmarking accuracy.

---

### 1e. Node key non-determinism (no `prv_key` in topology configs)

Every `NodeConfig` in the standard helpers (`grid_topo_config`,
`linear_three_config`, etc.) leaves `prv_key=None`.  The node subprocess
therefore generates a fresh identity from `SimRNG` on every run.  As noted
in §1a, this means encrypted packet bytes differ between runs.  Test
assertions are count-based and route-based (not fingerprint-based), so tests
are robust to this, but trace files from two runs are not directly comparable
byte-for-byte.

**Impact:** LOW for routing correctness; MEDIUM for trace reproducibility.

---

## 2. What IS deterministic today

The following sources of randomness are already seeded and reproducible:

| Source | How seeded |
|--------|-----------|
| Stagger delay values | `random.Random(scenario.seed)` |
| Link-loss dice rolls | Same Python PRNG |
| Traffic generator source/dest choice | Same Python PRNG |
| Topology structure | Fixed grid dimensions, edge weights, relay/endpoint assignments |
| Airtime computation | Pure function of fixed radio parameters |
| Propagation latency | Fixed per-link values from topology JSON |
| Collision window logic | Pure function of tx_start/tx_end and neighbor graph |
| MeshCore routing logic | Deterministic given same packet bytes and contact state |

The core problem is that the **inputs** to the collision model
(`tx_start`, `tx_end`) come from `asyncio.get_event_loop().time()` (a wall
clock), not a controlled simulated time source.  And the **delay values**
computed by privatemesh binaries come from `/dev/urandom`, not a seeded PRNG.

---

## 3. Plan A — Full Discrete-Event Simulation

### 3.1 Conceptual model

The orchestrator owns a single monotonically increasing integer
`sim_ms: int` (milliseconds, starting at 0).  Nothing in the system advances
this counter except the orchestrator's event queue.  No real sleeping happens
anywhere.  Every delay — stagger, airtime, propagation latency, retransmit
delay — is modelled as an event scheduled at a future `sim_ms` value.  Node
subprocesses run only when the orchestrator commands them to, and block on
stdin otherwise.

### 3.2 New protocol messages

**Node → orchestrator (new outputs):**

```
{"type": "sleep", "ms": N}
```
Node has a scheduled TX in N simulated ms from the current orchestrator
`sim_ms`.  Orchestrator should schedule a `wake` at `sim_ms + N`.

```
{"type": "idle"}
```
Node has no scheduled events and will only act again when it receives an
`rx` command.

**Orchestrator → node (new input):**

```
{"type": "wake", "sim_ms": T}
```
Advance the node's simulated clock to T ms and call `loop()`.

### 3.3 C++ changes

#### `SimClock` — replace wall clock with orchestrator-controlled counter

```cpp
class SimClock : public mesh::MillisecondClock, public mesh::RTCClock {
    uint32_t _sim_ms    = 0;   // set by orchestrator via "wake" command
    uint32_t _epoch_base = 0;

public:
    unsigned long getMillis() override  { return _sim_ms; }
    void          set(uint32_t ms)      { _sim_ms = ms; }
    uint32_t      get() const           { return _sim_ms; }
    // RTCClock: getCurrentTime() returns _epoch_base + _sim_ms / 1000
};
```

`main.cpp` handles the `wake` command by calling `clock.set(T)`, then calling
`node.loop()`.

#### `SimRNG` — replace `/dev/urandom` with a seeded PRNG

Use a well-known, fast, non-cryptographic PRNG (e.g. xoshiro256\*\* or
SplitMix64) seeded from the node's private key bytes.  Since the private key
is fixed for a simulation run (either from `--prv` or, once topology
generators are updated, from a deterministic key derived from the node name
and scenario seed), this makes all `SimRNG::random()` output reproducible.

For crypto operations (ECDH, encryption nonce generation), a simulation-grade
PRNG is sufficient — the simulator does not have security requirements.

**Key implication:** topology generators and helpers should start assigning
deterministic `prv_key` values to nodes (e.g. derived from
`HMAC(scenario_seed, node_name)`).  Until they do, key generation at startup
remains non-deterministic, but this only affects trace fingerprints, not
routing behavior.

#### `SimRadio::isSendComplete()` — probe-safe TX detection

The lookahead probe (below) calls `loop()` speculatively.  We need to detect
whether `startSendRaw()` fired during a probe call without actually emitting
to stdout.  Add a probe mode flag:

```cpp
class SimRadio : public mesh::Radio {
    bool _probe_mode = false;     // suppresses stdout during lookahead
    bool _tx_fired   = false;     // set by startSendRaw in probe mode
public:
    void  setProbeMode(bool on)   { _probe_mode = on; _tx_fired = false; }
    bool  txFiredInProbe() const  { return _tx_fired; }
    bool  startSendRaw(const uint8_t* bytes, int len) override;
};
```

In `startSendRaw()`:
```cpp
if (_probe_mode) {
    _tx_fired = true;
    _tx_pending = true;
    return true;            // don't emit JSON
}
// normal path: emit JSON "tx" event
```

#### Main loop — quiescence detection via lookahead

The main loop structural change is the most significant C++ work.  After
processing an incoming `rx` or `wake` command, the node must:

1. Call `loop()` at the current `sim_ms` to fire any immediately-due events.
2. Repeat step 1 until `loop()` does not fire a `tx` (a single `rx` can
   trigger multiple immediate retransmissions, e.g. path return + advert).
3. Run the **lookahead probe** to find the next scheduled event time:
   - Save `sim_ms = T_now`.
   - Enter probe mode on `SimRadio` (suppresses stdout).
   - Advance `sim_ms` by 1 ms at a time, calling `loop()` each step.
   - Stop when `loop()` fires a TX (detected via `txFiredInProbe()`), or
     when the probe horizon is reached (recommended: 10 000 ms, well above
     the maximum realistic retransmit delay of ≈ 5 × 533 ms ≈ 2665 ms).
   - Exit probe mode.  Restore `sim_ms = T_now`.
4. If the probe found an event at `T_now + delta`:
   emit `{"type":"sleep","ms":delta}` and block on stdin.
5. If no event was found within the horizon:
   emit `{"type":"idle"}` and block on stdin.

The probe loop runs at most 10 000 iterations; each iteration is
`clock.set(T)` + `node.loop()` — a few integer comparisons with no I/O.
On modern hardware this completes in under 0.5 ms real time.

**Probe correctness caveat:** `loop()` may advance MeshCore internal counters
or state machines other than the TX timer during the probe.  To make the
probe non-destructive beyond `sim_ms`, inspect whether MeshCore's `loop()`
does anything other than check timers and call `startSendRaw()`.  From the
source, `Dispatcher::loop()` performs:
- Timer check → `startSendRaw()` if due  (intercepted by probe mode)
- Rate-limiting checks  (pure reads, no visible side effects)
- ACK timeout check  (no `startSendRaw` in our usage)

The probe is therefore safe for the current simulator usage.  If future
MeshCore updates add side-effectful `loop()` operations, the probe horizon
check may need re-evaluation.

**"Blocking on stdin"** means the `select()` timeout changes from 1 ms to
infinity (no timeout) when the node is in the `sleep` or `idle` state.  The
node only resumes when the orchestrator sends a command.

### 3.4 Python / orchestrator changes

#### New module: `orchestrator/sim_clock.py`

A priority-queue event scheduler.  Tie-breaking by insertion sequence number
guarantees a deterministic total order when multiple events share the same
`sim_ms`:

```python
import heapq

class SimClock:
    def __init__(self):
        self._now_ms: int = 0
        self._seq: int = 0
        self._queue: list = []        # heap of (time_ms, seq, callback)

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def schedule(self, time_ms: int, callback) -> None:
        self._seq += 1
        heapq.heappush(self._queue, (time_ms, self._seq, callback))

    async def drain(self) -> None:
        """Run all scheduled events in simulated-time order."""
        while self._queue:
            t, _, cb = heapq.heappop(self._queue)
            self._now_ms = t
            await cb()
            # cb() may push more events; continue draining
```

No real `asyncio.sleep()` is ever called.  The entire simulation runs at CPU
speed, advancing `sim_ms` only via the event queue.

#### `orchestrator/router.py`

`_on_tx` becomes a synchronous dispatcher — no tasks, no sleeps:

```python
async def _on_tx(self, sender_name: str, event: dict) -> None:
    ...
    tx_start_ms = self._sim_clock.now_ms
    tx_end_ms   = tx_start_ms + int(airtime_ms)

    # Register TX in channel model using simulated ms (converted to seconds
    # for the existing ChannelModel interface, or ChannelModel updated to use ms).
    tx_start_s = tx_start_ms / 1000.0
    tx_end_s   = tx_end_ms   / 1000.0
    ...
    for link in self._topology.neighbours(sender_name):
        delivery_ms = tx_end_ms + int(link.latency_ms)
        self._sim_clock.schedule(
            delivery_ms,
            functools.partial(self._deliver_to_sync, sender_name, link,
                              hex_data, tx_id, tx_start_s, tx_end_s),
        )
```

`_deliver_to` becomes a plain (non-async) function called directly by the
event queue.  It no longer sleeps.  All `asyncio.get_event_loop().time()`
calls are replaced with `self._sim_clock.now_ms / 1000.0`.

The `sleep` / `idle` events from nodes are handled inside the existing
`_reader_loop` in `NodeAgent`:  when `etype == "sleep"`, schedule a
`send_wake(node, sim_clock.now_ms + sleep_ms)` event.

#### `orchestrator/traffic.py`

All `asyncio.sleep()` calls replaced with `sim_clock.schedule()`:

```python
async def run_initial_adverts(self, stagger_secs: float = 1.0) -> None:
    for agent in self._agents.values():
        delay_ms = int(self._rng.uniform(0.0, stagger_secs) * 1000)
        self._sim_clock.schedule(
            self._sim_clock.now_ms + delay_ms,
            functools.partial(self._send_advert_sync, agent),
        )
```

`run_periodic_adverts` and `run_traffic` become sequences of scheduled
callbacks rather than infinite async loops.

#### `orchestrator/runner.py`

The runner becomes a simple event-queue orchestration:

```python
async def _run_async(scenario, binary, trace_out=None):
    ...
    sim_clock = SimClock()
    # ... wire sim_clock into router, traffic, agents ...

    # Schedule initial advert flood and warmup-end:
    traffic.schedule_initial_adverts(stagger_secs=stagger)
    sim_clock.schedule(
        int(scenario.warmup_secs * 1000),
        functools.partial(start_traffic_phase, ...),
    )

    await sim_clock.drain()
    ...
```

There are no `await asyncio.sleep()` calls anywhere.  The `drain()` call
processes all events to completion and returns.

#### `orchestrator/metrics.py`

```python
# Replace time.monotonic() with simulated time:
sent_at = self._sim_clock.now_ms / 1000.0      # simulated seconds
# latency = (received_sim_s - sent_sim_s) * 1000.0  → ms
```

### 3.5 Quiescence barrier

The orchestrator may not advance `sim_ms` to the next queued event until
every node that was stimulated by the current event has emitted `sleep` or
`idle`.  Without this barrier, the orchestrator might schedule a delivery to
node A at `sim_ms = 1000` and, before A has processed it and potentially
emitted a TX, deliver another packet to B at `sim_ms = 1001` — which could
then cause a collision decision based on an incomplete picture.

**Implementation:** maintain a `_pending_nodes: set[str]` in the orchestrator.
When an `rx` or `wake` is sent to a node, add it to the set.  When the node
emits `sleep` or `idle`, remove it.  The `drain()` loop only advances `sim_ms`
when the set is empty.  This serialises stimulation across time steps but does
not serialise deliveries within a single `sim_ms` step (which is correct —
multiple simultaneous deliveries at the same `sim_ms` should all happen before
any of their downstream effects are processed).

### 3.6 What DES changes about observable behavior

| Property | Current | With DES |
|---|---|---|
| Retransmit delays | Fire within ~1 ms of wall-clock target | Fire at exactly the scheduled `sim_ms` |
| Simultaneous delivery ordering | asyncio-scheduler-dependent | FIFO by insertion sequence (deterministic) |
| Reported latency values | Wall-clock, includes event-loop stall | Simulated time, reproducible |
| Scenario run time | ~150 s for `grid/3x3/contention` | < 5 s (no real sleeping) |
| `GRID_10X10_CONTENTION` (currently ~10 min) | Slow | < 30 s |
| Interactive demo | Works in real time | Breaks — simulation completes instantly |

The interactive demo regression is the only meaningful quality sacrifice.
The existing `viz/` trace replay tool already provides better visualisation
than the live demo; a playback mode in `viz/` would substitute.

### 3.7 Risks and open questions

**R1 — Probe side effects.**  The lookahead probe calls `loop()` repeatedly
with advancing `sim_ms`.  If MeshCore's `loop()` has side effects other than
`startSendRaw()` (e.g. updating a rate-limit counter), those side effects
would fire speculatively.  The probe mode flag suppresses `startSendRaw()`
output but does not suppress other side effects.  Analysis of the current
MeshCore `Dispatcher::loop()` shows only timer checks and `startSendRaw()` —
but this must be re-verified when MeshCore is updated.

**R2 — Multi-packet lookahead.**  If a node schedules two TX events in quick
succession (e.g. advert + path reply at the same `sim_ms`), the probe finds
the first and stops.  The main loop must call `loop()` repeatedly (at fixed
`sim_ms`) until no TX fires, before entering the lookahead.  The structure
in §3.3 already handles this (step 2 loops until no TX at `T_now`).

**R3 — MeshCore advert interval timer.**  MeshCore may have an internal
periodic re-advertisement timer beyond `getRetransmitDelay`.  In the current
simulator, the orchestrator drives all advert floods explicitly via
`TrafficGenerator`.  If MeshCore's internal timer fires spuriously, it would
be detected by the lookahead probe and would appear as a very long
`sleep(N)` event.  This is correct behavior but may need to be
distinguished from retransmit delays in experiment analysis.

**R4 — Node key non-determinism.**  Until topology generators assign
deterministic `prv_key` values, each run generates different identities.  The
routing behavior is identical (delivery rates, witness counts), but raw packet
bytes and tracer fingerprints differ between runs.  Full trace reproducibility
requires topology-level `prv_key` determinism.  A simple scheme:
derive each node's 32-byte private key as `SHA256(node_name || scenario_seed)`.

---

## 4. Plan B — Middle path

Plan B fixes the two highest-impact non-determinism sources
(RNG values and asyncio ordering) without requiring a full DES event queue.
The wall-clock from `SimClock` is retained; the simulated time concept is
not introduced.

### 4.1 Fix 1: replace `SimRNG` with a seeded PRNG

Same change as Plan A §3.3: replace `/dev/urandom` with a PRNG seeded from
the node's private key.  This makes all retransmit delay **values**
deterministic and reproducible across runs.  The delay is still **enforced
by wall-clock** (via `SimClock`), but on a lightly-loaded machine the
wall-clock error is small (< 2 ms from the 1 ms polling loop) compared to
delay magnitudes of 500–2500 ms.

### 4.2 Fix 2: serialise asyncio deliveries within one TX event

In `router._on_tx`, replace concurrent `asyncio.create_task` calls with
sequential `await`:

```python
# Sort neighbours by name for deterministic ordering
for link in sorted(self._topology.neighbours(sender_name),
                   key=lambda l: l.other):
    await self._deliver_to(sender_name, link, hex_data, ...)
```

This removes the asyncio scheduling race for deliveries originating from the
same `tx` event.  The ordering is now deterministic (alphabetical by receiver
name, stable across runs).

**Semantic change:** deliveries that were previously concurrent now happen
strictly sequentially.  Node A processes its `rx` and may emit a new `tx`
before node B even receives the original packet.  In a DES model this would
not happen (all deliveries at the same `sim_ms` are processed together before
any downstream effects).

In practice, the semantic change is small: the sequential real-time delay
between deliveries to adjacent nodes is dominated by the asyncio overhead
(microseconds to low-milliseconds), which is negligible compared to the
533 ms airtime windows in contention scenarios.  For non-contention scenarios,
there are no collision windows and the ordering has no observable effect.

The sequential approach does make the simulation somewhat slower (cannot
deliver to 8 neighbours in parallel), but the difference is small for
9-node grids.  For 100-node grids it could add noticeable overhead.

### 4.3 Fix 3: assign deterministic `prv_key` in topology generators

Add a helper that derives `prv_key` from `HMAC-SHA256(scenario_seed, node_name)`.
Apply it in `grid_topo_config` and `linear_three_config`.  This eliminates
§1e and makes trace fingerprints reproducible across runs.

### 4.4 What Plan B does NOT fix

**Wall-clock timing jitter from `SimClock`.**  Even with deterministic delay
values (Fix 1), the delay is still enforced by the C++ main loop polling
`getMillis()` every 1 ms.  Under OS scheduling pressure, the actual elapsed
time for a 1650 ms delay might be 1655 ms.  Whether this changes a collision
outcome depends on the width of the collision window relative to the jitter.

- In the current `GRID_3X3_CONTENTION` experiment, the designed clearance
  is 1.27 s (§PLAN.md, stagger=20 s analysis).  A 50 ms jitter would not
  change the outcome.
- In a scenario with tight collision windows (< 100 ms clearance), even 5 ms
  jitter could flip a collision outcome.
- On a shared CI server with high load, jitter can reach hundreds of
  milliseconds.

**MetricsCollector latency.**  Still wall-clock (§1d).

### 4.5 Summary of what each plan delivers

| Non-determinism source | Current | Plan B | Plan A (DES) |
|---|---|---|---|
| Delay VALUES (`SimRNG`) | ❌ random | ✅ seeded | ✅ seeded |
| Delay TIMING (`SimClock`) | ❌ wall-clock | ❌ wall-clock | ✅ simulated |
| asyncio delivery ordering | ❌ non-deterministic | ✅ serialised | ✅ sequenced |
| Latency metrics | ❌ wall-clock | ❌ wall-clock | ✅ simulated |
| Node keys (trace fingerprints) | ❌ random per run | ✅ if prv_key fixed | ✅ if prv_key fixed |
| Works correctly under load | ❌ | Mostly (jitter usually small) | ✅ |
| Scenario run time | ~150 s | ~150 s | < 5 s |
| C++ protocol additions | none | none | `sleep` / `idle` / `wake` |
| C++ changes scope | none | SimRNG only | SimRNG + SimClock + main loop |
| Python changes scope | none | Small (serialise delivery, fix prv_key) | Medium (sim_clock, router, traffic, runner) |
| Interactive demo works | ✅ | ✅ | ❌ (needs replay mode) |

---

## 5. Recommended migration path

**Phase 1 (Plan B — low risk, immediate benefit):**

1. Replace `SimRNG` with a seeded PRNG.  One file change, no protocol change,
   no test updates needed.  This eliminates delay-value non-determinism for
   all current privatemesh binaries.
2. Serialise `router._on_tx` deliveries by receiver name.  One-line change
   in `router.py`.  Eliminates asyncio ordering non-determinism.
3. Add deterministic `prv_key` derivation to `grid_topo_config` and
   `linear_three_config`.  Makes trace fingerprints reproducible.

After Phase 1, results will be reproducible on a lightly-loaded development
machine for all current scenarios (the remaining `SimClock` jitter is small
relative to designed clearances).

**Phase 2 (Plan A — full DES, when needed):**

Implement the DES clock when any of the following becomes true:
- Contention scenarios with tight timing margins (< 100 ms clearance) are
  needed.
- The test suite must run on shared CI infrastructure under load.
- Larger scenario grids (10×10 contention, multi-hop chains) need to run in
  seconds rather than minutes for iterative research.
- The `GRID_10X10_CONTENTION` scenario becomes a regular part of the test
  suite.

The Phase 2 implementation is self-contained: the new `sim_clock.py` module
and the C++ main loop changes can be developed and tested independently of
Phase 1 changes.
