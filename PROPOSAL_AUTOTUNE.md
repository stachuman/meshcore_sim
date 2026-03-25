# Proposal: Exercising MeshCore Fork Autotune Delays in the Simulator

## Executive Summary

The stachuman/MeshCore fork (PR #2125) adds density-adaptive delay tuning
to the MeshCore routing layer: `Mesh::autoTuneByNeighborCount()` adjusts
`_tx_delay_factor`, `_direct_tx_delay_factor`, and `_rx_delay_base` from a
lookup table based on the number of active neighbors.  On real firmware,
`MyMesh::recalcAutoTune()` is called on every advert reception and every
5 minutes.

**The simulator currently cannot exercise this code.**  SimNode overrides
`getRetransmitDelay()` to return 0, making the fork's `Mesh::getRetransmitDelay()`
(which uses `_tx_delay_factor`) dead code.  No code in the simulator ever
calls `autoTuneByNeighborCount()`.

This proposal describes three approaches (A, B, C) to fix this, ordered by
increasing ambition.  We recommend starting with Approach B (new
`autotune_agent` privatemesh binary) as it provides the most useful
comparison data with moderate implementation effort and no changes to
existing code.

---

## 1. The Problem in Detail

### 1.1 Class hierarchy and override chain

```
Dispatcher  (MeshCore)
    └── Mesh  (MeshCore — fork adds _tx_delay_factor, autoTuneByNeighborCount)
        └── SimNode  (simulator — overrides getRetransmitDelay → 0)
            └── RoomServerNode  (simulator)
```

The fork modifies `Mesh::getRetransmitDelay()` in `MeshCore/src/Mesh.cpp`:

```cpp
uint32_t Mesh::getRetransmitDelay(const mesh::Packet* packet) {
    uint32_t t = (_radio->getEstAirtimeFor(...) * _tx_delay_factor);
    return _rng->nextInt(0, 5*t + 1);
}
```

But `SimNode::getRetransmitDelay()` in `node_agent/SimNode.h` is:

```cpp
uint32_t getRetransmitDelay(const mesh::Packet* packet) override { return 0; }
```

This override always wins.  The fork's `_tx_delay_factor` is never read.

### 1.2 Missing autotune trigger

On real firmware (MyMesh.cpp), autotune is triggered by:

1. **Every advert reception** → `putNeighbour()` → `recalcAutoTune()`
2. **Every 5 minutes** → periodic timer in MyMesh's main loop

SimNode skips BaseChatMesh/MyMesh entirely.  There is no neighbor table
(MyMesh's `neighbours[]` array), no `putNeighbour()`, and no periodic timer.
SimNode has its own `_contacts` vector but never calls `autoTuneByNeighborCount()`.

### 1.3 Timing model incompatibility

Even if the override were removed and autotune were called:

- **SimClock uses wall-clock time** (`steady_clock`).  A 5-minute autotune
  interval requires 5 minutes of real time.  Current warmup periods are
  2–75 seconds.
- **SimRNG uses `/dev/urandom`**.  Delay values drawn by
  `Mesh::getRetransmitDelay()` are truly random, not reproducible.
- These issues are documented in `DETERMINISM.md` and must be addressed
  for meaningful comparison experiments.

### 1.4 Existing `adaptive_delay` experiment

The `privatemesh/adaptive_delay/` directory already implements a
density-adaptive delay experiment, but it is **independent of the fork**.
It has its own density table, its own `_txdelay`/`_direct_txdelay` fields,
and its own `getRetransmitDelay()` override.  The fork's code is not used.

---

## 2. What Needs Testing

The core question is: **does the fork's autotune mechanism (as implemented
in `Mesh.cpp` and `DelayTuning.h`) improve collision resilience compared to
fixed delays?**

Specifically, we want to compare:

| Variant | getRetransmitDelay source | Autotune? | Delay table |
|---------|--------------------------|-----------|-------------|
| **Baseline** (`node_agent`) | Returns 0 always | No | None |
| **Existing adaptive** (`adaptive_agent`) | Sim's own formula | On advert (sim's own) | Sim's DENSITY_TABLE |
| **Fork autotune** (new) | Fork's `Mesh::getRetransmitDelay` | On advert + periodic | Fork's DELAY_TUNING_TABLE |

