"""results.py — Persistent simulation result storage and listing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from orchestrator.config import TopologyConfig, topology_to_dict


def save_result(
    topology_path: str,
    topology_config: TopologyConfig,
    trace_dict: dict[str, Any],
    output_dir: str = "output",
) -> Path:
    """Persist a simulation run to ``output/<topo_stem>/<timestamp>/``.

    Writes trace.json, topology.json, and metadata.json.
    Returns the run directory path.
    """
    now = datetime.now()
    topo_stem = Path(topology_path).stem
    ts_dir = now.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(output_dir) / topo_stem / ts_dir
    run_dir.mkdir(parents=True, exist_ok=True)

    # trace.json
    with open(run_dir / "trace.json", "w") as f:
        json.dump(trace_dict, f, indent=2)

    # topology.json
    topo_dict = topology_to_dict(topology_config)
    with open(run_dir / "topology.json", "w") as f:
        json.dump(topo_dict, f, indent=2)

    # metadata.json
    packets = trace_dict.get("packets", [])
    n_collisions = sum(len(p.get("collisions", [])) for p in packets)

    sim = topology_config.simulation
    radio = topology_config.radio

    meta: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "topology_name": topo_stem,
        "topology_path": topology_path,
        "n_packets": len(packets),
        "n_collisions": n_collisions,
        "duration_secs": sim.duration_secs,
        "warmup_secs": sim.warmup_secs,
        "seed": sim.seed,
    }
    if radio is not None:
        meta["radio"] = {
            "sf": radio.sf,
            "bw_hz": radio.bw_hz,
            "cr": radio.cr,
        }

    with open(run_dir / "metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    return run_dir


def list_runs(output_dir: str = "output") -> list[dict[str, Any]]:
    """Scan ``output/*/*/metadata.json`` and return run summaries sorted newest-first.

    Each entry: {run_dir, timestamp, topology_name, n_packets, n_collisions, label}.
    """
    base = Path(output_dir)
    if not base.is_dir():
        return []

    runs: list[dict[str, Any]] = []
    for meta_path in base.glob("*/*/metadata.json"):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        run_dir = str(meta_path.parent)
        topo_name = meta.get("topology_name", "?")
        ts_raw = meta.get("timestamp", "")
        n_pkts = meta.get("n_packets", 0)

        # Build a short time string for the label
        try:
            dt = datetime.fromisoformat(ts_raw)
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = ts_raw[:16]

        label = f"{topo_name} / {time_str} ({n_pkts} pkts)"

        runs.append({
            "run_dir": run_dir,
            "timestamp": ts_raw,
            "topology_name": topo_name,
            "n_packets": n_pkts,
            "n_collisions": meta.get("n_collisions", 0),
            "label": label,
        })

    # Sort newest-first
    runs.sort(key=lambda r: r["timestamp"], reverse=True)
    return runs


def load_run(run_dir: str) -> Optional[dict[str, Any]]:
    """Load and return trace.json from a run directory, or None on error."""
    trace_path = Path(run_dir) / "trace.json"
    if not trace_path.is_file():
        return None
    try:
        with open(trace_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
