# nexthop — Proactive Next-Hop Routing Table

**Experiment directory**: `privatemesh/nexthop/`
**Binary**: `privatemesh/nexthop/build/nexthop_agent`
**Diff from baseline**: `diff -u node_agent/SimNode.cpp privatemesh/nexthop/SimNode.cpp`
**Patch size**: ~148 lines added to `SimNode.cpp`; zero changes to `SimNode.h`
**Memory budget**: 64 × 73 bytes = **4 672 bytes**

---

## Hypothesis

The standard MeshCore node uses a *flood → path-exchange → direct* sequence: the
first message to any destination floods the entire network, which causes all relay
nodes between sender and receiver to observe that a conversation is starting.  Only
after the resulting PATH reply arrives does the sender switch to direct routing.

**Claim**: building a routing table from the advertisement flood that already
occurs during network warm-up allows the sender to use direct routing on the *first*
message — bypassing the flood round-trip entirely and reducing the number of relay
nodes that observe each message.

---

## Mechanism overview

### 1. Table population (`onAdvertRecv`)

Every received advertisement carries the relay path from the origin node to *this*
node in `packet->path[]`.  `onAdvertRecv` reverses this path
(`[rN, …, r2, r1]` → nearest-first for `sendDirect`) and calls `rt_upsert`.

If the reversed path would exceed `RT_PATH_CAP` bytes it is **rejected** and
only the hop-count metric is recorded (a *metric-only* entry, `path_bytes = 0`).
Storing a truncated path would cause `sendDirect` to drop packets midway through
the network without any error indication.

### 2. Table-directed sending (`sendTextTo`)

Before falling back to the standard path-exchange or flood, `sendTextTo` calls
`rt_find` on the first 4 bytes of the destination public key.  If a **useful**
entry is found — `metric > 1` (multi-hop) **and** `path_bytes > 0` (full path
stored) — the packet is sent directly via `sendDirect` without any prior flood.

Destinations where only a metric-only entry exists (path too long) or where the
node is a direct neighbour (`metric == 1`) fall through to the standard behaviour.

### 3. Metric-horizon flood suppression (`allowPacketForward`, Strategy C)

Relay nodes with a populated routing table compare the incoming flood packet's
current hop count against their *routing horizon* — the maximum metric of any
useful (`path_bytes > 0`) entry in their table.

A relay whose entire routing knowledge has been surpassed by the flood wave gains
nothing by re-broadcasting; it is suppressed (`return hops < horizon`).  Relays
with no useful table data relay unconditionally, matching standard behaviour.

**Scope — data packets only.** This suppression is applied **only** to encrypted
data payloads (`TXT_MSG`, `REQ`, `RESPONSE`, `GRP_TXT`, `GRP_DATA`, `ANON_REQ`).
Control and discovery packets (`ADVERT`, `PATH`, `ACK`) are always relayed
unconditionally.  Applying the horizon check to advertisements would suppress the
very flood that populates the routing table, leaving every node's horizon frozen at
1–2 hops and causing all subsequent message floods to die after a handful of relays.

This operates without decrypting or reading the packet destination and is therefore
compatible with encrypted payloads.

### 4. Bidirectional path exchange fix (`onPeerDataRecv`)

Standard `node_agent` only triggers a PATH reply when the incoming message is
flood-routed (`isRouteFlood()`).  Because `nexthop_agent` uses direct routing
for the *first* message to a destination, the receiver's `isRouteFlood()` check
never fires, and the receiver never learns a forward path.  This creates a
one-round reply asymmetry.

Fix: path exchange is now triggered on **both** `isRouteFlood()` **and**
`isRouteDirect()`, so the receiver learns the sender's path regardless of which
routing mode was used.

---

## Data structures

### `RouteEntry` (73 bytes each)

