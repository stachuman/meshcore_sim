"""
compare.py — side-by-side metric comparison of SimResult objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from experiments.runner import SimResult


# ---------------------------------------------------------------------------
# ComparisonTable
# ---------------------------------------------------------------------------

@dataclass
class ComparisonTable:
    """
    Holds a list of SimResult objects from the same scenario run under
    different binaries (or different configurations) and renders them as a
    formatted comparison table.
    """
    scenario_name: str
    results: list[SimResult]

    # ---- rendering ----

    def print(self) -> None:
        """Print a human-readable comparison table to stdout."""
        print(self._render())

    def _render(self) -> str:
        lines: list[str] = []
        sep = "=" * 72
        lines.append(sep)
        lines.append(f"  Experiment comparison: {self.scenario_name}")
        lines.append(sep)

        if not self.results:
            lines.append("  (no results)")
            lines.append(sep)
            return "\n".join(lines)

        # Column widths.
        label_w = max(len(r.label) for r in self.results)
        label_w = max(label_w, len("Variant"))

        # Header.
        h = (f"  {'Variant':<{label_w}}  {'Delivery':>8}  {'Avg witness':>11}"
             f"  {'Flood wc':>8}  {'Direct wc':>9}  {'Latency ms':>10}"
             f"  {'Pkt bytes':>9}  {'Hops':>6}  {'Collisions':>10}  {'Time s':>7}")
        lines.append("")
        lines.append(h)
        lines.append("  " + "-" * (len(h) - 2))

        for r in self.results:
            delivery = f"{r.delivery_rate * 100:.1f}%"
            avg_wc   = f"{r.avg_witness_count:.1f}"
            flood_wc = str(r.flood_witness_count)
            dir_wc   = str(r.direct_witness_count)
            lat      = f"{r.avg_latency_ms:.0f}"
            pkt_sz   = f"{r.avg_packet_size_bytes:.1f}"
            hops     = str(r.total_hops)
            coll     = str(r.collision_count)
            elapsed  = f"{r.elapsed_s:.1f}"
            lines.append(
                f"  {r.label:<{label_w}}  {delivery:>8}  {avg_wc:>11}"
                f"  {flood_wc:>8}  {dir_wc:>9}  {lat:>10}"
                f"  {pkt_sz:>9}  {hops:>6}  {coll:>10}  {elapsed:>7}"
            )

        # Reduction ratios (only when exactly 2 results and same scenario).
        if len(self.results) == 2:
            a, b = self.results
            lines.append("")
            lines.append("  Deltas (second minus first):")
            _delta_pct("  Delivery rate", a.delivery_rate, b.delivery_rate,
                       unit="%", scale=100, lines=lines)
            _delta("  Avg witness count", a.avg_witness_count, b.avg_witness_count,
                   lines=lines)
            _ratio("  Flood→direct witness reduction", a.flood_witness_count,
                   b.flood_witness_count, lines=lines)
            _delta("  Avg TXT packet size (bytes)", a.avg_packet_size_bytes,
                   b.avg_packet_size_bytes, lines=lines)
            _delta("  Total hops", a.total_hops, b.total_hops, lines=lines)

        lines.append(sep)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable summary of all results."""
        return {
            "scenario": self.scenario_name,
            "results": [
                {
                    "label":               r.label,
                    "binary":              r.binary,
                    "delivery_rate":       r.delivery_rate,
                    "avg_witness_count":   r.avg_witness_count,
                    "flood_witness_count": r.flood_witness_count,
                    "direct_witness_count": r.direct_witness_count,
                    "avg_latency_ms":          r.avg_latency_ms,
                    "avg_packet_size_bytes":   r.avg_packet_size_bytes,
                    "total_hops":              r.total_hops,
                    "collision_count":     r.collision_count,
                    "elapsed_s":           r.elapsed_s,
                }
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def compare(
    results: list[SimResult],
    scenario_name: Optional[str] = None,
) -> ComparisonTable:
    """
    Build a ComparisonTable from a list of SimResult objects.

    Parameters
    ----------
    results:
        List of SimResult objects, typically from the same Scenario run with
        different binaries.
    scenario_name:
        Override for the table header.  Defaults to the scenario_name of the
        first result, or "?" if the list is empty.
    """
    if scenario_name is None:
        scenario_name = results[0].scenario_name if results else "?"
    return ComparisonTable(scenario_name=scenario_name, results=results)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _delta(label: str, a: float, b: float, lines: list[str]) -> None:
    diff = b - a
    sign = "+" if diff > 0 else ""
    lines.append(f"    {label}: {sign}{diff:.1f}")


def _delta_pct(label: str, a: float, b: float, unit: str,
               scale: float, lines: list[str]) -> None:
    diff = (b - a) * scale
    sign = "+" if diff > 0 else ""
    lines.append(f"    {label}: {sign}{diff:.1f}{unit}")


def _ratio(label: str, a: float, b: float, lines: list[str]) -> None:
    if b == 0:
        lines.append(f"    {label}: N/A (denominator=0)")
        return
    ratio = a / b
    lines.append(f"    {label}: {ratio:.2f}×  ({a:.0f} → {b:.0f})")
