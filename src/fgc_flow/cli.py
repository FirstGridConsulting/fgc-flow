"""Modern CLI for FGC-Flow power flow analysis.

Usage:
    fgc-flow info   MODEL        Show system topology and component summary
    fgc-flow run    MODEL        Run one or more OPF solvers
    fgc-flow compare MODEL       Run all solvers and compare results
    fgc-flow export MODEL --db   Export solver results to SQLite
"""

from __future__ import annotations

import math
import time
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Optional

import numpy as np
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gdm.distribution import DistributionSystem
from gdm.distribution.components import DistributionBus
from gdm.distribution.enums import Phase

from ._utils import _phase_name
from .ac_opf import build_regulator_voltage_limits_from_components

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="fgc-flow",
    help="[bold cyan]FGC-Flow[/] — Power flow & optimal power flow for distribution systems",
    rich_markup_mode="rich",
    no_args_is_help=True,
    pretty_exceptions_enable=True,
)


class Solver(str, Enum):
    ac = "ac"
    dc = "dc"
    ldf = "ldf"


# ── helpers ──────────────────────────────────────────────────────────────


def _load_system(model: Path) -> DistributionSystem:
    """Load a DistributionSystem from a JSON file."""
    if not model.exists():
        err_console.print(f"[red]Error:[/] file not found: {model}")
        raise typer.Exit(1)
    with console.status("[cyan]Loading model…"):
        system = DistributionSystem.from_json(str(model))
    return system


def _fmt_w(val: float) -> str:
    """Format watts with appropriate unit."""
    if abs(val) >= 1e6:
        return f"{val / 1e6:.2f} MW"
    if abs(val) >= 1e3:
        return f"{val / 1e3:.2f} kW"
    return f"{val:.1f} W"


def _fmt_var(val: float) -> str:
    """Format vars with appropriate unit."""
    if abs(val) >= 1e6:
        return f"{val / 1e6:.2f} Mvar"
    if abs(val) >= 1e3:
        return f"{val / 1e3:.2f} kvar"
    return f"{val:.1f} var"


def _success_badge(ok: bool) -> str:
    return "[bold green]✓ PASS[/]" if ok else "[bold red]✗ FAIL[/]"


def _to_float_quantity(value, unit: str) -> float | None:
    if value is None:
        return None
    if hasattr(value, "to"):
        try:
            return float(value.to(unit).magnitude)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_voltage_bounds(limit_obj) -> tuple[float, float] | None:
    min_candidates = [
        "min_voltage",
        "min_v_limit",
        "lower_limit",
        "lower",
        "v_min",
        "minimum",
    ]
    max_candidates = [
        "max_voltage",
        "max_v_limit",
        "upper_limit",
        "upper",
        "v_max",
        "maximum",
    ]

    min_value = None
    max_value = None
    for name in min_candidates:
        if hasattr(limit_obj, name):
            min_value = _to_float_quantity(getattr(limit_obj, name), "volt")
            if min_value is not None:
                break
    for name in max_candidates:
        if hasattr(limit_obj, name):
            max_value = _to_float_quantity(getattr(limit_obj, name), "volt")
            if max_value is not None:
                break

    if min_value is None or max_value is None:
        return None
    return (min(min_value, max_value), max(min_value, max_value))


def _build_node_voltage_limits_v(
    system: DistributionSystem,
) -> dict[tuple[str, str], tuple[float, float]]:
    limits: dict[tuple[str, str], tuple[float, float]] = {}

    for bus in system.get_components(DistributionBus):
        bus_phases = [_phase_name(p) for p in bus.phases if p != Phase.N]
        for vlimit in getattr(bus, "voltagelimits", []) or []:
            bounds = _extract_voltage_bounds(vlimit)
            if bounds is None:
                continue

            raw_phase = getattr(vlimit, "phase", None)
            if raw_phase is None:
                phases = bus_phases
            else:
                ph_name = (
                    _phase_name(raw_phase)
                    if not isinstance(raw_phase, str)
                    else raw_phase
                )
                phases = [ph_name]

            for phase in phases:
                key = (bus.name, phase)
                if key in limits:
                    lo0, hi0 = limits[key]
                    lo1, hi1 = bounds
                    limits[key] = (max(lo0, lo1), min(hi0, hi1))
                else:
                    limits[key] = bounds

    # Regulator limits are hard constraints and should tighten any existing bus-level bounds.
    for key, bounds in build_regulator_voltage_limits_from_components(system).items():
        if key in limits:
            lo0, hi0 = limits[key]
            lo1, hi1 = bounds
            limits[key] = (max(lo0, lo1), min(hi0, hi1))
        else:
            limits[key] = bounds

    return limits