| Field | Type | Bytes | Description |
|-------|------|------:|-------------|
| `dest` | `uint8_t[4]` | 4 | First 4 bytes of destination public key |
| `path` | `uint8_t[64]` | 64 | Reversed relay path for `sendDirect` |
| `path_bytes` | `uint8_t` | 1 | Byte count in `path[]`; 0 = metric-only |
| `hash_sz` | `uint8_t` | 1 | Bytes per relay hash (normally 1) |
| `metric` | `uint8_t` | 1 | Hop count (1 = direct neighbour) |
| `age` | `uint8_t` | 1 | bit 7 = pinned flag; bits 6:0 = age counter |
| `use_count` | `uint8_t` | 1 | Times used for `sendDirect` (saturates at 255) |
| **Total** | | **73** | |

### Global state

| Variable | Type | Description |
|----------|------|-------------|
| `rt_table[RT_MAX_ROUTES]` | `RouteEntry[]` | The routing table |
| `rt_count` | `uint8_t` | Number of live entries |
| `rt_advert_seen` | `uint32_t` | Total adverts received (drives aging clock) |
| `rt_admits_this_cycle` | `uint8_t` | New entries admitted in current age window (Sybil limit) |

---

## Compile-time parameters

| Constant | Value | Rationale |
|----------|------:|-----------|
| `RT_MAX_ROUTES` | 64 | Exceeds expected network size (≤50 unique peers in typical deployments); 64 × 73 = 4 672 bytes |
| `RT_MAX_AGE` | 3 | Evict stale entries after 3 age cycles (≈ 48 adverts); balances freshness vs. churn |
| `RT_PATH_CAP` | 64 | Equals `MAX_PATH_SIZE`; guarantees no path is ever truncated in a valid packet |
| `RT_AGE_EVERY` | 16 | Age every 16 adverts received; roughly one full round of a small network |
| `RT_ADMIT_PER_CYCLE` | 4 | Sybil rate limit: an adversary can inject at most 4 new entries per 16-advert window |
| `RT_PINNED_BIT` | `0x80` | Bit 7 of `age`; set manually to protect important routes from eviction |
| `RT_AGE_MASK` | `0x7F` | Strips pinned bit to read/write the age counter |

---

## Table lifecycle

### Aging and expiry

Every `RT_AGE_EVERY` adverts, `rt_age_entries()` is called:
1. The Sybil admission counter (`rt_admits_this_cycle`) is reset to 0.
2. Each non-pinned entry has its age counter incremented.
3. Entries whose age counter exceeds `RT_MAX_AGE` are removed (swap-with-tail).
4. Pinned entries (`age & RT_PINNED_BIT != 0`) are skipped entirely.

### Upsert logic

`rt_upsert(dest, rev_path, path_bytes, hash_sz, metric)`:

1. **Existing entry** — if a better-or-equal metric arrives, update path and
   metric; reset the age counter to 0 (keep pinned bit).
2. **New entry, below Sybil limit** — admit into a free slot and increment
   `rt_admits_this_cycle`.
3. **New entry, Sybil limit reached** — emit a log line and drop silently.
4. **Table full** — find the worst eviction candidate using the priority order
   below, then replace it only if the incoming entry has a usable path
   (`path_bytes > 0`).

### Eviction priority (worst → first to evict)

| Priority | Condition | Rationale |
|----------|-----------|-----------|
| Never evict | `age & RT_PINNED_BIT` | Manually pinned routes are protected |
| 1st choice | `path_bytes == 0` (metric-only) | Useless for routing; replace freely |
| 2nd choice | `path_bytes > 0` && `use_count == 0` | Learned but never used |
| 3rd choice | `path_bytes > 0` && `use_count > 0`, stalest | Oldest active route |

A log message is emitted whenever a *useful-and-used* entry is displaced, to
help detect under-sized tables in deployment.

---

## Sybil / routing-table poisoning mitigation

An adversary that floods fake advertisements can attempt to fill the routing
table with useless entries, causing real destinations to be evicted.

Mitigations implemented:

| Measure | How it helps |
|---------|-------------|
| `RT_ADMIT_PER_CYCLE = 4` | Caps new entries per 16-advert window per node; bulk injection is rate-limited |
| `rt_admits_this_cycle` reset in `rt_age_entries` | Window is per-age-cycle, not per-second; clock is advert-count-driven |
| Eviction prefers metric-only / unused entries | Adversary entries start with `use_count = 0`; they are evicted first |
| Overflow log | Table-full eviction of useful entries triggers a diagnostic log |

