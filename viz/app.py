"""
app.py — Dash application factory for MeshCore topology visualisation.

Phase 1: static topology viewer.
  - Geo-aware layout when every node carries a non-zero lat/lon: renders on an
    OpenStreetMap tile layer (no API key required).
  - Force-directed layout (dash-cytoscape "cose") for synthetic topologies
    that have no geographic coordinates.

Usage:
    from viz.app import create_app
    app = create_app(pathlib.Path("topologies/boston_relays.json"))
    app.run(port=8050)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dash
import dash_cytoscape as cyto
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

# Register the extended cytoscape layout algorithms (cose-bilkent, etc.)
cyto.load_extra_layouts()

# ── Colour palette ────────────────────────────────────────────────────────────

_ROLE_COLOUR: dict[str, str] = {
    "relay":       "#3a86ff",   # blue
    "room_server": "#ffbe0b",   # amber
    "endpoint":    "#8d99ae",   # grey
}
_EDGE_COLOUR = "#adb5bd"
_EDGE_COLOUR_GEO = "rgba(173,181,189,0.6)"

# ── Helpers ───────────────────────────────────────────────────────────────────

_LABEL_LEN = 8   # characters shown on the map/graph; full ID still in hover


def _short(name: str) -> str:
    """Return a display label: first _LABEL_LEN chars, ellipsis if truncated."""
    return name[:_LABEL_LEN] + "…" if len(name) > _LABEL_LEN else name


def _load(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def _node_role(n: dict) -> str:
    if n.get("room_server"):
        return "room_server"
    if n.get("relay"):
        return "relay"
    return "endpoint"


def _has_geo(nodes: list[dict]) -> bool:
    """True when every node has a non-zero lat/lon pair."""
    if not nodes:
        return False
    for n in nodes:
        lat = n.get("lat")
        lon = n.get("lon")
        if lat is None or lon is None:
            return False
        # Nodes stuck at 0,0 are treated as having no fix
        if float(lat) == 0.0 and float(lon) == 0.0:
            return False
    return True


# ── Geo-map figure (Plotly scattermapbox) ─────────────────────────────────────

def _geo_figure(nodes: list[dict], edges: list[dict]) -> go.Figure:
    node_by_name = {n["name"]: n for n in nodes}

    # --- edge lines ---
    # Build a single trace with None separators between segments
    edge_lats: list[Any] = []
    edge_lons: list[Any] = []
    for e in edges:
        na = node_by_name.get(e["a"])
        nb = node_by_name.get(e["b"])
        if na is None or nb is None:
            continue
        edge_lats += [float(na["lat"]), float(nb["lat"]), None]
        edge_lons += [float(na["lon"]), float(nb["lon"]), None]

    edge_trace = go.Scattermapbox(
        lat=edge_lats,
        lon=edge_lons,
        mode="lines",
        line=dict(width=1, color=_EDGE_COLOUR_GEO),
        hoverinfo="none",
        showlegend=False,
    )

    # --- one node trace per role (groups the legend) ---
    role_buckets: dict[str, dict[str, list]] = {}
    for n in nodes:
        role = _node_role(n)
        if role not in role_buckets:
            role_buckets[role] = {"lats": [], "lons": [], "texts": []}
        b = role_buckets[role]
        b["lats"].append(float(n["lat"]))
        b["lons"].append(float(n["lon"]))
        b["texts"].append(
            f"<b>{_short(n['name'])}</b><br>"
            f"{n['name']}<br>"
            f"role: {role}<br>"
            f"lat: {n['lat']:.5f}  lon: {n['lon']:.5f}"
        )

    node_traces = [
        go.Scattermapbox(
            lat=b["lats"],
            lon=b["lons"],
            mode="markers",
            marker=dict(size=8, color=_ROLE_COLOUR.get(role, "#8d99ae")),
            text=b["texts"],
            hoverinfo="text",
            name=role,
        )
        for role, b in role_buckets.items()
    ]

    centre_lat = sum(float(n["lat"]) for n in nodes) / len(nodes)
    centre_lon = sum(float(n["lon"]) for n in nodes) / len(nodes)

    fig = go.Figure(data=[edge_trace] + node_traces)
    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=centre_lat, lon=centre_lon),
            zoom=10,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#ccc",
            borderwidth=1,
            x=0.01,
            y=0.99,
        ),
        uirevision="geo",
    )
    return fig


# ── Force-directed graph (dash-cytoscape) ─────────────────────────────────────

def _cyto_elements(nodes: list[dict], edges: list[dict]) -> list[dict]:
    elements: list[dict] = []
    for n in nodes:
        role = _node_role(n)
        elements.append({
            "data": {
                "id": n["name"],
                "label": _short(n["name"]),
                "role": role,
                "colour": _ROLE_COLOUR.get(role, "#8d99ae"),
            }
        })

    # Deduplicate undirected edges
    seen: set[frozenset] = set()
    for e in edges:
        key: frozenset = frozenset([e["a"], e["b"]])
        if key in seen:
            continue
        seen.add(key)
        elements.append({
            "data": {
                "source": e["a"],
                "target": e["b"],
                "loss":       e.get("loss", 0.0),
                "latency_ms": e.get("latency_ms", 0.0),
                "snr":        e.get("snr", 6.0),
                "rssi":       e.get("rssi", -90.0),
            }
        })
    return elements


_CYTO_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "background-color": "data(colour)",
            "font-size": "9px",
            "color": "#333",
            "text-valign": "bottom",
            "text-margin-y": "4px",
            "width": "20px",
            "height": "20px",
        },
    },
    {
        "selector": "edge",
        "style": {
            "line-color": _EDGE_COLOUR,
            "width": 1,
            "curve-style": "bezier",
        },
    },
    {
        "selector": "node:selected",
        "style": {
            "border-width": "3px",
            "border-color": "#e63946",
        },
    },
    {
        "selector": "edge:selected",
        "style": {
            "line-color": "#e63946",
            "width": 2,
        },
    },
]


# ── Sidebar helpers ───────────────────────────────────────────────────────────

def _sidebar(
    topology_path: Path,
    nodes: list[dict],
    edges: list[dict],
    geo: bool,
) -> html.Div:
    role_counts: dict[str, int] = {}
    for n in nodes:
        r = _node_role(n)
        role_counts[r] = role_counts.get(r, 0) + 1

    legend_items = [
        html.Div(
            [
                html.Span(style={
                    "display": "inline-block",
                    "width": "11px", "height": "11px",
                    "borderRadius": "50%",
                    "background": colour,
                    "marginRight": "6px",
                    "verticalAlign": "middle",
                }),
                html.Span(
                    f"{role}  ({role_counts.get(role, 0)})",
                    style={"fontSize": "13px"},
                ),
            ],
            style={"marginBottom": "5px"},
        )
        for role, colour in _ROLE_COLOUR.items()
        if role_counts.get(role, 0) > 0
    ]

    return html.Div(
        [
            html.H3(
                topology_path.stem,
                style={
                    "fontSize": "14px",
                    "fontWeight": "600",
                    "marginBottom": "10px",
                    "wordBreak": "break-all",
                    "color": "#212529",
                },
            ),
            html.P(f"Nodes: {len(nodes)}",
                   style={"margin": "2px 0", "fontSize": "13px"}),
            html.P(f"Edges: {len(edges)}",
                   style={"margin": "2px 0", "fontSize": "13px"}),
            html.P(
                "Layout: geo map" if geo else "Layout: force-directed",
                style={"margin": "2px 0", "fontSize": "12px", "color": "#6c757d"},
            ),
            html.Hr(style={"margin": "12px 0", "borderColor": "#dee2e6"}),
            *legend_items,
            html.Hr(style={"margin": "12px 0", "borderColor": "#dee2e6"}),
            html.Div(
                id="hover-info",
                style={
                    "fontSize": "12px",
                    "color": "#495057",
                    "whiteSpace": "pre-wrap",
                    "lineHeight": "1.6",
                },
                children="Hover over a node or edge for details.",
            ),
        ],
        style={
            "width": "210px",
            "minWidth": "210px",
            "padding": "16px",
            "background": "#f8f9fa",
            "borderRight": "1px solid #dee2e6",
            "overflowY": "auto",
            "fontFamily": "system-ui, -apple-system, sans-serif",
            "boxSizing": "border-box",
        },
    )


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(topology_path: Path) -> dash.Dash:
    """Build and return the Dash app for the given topology file."""
    raw = _load(topology_path)
    nodes: list[dict] = raw.get("nodes", [])
    edges: list[dict] = raw.get("edges", [])
    geo = _has_geo(nodes)

    app = dash.Dash(
        __name__,
        title=f"{topology_path.stem} — MeshCore viz",
    )

    sidebar = _sidebar(topology_path, nodes, edges, geo)

    if geo:
        main_panel = dcc.Graph(
            id="geo-graph",
            figure=_geo_figure(nodes, edges),
            style={"flex": "1", "height": "100vh"},
            config={"scrollZoom": True},
        )
    else:
        main_panel = cyto.Cytoscape(
            id="cyto-graph",
            elements=_cyto_elements(nodes, edges),
            layout={"name": "cose", "animate": False, "randomize": False},
            style={"flex": "1", "height": "100vh"},
            stylesheet=_CYTO_STYLESHEET,
            userZoomingEnabled=True,
            userPanningEnabled=True,
        )

    app.layout = html.Div(
        [sidebar, main_panel],
        style={
            "display": "flex",
            "height": "100vh",
            "overflow": "hidden",
            "fontFamily": "system-ui, -apple-system, sans-serif",
        },
    )

    # ── Callbacks ──────────────────────────────────────────────────────────

    if not geo:
        @app.callback(
            Output("hover-info", "children"),
            Input("cyto-graph", "mouseoverNodeData"),
            Input("cyto-graph", "mouseoverEdgeData"),
        )
        def _on_hover(
            node_data: dict | None,
            edge_data: dict | None,
        ) -> str:
            if node_data:
                nid = node_data['id']
                return (
                    f"{_short(nid)}\n"
                    f"{nid}\n"
                    f"Role: {node_data.get('role', '?')}"
                )
            if edge_data:
                loss_pct = float(edge_data.get("loss", 0)) * 100
                src, tgt = edge_data['source'], edge_data['target']
                return (
                    f"Edge\n"
                    f"  {_short(src)} ↔ {_short(tgt)}\n"
                    f"  {src}\n"
                    f"  {tgt}\n"
                    f"Loss:    {loss_pct:.1f}%\n"
                    f"Latency: {edge_data.get('latency_ms', 0):.1f} ms\n"
                    f"SNR:     {edge_data.get('snr', 0):.1f} dB\n"
                    f"RSSI:    {edge_data.get('rssi', 0):.0f} dBm"
                )
            return "Hover over a node or edge for details."

    return app