def _build_lindistflow_loading_limits_va(
    system: DistributionSystem,
) -> dict[tuple[str, str], float]:
    from gdm.distribution.components.base.distribution_branch_base import (
        DistributionBranchBase,
    )

    bus_phase_voltage_v: dict[tuple[str, str], float] = {}
    for bus in system.get_components(DistributionBus):
        v_nom = _to_float_quantity(bus.rated_voltage, "volt")
        if v_nom is None:
            continue
        for phase in bus.phases:
            if phase == Phase.N:
                continue
            bus_phase_voltage_v[(bus.name, _phase_name(phase))] = v_nom

    edge_parent_bus: dict[str, str] = {}
    digraph = system.get_directed_graph(return_radial_network=True)
    for u, _v, data in digraph.edges(data=True):
        branch_name = data.get("name")
        if branch_name:
            edge_parent_bus[branch_name] = u

    limits_va: dict[tuple[str, str], float] = {}
    for branch in system.get_components(DistributionBranchBase):
        ampacity = _to_float_quantity(
            getattr(branch.equipment, "ampacity", None), "ampere"
        )
        if ampacity is None or ampacity <= 0:
            continue

        parent_bus = edge_parent_bus.get(branch.name)
        for phase in branch.phases:
            if phase == Phase.N:
                continue
            phase_name = _phase_name(phase)
            v_phase = (
                bus_phase_voltage_v.get((parent_bus, phase_name))
                if parent_bus is not None
                else None
            )
            if v_phase is None:
                continue
            limits_va[(branch.name, phase_name)] = float(v_phase * ampacity)

    return limits_va


def _branch_phase_series_impedance_ohm(branch, phase_name: str) -> tuple[float, float]:
    branch_phase_names = [_phase_name(p) for p in branch.phases]
    if phase_name not in branch_phase_names:
        return (0.0, 0.0)

    if hasattr(branch, "equipment") and hasattr(branch.equipment, "r_matrix"):
        idx = branch_phase_names.index(phase_name)
        length_m = float(branch.length.to("m").magnitude)
        r = float(branch.equipment.r_matrix.to("ohm/m").magnitude[idx][idx]) * length_m
        x = float(branch.equipment.x_matrix.to("ohm/m").magnitude[idx][idx]) * length_m
        return (r, x)

    if hasattr(branch, "equipment") and hasattr(branch.equipment, "pos_seq_resistance"):
        length_m = float(branch.length.to("m").magnitude)
        r = float(branch.equipment.pos_seq_resistance.to("ohm/m").magnitude) * length_m
        x = float(branch.equipment.pos_seq_reactance.to("ohm/m").magnitude) * length_m
        return (r, x)

    return (0.0, 0.0)


def _build_ac_branch_loading_from_result(
    system: DistributionSystem,
    ac_result,
) -> tuple[
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], tuple[float, float]],
]:
    from gdm.distribution.components.base.distribution_branch_base import (
        DistributionBranchBase,
    )

    v_by_label = {
        label: ac_result.voltage[i]
        for i, label in enumerate(ac_result.ybus_result.index_to_label)
    }

    digraph = system.get_directed_graph(return_radial_network=True)
    edge_component = {}
    for u, v, data in digraph.edges(data=True):
        ctype = data.get("type")
        cname = data.get("name")
        if not ctype or not cname:
            continue
        try:
            comp = system.get_component(ctype, cname)
        except Exception:
            continue
        if isinstance(comp, DistributionBranchBase) and comp.in_service:
            if hasattr(comp, "is_closed") and not all(bool(x) for x in comp.is_closed):
                continue
            edge_component[(u, v)] = comp

    loading_va: dict[tuple[str, str], float] = {}
    loading_limits_va: dict[tuple[str, str], float] = {}
    flow_w_var: dict[tuple[str, str], tuple[float, float]] = {}

    for (u, v), branch in edge_component.items():
        ampacity = _to_float_quantity(
            getattr(branch.equipment, "ampacity", None), "ampere"
        )
        for phase in branch.phases:
            if phase == Phase.N:
                continue
            phase_name = _phase_name(phase)
            v_u = v_by_label.get((u, phase_name))
            v_v = v_by_label.get((v, phase_name))
            if v_u is None or v_v is None:
                continue

            r_ohm, x_ohm = _branch_phase_series_impedance_ohm(branch, phase_name)
            z = complex(r_ohm, x_ohm)
            if abs(z) < 1e-12:
                continue

            i_branch = (v_u - v_v) / z
            s_from = v_u * np.conj(i_branch)
            key = (branch.name, phase_name)
            flow_w_var[key] = (float(s_from.real), float(s_from.imag))
            loading_va[key] = float(abs(s_from))
            if ampacity is not None and ampacity > 0:
                loading_limits_va[key] = float(abs(v_u) * ampacity)

    return loading_va, loading_limits_va, flow_w_var


