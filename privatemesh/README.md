# privatemesh

A research sandbox for privacy-preserving routing experiments on top of
MeshCore.  Each experiment lives in its own subdirectory with its own
`SimNode.cpp`, `CMakeLists.txt`, and compiled binary.  The only file that
differs between an experiment and the baseline `node_agent` is `SimNode.cpp`
(and optionally `SimNode.h`); every other source file is referenced directly
from `../node_agent/`.

---

## Experiment directory layout

```
privatemesh/
├── README.md          ← this file
├── nexthop/           ← Experiment 1: proactive next-hop routing table
│   ├── CMakeLists.txt
│   ├── SimNode.h
│   ├── SimNode.cpp    ← THE PATCH (diff vs node_agent/SimNode.cpp)
│   └── build/
│       └── nexthop_agent
└── <future>/          ← e.g. pathblind/, onion/, …
    ├── CMakeLists.txt
    ├── SimNode.cpp
    └── build/
        └── <name>_agent
```

Adding a new experiment:
1. `mkdir privatemesh/<name>`
2. Copy `nexthop/CMakeLists.txt` → `<name>/CMakeLists.txt`; update binary name.
3. Copy `nexthop/SimNode.h` and `nexthop/SimNode.cpp` as starting points.
4. Patch only `SimNode.cpp` (and `SimNode.h` if new fields are needed).
5. `cd privatemesh/<name> && cmake -S . -B build && cmake --build build`
6. Add a binary constant and `Scenario` to `experiments/scenarios.py`.

---

## Design goals

| Goal | How it is achieved |
|------|--------------------|
| Minimal patch size | Only `SimNode.cpp` (and optionally `SimNode.h`) differ from `node_agent/`. All other sources are referenced directly from `../node_agent/`. |
| Measurable LoC | `diff node_agent/SimNode.cpp privatemesh/<exp>/SimNode.cpp \| grep '^[+-]' \| grep -v '^---\|^+++' \| wc -l` |
| Backwards compatibility | Old (`node_agent`) and new experiment binaries can run in the same simulation. Set the `binary` field per node in the topology JSON to model mixed firmware deployments. |
| No upstream changes | `MeshCore/` submodule is never modified. All routing changes stay inside this directory. |

---

## Prerequisites

Same as `node_agent/`:

| Tool | Notes |
|------|-------|
| C++17 compiler | AppleClang 17+ or GCC 12+ |
| CMake ≥ 3.16 | `brew install cmake` on macOS |
| OpenSSL 3.x | `brew install openssl@3` on macOS |

The MeshCore submodule must be checked out:
```sh
git submodule update --init
```

---

## Building an experiment

```sh
cd privatemesh/nexthop
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
# Binary → privatemesh/nexthop/build/nexthop_agent
```

---

## Running an experiment

Pass `--agent` to the orchestrator, or set `default_binary` in the topology
JSON's `simulation` block:

```sh
# All nodes use the nexthop binary
python3 -m orchestrator topologies/grid_10x10.json \
    --agent privatemesh/nexthop/build/nexthop_agent --duration 30 -v

# Mixed deployment: some nodes run node_agent, others run nexthop_agent
python3 -m orchestrator topologies/mixed.json --duration 30 -v
```

### Per-node binary in topology JSON

```json
{
  "nodes": [
    { "name": "alice",  "relay": false },
    { "name": "relay1", "relay": true,
      "binary": "./privatemesh/nexthop/build/nexthop_agent" },
    { "name": "bob",    "relay": false }
  ],
  "simulation": {
    "default_binary": "./node_agent/build/node_agent"
  }
}
```

---

## Measuring patch size

```sh
# Net changed lines (from repo root)
diff node_agent/SimNode.cpp privatemesh/nexthop/SimNode.cpp \
  | grep '^[+-]' | grep -v '^---\|^+++' | wc -l

# Full unified diff
diff -u node_agent/SimNode.cpp privatemesh/nexthop/SimNode.cpp
```

The goal for each experiment is to keep the diff as small as possible —
ideally under 150 lines concentrated in one or two routing hooks.

---

## Routing hooks in SimNode.cpp

These are the virtual methods in `mesh::Mesh` that control routing behaviour.
Privacy patches typically touch one or two of them.

| Method | Role | Privacy relevance |
|--------|------|-------------------|
| `allowPacketForward(pkt)` | Returns true if this relay should re-broadcast the packet. | Controls which nodes participate in forwarding — scope of a flood. |
| `getRetransmitDelay(pkt)` | Returns jitter (ms) before re-broadcasting. Currently returns 0 for deterministic simulation. | Timing side-channel mitigation. |
| `onPeerDataRecv(...)` | Called when a decrypted payload arrives. Also triggers path exchange. | Path exchange leaks topology; patch point for route hiding. |
| `onPeerPathRecv(...)` | Called when a PATH reply arrives; stores the direct route to the sender. | Accepts/rejects learned paths. |
| `onAdvertRecv(...)` | Called when an Advertisement is received; updates the contact list. | Peer discovery; routing table population. |
| `sendTextTo(...)` | Application helper: constructs and sends a text datagram. | Where routing strategy is selected (flood vs direct). |

---

## Experiments

### nexthop — proactive next-hop routing table

**Hypothesis**: building a routing table from advertisement floods allows the
sender to use direct routing immediately (bypassing the flood→path-exchange
round trip), reducing the number of relay nodes that observe each message.

**Mechanism**:
- `onAdvertRecv`: for each received advert, store a `RouteEntry` mapping
  the sender's 2-byte public-key prefix to the reversed relay path and a
  hop-count metric.  Entries are aged every `RT_AGE_EVERY` adverts received
  and evicted after `RT_MAX_AGE` cycles without refresh.
- `sendTextTo`: before falling back to flood or path-exchange direct, check
  the routing table.  If a multi-hop route is cached, use `sendDirect`
  immediately — no prior message to that destination required.

**Memory budget**: `RT_MAX_ROUTES × sizeof(RouteEntry) = 64 × 70 = 4480 bytes`

**Patch size**: ~148 lines added to `SimNode.cpp`, zero changes to `SimNode.h`.

**Binary**: `privatemesh/nexthop/build/nexthop_agent`

**Experiment runner**: `python3 -m experiments --scenario grid/3x3`
