# How the Simulation Works

A technical reference describing the architecture, components, and fidelity of
the meshcore\_sim simulator.  Written to help contributors understand what is
simulated, how, and where the simulation may diverge from real hardware.

---

## Table of contents

1.  [Architecture overview](#1-architecture-overview)
2.  [Node agent (C++ subprocess)](#2-node-agent-c-subprocess)
    - [SimRadio](#simradio)
    - [SimClock](#simclock)
    - [SimRNG](#simrng)
    - [SimNode (BaseChatMesh)](#simnode-basechatmesh)
3.  [Orchestrator (Python)](#3-orchestrator-python)
    - [Topology and links](#topology-and-links)
    - [NodeAgent wrapper](#nodeagent-wrapper)
    - [PacketRouter](#packetrouter)
    - [TrafficGenerator](#trafficgenerator)
    - [MetricsCollector](#metricscollector)
    - [PacketTracer](#packettracer)
4.  [Wire protocol](#4-wire-protocol)
5.  [Simulation lifecycle](#5-simulation-lifecycle)
6.  [RF physical-layer model](#6-rf-physical-layer-model)
    - [Airtime calculation](#airtime-calculation)
    - [RF contention and collisions](#rf-contention-and-collisions)
    - [Capture effect](#capture-effect)
7.  [What is real vs. what is simulated](#7-what-is-real-vs-what-is-simulated)
8.  [Known limitations and divergences from reality](#8-known-limitations-and-divergences-from-reality)

---

## 1. Architecture overview

```
                        Python Orchestrator Process
 ┌─────────────────────────────────────────────────────────────────────┐
 │                                                                     │
 │  ┌─────────────┐    ┌──────────────┐    ┌────────────────────────┐  │
 │  │ TopologyJSON │───▶│ Topology     │───▶│ PacketRouter           │  │
 │  │ (config.py)  │    │ (adjacency   │    │ · loss/latency/adv     │  │
 │  └─────────────┘    │  graph)       │    │ · airtime delay        │  │
 │                      └──────────────┘    │ · collision detection   │  │
 │                                          │ · deliver_rx() to node  │  │
 │  ┌──────────────────────────────────┐    └──────────┬─────────────┘  │
 │  │ TrafficGenerator                 │               │                │
 │  │ · staggered initial adverts      │               │                │
 │  │ · periodic re-adverts            │    ┌──────────▼─────────────┐  │
 │  │ · Poisson random text sends      │    │ ChannelModel           │  │
 │  └──────────────────────────────────┘    │ · TX window tracking   │  │
 │                                          │ · overlap detection     │  │
 │  ┌──────────────────────────────────┐    │ · capture effect        │  │
 │  │ MetricsCollector + PacketTracer  │    └────────────────────────┘  │
 │  │ · delivery rate, latency         │                                │
 │  │ · packet fingerprinting          │                                │
 │  │ · witness/exposure counting      │                                │
 │  └──────────────────────────────────┘                                │
 │                                                                     │
 │     NodeAgent(alice)   NodeAgent(relay1)   NodeAgent(bob)  ...      │
 │        │ stdin/stdout     │ stdin/stdout     │ stdin/stdout          │
 └────────┼──────────────────┼─────────────────┼───────────────────────┘
          │                  │                  │
    ┌─────▼──────┐    ┌─────▼──────┐    ┌─────▼──────┐
    │ node_agent │    │ node_agent │    │ node_agent │  OS processes
    │ (C++ bin)  │    │ (C++ bin)  │    │ (C++ bin)  │  (one per node)
    │            │    │            │    │            │
    │ SimNode    │    │ SimNode    │    │ SimNode    │
    │ SimRadio   │    │ SimRadio   │    │ SimRadio   │
    │ SimClock   │    │ SimClock   │    │ SimClock   │
    │ SimRNG     │    │ SimRNG     │    │ SimRNG     │
    │ MeshCore   │    │ MeshCore   │    │ MeshCore   │  (real C++ code,
    │ (unmodified│    │ (unmodified│    │ (unmodified│   compiled as-is)
    │  submodule)│    │  submodule)│    │  submodule)│
    └────────────┘    └────────────┘    └────────────┘
```

**Key design principle:** each simulated node runs the **real MeshCore C++
code** (routing, cryptography, packet handling) in its own OS process.  The
Python orchestrator acts as the "physical world" — it controls which nodes
can hear each other, applies link loss, propagation delay, and RF contention.
No changes to the MeshCore source code are required.

---

## 2. Node agent (C++ subprocess)

Each node is a standalone `node_agent` binary that links against the MeshCore
submodule.  It communicates with the orchestrator via newline-delimited JSON
on stdin/stdout.

The binary contains four shim layers on top of unmodified MeshCore:

### SimRadio

**File:** `node_agent/SimRadio.h/.cpp`
**Implements:** `mesh::Radio` (the abstract radio interface MeshCore expects)

SimRadio bridges the gap between MeshCore's expectation of a physical radio
and the orchestrator-mediated simulation:

| Method | Real radio | SimRadio |
|--------|-----------|----------|
| `recvRaw()` | Reads bytes from RF hardware | Pops from an in-memory queue filled by `enqueue()` (called when the orchestrator delivers an `rx` command) |
| `startSendRaw()` | Transmits bytes over RF | Writes a JSON `{"type":"tx","hex":"..."}` line to stdout; records the expected airtime completion time |
| `isSendComplete()` | Polls hardware TX-done flag | Returns `true` only after `getEstAirtimeFor(len)` milliseconds of wall-clock time have elapsed since `startSendRaw()` |
| `getEstAirtimeFor(len)` | Hardware-specific estimate | Computes on-air time using the **Semtech AN1200.13** formula (LoRa modulation) with configurable SF, BW, CR |
| `packetScore()` | Maps SNR to a quality metric | Linear mapping: SNR -5 dB -> 0.0, SNR +10 dB -> 1.0 |
| `isInRecvMode()` | True when radio is listening | True when not transmitting (`!_tx_pending`) |

**Why `isSendComplete()` matters:** MeshCore's `Dispatcher` has a built-in
duty-cycle budget.  Each TX drains the budget by the airtime reported via
`isSendComplete()`.  By making SimRadio wait for the real airtime to elapse,
the Dispatcher's duty-cycle enforcement works correctly without any changes
to MeshCore.  This means relay retransmissions are naturally spaced, which is
critical for realistic collision modelling.

**Airtime formula** (Semtech AN1200.13 section 4):

```
T_sym     = 2^SF / BW                            (symbol duration)
T_preamble = (N_preamble + 4.25) * T_sym

DE        = 1 if T_sym >= 16 ms else 0           (low data-rate optimisation)
N_payload = 8 + max(ceil((8*PL - 4*SF + 44) / (4*(SF - 2*DE))) * (CR+4), 0)

T_packet  = T_preamble + N_payload * T_sym
```

Both the C++ (`SimRadio::getEstAirtimeFor`) and Python (`orchestrator/airtime.py`)
implementations use this identical formula.  Default parameters are
SF8 / BW 62.5 kHz / CR 4/8 (EU Narrow), configurable via `--sf`, `--bw`, `--cr`
CLI flags.

### SimClock

**File:** `node_agent/SimClock.h/.cpp`
**Implements:** `mesh::MillisecondClock` + `mesh::RTCClock`

- `getMillis()` returns `std::chrono::steady_clock` elapsed time since process
  start (monotonic millisecond counter).
- `getCurrentTime()` returns a Unix epoch (seconds).  The orchestrator can set
  the epoch base via a `{"type":"time","epoch":N}` command at startup.
- `getCurrentTimeUnique()` (inherited from RTCClock) returns a strictly
  increasing timestamp — used by MeshCore to avoid ACK CRC collisions.

**Note:** SimClock uses **real wall-clock time**, not simulated time.  This
means simulation results are not perfectly deterministic — see
[Known limitations](#8-known-limitations-and-divergences-from-reality).

### SimRNG

**File:** `node_agent/SimRNG.h/.cpp`
**Implements:** `mesh::RNG`

A deterministic PRNG using the **xoshiro256\*\*** algorithm (Blackman & Vigna).

Seeding:
- If `--prv <128-hex-char key>` is provided: seed from the key bytes.
- Otherwise: seed from the node's `--name` string bytes.

This ensures:
1. Each node gets a **unique** identity and unique random sequence.
2. Given the same name or key, the node produces the **same** identity
   across runs (important for reproducibility).

MeshCore uses the RNG for: identity generation (Ed25519 keypair), retransmit
delay jitter, and various internal randomness needs.

### SimNode (BaseChatMesh)

**File:** `node_agent/SimNode.h/.cpp`
**Inherits:** `BaseChatMesh` (MeshCore's real chat protocol implementation)

MeshCore's class hierarchy:

```
Dispatcher            raw radio loop, packet queue, duty-cycle enforcement
  +-- Mesh            flood routing, dedup, crypto dispatch, path-building API
       +-- BaseChatMesh   contact book, path exchange, ACKs, retries, UI hooks
            +-- SimNode   our implementation of the "UI" hooks
```

By inheriting from `BaseChatMesh`, SimNode runs the **real MeshCore code** for:

| Feature | Status | Notes |
|---------|--------|-------|
| Flood routing and deduplication | Real | via `Mesh` (inherited) |
| Ed25519 + X25519 cryptography | Real | via crypto shims backed by OpenSSL 3 |
| Contact management (32-slot array) | Real | via `BaseChatMesh` |
| ECDH shared-secret computation | Real | `X25519(prv_a, pub_b)` |
| Message encryption/decryption | Real | `encryptThenMAC` / `MACThenDecrypt` |
| Path exchange (flood out, direct back) | Real | `onPeerDataRecv` / `onPeerPathRecv` |
| ACK tracking | Real | `processAck()` matches expected CRC |
| Timeout and retry (up to 3 attempts) | Real | `onSendTimeout()` calls `sendMessage()` again |
| Duty-cycle enforcement | Real | Dispatcher's token-bucket, driven by SimRadio timing |
| Retransmit delay | Real | `getRetransmitDelay()` — airtime-proportional random jitter |
| Advertisement broadcast | Real | `createSelfAdvert()` + `sendFlood()` |
| Signed message handling | Real | `onSignedMessageRecv()` delegates to `onMessageRecv()` |

What SimNode **adds** on top of BaseChatMesh:
- JSON event emission: every callback (`onMessageRecv`, `onDiscoveredContact`,
  `processAck`, etc.) writes a JSON line to stdout.
- `sendTextTo(pub_hex, text)`: looks up a contact by public key prefix, calls
  `BaseChatMesh::sendMessage()`, and stores a `PendingMsg` for ACK tracking.
- `broadcastAdvert(name)`: calls `createSelfAdvert()` + `sendFlood()`.
- `loop()`: calls `BaseChatMesh::loop()` which drives the Dispatcher.

**RoomServerNode** is a subclass of SimNode that overrides `onMessageRecv()` to
re-broadcast received messages to all other contacts — a simple message hub.

### Main loop

**File:** `node_agent/main.cpp`

Uses `select()` with a 1 ms timeout to multiplex between:
1. Reading JSON commands from stdin (orchestrator -> node).
2. Calling `node.loop()` to drive MeshCore's internal processing.

This tight loop ensures the Dispatcher processes packets promptly and timing-
sensitive operations (ACK timeouts, retransmit delays) fire at approximately
the right time.

---

## 3. Orchestrator (Python)

The orchestrator (`python3 -m orchestrator`) is the simulation engine.  It
manages all node subprocesses, controls the simulated RF environment, generates
traffic, and collects metrics.

### Topology and links

**Files:** `orchestrator/config.py`, `orchestrator/topology.py`

The topology is loaded from a JSON file containing `nodes`, `edges`, and
`simulation` sections.  The `Topology` class builds a **directed adjacency
graph**: each edge becomes two `EdgeLink` objects (one per direction), each
carrying direction-specific loss, latency, and SNR values.

Asymmetric links are supported via `a_to_b` / `b_to_a` overrides in the
edge JSON.  A fully one-way link is expressed as `"loss": 1.0` in one direction.

### NodeAgent wrapper

**File:** `orchestrator/node.py`

Each `NodeAgent` object owns one `asyncio.subprocess.Process`.  It provides:
- `start()` / `wait_ready()` / `quit()` — lifecycle management.
- `deliver_rx(hex, snr, rssi)` — write an `rx` command to the node's stdin
  (RSSI is derived as `snr + noise_floor`).
- `send_text(dest, text)` — write a `send_text` command.
- `broadcast_advert(name)` — write an `advert` command.
- A reader loop that parses every JSON line from stdout and dispatches events
  to callbacks.

The orchestrator spawns nodes in batches of 50 to avoid file-descriptor
exhaustion on large topologies.

### PacketRouter

**File:** `orchestrator/router.py`

The central dispatch hub.  When a node emits a `tx` event:

1. **Record TX** in the packet tracer (returns a `tx_id`).
2. **Register TX** in the channel model (for collision detection).
3. **For each radio neighbour** of the sender, fire an independent
   `asyncio.create_task` that:

   a. Rolls against `link.loss` — drop if lost.

   b. Checks the adversarial filter on the **receiver** — may drop, corrupt,
      or queue for replay.

   c. Sleeps until delivery time:
      - With RF model: `tx_end + latency_ms` (packet must finish on-air first).
      - Without: just `latency_ms`.

   d. Checks half-duplex: if the **receiver** is currently transmitting
      (any registered TX overlaps the packet's reception window), the packet
      is dropped.

   e. Checks for RF collision via `ChannelModel.is_lost()`.

   f. Records the delivery in the packet tracer.

   g. Calls `receiver.deliver_rx()` to write the `rx` command.

Because each delivery is an independent asyncio task, many packets can be
in flight simultaneously, correctly modelling concurrent transmissions.

### TrafficGenerator

**File:** `orchestrator/traffic.py`

Three jobs:

1. **Initial advertisement flood** — sends one `advert` command to each node,
   staggered over a time window proportional to `node_count * airtime * 2`.
   This stagger prevents all adverts from colliding in the contention model.

2. **Periodic re-advertisement** — repeats the advert flood at the configured
   interval (default 30 s) so nodes can discover new contacts.

3. **Random text traffic** — after the warmup period, generates Poisson-
   distributed text messages between random endpoint pairs that have already
   exchanged advertisements.  Each message has a unique timestamp embedded
   in the text for delivery tracking.

### MetricsCollector

**File:** `orchestrator/metrics.py`

Tracks:
- Per-node TX/RX packet counts (orchestrator-level).
- Message delivery rate (send attempt -> `recv_text` match by text content).
- Latency (wall-clock: `send_text` command -> `recv_text` event).
- ACK outcomes (confirmed / retried / failed — parsed from node log events).
- Link loss, collision, and adversarial event counts.
- Contact discovery percentage per endpoint.
- RSS memory usage snapshot at simulation end.

### PacketTracer

**File:** `orchestrator/tracer.py`

Uses `orchestrator/packet.py` to decode the MeshCore wire format and extract
a **stable fingerprint** for each logical packet: `hex(payload_type || payload)`.
The payload bytes are invariant across all hops (only the path field changes).

For every observed packet:
- `record_tx()` — creates/updates a `PacketTrace`, returns a `tx_id`.
- `record_rx()` — adds a `HopRecord` to the trace.
- `record_collision()` — adds a `CollisionRecord`.

This gives us:
- **Witness count** — how many (sender, receiver) pairs observed a packet.
- **Route type** — flood vs. direct routing per packet.
- **Relay delays** — time between receiving a packet and retransmitting it.
- **Flood propagation time** — first TX to last delivery.
- **Channel utilization** — total airtime as a fraction of simulation time.

---

## 4. Wire protocol

All communication is **newline-delimited JSON** (one object per line).

### Orchestrator -> Node (stdin)

| `type` | Fields | Description |
|--------|--------|-------------|
| `time` | `epoch: int` | Set the simulated Unix epoch (sent once at startup) |
| `rx` | `hex: str`, `snr: float`, `rssi: float` | Deliver a received packet (rssi derived as snr + noise_floor) |
| `send_text` | `dest: str`, `text: str` | Send encrypted text to a contact |
| `advert` | `name: str` | Broadcast a self-advertisement |
| `quit` | — | Shut down cleanly |

### Node -> Orchestrator (stdout)

| `type` | Fields | Description |
|--------|--------|-------------|
| `ready` | `pub: str`, `is_relay: bool`, `role: str`, `name: str` | Initialization complete |
| `tx` | `hex: str` | Node wants to transmit a packet (orchestrator routes it) |
| `recv_text` | `from: str`, `name: str`, `text: str` | Decrypted text received |
| `room_post` | `from: str`, `name: str`, `text: str` | Room server forwarded a message |
| `advert` | `pub: str`, `name: str` | A peer's advertisement was received |
| `ack` | `crc: uint32` | ACK received for a sent packet |
| `log` | `msg: str` | Informational log |

The `tx` event is the key control point: the node decides *what* to transmit
(using real MeshCore routing logic), but the orchestrator decides *who hears it*
(based on the topology graph, link parameters, and RF model).

---

## 5. Simulation lifecycle

A typical simulation run proceeds through these phases:

```
Time ─────────────────────────────────────────────────────────────▶

Phase 1: Startup           Phase 2: Warmup        Phase 3: Traffic     Phase 4: Grace     Phase 5: Shutdown
 ├── spawn nodes ──────────┤── staggered adverts ──┤── random sends ────┤── drain ACKs ─────┤── quit + report
 │   (batches of 50)       │   + periodic re-ads   │   (Poisson)        │   (no new sends)  │
 │   wait for "ready"      │                       │                    │                   │
 │   send "time" command   │                       │                    │                   │
```

**Phase 1 — Startup** (a few seconds):
- Spawn one OS process per node (batched to avoid FD exhaustion).
- Send a `time` command with the configured epoch.
- Wait for each node to emit its `ready` event (timeout: 15 s).

**Phase 2 — Warmup** (configurable, auto-derived from radio params):
- Staggered initial advertisement flood: each node broadcasts an advert at
  a random time within a window proportional to `node_count * airtime * 2`.
- Nodes discover each other's public keys (ECDH key exchange happens
  automatically inside MeshCore upon receiving an advert).
- Periodic re-advertisements run throughout the simulation.
- The warmup must be long enough for adverts to propagate through the entire
  network; the orchestrator auto-adjusts: `warmup = stagger + 10s margin`.

**Phase 3 — Traffic** (configurable duration):
- The `TrafficGenerator` sends Poisson-distributed text messages between
  random endpoint pairs that have already exchanged advertisements.
- The first message between any pair floods through the network.
- MeshCore's path exchange mechanism kicks in: the destination sends a PATH
  packet back along the reverse route.
- Subsequent messages between the same pair use DIRECT routing (fewer hops,
  fewer witnesses).
- ACKs and retries happen automatically via BaseChatMesh.

**Phase 4 — Grace** (auto-computed):
- No new messages are sent.
- The simulation stays alive for `stagger + flood_timeout` seconds so that
  in-flight messages can complete their ACK/retry cycles.
- `flood_timeout = (3+1) * (500 + 16 * airtime_ms)` — four attempts at the
  MeshCore companion_radio timeout formula.

**Phase 5 — Shutdown**:
- Sample RSS (resident set size) of each subprocess.
- Snapshot contact discovery and pub-to-name mappings.
- Send `quit` to all nodes; force-kill after 2 s if needed.
- Print the metrics report and packet path trace.
- Optionally write trace JSON and launch the visualiser.

---

## 6. RF physical-layer model

### Airtime calculation

Every packet transmission has a computed on-air time based on the LoRa
modulation parameters (Semtech AN1200.13):

```
  SF=8, BW=62500 Hz, CR=4/8:  40-byte packet ~443 ms
  SF=10, BW=250000 Hz, CR=4/5: 40-byte packet ~330 ms
```

The airtime is used in three places:
1. **SimRadio C++**: `isSendComplete()` blocks for the airtime, enabling
   MeshCore's Dispatcher duty-cycle enforcement.
2. **PacketRouter Python**: delivery is delayed until `tx_start + airtime`
   (the packet must finish transmitting before it arrives at any receiver).
3. **ChannelModel Python**: TX windows are tracked for collision detection.

### RF contention and collisions

When two nodes transmit simultaneously and their signals overlap at a shared
receiver, a collision occurs.  The collision detection works as follows:

#### Step 1: Register every transmission

When a node emits a `tx` event, the `PacketRouter._on_tx()` callback computes
the TX time window and registers it with the `ChannelModel`:

```
  tx_start = event_loop.time()                       (wall-clock)
  tx_end   = tx_start + lora_airtime_ms(...) / 1000  (airtime from Semtech formula)

  channel.register_tx(sender_name, tx_start, tx_end, tx_id)
```

The `ChannelModel` stores this in a dict keyed by `tx_id`:
```
  _active[tx_id] = (sender_name, tx_start, tx_end)
```

Old entries (> 5 s) are pruned on each new TX to bound memory.

#### Step 2: Deliver with delay

For each radio neighbour of the sender, an independent asyncio task runs:

```
  1. Roll link loss          — may drop immediately
  2. Apply adversarial filter — may drop/corrupt
  3. Sleep until tx_end + latency_ms  — packet must finish on-air first
  4. Check for collision      ← THIS IS WHERE DETECTION HAPPENS
  5. Record in tracer
  6. Deliver to node via stdin
```

The collision check happens **after** the propagation delay sleep, at the
moment the packet would arrive at the receiver.  This means other transmissions
that started during our airtime window are already registered.

#### Step 3: Collision check (`ChannelModel.is_lost()`)

For each packet being delivered to a receiver, `is_lost()` iterates over
**all active transmissions** and checks three conditions:

```python
for other_id, (other_sender, other_start, other_end) in _active.items():
    # Skip: same TX event (self)
    if other_id == tx_id: continue
    # Skip: same sender, different packet (can't interfere with yourself)
    if other_sender == primary_sender: continue

    # Condition 1: TEMPORAL OVERLAP
    # Two windows [s1,e1] and [s2,e2] overlap iff s1 < e2 AND s2 < e1
    if other_start >= tx_end or other_end <= tx_start:
        continue  # no overlap

    # Condition 2: SPATIAL REACHABILITY
    # The interferer must be able to reach this receiver
    if receiver not in neighbors[other_sender]:
        continue  # interferer can't reach this receiver

    # Condition 3: CAPTURE EFFECT (only with lat/lon positions)
    if positions available:
        primary_rssi   = -10 * n * log10(dist(primary_sender, receiver))
        interferer_rssi = -10 * n * log10(dist(other_sender, receiver))
        if primary_rssi - interferer_rssi >= 6.0 dB:
            continue  # primary signal strong enough to survive

    return True  # COLLISION — packet lost at this receiver
```

#### Concrete example

```
           relay_A
          /       \
  alice ─── relay_B ─── bob
          \       /
           relay_C

  Alice sends a flood packet.  relay_A, relay_B, relay_C all receive it
  and retransmit.

  If relay_A and relay_C retransmit at overlapping times:
    relay_A TX window: [1000 ms, 1443 ms]    (443 ms airtime)
    relay_C TX window: [1200 ms, 1643 ms]    (443 ms airtime)
    Overlap: 1200 ms to 1443 ms = 243 ms

  At bob:
    - relay_A can reach bob (neighbour)  ✓
    - relay_C can reach bob (neighbour)  ✓
    - Temporal overlap                   ✓
    → Both packets are lost at bob (hard collision)

  At relay_B:
    - relay_A can reach relay_B (neighbour)  ✓
    - relay_C can reach relay_B (neighbour)  ✓
    - Temporal overlap                       ✓
    → Both packets are lost at relay_B too

  With capture effect (edge SNR values differ):
    - relay_A → bob SNR: 12.0 dB
    - relay_C → bob SNR: -6.0 dB
    - Difference: 18.0 dB ≥ 6 dB threshold
    → relay_A's packet SURVIVES at bob (capture effect)
```

#### Important subtlety: collision is checked per-receiver

The same TX event can result in:
- Successful delivery to one neighbour (no interferer reaches it).
- Collision at another neighbour (an interferer overlaps there).

Each `_deliver_to()` task independently calls `is_lost()` for its specific
receiver, so collision outcomes are correctly computed per link.

### Capture effect

The model applies the **LoRa capture effect**: if the primary signal's edge
SNR is at least 6 dB stronger than the interferer's edge SNR at the same
receiver, the primary survives the collision.  SNR differences are equivalent
to RSSI differences (the noise floor cancels out at the same receiver).

If both signals have equal SNR, neither captures and both are lost (hard
collision).

---

## 7. What is real vs. what is simulated

### Running the real MeshCore code

| Component | Real or simulated | Details |
|-----------|-------------------|---------|
| Flood routing (dedup, forwarding) | **Real** | Unmodified `Mesh::routeRecvPacket()` |
| Ed25519 signing / verification | **Real** | OpenSSL 3 EVP wrappers |
| X25519 ECDH key exchange | **Real** | OpenSSL 3 EVP |
| AES-128 encrypt-then-MAC | **Real** | OpenSSL 3 EVP |
| SHA-256 / HMAC-SHA-256 | **Real** | OpenSSL 3 EVP |
| Packet serialisation (wire format) | **Real** | Unmodified `Packet::writeTo()` / `readFrom()` |
| Contact management (32 slots) | **Real** | BaseChatMesh's contact array |
| Path exchange protocol | **Real** | BaseChatMesh `onPeerDataRecv` / `onPeerPathRecv` |
| ACK generation and matching | **Real** | BaseChatMesh `processAck()` with CRC matching |
| Retry logic (up to 3 attempts) | **Real** | `onSendTimeout()` calls `sendMessage()` |
| Retransmit delay jitter | **Real** | `getRetransmitDelay()` = `rand(0, 5 * airtime * 0.5)` |
| Duty-cycle enforcement | **Real** | Dispatcher token-bucket, driven by SimRadio timing |
| Flood deduplication | **Real** | `SimpleMeshTables` hash table |
| Advertisement creation | **Real** | `createSelfAdvert()` |
| Packet hash (for dedup) | **Real** | `calculatePacketHash()` |

### Simulated by the orchestrator

| Component | How it's simulated | Fidelity notes |
|-----------|-------------------|----------------|
| Radio propagation | Topology graph with per-link parameters | No continuous RF model; links are either connected or not |
| Packet loss | Independent Bernoulli trial per delivery | Configurable per-link `loss` probability |
| Propagation delay | `asyncio.sleep(latency_ms + airtime_ms)` | Wall-clock based; not perfectly deterministic |
| RF collisions | Time-window overlap check at shared receivers | Overlap detection uses asyncio event-loop time |
| Capture effect | Log-distance path loss from lat/lon | Simplified: no fading, multipath, or antenna gain |
| Half-duplex | Node-level: `isInRecvMode()` returns false during TX; Orchestrator-level: `ChannelModel.is_receiver_busy()` drops packets arriving at a transmitting node | Both the C++ node and the Python orchestrator enforce half-duplex |
| Channel utilization | Airtime bookkeeping in tracer | Measured, not enforced globally |

### Not simulated

| Feature | Why not | Impact |
|---------|---------|--------|
| Frequency hopping | MeshCore uses a single channel | N/A for current protocol |
| Multi-channel operation | Single channel assumption | Would need separate ChannelModel per channel |
| Physical-layer bit errors | Loss is modelled as whole-packet loss probability | No partial corruption in normal mode |
| Multipath / fading | Links have fixed parameters | No time-varying channel quality |
| Antenna patterns / gain | All nodes assumed omnidirectional with equal power | Capture effect uses distance only |
| Battery / power management | Not modelled | Nodes run indefinitely |
| Group channels | `onChannelMessageRecv()` is a no-op | Group messaging not exercised |
| Keep-alive connections | Not needed for simulation | No persistent connection tracking |
| GPS / location awareness | Nodes don't know their own position | lat/lon used only by orchestrator |

---

## 8. Known limitations and divergences from reality

### Non-determinism

The simulation uses **real wall-clock time** in two critical places:

1. **SimClock (C++)**: `getMillis()` reads `std::chrono::steady_clock`.
   MeshCore's retransmit delays, ACK timeouts, and duty-cycle computations
   all depend on this clock.  On a loaded machine, these timings may drift.

2. **asyncio (Python)**: All packet deliveries use `asyncio.sleep()` and
   `asyncio.get_event_loop().time()`.  Event ordering between concurrent
   tasks depends on the OS scheduler.

**Consequence:** Two runs with the same seed can produce different collision
patterns and slightly different delivery rates.  The RNG seed controls which
messages are sent and which links drop packets, but not the precise timing of
concurrent events.

**Mitigation:** The `--seed` flag makes the following deterministic:
- Which endpoint pairs exchange messages and when.
- Which links drop packets (loss probability).
- Node identities (Ed25519 keypairs derived from name or key).
- Retransmit delay values (deterministic PRNG per node).

### Timing granularity

The node agent's main loop polls stdin with a 1 ms `select()` timeout.  This
means:
- Minimum response time to an `rx` command is ~1 ms.
- MeshCore's `Dispatcher::loop()` is called every ~1 ms.
- Wall-clock jitter (up to a few ms) is introduced on every operation.

On real hardware, the radio interrupt fires immediately upon packet reception,
and the main loop is typically driven at a higher frequency.

### Packet pool size

The `StaticPoolPacketManager` is initialized with a pool of 16 packets.  If
MeshCore needs more concurrent packets than this (e.g., in a very dense flood),
`sendMessage()` may fail with `MSG_SEND_FAILED`.  Real devices typically have
similar constraints (small RAM), so this is representative.

### Contact slot limit

BaseChatMesh supports a maximum of 32 contacts.  In topologies with more than
32 unique node identities, some contacts will be evicted from the contact
array, potentially breaking direct routing to those nodes.  This matches real
device behaviour.

### Airtime symmetry assumption

Both the C++ and Python airtime formulas use the **same** parameters for all
nodes.  In reality, different nodes might use different SF/BW/CR settings
(adaptive data rate).  The simulator assumes a single radio configuration
shared by all nodes.

### Half-duplex enforcement

LoRa radios are half-duplex: they cannot receive while transmitting.  This is
enforced at **two levels**:

1. **C++ node**: SimRadio reports `isInRecvMode() = false` during TX, and the
   Dispatcher returns early in its loop without calling `checkRecv()`.
2. **Python orchestrator**: Before delivering a packet, the router checks
   `ChannelModel.is_receiver_busy()`.  If the receiver has any registered TX
   whose time window overlaps the incoming packet's reception window
   (`[tx_start + latency, tx_end + latency]`), the packet is dropped and
   counted as a "half-duplex RX drop" in the metrics report.

This means packets that would arrive while a node is transmitting are correctly
discarded by the orchestrator before they ever reach the node's stdin.

### Collision detection resolution

The `ChannelModel` checks for overlapping TX windows using asyncio event-loop
timestamps.  Because the event loop's time resolution is limited by the OS
scheduler (typically ~1 ms on Linux), very short overlaps (<1 ms) may be missed.
For LoRa packets with airtimes of hundreds of milliseconds this is negligible,
but could matter for very short packets or high bandwidth settings.

### Single-process-per-node overhead

Each node runs as a separate OS process.  For large topologies (100+ nodes),
this consumes significant resources:
- ~5 file descriptors per node (stdin, stdout, stderr + pipes).
- ~1-2 MB RSS per node process.
- Process spawning takes a few seconds for 100 nodes.

The orchestrator raises the file descriptor limit and spawns in batches of 50
to manage this.  The practical ceiling is around 200-300 nodes depending on the
host machine.

### Retransmit delay formula

SimNode implements `getRetransmitDelay()` matching the MeshCore
`companion_radio/MyMesh.cpp` formula:

```c++
uint32_t t = (uint32_t)(radio->getEstAirtimeFor(pkt_size) * 0.5f);
return rng->nextInt(0, 5 * t + 1);
```

And similarly `getDirectRetransmitDelay()` with factor 0.2 instead of 0.5.

These match the real firmware implementation.  When compiled against a MeshCore
fork with `helpers/DelayTuning.h`, the simulator also calls
`autoTuneByNeighborCount()` for density-adaptive delay scaling (guarded by
`#if __has_include`).

### Adversarial model scope

Adversarial behaviour is modelled at the **receiver** level: a compromised
relay manipulates packets it receives.  This covers:
- Selective dropping (blackhole attack).
- Bit-flipping corruption.
- Delayed replay.

It does **not** model:
- Adversarial packet crafting (injecting novel packets with forged headers).
- Sybil attacks (creating multiple fake node identities).
- Timing analysis attacks (correlating TX/RX times across colluding nodes).
- Active probing (sending queries to map the routing state).

---

## Summary: confidence in simulation correctness

| Aspect | Confidence | Rationale |
|--------|-----------|-----------|
| Routing correctness | **High** | Real MeshCore code; 399 tests including integration |
| Cryptographic correctness | **High** | Real MeshCore crypto with OpenSSL 3; verified against NIST vectors |
| Path exchange (flood -> direct) | **High** | Real BaseChatMesh; confirmed by grid routing tests |
| ACK / retry behaviour | **High** | Real BaseChatMesh; confirmed by integration tests |
| Retransmit delay distribution | **High** | Real formula with deterministic PRNG |
| Airtime calculation | **High** | Semtech AN1200.13 formula; verified against Semtech calculator |
| Collision detection | **Medium** | Correct algorithm but wall-clock timing introduces jitter |
| Duty-cycle enforcement | **Medium** | Real Dispatcher, but wall-clock timing may drift |
| Absolute timing accuracy | **Low** | Wall-clock based; results vary across runs |
| Half-duplex enforcement | **High** | Blocked at orchestrator level; packets arriving during TX are dropped |