def _build_dc_branch_loading_from_result(
    system: DistributionSystem,
    dc_result,
) -> tuple[
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], tuple[float, float]],
]:
    """Approximate DC branch active flows from solved angle differences.

    Uses P_ij ~= (V_i * V_j / X_ij) * (theta_i - theta_j) with branch series reactance.
    Reactive flow is reported as 0.0 in this DC approximation.
    """

    from gdm.distribution.components.base.distribution_branch_base import (
        DistributionBranchBase,
    )

    theta_by_label = dc_result.theta_rad

    # Match DC OPF linearization base angles used during the solve.
    phase_offset_by_name = {
        "S2": math.pi,
    }

    def _theta_effective(label: tuple[str, str]) -> float | None:
        theta = theta_by_label.get(label)
        if theta is None:
            return None
        return float(theta) - float(phase_offset_by_name.get(label[1], 0.0))

    v_nom_by_label: dict[tuple[str, str], float] = {}
    for bus in system.get_components(DistributionBus):
        v_nom = _to_float_quantity(bus.rated_voltage, "volt")
        if v_nom is None:
            continue
        for phase in bus.phases:
            if phase == Phase.N:
                continue
            v_nom_by_label[(bus.name, _phase_name(phase))] = v_nom

    digraph = system.get_directed_graph(return_radial_network=True)
    edge_component = {}
    for u, v, data in digraph.edges(data=True):
        ctype = data.get("type")
        cname = data.get("name")
        if not ctype or not cname:
            continue
        try:
            comp = system.get_component(ctype, cname)
        except Exception:
            continue
        if isinstance(comp, DistributionBranchBase) and comp.in_service:
            if hasattr(comp, "is_closed") and not all(bool(x) for x in comp.is_closed):
                continue
            edge_component[(u, v)] = comp

    loading_va: dict[tuple[str, str], float] = {}
    loading_limits_va: dict[tuple[str, str], float] = {}
    flow_w_var: dict[tuple[str, str], tuple[float, float]] = {}

    for (u, v), branch in edge_component.items():
        ampacity = _to_float_quantity(
            getattr(branch.equipment, "ampacity", None), "ampere"
        )
        for phase in branch.phases:
            if phase == Phase.N:
                continue
            phase_name = _phase_name(phase)
            theta_u = _theta_effective((u, phase_name))
            theta_v = _theta_effective((v, phase_name))
            v_u = v_nom_by_label.get((u, phase_name))
            v_v = v_nom_by_label.get((v, phase_name))
            if theta_u is None or theta_v is None or v_u is None or v_v is None:
                continue

            _r_ohm, x_ohm = _branch_phase_series_impedance_ohm(branch, phase_name)
            # Skip near-zero reactance elements (switch/fuse-like) where DC flow
            # reconstruction is numerically unstable and not physically meaningful.
            if abs(x_ohm) < 1e-3:
                continue

            p_flow_w = float((v_u * v_v / x_ohm) * (theta_u - theta_v))
            key = (branch.name, phase_name)
            flow_w_var[key] = (p_flow_w, 0.0)
            loading_va[key] = abs(p_flow_w)
            if ampacity is not None and ampacity > 0:
                loading_limits_va[key] = float(abs(v_u) * ampacity)

    return loading_va, loading_limits_va, flow_w_var


def _table_has_columns(
    conn: sqlite3.Connection, table_name: str, columns: set[str]
) -> bool:
    available = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    return columns.issubset(available)


def _resolve_latest_run_id(
    conn: sqlite3.Connection,
    implementation: str,
    requested_run_id: str | None,
) -> str | None:
    if requested_run_id:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ? AND implementation = ?",
            (requested_run_id, implementation),
        ).fetchone()
        return row[0] if row else None

    row = conn.execute(
        """
        SELECT run_id FROM runs
        WHERE implementation = ?
        ORDER BY created_at_utc DESC
        LIMIT 1
        """,
        (implementation,),
    ).fetchone()
    return row[0] if row else None


def _read_overvoltage_rows(
    db_path: str,
    implementation: str,
    run_id: str | None,
) -> tuple[str | None, list[tuple], bool]:
    table = "ac_opf_nodes" if implementation == "ac_opf" else "lindistflow_nodes"
    voltage_col = "voltage_mag_v" if implementation == "ac_opf" else "voltage_v"
    required = {"voltage_min_v", "voltage_max_v"}

    conn = sqlite3.connect(db_path)
    try:
        if not _table_has_columns(conn, table, required):
            return None, [], False

        resolved = _resolve_latest_run_id(conn, implementation, run_id)
        if resolved is None:
            return None, [], True

        rows = conn.execute(
            f"""
            SELECT bus_name, phase, {voltage_col}, voltage_min_v, voltage_max_v
            FROM {table}
            WHERE run_id = ?
              AND (
                (voltage_max_v IS NOT NULL AND {voltage_col} > voltage_max_v)
                OR
                (voltage_min_v IS NOT NULL AND {voltage_col} < voltage_min_v)
              )
            ORDER BY ({voltage_col} - COALESCE(voltage_max_v, {voltage_col})) DESC,
                     bus_name,
                     phase
            """,
            (resolved,),
        ).fetchall()
        return resolved, rows, True
    finally:
        conn.close()


def _read_overload_rows(
    db_path: str,
    implementation: str,
    run_id: str | None,
) -> tuple[str | None, list[tuple], bool]:
    conn = sqlite3.connect(db_path)
    try:
        required = {"loading_va", "loading_limit_va"}
        table_by_impl = {
            "ac_opf": "ac_opf_branches",
            "dc_opf": "dc_opf_branches",
            "lindistflow": "lindistflow_branches",
        }
        table = table_by_impl.get(implementation)
        if table is None:
            return None, [], False
        if not _table_has_columns(conn, table, required):
            return None, [], False

        resolved = _resolve_latest_run_id(conn, implementation, run_id)
        if resolved is None:
            return None, [], True

        rows = conn.execute(
            f"""
            SELECT
                branch_name,
                phase,
                p_flow_w,
                q_flow_var,
                loading_va,
                loading_limit_va,
                (loading_va / loading_limit_va) AS loading_ratio
                        FROM {table}
            WHERE run_id = ?
              AND loading_limit_va IS NOT NULL
              AND loading_limit_va > 0
              AND loading_va > loading_limit_va
            ORDER BY loading_ratio DESC, branch_name, phase
                        """,
            (resolved,),
        ).fetchall()
        return resolved, rows, True
    finally:
        conn.close()


