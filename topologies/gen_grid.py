#!/usr/bin/env python3
"""
gen_grid.py — Generate a rectangular grid MeshCore topology.

Usage:
    python3 topologies/gen_grid.py [ROWS [COLS]] [options]
    python3 topologies/gen_grid.py 10 10 -o topologies/grid_10x10.json
    python3 topologies/gen_grid.py 5 5 --loss 0.0 --latency 10

Grid layout:
    n_0_0  ──  n_0_1  ──  ...  ──  n_0_{C-1}
      |                                |
    n_1_0  ──  n_1_1  ──  ...  ──  n_1_{C-1}
      |                                |
    ...                              ...
      |                                |
    n_{R-1}_0 ── ... ── n_{R-1}_{C-1}

Traffic corners:
    SOURCE:      n_0_0           (endpoint)
    DESTINATION: n_{R-1}_{C-1}  (endpoint)
    ALL OTHERS:  relays

Edges connect orthogonally adjacent nodes only (4-connectivity).
"""

from __future__ import annotations

import argparse
import json
import sys


def node_name(row: int, col: int) -> str:
    return f"n_{row}_{col}"


def gen_grid(
    rows: int,
    cols: int,
    loss: float = 0.02,
    latency_ms: float = 20.0,
    snr: float = 8.0,
    rssi: float = -85.0,
    warmup_secs: float = 10.0,
    duration_secs: float = 120.0,
    traffic_interval_secs: float = 10.0,
    advert_interval_secs: float = 30.0,
    default_binary: str = "./node_agent/build/node_agent",
) -> dict:
    """Return a topology dict suitable for json.dumps()."""

    src = node_name(0, 0)
    dst = node_name(rows - 1, cols - 1)

    nodes = []
    for r in range(rows):
        for c in range(cols):
            name = node_name(r, c)
            is_relay = name not in (src, dst)
            nodes.append({"name": name, "relay": is_relay})

    edges = []
    for r in range(rows):
        for c in range(cols):
            # Horizontal: (r,c) ↔ (r, c+1)
            if c + 1 < cols:
                edges.append({
                    "a": node_name(r, c),
                    "b": node_name(r, c + 1),
                    "loss": loss,
                    "latency_ms": latency_ms,
                    "snr": snr,
                    "rssi": rssi,
                })
            # Vertical: (r,c) ↔ (r+1, c)
            if r + 1 < rows:
                edges.append({
                    "a": node_name(r, c),
                    "b": node_name(r + 1, c),
                    "loss": loss,
                    "latency_ms": latency_ms,
                    "snr": snr,
                    "rssi": rssi,
                })

    return {
        "_comment": (
            f"{rows}×{cols} grid topology. "
            f"SOURCE={src} (endpoint) → DEST={dst} (endpoint). "
            f"All {rows * cols - 2} interior nodes are relays."
        ),
        "nodes": nodes,
        "edges": edges,
        "simulation": {
            "warmup_secs": warmup_secs,
            "duration_secs": duration_secs,
            "traffic_interval_secs": traffic_interval_secs,
            "advert_interval_secs": advert_interval_secs,
            "default_binary": default_binary,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("rows", type=int, nargs="?", default=10,
                   help="Number of rows (default 10)")
    p.add_argument("cols", type=int, nargs="?", default=None,
                   help="Number of cols (default = rows, i.e. square grid)")
    p.add_argument("--loss",    type=float, default=0.02,
                   help="Packet loss probability [0,1] (default 0.02)")
    p.add_argument("--latency", type=float, default=20.0,
                   help="One-way propagation delay ms per hop (default 20)")
    p.add_argument("--snr",     type=float, default=8.0,
                   help="SNR dB (default 8)")
    p.add_argument("--rssi",    type=float, default=-85.0,
                   help="RSSI dBm (default -85)")
    p.add_argument("--warmup",           type=float, default=10.0,
                   help="Warmup secs before traffic starts (default 10)")
    p.add_argument("--duration",         type=float, default=120.0,
                   help="Total simulation duration secs (default 120)")
    p.add_argument("--traffic-interval", type=float, default=10.0,
                   help="Mean secs between random sends (default 10)")
    p.add_argument("--advert-interval",  type=float, default=30.0,
                   help="Advertisement re-flood interval secs (default 30)")
    p.add_argument("-o", "--out", help="Output file (default: stdout)")
    args = p.parse_args()

    cols = args.cols if args.cols is not None else args.rows
    if args.rows < 2 or cols < 2:
        p.error("Grid must be at least 2×2")

    topo = gen_grid(
        rows=args.rows,
        cols=cols,
        loss=args.loss,
        latency_ms=args.latency,
        snr=args.snr,
        rssi=args.rssi,
        warmup_secs=args.warmup,
        duration_secs=args.duration,
        traffic_interval_secs=args.traffic_interval,
        advert_interval_secs=args.advert_interval,
    )
    text = json.dumps(topo, indent=2) + "\n"

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"Written {args.rows}×{cols} grid to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