Key differences between the sim's adaptive_delay and the fork's autotune:

| Aspect | `adaptive_delay` (sim) | Fork autotune |
|--------|----------------------|---------------|
| txdelay formula | `5 × LORA_AIRTIME_MS × td` | `5 × getEstAirtimeFor(pkt_len) × _tx_delay_factor` |
| Airtime source | Fixed `330 ms` constant | Dynamic from radio params + packet length |
| Direct delay | Own `_direct_txdelay` field | `Mesh::getDirectRetransmitDelay()` (separate virtual) |
| rx_delay_base | Not modelled | `calcRxDelay(score, air_time)` — priority queueing by SNR |
| Periodic recalc | No (advert-triggered only) | Every 5 minutes |
| Density table | 10 entries, tx+direct only | 13 entries, tx+direct+rx_delay_base |
| Table values | Similar but not identical | From `DelayTuning.h` |

---

## 3. Proposed Approaches

### Approach A — Minimal: Remove override, call autoTune from SimNode

**Scope:** Modify `node_agent/SimNode.h` and `node_agent/SimNode.cpp` only.

**Changes:**

1. Remove the `getRetransmitDelay` override from `node_agent/SimNode.h`
   (delete line 46), allowing `Mesh::getRetransmitDelay()` to run.

2. In `SimNode::onAdvertRecv()`, after updating `_contacts`, call:
   ```cpp
   autoTuneByNeighborCount((int)_contacts.size());
   ```

**Pros:**
- Minimal code change (2 lines).
- Exercises the fork's actual `Mesh::getRetransmitDelay()` and
  `autoTuneByNeighborCount()`.

**Cons:**
- **Breaks all existing tests.**  The baseline `node_agent` currently relies
  on 0-delay for deterministic flood propagation.  Non-zero delays under
  wall-clock timing make test outcomes load-dependent (DETERMINISM.md §1b).
- **Breaks the upstream (non-fork) build.**  Upstream `Mesh.cpp` also has
  `getRetransmitDelay()` with `_tx_delay_factor`, but the upstream doesn't
  have `autoTuneByNeighborCount()` or `DelayTuning.h`.
- No A/B comparison possible — you'd need to rebuild with different MeshCore
  submodule commits to compare.

**Verdict:** Not recommended as a first step.  Too disruptive and doesn't
support easy comparison.

---

### Approach B — New `autotune_agent` privatemesh experiment (Recommended)

**Scope:** New directory `privatemesh/autotune/` with its own SimNode that
delegates to the fork's Mesh layer.  No changes to existing code.

**Concept:** Follow the existing `privatemesh/adaptive_delay/` pattern:
create a new SimNode subclass that removes the `getRetransmitDelay()`
override and calls `autoTuneByNeighborCount()` in `onAdvertRecv()`.  The
resulting binary (`autotune_agent`) can be compared against `node_agent`
and `adaptive_agent` using the existing `experiments/` framework.

**Changes:**

1. **`privatemesh/autotune/SimNode.h`** — copy from `node_agent/SimNode.h` but:
   - Remove the `getRetransmitDelay` override entirely.
   - Optionally also remove the `getDirectRetransmitDelay` override (if present)
     to let the fork's `Mesh::getDirectRetransmitDelay()` run too.