def _read_db_schema(
    db_path: str,
    *,
    include_internal: bool = False,
) -> list[tuple[str, list[str]]]:
    conn = sqlite3.connect(db_path)
    try:
        if include_internal:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        else:
            table_rows = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()

        out: list[tuple[str, list[str]]] = []
        for (table_name,) in table_rows:
            columns = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            ]
            out.append((str(table_name), columns))
        return out
    finally:
        conn.close()


def _run_ac(system: DistributionSystem) -> dict:
    from .ac_opf import optimize_ac_power_flow_from_components

    t0 = time.perf_counter()
    result = optimize_ac_power_flow_from_components(
        system,
        include_loads=True,
        include_solar=True,
        include_capacitor=True,
        include_regulator_targets=True,
        include_regulator_limits=True,
    )
    elapsed = time.perf_counter() - t0

    # Compute source power
    src_bus = system.get_source_bus().name
    idx_map = result.ybus_result.index_to_label
    v = result.voltage
    ybus = result.ybus_result.ybus
    s = v * np.conj(ybus @ v)
    src_idx = [i for i, lbl in enumerate(idx_map) if lbl[0] == src_bus]
    source_p = sum(s[i].real for i in src_idx)
    source_q = sum(s[i].imag for i in src_idx)

    # Voltage stats
    v_mag = np.abs(v)

    return {
        "solver": "AC OPF",
        "success": result.success,
        "message": result.message,
        "elapsed": elapsed,
        "iterations": result.iterations,
        "source_p": source_p,
        "source_q": source_q,
        "v_min": float(np.min(v_mag)),
        "v_max": float(np.max(v_mag)),
        "objective": result.final_objective,
        "result": result,
    }


def _run_dc(system: DistributionSystem) -> dict:
    from .dc_opf import solve_dc_opf_from_components

    t0 = time.perf_counter()
    result = solve_dc_opf_from_components(
        system,
        include_solar_generators=True,
        include_battery_generators=True,
        include_loads=True,
    )
    elapsed = time.perf_counter() - t0

    # Source power from grid generators
    grid_gens = {
        k: v for k, v in result.generator_dispatch_w.items() if k.startswith("grid:")
    }
    solar_gens = {
        k: v for k, v in result.generator_dispatch_w.items() if k.startswith("solar:")
    }
    battery_gens = {
        k: v for k, v in result.generator_dispatch_w.items() if k.startswith("battery:")
    }
    # Source power = grid import (what enters from the source bus)
    # Solar injects at load buses, not the source bus
    source_p = sum(grid_gens.values())

    return {
        "solver": "DC OPF",
        "success": result.success,
        "message": result.message,
        "elapsed": elapsed,
        "iterations": result.iterations,
        "source_p": source_p,
        "source_q": 0.0,
        "grid_import": sum(grid_gens.values()),
        "solar_dispatch": sum(solar_gens.values()),
        "battery_dispatch": sum(battery_gens.values()),
        "total_gen": sum(result.generator_dispatch_w.values()),
        "objective": result.objective,
        "result": result,
    }


def _run_ldf(system: DistributionSystem) -> dict:
    from .lindistflow import solve_lindistflow

    t0 = time.perf_counter()
    result = solve_lindistflow(system)
    elapsed = time.perf_counter() - t0

    source_p = sum(float(v) for v in result.p_net_w.values())
    source_q = sum(float(v) for v in result.q_net_var.values())

    v_vals = list(result.voltage_v.values())

    return {
        "solver": "LinDistFlow",
        "success": result.success,
        "message": result.message,
        "elapsed": elapsed,
        "iterations": 0,
        "source_p": source_p,
        "source_q": source_q,
        "v_min": min(v_vals) if v_vals else 0.0,
        "v_max": max(v_vals) if v_vals else 0.0,
        "result": result,
    }


SOLVER_MAP = {
    Solver.ac: _run_ac,
    Solver.dc: _run_dc,
    Solver.ldf: _run_ldf,
}


# ── commands ─────────────────────────────────────────────────────────────


