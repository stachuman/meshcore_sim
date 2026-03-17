# tools/fetch_topology.py

Downloads a live network map from any
[meshcore-mqtt-live-map](https://github.com/yellowcooln/meshcore-mqtt-live-map)
instance and converts it to simulator topology JSON — ready to run with
`python3 -m orchestrator`.

No external dependencies; stdlib only.

---

## Quick start

```sh
# 1. Check network size — no credentials needed
python3 tools/fetch_topology.py --stats --host live.bostonme.sh

# 2. Fetch the relay backbone (edges seen ≥ 5 times)
#    TOKEN = the hex string after ?token= in the live.bostonme.sh URL bar
python3 tools/fetch_topology.py \
    --token <TOKEN> \
    --only-relays --min-edge-count 5 \
    --output topologies/boston_relays.json --verbose

# 3. Simulate it
python3 -m orchestrator topologies/boston_relays.json --duration 120
```

---

## Authentication

The `/stats` endpoint is public (no credentials).  Fetching the full map
requires a token or a browser cookie.

**Use `--token` if you can — it is the most reliable method.**
The token is a long hex string embedded in the live-map URL and is also
accepted by the scraper directly.  See [Method 1](#method-1-token-from-the-url-easiest) below.

---

### Method 1 — token from the URL (easiest)

The live-map embeds the access token right in the page URL.

1. Open `https://live.bostonme.sh` in your browser and wait for the map
   to fully load.

2. Look at the browser's **address bar**.  The URL will look like:
   ```
   https://live.bostonme.sh/map?token=b94f921bec95f8fdcc0064310c91dd038f5c1e4c82f2fff9b50539a8ac0843e1
   ```

3. Copy everything after `?token=` — that long hex string is your token.

4. Pass it to `--token`:
   ```sh
   python3 tools/fetch_topology.py \
       --token b94f921bec95f8fdcc0064310c91dd038f5c1e4c82f2fff9b50539a8ac0843e1 \
       --only-relays --min-edge-count 5 \
       --output topologies/boston_relays.json --verbose
   ```

If the URL does not contain `?token=`, the server may be using cookie-only
auth — see [Method 2](#method-2-browser-cookie-fallback) below.

---

### Method 2 — browser cookie (fallback)

Use this if the URL has no `?token=` in it.

The live-map protects its data with a Cloudflare "bot check" that runs when
you first open the page.  Once you pass it, the server stores a session
cookie in your browser that lasts about 24 hours.  You can copy that
cookie and give it to the scraper.

**Step-by-step (Firefox shown; Chrome/Edge are identical):**

1. Open `https://live.bostonme.sh`.  Wait for the map to fully load —
   this completes the bot challenge.

2. Open DevTools: press **F12**, or right-click anywhere on the page →
   **Inspect**.

3. Click the **Network** tab at the top of the DevTools panel.
   *(If you can't see it, click the `»` arrow to find it in the overflow.)*

4. Reload the page (`Cmd+R` on Mac, `Ctrl+R` on Windows/Linux).
   A list of network requests will appear.

5. In the filter box, type `snapshot`.  Click the `/snapshot` request
   that appears.

6. In the right-hand panel, click **Headers** (or **Request Headers**).
   Find the row labelled **`cookie`** (lowercase).  It will look like:
   ```
   meshmap_auth=EqCYXS_xIcj7VEiQWBHsJUKm-GgaSCwVnKetKYC3uFM
   ```
   *(There may be additional cookies separated by `;`.)*

7. Copy the **entire value** — everything to the right of `cookie:`,
   not including the word `cookie:` itself.

8. Pass it to `--raw-cookie`:
   ```sh
   python3 tools/fetch_topology.py \
       --raw-cookie 'meshmap_auth=EqCYXS_xIcj7VEiQWBHsJUKm-GgaSCwVnKetKYC3uFM' \
       --only-relays --min-edge-count 5 \
       --output topologies/boston_relays.json --verbose
   ```

   If there were multiple cookies (e.g. `cf_clearance=…; meshmap_auth=…`),
   copy and pass all of them as one string.

> **Common mistake — including the header name:** do NOT pass
> `--raw-cookie 'Cookie: meshmap_auth=…'`.  The word `Cookie:` is the
> header name, not part of the value.  Pass only what comes after the
> colon.  The scraper strips this prefix automatically if you accidentally
> include it, but it is cleaner to omit it.

---

### Summary table

| Flag | What to pass | When to use |
|------|-------------|-------------|
| `--token HEX` | The hex string after `?token=` in the browser URL | Best option — use this if the URL contains a token |
| `--cookie VALUE` | Just the `meshmap_auth` cookie value (no `meshmap_auth=` prefix) | Quick alternative to `--raw-cookie` when there is only one cookie |
| `--raw-cookie STRING` | The full cookie header value from DevTools (everything after `cookie:`) | When the URL has no token, or when multiple cookies are needed |

---

## How it works

### Data source

`/snapshot` returns a JSON object with two main arrays:

| Key | Content |
|-----|---------|
| `devices` | Map of `device_id → {name, lat, lon, role, rssi, snr, …}` |
| `history_edges` | Array of `{a: [lat, lon], b: [lat, lon], count: N}` — observed RF links with packet counts |

History edges record endpoints as GPS coordinates rather than device IDs.
The scraper resolves coordinates to IDs by exact match first, then a 100 m
nearest-neighbour fallback to handle floating-point rounding.

### Conversion pipeline

1. **Build node list** — iterate `devices`; skip nodes without a GPS fix
   (`lat ≈ lon ≈ 0`).  If `--only-relays` is set, skip non-repeater nodes.
2. **Resolve edges** — for each `history_edge` with `count ≥ --min-edge-count`,
   resolve both endpoints to device IDs and compute the great-circle distance.
   Edges beyond `--max-distance-km` are dropped as implausible.
3. **Map roles** — `device_role = "repeater"` → `"relay": true`;
   `device_role = "room_server"` → `"room_server": true`; all others are
   plain endpoints.
4. **Signal quality** — SNR and RSSI on each edge are the mean of the two
   endpoint nodes' last-known values.  Loss defaults to 5 % (a reasonable
   outdoor LoRa estimate; tune per-edge after export).
5. **Write topology JSON** — edges are sorted by count descending so the
   strongest links appear first.

### Role mapping

| `device_role` in snapshot | Topology JSON field |
|---------------------------|---------------------|
| `"repeater"` | `"relay": true` |
| `"room_server"` | `"room_server": true` |
| `"companion"` / anything else | endpoint (neither field set) |

### Geographic coordinates

Every node in the output JSON includes `"lat"` and `"lon"` fields (WGS-84
decimal degrees, rounded to 6 decimal places ≈ 0.1 m precision), taken
directly from the live snapshot.  The simulator ignores these fields
entirely — they are carried through transparently so that future
visualisation tools can overlay the topology on a map without re-fetching
the snapshot.  Nodes without a valid GPS fix (lat ≈ lon ≈ 0) are excluded
from the output altogether.

---

## CLI reference

```
python3 tools/fetch_topology.py [OPTIONS]
```

### Authentication (mutually exclusive)

| Flag | Description |
|------|-------------|
| `--token TOKEN` | PROD_TOKEN bearer token |
| `--cookie VALUE` | `meshmap_auth` cookie value |
| `--raw-cookie STRING` | Cookie header **value only** — everything after `Cookie:`, e.g. `"cf_clearance=abc; meshmap_auth=xyz"` |

### Network source

| Flag | Default | Description |
|------|---------|-------------|
| `--host HOST` | `live.bostonme.sh` | Hostname of the live-map instance |
| `--stats` | — | Print public `/stats` summary and exit (no auth) |

### Filtering

| Flag | Default | Description |
|------|---------|-------------|
| `--min-edge-count N` | `1` | Drop edges observed fewer than N times |
| `--only-relays` | off | Include repeater nodes only |
| `--max-distance-km KM` | `50` | Drop edges longer than KM km |

### Output topology parameters

These are written into the `simulation` block of the output JSON and can
be overridden per-run with the orchestrator's CLI flags.

| Flag | Default | Description |
|------|---------|-------------|
| `--warmup-secs S` | `15` | `simulation.warmup_secs` |
| `--duration-secs S` | `120` | `simulation.duration_secs` |
| `--binary PATH` | `./node_agent/build/node_agent` | `simulation.default_binary` |
| `--seed N` | `42` | RNG seed |

### Diagnostics

| Flag | Description |
|------|-------------|
| `--output FILE` / `-o FILE` | Write JSON to file (default: stdout) |
| `--verbose` / `-v` | Print conversion statistics to stderr |
| `--debug` | Print HTTP response body on auth errors |

---

## Example: full Boston snapshot

```sh
# Stats (no credentials)
python3 tools/fetch_topology.py --stats
#   Host:              https://live.bostonme.sh
#   Mapped devices:    215
#   Seen devices:      273
#   Active routes:     10
#   History edges:     820
#   MQTT online:       19 (16 feeding)
#   Last activity:     2026-03-17 13:50:43 UTC

# Relay backbone, strong links only
python3 tools/fetch_topology.py \
    --token <TOKEN> \
    --only-relays --min-edge-count 5 \
    --output topologies/boston_relays.json \
    --verbose
#   Snapshot: 215 devices, 819 history edges
#   Output:   191 nodes (191 relays, 0 endpoints, 0 room-servers)
#             573 edges (min_count=5, max_dist=50.0 km)
#             edge count range: 5–2433
#             distance range:   0.01–38.8 km
#   Written to topologies/boston_relays.json

# Simulate
python3 -m orchestrator topologies/boston_relays.json --duration 60
```

---

## Caveats and tuning

- **Loss is a default estimate.** The scraper sets `loss: 0.05` (5 %) on
  every edge.  Real LoRa link quality varies widely; edit the JSON or add
  per-edge `a_to_b` / `b_to_a` overrides before running a serious experiment.
- **Coordinates are at last-seen position.** Nodes that were mobile may have
  moved since their last report.  Edge distances are computed from the
  reported coordinates.
- **Large topologies need more file descriptors.** The orchestrator raises the
  process FD limit automatically on startup (via `resource.setrlimit`), but if
  you run >400 nodes you may still need `ulimit -n 4096` in your shell before
  invoking the orchestrator.
- **Cookie lifetime.** Browser cookies (`meshmap_auth`) expire in approximately
  24 hours.  Bearer tokens (`--token`) do not expire unless revoked.
