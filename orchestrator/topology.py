"""
topology.py — Adjacency graph built from TopologyConfig.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import TopologyConfig, NodeConfig, EdgeConfig, DirectionalOverrides


@dataclass
class EdgeLink:
    """One directed view of an edge (towards `other`)."""
    other: str
    loss: float
    latency_ms: float
    snr: float


def _resolve(base: float, override: float | None) -> float:
    """Return override if explicitly set, otherwise the symmetric base value."""
    return override if override is not None else base


class Topology:
    def __init__(self, config: TopologyConfig) -> None:
        self._node_map: dict[str, NodeConfig] = {
            n.name: n for n in config.nodes
        }
        # Build adjacency list — each direction may have independent parameters.
        self._adj: dict[str, list[EdgeLink]] = {
            n.name: [] for n in config.nodes
        }
        for edge in config.edges:
            fwd = edge.a_to_b  # overrides for a → b
            rev = edge.b_to_a  # overrides for b → a

            self._adj[edge.a].append(EdgeLink(
                other=edge.b,
                loss=      _resolve(edge.loss,       fwd.loss       if fwd else None),
                latency_ms=_resolve(edge.latency_ms, fwd.latency_ms if fwd else None),
                snr=       _resolve(edge.snr,        fwd.snr        if fwd else None),
            ))
            self._adj[edge.b].append(EdgeLink(
                other=edge.a,
                loss=      _resolve(edge.loss,       rev.loss       if rev else None),
                latency_ms=_resolve(edge.latency_ms, rev.latency_ms if rev else None),
                snr=       _resolve(edge.snr,        rev.snr        if rev else None),
            ))

    def neighbours(self, node_name: str) -> list[EdgeLink]:
        """Return all nodes directly reachable from node_name."""
        return self._adj.get(node_name, [])

    def node_config(self, name: str) -> NodeConfig:
        return self._node_map[name]

    def all_names(self) -> list[str]:
        return list(self._node_map)

    def endpoint_names(self) -> list[str]:
        """Non-relay nodes only."""
        return [n for n, cfg in self._node_map.items() if not cfg.relay]

    def relay_names(self) -> list[str]:
        return [n for n, cfg in self._node_map.items() if cfg.relay]