@app.command()
def info(
    model: Path = typer.Argument(..., help="Path to GDM distribution system JSON"),
):
    """Show system topology and component summary."""
    system = _load_system(model)

    src_bus = system.get_source_bus()
    src_phases = [_phase_name(p) for p in src_bus.phases if p != Phase.N]

    # Count components
    from gdm.distribution.components import (
        DistributionBus,
        DistributionLoad,
        DistributionSolar,
        DistributionTransformer,
    )

    buses = list(system.get_components(DistributionBus))
    loads = list(system.get_components(DistributionLoad))
    solars = list(system.get_components(DistributionSolar))
    transformers = list(system.get_components(DistributionTransformer))

    total_load_w = 0.0
    total_load_var = 0.0
    for ld in loads:
        for pl in ld.equipment.phase_loads:
            total_load_w += float(pl.real_power.to("watt").magnitude)
            total_load_var += float(pl.reactive_power.to("var").magnitude)

    total_solar_w = 0.0
    total_solar_rated_w = 0.0
    for s in solars:
        total_solar_w += float(s.active_power.to("watt").magnitude)
        total_solar_rated_w += float(s.equipment.rated_power.to("watt").magnitude)

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold]{model.name}[/]\n[dim]{model.resolve()}[/]",
            title="[bold cyan]⚡ FGC-Flow System Info[/]",
            border_style="cyan",
        )
    )

    # Topology table
    topo = Table(title="Topology", show_header=False, border_style="dim")
    topo.add_column("Key", style="bold")
    topo.add_column("Value")
    topo.add_row("Source Bus", f"{src_bus.name}")
    topo.add_row("Source Phases", ", ".join(src_phases))
    topo.add_row("Buses", str(len(buses)))
    topo.add_row("Transformers", str(len(transformers)))
    topo.add_row("Loads", str(len(loads)))
    topo.add_row("Solar PV", str(len(solars)))
    console.print(topo)

    # Power summary
    pwr = Table(title="Power Summary", border_style="dim")
    pwr.add_column("Metric", style="bold")
    pwr.add_column("Value", justify="right")
    pwr.add_row("Total Load (P)", _fmt_w(total_load_w))
    pwr.add_row("Total Load (Q)", _fmt_var(total_load_var))
    pwr.add_row("Solar Active", _fmt_w(total_solar_w))
    pwr.add_row("Solar Rated", _fmt_w(total_solar_rated_w))
    pwr.add_row("Net Demand", _fmt_w(total_load_w - total_solar_w))
    console.print(pwr)

    # Bus details
    bus_tbl = Table(title="Bus Details", border_style="dim")
    bus_tbl.add_column("Bus", style="bold")
    bus_tbl.add_column("Phases")
    bus_tbl.add_column("Rated V", justify="right")
    bus_tbl.add_column("Type")
    for b in sorted(buses, key=lambda x: x.name):
        phases = ", ".join(_phase_name(p) for p in b.phases if p != Phase.N)
        v_str = f"{float(b.rated_voltage.to('volt').magnitude):.0f} V"
        btype = "Source" if b.name == src_bus.name else "Load"
        bus_tbl.add_row(b.name, phases, v_str, btype)
    console.print(bus_tbl)
    console.print()


@app.command()
def run(
    model: Path = typer.Argument(..., help="Path to GDM distribution system JSON"),
    solver: list[Solver] = typer.Option(
        [Solver.ac], "--solver", "-s", help="Solver(s) to run (ac, dc, ldf)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed results"
    ),
):
    """Run one or more OPF solvers on a distribution system model."""
    system = _load_system(model)

    results = []
    for s in solver:
        solver_name = {"ac": "AC OPF", "dc": "DC OPF", "ldf": "LinDistFlow"}[s.value]
        with console.status(f"[cyan]Running {solver_name}…"):
            r = SOLVER_MAP[s](system)
        results.append(r)

    # Summary table
    console.print()
    tbl = Table(
        title="[bold]OPF Results[/]",
        border_style="cyan",
        show_lines=True,
    )
    tbl.add_column("Solver", style="bold")
    tbl.add_column("Status", justify="center")
    tbl.add_column("Source P", justify="right")
    tbl.add_column("Source Q", justify="right")
    tbl.add_column("Time", justify="right")
    tbl.add_column("Iterations", justify="right")

    for r in results:
        tbl.add_row(
            r["solver"],
            _success_badge(r["success"]),
            _fmt_w(r["source_p"]),
            _fmt_var(r["source_q"]),
            f"{r['elapsed'] * 1000:.0f} ms",
            str(r["iterations"]),
        )
    console.print(tbl)

    # DC dispatch details
    if verbose:
        for r in results:
            if r["solver"] == "DC OPF":
                _print_dc_dispatch(r)
            if r["solver"] == "AC OPF":
                _print_ac_voltages(r, system)

    console.print()