**Limitation**: the current key is a 4-byte prefix of a cryptographic public key,
so Sybil nodes must generate real key pairs that happen to prefix-collide — the
cost of poisoning a specific destination entry is ~2³² key-generation attempts.
Bulk table inflation (filling with random non-colliding keys) is bounded by
`RT_ADMIT_PER_CYCLE`.

---

## Destination key collision probability

The routing table key is `dest[4]` — the first 4 bytes of the 32-byte public key.

| Key size | At 100 nodes | At 10 000 nodes | Birthday bound (1% collision) |
|----------|-------------|-----------------|-------------------------------|
| 2 bytes (old) | ~7% | near-certain | ~256 nodes |
| 4 bytes (current) | ~0.00012% | ~0.001% | ~92 000 nodes |

4-byte keys make collisions negligible for any realistic mesh deployment.

---

## Relay hash collisions (known limitation)

MeshCore V1 uses **1-byte** relay hashes in the `path[]` field.  With only 256
possible hash values, in a 100-node network approximately 40% of nodes share any
given relay hash.  This means:

- `allowPacketForward` (and any direct routing via `sendDirect`) can forward
  packets to unintended relays that happen to share a hash with the intended one.
- Witness counts are inflated relative to a system with longer hashes.
- This is an inherent property of the MeshCore V1 wire format; it is not
  introduced by the nexthop experiment.

---

## Backward compatibility

`nexthop_agent` nodes are fully wire-compatible with standard `node_agent` nodes:

| Interaction | Behaviour |
|-------------|-----------|
| `nexthop` → `node_agent` (direct) | `node_agent` receives and decrypts normally; routing type is transparent to the recipient |
| `node_agent` → `nexthop` (flood) | `nexthop` receives, decrypts, and runs path exchange; reply uses table-direct if available |
| Mixed relay network | Standard relays forward flood packets unchanged; `nexthop` relays may suppress based on Strategy C horizon |
| PATH reply | Standard path-exchange PATH packets are consumed by both node types identically |

One-sided asymmetry that **was** present before fix #7: if `nexthop` used
table-direct for the first message, the receiver (a standard `node_agent`)
would never trigger a PATH reply because `isRouteFlood()` returned false.
The bidirectional path-exchange fix resolves this; `nexthop` receivers now
trigger on both flood and direct.

---

## Building

```sh
cd privatemesh/nexthop
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
# Binary: privatemesh/nexthop/build/nexthop_agent
```

Prerequisites are identical to `node_agent`: C++17, CMake ≥ 3.16, OpenSSL 3.x.

---

## Running experiments

```sh
# All scenarios, compare baseline vs nexthop:
python3 -m experiments

# One scenario only:
python3 -m experiments --scenario grid/3x3

# Only the nexthop binary:
python3 -m experiments --scenario grid/3x3 --binary nexthop

# List available scenarios and binaries:
python3 -m experiments --list
```

---

## Measuring patch size

```sh
# From repo root — net changed lines (unified diff):
diff -u node_agent/SimNode.cpp privatemesh/nexthop/SimNode.cpp \
  | grep '^[+-]' | grep -v '^---\|^+++' | wc -l
```

Target: ≤ 150 lines, concentrated in the six routing hooks.

---

## Open questions / future work

- **Strategy C impact measurement**: the flood-suppression horizon reduces relay
  participation, but the privacy gain (fewer witnesses per message) needs to be
  quantified against delivery-rate cost on sparse topologies.
- **Longer relay hashes**: moving to 2- or 4-byte relay hashes would eliminate
  the 1-byte collision problem but requires a MeshCore wire-format change.
- **Pinned entries**: the `RT_PINNED_BIT` infrastructure is in place but no
  mechanism yet sets the bit automatically (e.g. for frequently-used contacts).
- **`RT_ADMIT_PER_CYCLE` tuning**: the value of 4 is conservative; on a fast-
  moving network it may delay learning of new nodes after an age cycle.
- **Multi-path diversity**: the table stores only the *best* (shortest) path per
  destination; storing a secondary path would improve resilience to link loss.