2. **`privatemesh/autotune/SimNode.cpp`** — copy from `node_agent/SimNode.cpp` but:
   - In `onAdvertRecv()`, add `autoTuneByNeighborCount((int)_contacts.size());`
     after updating the contacts list.
   - Add periodic autotune in the main loop or in `SimNode::loop()`:
     every 5 simulated minutes, call `autoTuneByNeighborCount()`.
   - Emit `txdelay_update` JSON events (like adaptive_delay does) for
     observability.

3. **`privatemesh/autotune/CMakeLists.txt`** — follow `adaptive_delay/CMakeLists.txt`
   pattern.  Build produces `autotune_agent`.

4. **Experiment scenario** — add `grid/3x3/autotune` and `grid/10x10/autotune`
   scenarios to `experiments/` that compare `node_agent` vs `adaptive_agent`
   vs `autotune_agent`.

**Interaction with the fork's code:**

Since the `autotune_agent` links against the fork's MeshCore (via the
submodule in `meshcore_sim_fork/`), the following fork code paths are
exercised directly:

- `Mesh::getRetransmitDelay()` — uses `_tx_delay_factor` and
  `_radio->getEstAirtimeFor()`
- `Mesh::getDirectRetransmitDelay()` — uses `_direct_tx_delay_factor`
- `Mesh::calcRxDelay()` — uses `_rx_delay_base`
- `Mesh::autoTuneByNeighborCount()` — looks up `DelayTuning.h` table
- `Mesh::setDelayFactors()` — called by autoTune internally

**Pros:**
- No changes to existing `node_agent` or tests.
- Direct comparison via `experiments/` framework.
- Exercises the fork's actual C++ code — not a reimplementation.
- Follows established project patterns (`privatemesh/` sandbox).
- Can run in both `meshcore_sim` (upstream) and `meshcore_sim_fork` builds,
  though meaningful only with the fork's MeshCore.

**Cons:**
- Still subject to wall-clock timing issues (DETERMINISM.md).  Results may
  vary between runs.  Mitigations below.
- Requires sufficient warmup time for autotune to take effect.

---

### Approach C — Full DES + autotune integration

**Scope:** Implement DES (DETERMINISM.md Plan A) first, then build
`autotune_agent` on top.

This would give fully reproducible results by replacing wall-clock timing
with simulated time.  The autotune 5-minute interval would be a simulated
5 minutes (instant in real time).

**Pros:**
- Fully reproducible results.
- Fast simulation (no real-time waiting).
- Proper testing of the 5-minute periodic recalc.

**Cons:**
- Large implementation effort (C++ protocol changes + Python event queue).
- Not needed to get initial comparison data.
- Can be done incrementally after Approach B proves the concept.

**Verdict:** Do this as Phase 2 after Approach B validates the experiment
design.

---

## 4. Recommended Implementation Plan

### Phase 1: `autotune_agent` with Plan B determinism fixes

Implement Approach B (new privatemesh binary) alongside the
minimal-effort determinism fixes from DETERMINISM.md Plan B:

#### Step 1: Replace SimRNG with seeded PRNG

**Files:** `node_agent/SimRNG.h`, `node_agent/SimRNG.cpp`

Replace `/dev/urandom` reads with a fast seeded PRNG (xoshiro256** or
SplitMix64).  Seed from the node's `--prv` key bytes.  This makes
`Mesh::getRetransmitDelay()` produce reproducible delay values.

One-file change, no protocol change, no test updates needed.

#### Step 2: Create `privatemesh/autotune/` directory

**New files:**
- `privatemesh/autotune/SimNode.h`
- `privatemesh/autotune/SimNode.cpp`
- `privatemesh/autotune/CMakeLists.txt`

The SimNode in this variant:
- Does NOT override `getRetransmitDelay()` or `getDirectRetransmitDelay()`
  → the fork's `Mesh::` implementations run.
- Calls `autoTuneByNeighborCount(_contacts.size())` in `onAdvertRecv()`.
- Emits JSON `txdelay_update` events showing current delay factors.
- Optionally: tracks a "last autotune" millisecond timestamp and calls
  autotune every 300,000 ms (5 min) from the main `loop()` or from a
  check in `onAdvertRecv`.