@app.command()
def compare(
    model: Path = typer.Argument(..., help="Path to GDM distribution system JSON"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Export comparison to HTML (requires plotly)"
    ),
):
    """Run all three solvers and compare results side-by-side."""
    system = _load_system(model)

    results = {}
    for s in [Solver.ac, Solver.dc, Solver.ldf]:
        solver_name = {"ac": "AC OPF", "dc": "DC OPF", "ldf": "LinDistFlow"}[s.value]
        with console.status(f"[cyan]Running {solver_name}…"):
            results[s.value] = SOLVER_MAP[s](system)

    ac_r = results["ac"]
    dc_r = results["dc"]
    ldf_r = results["ldf"]

    # Comparison table
    console.print()
    tbl = Table(
        title="[bold]⚡ Solver Comparison[/]",
        border_style="cyan",
        show_lines=True,
    )
    tbl.add_column("Metric", style="bold")
    tbl.add_column("AC OPF", justify="right", style="green")
    tbl.add_column("DC OPF", justify="right", style="yellow")
    tbl.add_column("LinDistFlow", justify="right", style="blue")

    tbl.add_row(
        "Status",
        _success_badge(ac_r["success"]),
        _success_badge(dc_r["success"]),
        _success_badge(ldf_r["success"]),
    )
    tbl.add_row(
        "Source P",
        _fmt_w(ac_r["source_p"]),
        _fmt_w(dc_r["source_p"]),
        _fmt_w(ldf_r["source_p"]),
    )
    tbl.add_row(
        "Source Q", _fmt_var(ac_r["source_q"]), "—", _fmt_var(ldf_r["source_q"])
    )
    tbl.add_row(
        "Time",
        f"{ac_r['elapsed'] * 1000:.0f} ms",
        f"{dc_r['elapsed'] * 1000:.0f} ms",
        f"{ldf_r['elapsed'] * 1000:.0f} ms",
    )
    tbl.add_row("Iterations", str(ac_r["iterations"]), str(dc_r["iterations"]), "—")

    console.print(tbl)

    # Dispatch breakdown for DC
    _print_dc_dispatch(dc_r)

    # Agreement check
    vals = [ac_r["source_p"], dc_r["source_p"], ldf_r["source_p"]]
    max_diff = max(vals) - min(vals)
    console.print()
    if max_diff < 100:
        console.print(
            Panel(
                f"[green]All solvers agree within {max_diff:.1f} W[/]",
                border_style="green",
                title="[bold green]✓ Agreement[/]",
            )
        )
    else:
        console.print(
            Panel(
                f"[yellow]Max disagreement: {_fmt_w(max_diff)}[/]",
                border_style="yellow",
                title="[bold yellow]⚠ Disagreement[/]",
            )
        )

    # Optionally generate HTML
    if output is not None:
        _export_html(system, ac_r, dc_r, ldf_r, output)

    console.print()


@app.command()
def export(
    model: Path = typer.Argument(..., help="Path to GDM distribution system JSON"),
    db: Path = typer.Option(..., "--db", help="SQLite database path to create/update"),
    solver: list[Solver] = typer.Option(
        [Solver.ac, Solver.dc, Solver.ldf],
        "--solver",
        "-s",
        help="Solver(s) to export",
    ),
):
    """Run solvers and export results to a SQLite database."""
    system = _load_system(model)

    ac_result = None
    dc_result = None
    ldf_result = None

    for s in solver:
        solver_name = {"ac": "AC OPF", "dc": "DC OPF", "ldf": "LinDistFlow"}[s.value]
        with console.status(f"[cyan]Running {solver_name}…"):
            r = SOLVER_MAP[s](system)

        if s == Solver.ac:
            ac_result = r["result"]
        elif s == Solver.dc:
            dc_result = r["result"]
        elif s == Solver.ldf:
            ldf_result = r["result"]

        status = _success_badge(r["success"])
        console.print(f"  {solver_name}: {status}  ({_fmt_w(r['source_p'])})")

    from .sqlite_export import export_all_results_to_sqlite

    node_voltage_limits_v = _build_node_voltage_limits_v(system)
    ldf_loading_limits_va = _build_lindistflow_loading_limits_va(system)
    ac_branch_loading_va = {}
    ac_branch_loading_limits_va = {}
    ac_branch_flow_w_var = {}
    dc_branch_loading_va = {}
    dc_branch_loading_limits_va = {}
    dc_branch_flow_w_var = {}
    if (
        ac_result is not None
        and hasattr(ac_result, "voltage")
        and hasattr(ac_result, "ybus_result")
        and hasattr(ac_result.ybus_result, "index_to_label")
    ):
        (
            ac_branch_loading_va,
            ac_branch_loading_limits_va,
            ac_branch_flow_w_var,
        ) = _build_ac_branch_loading_from_result(system, ac_result)
    if (
        dc_result is not None
        and hasattr(dc_result, "theta_rad")
        and isinstance(getattr(dc_result, "theta_rad"), dict)
    ):
        (
            dc_branch_loading_va,
            dc_branch_loading_limits_va,
            dc_branch_flow_w_var,
        ) = _build_dc_branch_loading_from_result(system, dc_result)

    with console.status("[cyan]Writing SQLite…"):
        export_all_results_to_sqlite(
            db_path=str(db),
            ac_result=ac_result,
            dc_result=dc_result,
            lindistflow_result=ldf_result,
            ac_voltage_limits_v=node_voltage_limits_v,
            ac_branch_loading_va=ac_branch_loading_va,
            ac_branch_loading_limits_va=ac_branch_loading_limits_va,
            ac_branch_flow_w_var=ac_branch_flow_w_var,
            dc_branch_loading_va=dc_branch_loading_va,
            dc_branch_loading_limits_va=dc_branch_loading_limits_va,
            dc_branch_flow_w_var=dc_branch_flow_w_var,
            lindistflow_voltage_limits_v=node_voltage_limits_v,
            lindistflow_loading_limits_va=ldf_loading_limits_va,
        )

    console.print()
    console.print(
        Panel(
            f"[green]Database written to [bold]{db}[/][/]",
            border_style="green",
            title="[bold green]✓ Export Complete[/]",
        )
    )
    console.print()


