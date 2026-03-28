"""sim_panel.py — Tab 2: Simulation config forms, run button, log streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from types import SimpleNamespace
from typing import Optional

from nicegui import ui

from orchestrator.config import (
    RadioConfig, SimulationConfig, TopologyConfig, topology_to_dict,
)
from .results import save_result, list_runs
from .state import AppState
from .trace_loader import load_trace

log = logging.getLogger(__name__)

# -- Bandwidth options (Hz → display label) ----------------------------------

BW_OPTIONS = {
    62_500:  "62.5 kHz",
    125_000: "125 kHz",
    250_000: "250 kHz",
    500_000: "500 kHz",
}

# -- Coding-rate options (offset → display label) ----------------------------

CR_OPTIONS = {
    1: "4/5",
    2: "4/6",
    3: "4/7",
    4: "4/8",
}


# ---------------------------------------------------------------------------
# Custom logging handler → ui.log() bridge
# ---------------------------------------------------------------------------

class UILogHandler(logging.Handler):
    """Logging handler that pushes formatted records into a NiceGUI ui.log."""

    def __init__(self, log_widget: ui.log):
        super().__init__()
        self._log = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._log.push(msg)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# Sidebar: radio + sim config forms
# ---------------------------------------------------------------------------

def render_sidebar(state: AppState) -> None:
    """Render radio/sim config forms and Run button in the sidebar."""

    if state.topology is None:
        ui.label("Simulation").classes("text-h6")
        ui.label("Load a topology first.").classes("text-grey")
        return

    topo = state.topology
    radio = topo.radio or RadioConfig()
    sim = topo.simulation

    ui.label("Simulation").classes("text-h6")

    # -- Run button + status (at top so always visible) --
    run_btn = ui.button("Run Simulation", icon="play_arrow").classes(
        "w-full"
    ).props("color=primary")
    status_label = ui.label("").classes("text-caption")

    ui.separator()

    # -- Radio config card --
    with ui.expansion("Radio Config", icon="radio").classes("w-full"):
        sf_select = ui.select(
            options=[7, 8, 9, 10, 11, 12],
            value=radio.sf,
            label="Spreading Factor",
        ).classes("w-full")

        bw_select = ui.select(
            options=BW_OPTIONS,
            value=radio.bw_hz,
            label="Bandwidth",
        ).classes("w-full")

        # Normalise CR: accept both offset (1-4) and RadioLib denominator (5-8)
        cr_val = radio.cr
        if cr_val not in CR_OPTIONS:
            cr_val = max(1, min(4, cr_val - 4 if cr_val >= 5 else cr_val))
        cr_select = ui.select(
            options=CR_OPTIONS,
            value=cr_val,
            label="Coding Rate",
        ).classes("w-full")

        preamble_input = ui.number(
            label="Preamble Symbols",
            value=radio.preamble_symbols,
            min=4, max=65535, step=1, precision=0,
        ).classes("w-full")

    # -- Simulation config card --
    with ui.expansion("Sim Config", icon="settings").classes("w-full"):
        warmup_input = ui.number(
            label="Warmup (s)",
            value=sim.warmup_secs,
            min=0, step=1.0,
        ).classes("w-full")

        duration_input = ui.number(
            label="Duration (s)",
            value=sim.duration_secs,
            min=1, step=1.0,
        ).classes("w-full")

        traffic_input = ui.number(
            label="Traffic Interval (s)",
            value=sim.traffic_interval_secs,
            min=0.1, step=1.0,
        ).classes("w-full")

        advert_input = ui.number(
            label="Advert Interval (s)",
            value=sim.advert_interval_secs,
            min=1, step=1.0,
        ).classes("w-full")

        seed_input = ui.number(
            label="Seed (empty = random)",
            value=sim.seed,
            step=1, precision=0,
        ).classes("w-full")

    # Store refs for the run handler
    state._sim_ui = SimpleNamespace(
        sf=sf_select,
        bw=bw_select,
        cr=cr_select,
        preamble=preamble_input,
        warmup=warmup_input,
        duration=duration_input,
        traffic=traffic_input,
        advert=advert_input,
        seed=seed_input,
        status=status_label,
        run_btn=run_btn,
    )

    run_btn.on("click", lambda: _start_simulation(state))


# ---------------------------------------------------------------------------
# Main panel: log output
# ---------------------------------------------------------------------------

def render_main(state: AppState) -> None:
    """Render the log output panel."""
    log_widget = ui.log(max_lines=2000).classes("w-full h-full").style(
        "font-family: monospace; font-size: 0.85em"
    )
    state._sim_log_widget = log_widget


# ---------------------------------------------------------------------------
# Run simulation
# ---------------------------------------------------------------------------

async def _start_simulation(state: AppState) -> None:
    """Save topology to tempfile, build args, run orchestrator."""

    if state.sim_running:
        ui.notify("Simulation already running", type="warning")
        return

    if state.topology is None:
        ui.notify("No topology loaded", type="negative")
        return

    sim_ui = state._sim_ui
    log_widget: ui.log = state._sim_log_widget

    # -- Apply form values to topology before saving --
    _apply_form_to_topology(state)

    # -- Write topology to temp file --
    topo_dict = topology_to_dict(state.topology)
    fd, topo_path = tempfile.mkstemp(suffix=".json", prefix="wb_topo_")
    os.close(fd)
    with open(topo_path, "w") as f:
        json.dump(topo_dict, f, indent=2)

    # -- Trace output temp file --
    fd2, trace_path = tempfile.mkstemp(suffix=".json", prefix="wb_trace_")
    os.close(fd2)

    # -- Build args namespace --
    args = SimpleNamespace(
        topology=topo_path,
        duration=None,      # already baked into topology
        warmup=None,
        traffic_interval=None,
        advert_interval=None,
        agent=None,         # use topology default_binary
        max_heap_kb=None,
        seed=None,          # already baked into topology
        log_level="info",
        report=None,
        trace_out=trace_path,
    )

    # -- Set up logging bridge --
    log_widget.clear()
    handler = UILogHandler(log_widget)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S"))
    handler.setLevel(logging.DEBUG)

    # Attach to the orchestrator root logger so all submodules are captured
    orch_logger = logging.getLogger("orchestrator")
    orch_logger.addHandler(handler)
    orch_logger.setLevel(logging.INFO)

    # -- UI state --
    state.sim_running = True
    sim_ui.run_btn.disable()
    sim_ui.status.set_text("Running...")
    sim_ui.status.classes("text-primary", remove="text-negative text-positive")

    log_widget.push("--- Simulation started ---")

    try:
        from orchestrator.__main__ import run
        rc = await run(args)

        if rc == 0:
            sim_ui.status.set_text("Completed successfully")
            sim_ui.status.classes("text-positive", remove="text-primary text-negative")
            log_widget.push("--- Simulation finished (success) ---")

            # Auto-load trace and refresh trace viewer
            if os.path.exists(trace_path) and os.path.getsize(trace_path) > 0:
                state.trace = load_trace(trace_path)
                n_pkts = len(state.trace.get("packets", []))
                log_widget.push(f"--- Trace loaded: {n_pkts} packets ---")

                # Persist to output/<topo>/<timestamp>/
                try:
                    run_dir = save_result(
                        topology_path=state.topology_path or "unknown",
                        topology_config=state.topology,
                        trace_dict=state.trace,
                        output_dir=state._output_dir,
                    )
                    state._current_run_dir = str(run_dir)
                    state._available_runs = list_runs(state._output_dir)
                    log_widget.push(f"--- Saved to {run_dir} ---")
                    ui.notify(
                        f"Trace: {n_pkts} packets. Saved to {run_dir}",
                        type="positive",
                    )
                    if state._refresh_run_selector:
                        state._refresh_run_selector()
                except Exception as save_exc:
                    log_widget.push(f"--- Warning: could not save result: {save_exc} ---")
                    ui.notify(f"Simulation complete. Trace: {n_pkts} packets.", type="positive")

                # Refresh trace viewer tabs so they pick up the new trace
                if state._refresh_trace_main:
                    state._refresh_trace_main()
                if state._refresh_trace_sidebar:
                    state._refresh_trace_sidebar()
            else:
                ui.notify("Simulation complete (no trace data).", type="info")
        else:
            sim_ui.status.set_text(f"Failed (rc={rc})")
            sim_ui.status.classes("text-negative", remove="text-primary text-positive")
            log_widget.push(f"--- Simulation failed (rc={rc}) ---")
            ui.notify(f"Simulation failed with rc={rc}", type="negative")

    except Exception as exc:
        sim_ui.status.set_text(f"Error: {exc}")
        sim_ui.status.classes("text-negative", remove="text-primary text-positive")
        log_widget.push(f"--- ERROR: {exc} ---")
        ui.notify(f"Simulation error: {exc}", type="negative")
        log.exception("Simulation failed")

    finally:
        orch_logger.removeHandler(handler)
        state.sim_running = False
        sim_ui.run_btn.enable()

        # Clean up temp topology file (keep trace)
        try:
            os.unlink(topo_path)
        except OSError:
            pass


def _apply_form_to_topology(state: AppState) -> None:
    """Read current form values and update the topology config in-place."""
    sim_ui = state._sim_ui
    topo = state.topology

    # Radio config
    if topo.radio is None:
        topo.radio = RadioConfig()
    topo.radio.sf = int(sim_ui.sf.value)
    topo.radio.bw_hz = int(sim_ui.bw.value)
    topo.radio.cr = int(sim_ui.cr.value)
    topo.radio.preamble_symbols = int(sim_ui.preamble.value)

    # Sim config
    topo.simulation.warmup_secs = float(sim_ui.warmup.value)
    topo.simulation.duration_secs = float(sim_ui.duration.value)
    topo.simulation.traffic_interval_secs = float(sim_ui.traffic.value)
    topo.simulation.advert_interval_secs = float(sim_ui.advert.value)
    seed_val = sim_ui.seed.value
    topo.simulation.seed = int(seed_val) if seed_val is not None else None
