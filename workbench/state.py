"""state.py — Per-session shared state for the workbench."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from orchestrator.config import TopologyConfig


@dataclass
class AppState:
    """Mutable state shared across all tabs within a single browser session."""

    # -- Topology --
    topology: Optional[TopologyConfig] = None
    topology_path: Optional[str] = None
    dirty: bool = False  # unsaved topology edits

    # -- Map references (set by app.py after render) --
    leaflet_map: Any = None                               # ui.leaflet instance
    markers: dict = field(default_factory=dict)            # {node_name: Marker}
    edge_layers: list = field(default_factory=list)        # [(EdgeConfig, GenericLayer)]
    positions: dict = field(default_factory=dict)          # {node_name: (lat, lon)}

    # -- Trace --
    trace: Optional[dict] = None
    event_timeline: list = field(default_factory=list)
    current_time: float = 0.0

    # -- Playback --
    playing: bool = False
    play_speed: float = 1.0
    type_filter: Optional[set] = None

    # -- Simulation --
    sim_running: bool = False
    sim_log: list = field(default_factory=list)
    _sim_ui: Any = None            # SimpleNamespace of form widget refs (set by sim_panel)
    _sim_log_widget: Any = None    # ui.log widget (set by sim_panel)

    # -- Trace viewer (set by trace_viewer.py) --
    _trace_ui: Any = None              # SimpleNamespace of trace viewer widget refs
    _refresh_trace_main: Any = None    # refresh function for trace main panel
    _refresh_trace_sidebar: Any = None # refresh function for trace sidebar

    # -- Selection --
    selected_node: Optional[str] = None
    selected_edge: Optional[tuple] = None
    _selected_pkt_idx: Optional[int] = None  # packet clicked in trace table

    # -- Event inspection (Phase 5) --
    _selected_node_name: Optional[str] = None
    _selected_edge_key: Optional[tuple] = None
    _node_trace_stats: Optional[dict] = None
    _edge_trace_stats: Optional[dict] = None
    _refresh_detail_panel: Any = None

    # -- Persistent results --
    _output_dir: str = "output"
    _available_runs: list = field(default_factory=list)
    _current_run_dir: Optional[str] = None
    _refresh_run_selector: Any = None