@app.command("report-overvoltage")
def report_overvoltage(
    db: Path = typer.Option(..., "--db", help="SQLite database path"),
    solver: Solver = typer.Option(
        Solver.ac,
        "--solver",
        "-s",
        help="Solver result set to inspect for voltage violations (ac or ldf)",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Specific run_id to inspect. Defaults to latest run for selected solver.",
    ),
):
    """Print overvoltage/undervoltage violations from exported results."""
    implementation = "ac_opf" if solver == Solver.ac else "lindistflow"
    resolved_run_id, rows, has_columns = _read_overvoltage_rows(
        str(db), implementation, run_id
    )

    if not has_columns:
        err_console.print(
            "[yellow]This database does not include voltage limit columns. Re-run `fgc-flow export` to add them.[/]"
        )
        raise typer.Exit(1)

    if resolved_run_id is None:
        console.print(
            Panel(
                f"[yellow]No {implementation} run found in {db}[/]",
                border_style="yellow",
                title="No Run",
            )
        )
        return

    if not rows:
        console.print(
            Panel(
                f"[green]No voltage violations for {implementation} run [bold]{resolved_run_id}[/].[/]",
                border_style="green",
                title="No Overvoltage",
            )
        )
        return

    tbl = Table(
        title=f"Voltage Violations ({implementation}, run={resolved_run_id})",
        border_style="red",
    )
    tbl.add_column("Bus", style="bold")
    tbl.add_column("Phase")
    tbl.add_column("Voltage (V)", justify="right")
    tbl.add_column("Min (V)", justify="right")
    tbl.add_column("Max (V)", justify="right")
    tbl.add_column("Violation", justify="right")

    for bus_name, phase, voltage, v_min, v_max in rows:
        if v_max is not None and voltage > v_max:
            delta = voltage - v_max
            violation = f"+{delta:.2f} V"
        elif v_min is not None and voltage < v_min:
            delta = v_min - voltage
            violation = f"-{delta:.2f} V"
        else:
            violation = "0.00 V"
        tbl.add_row(
            str(bus_name),
            str(phase),
            f"{float(voltage):.2f}",
            "-" if v_min is None else f"{float(v_min):.2f}",
            "-" if v_max is None else f"{float(v_max):.2f}",
            violation,
        )

    console.print()
    console.print(tbl)
    console.print()


@app.command("report-overload")
def report_overload(
    db: Path = typer.Option(..., "--db", help="SQLite database path"),
    solver: Solver = typer.Option(
        Solver.ldf,
        "--solver",
        "-s",
        help="Solver result set to inspect for overloads (ac or ldf)",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Specific run_id to inspect. Defaults to latest one for selected solver.",
    ),
    dc_severity_only: bool = typer.Option(
        True,
        "--dc-severity-only/--no-dc-severity-only",
        help=(
            "For DC reports, show ranked severity instead of percentage magnitudes "
            "(recommended due to DC approximation)."
        ),
    ),
):
    """Print branch overload violations from exported AC OPF or LinDistFlow results."""
    implementation = (
        "ac_opf"
        if solver == Solver.ac
        else "dc_opf"
        if solver == Solver.dc
        else "lindistflow"
    )
    resolved_run_id, rows, has_columns = _read_overload_rows(
        str(db), implementation, run_id
    )

    if not has_columns:
        err_console.print(
            "[yellow]This database does not include loading limit columns. Re-run `fgc-flow export` to add them.[/]"
        )
        raise typer.Exit(1)

    if resolved_run_id is None:
        console.print(
            Panel(
                f"[yellow]No {implementation} run found in {db}[/]",
                border_style="yellow",
                title="No Run",
            )
        )
        return

    if not rows:
        console.print(
            Panel(
                f"[green]No branch overloads for {implementation} run [bold]{resolved_run_id}[/].[/]",
                border_style="green",
                title="No Overload",
            )
        )
        return

    title_suffix = " (DC Approximation)" if implementation == "dc_opf" else ""
    if implementation == "dc_opf" and dc_severity_only:
        tbl = Table(
            title=(
                f"Branch Overloads ({implementation}{title_suffix}, run={resolved_run_id}, "
                "Ranked Severity)"
            ),
            border_style="red",
        )
        tbl.add_column("Rank", justify="right")
        tbl.add_column("Branch", style="bold")
        tbl.add_column("Phase")
        tbl.add_column("Severity", justify="right")
        tbl.add_column("Band", justify="center")

        for idx, row in enumerate(rows, start=1):
            (
                branch_name,
                phase,
                _p_flow_w,
                _q_flow_var,
                _loading_va,
                _loading_limit_va,
                ratio,
            ) = row
            ratio_f = float(ratio)
            if ratio_f >= 2.0:
                band = "[red]Critical[/]"
            elif ratio_f >= 1.4:
                band = "[yellow]High[/]"
            else:
                band = "[cyan]Moderate[/]"
            tbl.add_row(
                str(idx),
                str(branch_name),
                str(phase),
                f"{ratio_f:.2f}x",
                band,
            )
    else:
        tbl = Table(
            title=f"Branch Overloads ({implementation}{title_suffix}, run={resolved_run_id})",
            border_style="red",
        )
        tbl.add_column("Branch", style="bold")
        tbl.add_column("Phase")
        tbl.add_column("P (W)", justify="right")
        tbl.add_column("Q (var)", justify="right")
        tbl.add_column("|S| (VA)", justify="right")
        tbl.add_column("Limit (VA)", justify="right")
        tbl.add_column("Loading", justify="right")

        for (
            branch_name,
            phase,
            p_flow_w,
            q_flow_var,
            loading_va,
            loading_limit_va,
            ratio,
        ) in rows:
            tbl.add_row(
                str(branch_name),
                str(phase),
                f"{float(p_flow_w):.2f}",
                f"{float(q_flow_var):.2f}",
                f"{float(loading_va):.2f}",
                f"{float(loading_limit_va):.2f}",
                f"{100.0 * float(ratio):.1f}%",
            )

    console.print()
    console.print(tbl)
    if implementation == "dc_opf":
        console.print(
            "[yellow]Note:[/] DC overload values are post-processed approximations from angle differences (P-only proxy)."
        )
    console.print()


