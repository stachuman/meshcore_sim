#!/usr/bin/env python3
"""
tools/import_topology.py — Convert meshcore-optimizer topology JSON to
simulator topology JSON.

The meshcore-optimizer tool maps a live MeshCore network by probing nodes
via traces and neighbor tables.  Its output has gaps that make it unsuitable
for direct simulation: incomplete edges, no companion endpoints, stub nodes,
directed-only edges, no loss/latency, no radio/simulation section, and
emoji-laden names.

This tool fills those gaps:
  1. Filters stub nodes (no coordinates, short prefixes)
  2. Sanitises names (strips emoji / non-ASCII, truncates)
  3. Merges directed edges into undirected with directional overrides
  4. Estimates missing edges via log-distance path loss
  5. Maps SNR to packet loss probability
  6. Adds companion endpoint nodes
  7. Adds radio + simulation sections

No external dependencies — stdlib only.

Examples
--------
# Basic conversion with 5 companion endpoints (default):
python3 tools/import_topology.py ../meshcore-optimizer/topology.json \\
    --output topologies/gdansk.json --verbose

# Disable gap-filling, custom radio params:
python3 tools/import_topology.py topology.json \\
    --no-fill-gaps --sf 12 --bw-hz 125000 -o output.json -v
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Source confidence ranking: higher = more trustworthy
_SOURCE_RANK = {"neighbors": 3, "trace": 2, "inferred": 1}

# SNR-to-loss mapping table (lower bound → loss)
_SNR_LOSS_TABLE = [
    (6.0,   0.02),   # Excellent
    (0.0,   0.05),   # Good
    (-6.0,  0.10),   # Marginal
    (-10.0, 0.20),   # Poor
    (-12.0, 0.35),   # Near limit
]
_SNR_LOSS_FLOOR = 0.50  # SNR < -12 dB

# Log-distance path loss model parameters (suburban outdoor)
_SNR_REF = 10.0     # dB at reference distance
_D_REF_KM = 1.0     # reference distance in km
_PATH_LOSS_N = 3.0   # path loss exponent
_SNR_FLOOR = -12.0   # LoRa SF10 demodulation floor (dB)
_MAX_GAP_KM = 30.0   # max practical LoRa range (km)

# Reverse-direction SNR penalty when only one direction is measured
_REVERSE_SNR_PENALTY = 5.0  # dB

# Companion link parameters (phone sitting next to repeater)
_COMPANION_SNR = 12.0
_COMPANION_RSSI = -60.0
_COMPANION_LOSS = 0.02
_COMPANION_LATENCY_MS = 5.0

# EU Narrow defaults — standard European MeshCore configuration.
# Edit the radio section in the output JSON to match your network.
_DEFAULT_SF = 8
_DEFAULT_BW_HZ = 62_500
_DEFAULT_CR = 4        # CR4/8
_DEFAULT_PREAMBLE = 8

# Companion name sequence
_COMPANION_NAMES = [
    "alice", "bob", "charlie", "dave", "eve", "frank",
    "grace", "heidi", "ivan", "judy", "karl", "linda",
    "mike", "nancy", "oscar", "peggy", "quinn", "ruth",
    "steve", "tina", "ursula", "victor", "wendy", "xavier",
    "yvonne", "zack",
]


# ---------------------------------------------------------------------------
# Geo helpers (duplicated from fetch_topology.py — stdlib only)
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (WGS-84 sphere approximation)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Name sanitisation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"[^A-Za-z0-9_ -]")


def sanitize_name(raw_name: str, prefix: str) -> str:
    """Strip emoji/non-ASCII, truncate to 20 chars, fall back to prefix."""
    clean = _NAME_RE.sub("", raw_name).strip()
    # Collapse runs of spaces/underscores
    clean = re.sub(r"[\s_]+", "_", clean)
    # Strip leading/trailing underscores or hyphens
    clean = clean.strip("_-")
    if not clean:
        clean = prefix
    return clean[:20]


# ---------------------------------------------------------------------------
# SNR → loss mapping
# ---------------------------------------------------------------------------

def snr_to_loss(snr: float) -> float:
    """Map SNR (dB) to packet loss probability."""
    for threshold, loss in _SNR_LOSS_TABLE:
        if snr >= threshold:
            return loss
    return _SNR_LOSS_FLOOR


# ---------------------------------------------------------------------------
# Gap-fill: estimate SNR from distance
# ---------------------------------------------------------------------------

def estimate_snr(distance_km: float) -> float:
    """Log-distance path loss model: SNR_est = SNR_ref - 10*n*log10(d/d_ref)."""
    if distance_km <= 0:
        return _SNR_REF
    return _SNR_REF - 10.0 * _PATH_LOSS_N * math.log10(distance_km / _D_REF_KM)


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def import_topology(
    optimizer_json: dict,
    *,
    companions: int = 5,
    fill_gaps: bool = True,
    max_gap_km: float = _MAX_GAP_KM,
    sf: int = _DEFAULT_SF,
    bw_hz: int = _DEFAULT_BW_HZ,
    cr: int = _DEFAULT_CR,
    preamble_symbols: int = _DEFAULT_PREAMBLE,
    verbose: bool = False,
) -> dict:
    """Convert meshcore-optimizer JSON to simulator topology JSON."""

    raw_nodes = optimizer_json.get("nodes", {})
    raw_edges = optimizer_json.get("edges", [])

    # ------------------------------------------------------------------
    # 1. Parse & filter nodes
    # ------------------------------------------------------------------
    nodes = {}  # prefix → node dict
    total_raw = len(raw_nodes)
    skipped_coords = 0
    skipped_prefix = 0

    for prefix, ndata in raw_nodes.items():
        # Drop stub nodes: short prefix
        if len(prefix) < 8:
            skipped_prefix += 1
            continue

        lat = float(ndata.get("lat", 0.0))
        lon = float(ndata.get("lon", 0.0))

        # Drop stub nodes: no coordinates
        if lat == 0.0 and lon == 0.0:
            skipped_coords += 1
            continue

        name = sanitize_name(ndata.get("name", prefix), prefix)

        # Ensure unique names
        base_name = name
        counter = 2
        existing_names = {n["name"] for n in nodes.values()}
        while name in existing_names:
            name = f"{base_name[:17]}_{counter}"
            counter += 1

        nodes[prefix] = {
            "name": name,
            "prefix": prefix,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "relay": True,  # all real nodes are infrastructure repeaters
        }

    surviving_prefixes = set(nodes.keys())

    if verbose:
        print(f"  Nodes: {total_raw} raw → {len(nodes)} surviving "
              f"(dropped {skipped_prefix} short-prefix, "
              f"{skipped_coords} no-coords)", file=sys.stderr)

    # ------------------------------------------------------------------
    # 2. Merge directed edges into undirected with directional overrides
    # ------------------------------------------------------------------
    # Group by unordered pair
    pair_edges: dict[frozenset, list[dict]] = {}
    edges_dropped = 0

    for e in raw_edges:
        src = e.get("from", "")
        dst = e.get("to", "")

        # Drop edges where either endpoint was filtered out
        if src not in surviving_prefixes or dst not in surviving_prefixes:
            edges_dropped += 1
            continue

        pair = frozenset([src, dst])
        if pair not in pair_edges:
            pair_edges[pair] = []
        pair_edges[pair].append(e)

    # Build merged undirected edges
    merged_edges = []  # list of edge dicts
    edge_set = set()  # frozenset of (prefix_a, prefix_b) for gap detection

    for pair, directed_list in pair_edges.items():
        endpoints = sorted(pair)  # stable ordering
        a_prefix, b_prefix = endpoints[0], endpoints[1]

        # Separate A→B and B→A edges, picking best by source confidence
        a_to_b_edges = [e for e in directed_list
                        if e["from"] == a_prefix and e["to"] == b_prefix]
        b_to_a_edges = [e for e in directed_list
                        if e["from"] == b_prefix and e["to"] == a_prefix]

        def best_edge(candidates: list[dict]) -> Optional[dict]:
            if not candidates:
                return None
            return max(candidates,
                       key=lambda e: _SOURCE_RANK.get(e.get("source", ""), 0))

        best_ab = best_edge(a_to_b_edges)
        best_ba = best_edge(b_to_a_edges)

        if best_ab is None and best_ba is None:
            continue

        # Extract SNR values
        # A→B: SNR measured at B (what B sees when A transmits)
        # B→A: SNR measured at A (what A sees when B transmits)
        if best_ab is not None and best_ba is not None:
            snr_a_to_b = float(best_ab.get("snr_db", 0.0))
            snr_b_to_a = float(best_ba.get("snr_db", 0.0))
        elif best_ab is not None:
            snr_a_to_b = float(best_ab.get("snr_db", 0.0))
            snr_b_to_a = snr_a_to_b - _REVERSE_SNR_PENALTY
        else:
            snr_b_to_a = float(best_ba.get("snr_db", 0.0))
            snr_a_to_b = snr_b_to_a - _REVERSE_SNR_PENALTY

        loss_ab = snr_to_loss(snr_a_to_b)
        loss_ba = snr_to_loss(snr_b_to_a)

        edge = {
            "a": nodes[a_prefix]["name"],
            "b": nodes[b_prefix]["name"],
            "loss": loss_ab,
            "latency_ms": 20.0,
            "snr": round(snr_a_to_b, 2),
            "rssi": -90.0,
        }

        # Add directional overrides if the two directions differ
        if abs(snr_a_to_b - snr_b_to_a) > 0.01 or abs(loss_ab - loss_ba) > 0.001:
            edge["a_to_b"] = {
                "snr": round(snr_a_to_b, 2),
                "loss": loss_ab,
            }
            edge["b_to_a"] = {
                "snr": round(snr_b_to_a, 2),
                "loss": loss_ba,
            }

        merged_edges.append(edge)
        edge_set.add(frozenset([a_prefix, b_prefix]))

    if verbose:
        print(f"  Edges: {len(raw_edges)} directed → {len(merged_edges)} "
              f"undirected (dropped {edges_dropped} with filtered endpoints)",
              file=sys.stderr)

    # ------------------------------------------------------------------
    # 3. Estimate missing edges (gap-filling)
    # ------------------------------------------------------------------
    gap_filled = 0

    if fill_gaps:
        prefix_list = sorted(nodes.keys())
        for i, p1 in enumerate(prefix_list):
            for p2 in prefix_list[i + 1:]:
                pair = frozenset([p1, p2])
                if pair in edge_set:
                    continue

                n1 = nodes[p1]
                n2 = nodes[p2]
                dist = haversine_km(n1["lat"], n1["lon"], n2["lat"], n2["lon"])

                if dist > max_gap_km:
                    continue

                snr_est = estimate_snr(dist)
                if snr_est <= _SNR_FLOOR:
                    continue

                loss = snr_to_loss(snr_est)

                merged_edges.append({
                    "a": n1["name"],
                    "b": n2["name"],
                    "loss": loss,
                    "latency_ms": 20.0,
                    "snr": round(snr_est, 2),
                    "rssi": -90.0,
                })
                edge_set.add(pair)
                gap_filled += 1

        if verbose:
            print(f"  Gap-fill: {gap_filled} estimated edges added "
                  f"(max {max_gap_km} km, SNR floor {_SNR_FLOOR} dB)",
                  file=sys.stderr)

    # ------------------------------------------------------------------
    # 4. Build output node list (relays only, before companions)
    # ------------------------------------------------------------------
    nodes_out = []
    for prefix in sorted(nodes.keys()):
        n = nodes[prefix]
        nodes_out.append({
            "name": n["name"],
            "relay": True,
            "lat": n["lat"],
            "lon": n["lon"],
        })

    # ------------------------------------------------------------------
    # 5. Add companion endpoints
    # ------------------------------------------------------------------
    if companions > 0:
        # Random relay selection (seeded for reproducibility)
        if len(nodes_out) > 0:
            rng = random.Random(42)
            relay_list = [(n["name"], n["lat"], n["lon"]) for n in nodes_out]
            n_companions = min(companions, len(relay_list), len(_COMPANION_NAMES))
            selected = rng.sample(relay_list, n_companions)

            # Create companion nodes and edges
            for i, (relay_name, lat, lon) in enumerate(selected):
                comp_name = _COMPANION_NAMES[i]
                nodes_out.append({
                    "name": comp_name,
                    "relay": False,
                    "lat": lat,
                    "lon": lon,
                })
                merged_edges.append({
                    "a": comp_name,
                    "b": relay_name,
                    "snr": _COMPANION_SNR,
                    "rssi": _COMPANION_RSSI,
                    "loss": _COMPANION_LOSS,
                    "latency_ms": _COMPANION_LATENCY_MS,
                })

            if verbose:
                print(f"  Companions: {len(selected)} added "
                      f"({', '.join(_COMPANION_NAMES[i] for i in range(len(selected)))})",
                      file=sys.stderr)
                for i, (relay_name, _, _) in enumerate(selected):
                    print(f"    {_COMPANION_NAMES[i]} → {relay_name}",
                          file=sys.stderr)

    # ------------------------------------------------------------------
    # 6. Compute warmup and assemble topology
    # ------------------------------------------------------------------
    node_count = len(nodes_out)
    # Auto-compute warmup: stagger + propagation margin
    # Use the same formula as airtime.py: node_count * airtime * margin
    t_sym_ms = (2 ** sf) / (bw_hz / 1000.0)
    de = 1 if t_sym_ms >= 16.0 else 0
    numerator = 8 * 40 - 4 * sf + 28 + 16 - 0  # 40-byte advert, CRC, explicit header
    denominator = 4 * (sf - 2 * de)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)
    t_preamble_ms = (8 + 4.25) * t_sym_ms
    airtime_ms = t_preamble_ms + payload_symbols * t_sym_ms
    warmup_secs = max(10.0, node_count * airtime_ms / 1000.0 * 2.0)

    topology = {
        "radio": {
            "sf": sf,
            "bw_hz": bw_hz,
            "cr": cr,
            "preamble_symbols": preamble_symbols,
        },
        "nodes": nodes_out,
        "edges": merged_edges,
        "simulation": {
            "warmup_secs": round(warmup_secs, 1),
            "duration_secs": 300.0,
            "traffic_interval_secs": 15.0,
            "advert_interval_secs": 60.0,
            "default_binary": "./node_agent/build/node_agent",
            "seed": 42,
        },
    }

    if verbose:
        print(f"  Output: {len(nodes_out)} nodes, {len(merged_edges)} edges",
              file=sys.stderr)
        print(f"  Radio:  SF{sf} / BW{bw_hz // 1000} kHz / CR4/{cr + 4}",
              file=sys.stderr)
        print(f"  Warmup: {warmup_secs:.1f}s (auto-computed from {node_count} nodes)",
              file=sys.stderr)

    return topology


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert meshcore-optimizer topology JSON to simulator format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input", metavar="INPUT",
        help="Path to meshcore-optimizer topology.json",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="Output path (default: stdout)",
    )
    parser.add_argument(
        "--companions", type=int, default=5, metavar="N",
        help="Number of companion endpoints to add (default: 5)",
    )
    parser.add_argument(
        "--no-fill-gaps", action="store_true",
        help="Don't add estimated edges for unobserved node pairs",
    )
    parser.add_argument(
        "--max-gap-km", type=float, default=_MAX_GAP_KM, metavar="KM",
        help=f"Max distance for gap-fill edges (default: {_MAX_GAP_KM})",
    )

    radio = parser.add_argument_group(
        "radio parameters",
        "LoRa radio configuration written into the output JSON.",
    )
    radio.add_argument(
        "--sf", type=int, default=_DEFAULT_SF, metavar="N",
        help=f"Spreading factor 7-12 (default: {_DEFAULT_SF})",
    )
    radio.add_argument(
        "--bw-hz", type=int, default=_DEFAULT_BW_HZ, metavar="HZ",
        dest="bw_hz",
        help=f"Bandwidth in Hz (default: {_DEFAULT_BW_HZ})",
    )
    radio.add_argument(
        "--cr", type=int, default=_DEFAULT_CR, metavar="N",
        help=f"Coding-rate offset: 1=CR4/5, 2=CR4/6, 3=CR4/7, 4=CR4/8 (default: {_DEFAULT_CR})",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print conversion statistics to stderr",
    )

    args = parser.parse_args()

    with open(args.input) as f:
        optimizer_json = json.load(f)

    topology = import_topology(
        optimizer_json,
        companions=args.companions,
        fill_gaps=not args.no_fill_gaps,
        max_gap_km=args.max_gap_km,
        sf=args.sf,
        bw_hz=args.bw_hz,
        cr=args.cr,
        verbose=args.verbose,
    )

    out = json.dumps(topology, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(out)
            fh.write("\n")
        if args.verbose:
            print(f"  Written to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