#### Step 3: Extend warmup periods

**File:** `experiments/scenarios.py` (or wherever scenarios are defined)

For autotune scenarios, set `warmup_secs` to at least 600 (10 minutes)
to allow:
- Multiple advert rounds (so neighbor counts stabilize)
- At least one periodic autotune cycle (5 minutes)

Under wall-clock timing this means 10+ minutes of real simulation time.
This is acceptable for research experiments but not for the CI test suite.

#### Step 4: Serialise asyncio delivery ordering

**File:** `orchestrator/router.py`

Sort neighbours by name in `_on_tx` before creating delivery tasks.
One-line change from DETERMINISM.md Plan B §4.2.  Eliminates the
asyncio ordering non-determinism.

#### Step 5: Add experiment scenarios

**File:** `experiments/scenarios.py` (or equivalent)

```python
GRID_3X3_AUTOTUNE = Scenario(
    name="grid/3x3/autotune",
    topology=grid_topo_config(3, 3),
    binary="./privatemesh/autotune/build/autotune_agent",
    rf_model="contention",
    warmup_secs=600,
    stagger_secs=20,
    readvert_secs=35,
    rounds=2,
    seed=42,
)
```

#### Step 6: Integration test

Add `sim_tests/test_autotune.py` with basic smoke tests:
- `autotune_agent` starts and emits `ready`.
- `txdelay_update` events appear after adverts.
- Delay factors match expected values from `DelayTuning.h` for given
  neighbor counts.
- Message delivery works (non-zero delivery rate under contention).

### Phase 2: DES for fast + reproducible experiments

After Phase 1 produces meaningful comparison data, implement DES
(DETERMINISM.md Plan A) to:
- Eliminate wall-clock jitter entirely.
- Run 10-minute warmups in < 1 second.
- Make results CI-friendly and fully reproducible.
- Enable 5-minute periodic autotune without 5 minutes of real waiting.

---

## 5. Expected Results and Comparison Points

With `autotune_agent` exercising the fork's actual code, we can measure:

| Metric | node_agent | adaptive_agent | autotune_agent |
|--------|-----------|----------------|----------------|
| Delivery rate (contention) | 0% (expected) | ~100% | ? |
| RF collision count | High | Lower | ? |
| Avg latency | N/A | ~2000 ms | ? |
| txdelay at n=4 neighbors | 0 | 1.3 (sim table) | 1.3 (fork table) |
| direct_txdelay at n=4 | 0 | 0.7 (sim table) | 0.7 (fork table) |
| rx_delay_base at n=4 | N/A | N/A | 3.0 (fork table) |
| Dynamic airtime calc | No | No (fixed 330ms) | Yes (per-packet) |

Key differences to highlight in results:
1. **Dynamic vs fixed airtime**: fork uses `getEstAirtimeFor(pkt_len)`
   which varies with packet length; adaptive_delay uses a fixed 330 ms.
2. **rx_delay_base**: fork includes SNR-based receive prioritization;
   adaptive_delay does not model this.
3. **Table granularity**: fork has 13 entries vs adaptive_delay's 10.
4. **Periodic recalc**: fork re-evaluates every 5 minutes even without
   new adverts; adaptive_delay only updates on advert reception.

---

## 6. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Wall-clock jitter flips collision outcomes between runs | Results not reproducible | Phase 1 seeded PRNG + serialised delivery reduces variance; Phase 2 DES eliminates it |
| 10-minute warmup makes experiments slow | Developer friction | Acceptable for research; DES in Phase 2 makes it instant |
| Fork's `getEstAirtimeFor()` returns unexpected values via SimRadio | Wrong delay magnitudes | Verify SimRadio implements `getEstAirtimeFor()` correctly; add unit test |
| autotune_agent only works with fork MeshCore (not upstream) | Build breaks in upstream clone | Document clearly; only build in `meshcore_sim_fork/` |
| Fork's DelayTuning table evolves as PR #2125 is refined | Experiment results become stale | Pin MeshCore submodule to specific commit; re-run when table changes |