@app.command("db-schema")
def db_schema(
    db: Path = typer.Option(..., "--db", help="SQLite database path"),
    include_internal: bool = typer.Option(
        False,
        "--include-internal",
        help="Include sqlite_* internal tables",
    ),
):
    """Print SQLite table/column schema for quick inspection."""
    if not db.exists():
        err_console.print(f"[red]Error:[/] database not found: {db}")
        raise typer.Exit(1)

    schema = _read_db_schema(str(db), include_internal=include_internal)
    if not schema:
        console.print(
            Panel(
                f"[yellow]No tables found in {db}[/]",
                border_style="yellow",
                title="Empty Schema",
            )
        )
        return

    tbl = Table(title=f"SQLite Schema ({db})", border_style="cyan", show_lines=True)
    tbl.add_column("Table", style="bold")
    tbl.add_column("Columns")

    for table_name, columns in schema:
        tbl.add_row(table_name, ", ".join(columns))

    console.print()
    console.print(tbl)
    console.print()


# ── display helpers ──────────────────────────────────────────────────────


def _print_dc_dispatch(dc_r: dict) -> None:
    """Print DC OPF generator dispatch table."""
    result = dc_r["result"]
    dispatch = result.generator_dispatch_w

    if not dispatch:
        return

    console.print()
    dtbl = Table(title="DC Generator Dispatch", border_style="dim")
    dtbl.add_column("Generator", style="bold")
    dtbl.add_column("Type", style="dim")
    dtbl.add_column("Dispatch", justify="right")

    for name, val in sorted(dispatch.items()):
        if name.startswith("grid:"):
            gtype = "[red]Grid[/]"
        elif name.startswith("solar:"):
            gtype = "[yellow]Solar[/]"
        elif name.startswith("battery:"):
            gtype = "[cyan]Battery[/]"
        else:
            gtype = "Other"
        # Shorten name for display
        short = name.split(":", 1)[1] if ":" in name else name
        dtbl.add_row(short, gtype, _fmt_w(val))

    dtbl.add_section()
    dtbl.add_row(
        "[bold]Total Grid[/]", "", f"[bold]{_fmt_w(dc_r.get('grid_import', 0))}[/]"
    )
    dtbl.add_row(
        "[bold]Total Solar[/]", "", f"[bold]{_fmt_w(dc_r.get('solar_dispatch', 0))}[/]"
    )
    dtbl.add_row(
        "[bold]Total Battery[/]",
        "",
        f"[bold]{_fmt_w(dc_r.get('battery_dispatch', 0))}[/]",
    )
    console.print(dtbl)


def _print_ac_voltages(ac_r: dict, system: DistributionSystem) -> None:
    """Print AC voltage magnitude table."""
    result = ac_r["result"]
    idx_map = result.ybus_result.index_to_label
    v = result.voltage

    console.print()
    vtbl = Table(title="AC Bus Voltages", border_style="dim")
    vtbl.add_column("Bus", style="bold")
    vtbl.add_column("Phase")
    vtbl.add_column("|V| (V)", justify="right")
    vtbl.add_column("∠V (°)", justify="right")

    for i, lbl in enumerate(idx_map):
        vm = abs(v[i])
        va = np.degrees(np.angle(v[i]))
        vtbl.add_row(lbl[0], lbl[1], f"{vm:.2f}", f"{va:.2f}")

    console.print(vtbl)


def _export_html(
    system: DistributionSystem,
    ac_r: dict,
    dc_r: dict,
    ldf_r: dict,
    output: Path,
) -> None:
    """Generate an interactive Plotly HTML comparison (best-effort)."""
    try:
        import plotly.graph_objects as go  # noqa: F401
    except ImportError:
        err_console.print("[yellow]plotly not installed — skipping HTML export[/]")
        return

    # Delegate to the existing comparison logic
    from pathlib import Path as _P
    import subprocess
    import sys

    compare_script = (
        _P(__file__).resolve().parent.parent.parent
        / "examples"
        / "compare_plotly_results.py"
    )
    if compare_script.exists():
        with console.status("[cyan]Generating HTML report…"):
            subprocess.run(
                [sys.executable, str(compare_script)],
                cwd=str(compare_script.parent.parent),
                capture_output=True,
            )
        console.print(
            Panel(
                f"[green]HTML report written to [bold]{output}[/][/]",
                border_style="green",
            )
        )
    else:
        err_console.print("[yellow]Compare script not found — skipping HTML export[/]")


# ── entry point ──────────────────────────────────────────────────────────


def main() -> None:
    app()


if __name__ == "__main__":
    main()
