# privatemesh

A research sandbox for privacy-preserving routing experiments on top of
MeshCore.

The `privatemesh_agent` binary is **functionally identical to `node_agent`**
until you modify `SimNode.cpp`.  It shares every source file with `node_agent`
except that one — `SimNode.h` and `SimNode.cpp` are local copies, so all
routing patches live here without touching the upstream `node_agent/` code.

---

## Design goals

| Goal | How it is achieved |
|------|--------------------|
| Minimal patch size | Only `SimNode.cpp` (and optionally `SimNode.h`) differ from `node_agent/`. All other sources are referenced directly from `../node_agent/`. |
| Measurable LoC | `diff ../node_agent/SimNode.cpp SimNode.cpp \| grep '^[+-]' \| grep -v '^---\|^+++' \| wc -l` gives the net changed lines. |
| Backwards compatibility | Old (`node_agent`) and new (`privatemesh_agent`) binaries can run in the same simulation. Assign the `binary` field per node in the topology JSON to model mixed firmware deployments. |
| No upstream changes | The `MeshCore/` submodule is never modified. All routing changes stay inside this directory. |

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

## Build

```sh
cd privatemesh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

The binary is written to `privatemesh/build/privatemesh_agent`.

For a debug build:
```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

---

## Running an experiment

Pass `--agent` to the orchestrator (or set `default_binary` in the topology
JSON's `simulation` block):

```sh
# All nodes run the privatemesh binary
python3 -m orchestrator topologies/grid_10x10.json \
    --agent privatemesh/build/privatemesh_agent --duration 30 --seed 42 -v

# Mixed deployment: some nodes use node_agent, some use privatemesh_agent
# (set "binary" per-node in the topology JSON — see below)
python3 -m orchestrator topologies/mixed.json --duration 30 -v
```

### Per-node binary in topology JSON

```json
{
  "nodes": [
    { "name": "alice",  "relay": false },
    { "name": "relay1", "relay": true,
      "binary": "./privatemesh/build/privatemesh_agent" },
    { "name": "bob",    "relay": false }
  ],
  "simulation": {
    "default_binary": "./node_agent/build/node_agent"
  }
}
```

Nodes without an explicit `binary` field use `simulation.default_binary` —
here, the unmodified `node_agent`.  This models a real deployment where only
some relay firmware has been updated.

### Whole-topology switch via `simulation.default_binary`

```json
{
  "simulation": {
    "default_binary": "./privatemesh/build/privatemesh_agent"
  }
}
```

---

## Measuring patch size

```sh
# Net changed lines (additions + deletions, excludes diff header lines)
diff ../node_agent/SimNode.cpp SimNode.cpp \
  | grep '^[+-]' | grep -v '^---\|^+++' | wc -l

# Full unified diff
diff -u ../node_agent/SimNode.cpp SimNode.cpp
```

The goal for each experiment is to keep the diff as small as possible — ideally
a handful of lines concentrated in one routing hook.

---

## Source layout

```
privatemesh/
├── CMakeLists.txt   — builds privatemesh_agent; only SimNode.cpp is local
├── SimNode.h        — local copy of node_agent/SimNode.h (modify as needed)
├── SimNode.cpp      — local copy of node_agent/SimNode.cpp — THE ONLY DIFF
└── build/
    └── privatemesh_agent
```

All other compiled files (`main.cpp`, `SimRadio.*`, `SimClock.*`, `SimRNG.*`,
`crypto_shim/`, `arduino_shim/`, MeshCore sources) are referenced directly
from `../node_agent/` and are never duplicated here.

---

## Routing hooks in SimNode.cpp

These are the virtual methods in `mesh::Mesh` that control routing behaviour.
Privacy patches typically touch one or two of them.

| Method | Role | Privacy relevance |
|--------|------|-------------------|
| `allowPacketForward(pkt)` | Returns true if this relay should re-broadcast the packet. | Controls which nodes participate in forwarding — scope of a flood. |
| `getRetransmitDelay(pkt)` | Returns jitter (ms) before re-broadcasting. Currently returns 0 for deterministic simulation. | Timing side-channel mitigation. |
| `onPeerDataRecv(...)` | Called when a decrypted payload arrives. Also triggers path exchange (stores reverse route, floods PATH reply). | Path exchange leaks topology; patch point for route hiding. |
| `onPeerPathRecv(...)` | Called when a PATH reply arrives; stores the direct route to the sender. | Accepts/rejects learned paths. |
| `onAdvertRecv(...)` | Called when an Advertisement is received; updates the contact list and pre-computes ECDH secrets. | Peer discovery; timing of contact learning. |

The current `node_agent` (and initial `privatemesh`) implementation uses
standard MeshCore flood routing with path exchange: the first message floods,
the PATH reply teaches both endpoints their direct routes, and subsequent
messages are routed directly.