### SimRadio.getEstAirtimeFor() — CRITICAL: returns wrong values

The fork's `Mesh::getRetransmitDelay()` calls `_radio->getEstAirtimeFor(len)`.
**SimRadio's current implementation is wildly inaccurate:**

```cpp
// node_agent/SimRadio.cpp:35
uint32_t SimRadio::getEstAirtimeFor(int len_bytes) {
    // LoRa SF7 BW125 approximation: ~8 bytes/ms at the air.
    return (uint32_t)(len_bytes * 1000 / 8);   // = len_bytes * 125 ms
}
```

For a typical 40-byte MeshCore packet: `40 * 125 = 5000 ms`.
Real LoRa at SF10/BW250 (MeshCore defaults): **~330 ms**.

The fork's delay formula would compute:
`5 × 5000 × 1.0 = 25,000 ms` (25 seconds!) instead of the intended
`5 × 330 × 1.0 = 1,650 ms`.

**Fix required before Approach B can work:**  Replace the approximation
with the proper Semtech AN1200.13 formula.  The orchestrator already has
this in `orchestrator/airtime.py` — it needs to be ported to C++ in
SimRadio, or the formula can be simplified to use the configured radio
parameters (SF, BW, CR) passed at construction time.

Alternatively, SimRadio could accept radio parameters via constructor or
command-line flags and compute airtime properly.  This is a prerequisite
for Phase 1.

---

## 7. Relationship to Existing Work

This proposal builds on and complements:

- **`privatemesh/adaptive_delay/`** — existing experiment that validates the
  simulator's ability to test delay-based collision mitigation.  The
  `autotune_agent` follows the same pattern but exercises the fork's code
  instead of a reimplementation.

- **`DETERMINISM.md`** — the determinism fixes (seeded RNG, serialised
  delivery) proposed there are prerequisites for meaningful comparison
  experiments.  This proposal's Phase 1 implements the "Plan B" subset.

- **`experiments/` framework** — the existing `Scenario` / `SimResult` /
  `ComparisonTable` infrastructure supports multi-variant comparison out of
  the box.  No framework changes needed.

---

## 8. Decision Summary

| Question | Recommendation |
|----------|---------------|
| How to exercise the fork's autotune code? | New `privatemesh/autotune/` binary (Approach B) |
| When to implement DES? | After initial comparison data validates the experiment design |
| What determinism fixes are needed now? | Seeded PRNG + serialised delivery (Plan B from DETERMINISM.md) |
| How long should warmup be? | 600 s (10 minutes) to allow autotune stabilization |
| Where does this run? | Only in `meshcore_sim_fork/` (requires fork's MeshCore) |

---

## Appendix: Fork Code References

| File | Key content |
|------|-------------|
| `MeshCore/src/Mesh.h:38-40` | `_tx_delay_factor`, `_direct_tx_delay_factor`, `_rx_delay_base` members |
| `MeshCore/src/Mesh.cpp:18-21` | `getRetransmitDelay()` using `_tx_delay_factor` |
| `MeshCore/src/Mesh.cpp:35-40` | `autoTuneByNeighborCount()` implementation |
| `MeshCore/src/helpers/DelayTuning.h` | 13-entry density lookup table |
| `MeshCore/examples/simple_repeater/MyMesh.cpp:63` | `AUTOTUNE_INTERVAL_MILLIS = 5 min` |
| `MeshCore/examples/simple_repeater/MyMesh.cpp:89` | `recalcAutoTune()` called from `putNeighbour()` |
| `MeshCore/examples/simple_repeater/MyMesh.cpp:541-552` | `recalcAutoTune()` implementation |
