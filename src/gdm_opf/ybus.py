"""Build Y-bus matrices from a grid-data-models DistributionSystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
import math

import numpy as np

from gdm.distribution import DistributionSystem
from gdm.distribution.components import (
    DistributionBus,
    DistributionTransformer,
    GeometryBranch,
    SequenceImpedanceBranch,
)
from gdm.distribution.components.base.distribution_branch_base import (
    DistributionBranchBase,
)
from gdm.distribution.components.base.distribution_switch_base import (
    DistributionSwitchBase,
)
from gdm.distribution.enums import Phase

from ._utils import _phase_name, _phase_voltage

BusPhaseLabel = Tuple[str, str]


@dataclass(frozen=True)
class YBusResult:
    """Container for Y-bus output and node indexing metadata."""

    ybus: np.ndarray
    index_to_label: List[BusPhaseLabel]
    label_to_index: Dict[BusPhaseLabel, int]


def _build_bus_phase_index(
    system: DistributionSystem,
    include_neutral: bool,
) -> tuple[list[BusPhaseLabel], dict[BusPhaseLabel, int]]:
    labels: list[BusPhaseLabel] = []
    for bus in sorted(system.get_components(DistributionBus), key=lambda b: b.name):
        for phase in bus.phases:
            if not include_neutral and phase == Phase.N:
                continue
            labels.append((bus.name, _phase_name(phase)))

    label_to_index = {label: i for i, label in enumerate(labels)}
    return labels, label_to_index


def _active_branch_phase_indices(
    branch: DistributionBranchBase,
    include_neutral: bool,
    include_open_switches: bool,
) -> list[int]:
    active = []
    for i, phase in enumerate(branch.phases):
        if not include_neutral and phase == Phase.N:
            continue
        if isinstance(branch, DistributionSwitchBase) and not include_open_switches:
            if not bool(branch.is_closed[i]):
                continue
        active.append(i)
    return active


def _matrix_branch_series_admittance(
    branch: DistributionBranchBase, active_idx: list[int]
) -> np.ndarray:
    # All matrix-impedance branch/switch/fuse/recloser models share these fields.
    r = branch.equipment.r_matrix.to("ohm/m").magnitude
    x = branch.equipment.x_matrix.to("ohm/m").magnitude
    length_m = float(branch.length.to("m").magnitude)
    z = (r + 1j * x) * length_m
    z = z[np.ix_(active_idx, active_idx)]
    try:
        return np.linalg.inv(z)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(z)


def _matrix_branch_shunt_admittance(
    branch: DistributionBranchBase,
    active_idx: list[int],
    frequency_hz: float,
) -> np.ndarray:
    c = branch.equipment.c_matrix.to("farad/m").magnitude
    length_m = float(branch.length.to("m").magnitude)
    c_total = c[np.ix_(active_idx, active_idx)] * length_m
    return 1j * 2.0 * math.pi * frequency_hz * c_total


def _sequence_branch_series_admittance(
    branch: SequenceImpedanceBranch,
    active_idx: list[int],
) -> np.ndarray:
    phases = [branch.phases[i] for i in active_idx]
    n = len(phases)
    length_m = float(branch.length.to("m").magnitude)

    z1 = (
        branch.equipment.pos_seq_resistance.to("ohm/m").magnitude
        + 1j * branch.equipment.pos_seq_reactance.to("ohm/m").magnitude
    ) * length_m
    z0 = (
        branch.equipment.zero_seq_resistance.to("ohm/m").magnitude
        + 1j * branch.equipment.zero_seq_reactance.to("ohm/m").magnitude
    ) * length_m

    if n == 3 and set(phases) == {Phase.A, Phase.B, Phase.C}:
        z_self = (2.0 * z1 + z0) / 3.0
        z_mutual = (z0 - z1) / 3.0
        z = np.full((3, 3), z_mutual, dtype=np.complex128)
        np.fill_diagonal(z, z_self)
    else:
        z = np.eye(n, dtype=np.complex128) * z1

    try:
        return np.linalg.inv(z)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(z)


def _stamp_branch(
    ybus: np.ndarray,
    label_to_index: dict[BusPhaseLabel, int],
    bus_u: str,
    bus_v: str,
    phase_names: list[str],
    y_series: np.ndarray,
    y_shunt: np.ndarray | None = None,
) -> None:
    u_idx = [label_to_index[(bus_u, p)] for p in phase_names]
    v_idx = [label_to_index[(bus_v, p)] for p in phase_names]

    ybus[np.ix_(u_idx, u_idx)] += y_series
    ybus[np.ix_(v_idx, v_idx)] += y_series
    ybus[np.ix_(u_idx, v_idx)] -= y_series
    ybus[np.ix_(v_idx, u_idx)] -= y_series

    if y_shunt is not None:
        ybus[np.ix_(u_idx, u_idx)] += 0.5 * y_shunt
        ybus[np.ix_(v_idx, v_idx)] += 0.5 * y_shunt


def _stamp_transformer(
    ybus: np.ndarray,
    label_to_index: dict[BusPhaseLabel, int],
    transformer: DistributionTransformer,
    include_neutral: bool,
) -> None:
    if len(transformer.buses) < 2 or len(transformer.equipment.windings) < 2:
        return

    bus_u = transformer.buses[0]
    bus_v = transformer.buses[1]
    w_u = transformer.equipment.windings[0]
    w_v = (
        transformer.equipment.windings[1]
        if len(transformer.equipment.windings) > 1
        else w_u
    )

    r_pu = float(transformer.equipment.pct_full_load_loss) / 100.0
    x_pu = float(transformer.equipment.winding_reactances[0]) / 100.0

    v_u_phase = _phase_voltage(w_u.rated_voltage, w_u.voltage_type)
    v_v_phase = _phase_voltage(w_v.rated_voltage, w_v.voltage_type)
    s_phase = float(w_u.rated_power.to("va").magnitude) / max(1, int(w_u.num_phases))
    if s_phase <= 0:
        return

    z_base = (v_u_phase * v_u_phase) / s_phase
    z = (r_pu + 1j * x_pu) * z_base
    if abs(z) == 0:
        return

    y = 1.0 / z
    # Turns ratio: a = V_primary / V_secondary
    a = v_u_phase / v_v_phase if v_v_phase > 0 else 1.0

    common_phases = [
        p
        for p in transformer.winding_phases[0]
        if p in transformer.winding_phases[1] and (include_neutral or p != Phase.N)
    ]

    for phase in common_phases:
        p = _phase_name(phase)
        u_label = (bus_u.name, p)
        v_label = (bus_v.name, p)
        if u_label not in label_to_index or v_label not in label_to_index:
            continue
        i = label_to_index[u_label]
        j = label_to_index[v_label]
        # Proper two-winding transformer admittance model with turns ratio
        ybus[i, i] += y
        ybus[j, j] += a * a * y
        ybus[i, j] -= a * y
        ybus[j, i] -= a * y

    # Center-tapped (split-phase) transformer: primary phases (e.g. A/B/C) and
    # secondary phases (e.g. S1/S2) have different names, so common_phases is
    # empty.  Model each secondary winding as an independent path from the
    # primary, splitting admittance so the parallel combination equals the total.
    if not common_phases and len(transformer.equipment.windings) >= 3:
        num_sec = len(transformer.equipment.windings) - 1
        primary_phases = [
            p for p in transformer.winding_phases[0] if include_neutral or p != Phase.N
        ]
        for w_idx in range(1, len(transformer.equipment.windings)):
            bus_sec = (
                transformer.buses[w_idx] if w_idx < len(transformer.buses) else bus_v
            )
            w_sec = transformer.equipment.windings[w_idx]
            v_sec = _phase_voltage(w_sec.rated_voltage, w_sec.voltage_type)
            if v_sec <= 0:
                continue
            a_w = v_u_phase / v_sec
            y_w = y / num_sec
            winding_phase_list = list(transformer.winding_phases[w_idx])
            sec_phases = [
                p for p in winding_phase_list if include_neutral or p != Phase.N
            ]
            # Determine polarity from phase ordering: if the neutral
            # appears before the signal phase (e.g. [N, S2]), the node
            # voltage is inverted relative to the primary → negative
            # effective turns ratio.
            n_pos = next(
                (idx for idx, p in enumerate(winding_phase_list) if p == Phase.N),
                None,
            )
            sig_pos = next(
                (idx for idx, p in enumerate(winding_phase_list) if p != Phase.N),
                None,
            )
            if n_pos is not None and sig_pos is not None and n_pos < sig_pos:
                a_w = -a_w
            for p_phase in primary_phases:
                u_label = (bus_u.name, _phase_name(p_phase))
                if u_label not in label_to_index:
                    continue
                i = label_to_index[u_label]
                for s_phase in sec_phases:
                    v_label = (bus_sec.name, _phase_name(s_phase))
                    if v_label not in label_to_index:
                        continue
                    j = label_to_index[v_label]
                    ybus[i, i] += y_w
                    ybus[j, j] += a_w * a_w * y_w
                    ybus[i, j] -= a_w * y_w
                    ybus[j, i] -= a_w * y_w


def _as_sparse_if_requested(ybus: np.ndarray, sparse: bool):
    if not sparse:
        return ybus
    try:
        from scipy.sparse import csr_matrix
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "SciPy is required for sparse=True. Install with `pip install gdm-opf[sparse]`."
        ) from exc
    return csr_matrix(ybus)


def calculate_ybus(
    system: DistributionSystem,
    *,
    include_neutral: bool = False,
    include_shunt: bool = False,
    frequency_hz: float = 60.0,
    include_transformers: bool = True,
    include_open_switches: bool = False,
    convert_geometry_to_matrix: bool = True,
    sparse: bool = False,
) -> YBusResult:
    """Calculate a phase-domain Y-bus matrix for a DistributionSystem.

    Parameters
    ----------
    system : DistributionSystem
        Input distribution system.
    include_neutral : bool, optional
        Include neutral phase nodes in the Y-bus index.
    include_shunt : bool, optional
        Include line charging from branch capacitance matrix (pi model).
    frequency_hz : float, optional
        System frequency for shunt charging calculations.
    include_transformers : bool, optional
        Stamp series admittance for two-winding transformers.
    include_open_switches : bool, optional
        Include open switch phases. By default open phases are excluded.
    convert_geometry_to_matrix : bool, optional
        Convert `GeometryBranch` to `MatrixImpedanceBranch` on a system copy before stamping.
    sparse : bool, optional
        Return SciPy CSR matrix when True.

    Returns
    -------
    YBusResult
        Matrix and node indexing metadata.
    """

    working_system = system.deepcopy() if convert_geometry_to_matrix else system
    if convert_geometry_to_matrix and list(
        working_system.get_components(GeometryBranch)
    ):
        working_system.convert_geometry_to_matrix_representation()

    index_to_label, label_to_index = _build_bus_phase_index(
        working_system, include_neutral
    )
    n = len(index_to_label)
    ybus = np.zeros((n, n), dtype=np.complex128)

    for branch in working_system.get_components(DistributionBranchBase):
        if not branch.in_service:
            continue
        if not hasattr(branch, "equipment"):
            continue

        active_idx = _active_branch_phase_indices(
            branch, include_neutral, include_open_switches
        )
        if not active_idx:
            continue

        phase_names = [_phase_name(branch.phases[i]) for i in active_idx]
        if any((branch.buses[0].name, p) not in label_to_index for p in phase_names):
            continue
        if any((branch.buses[1].name, p) not in label_to_index for p in phase_names):
            continue

        if isinstance(branch, SequenceImpedanceBranch):
            y_series = _sequence_branch_series_admittance(branch, active_idx)
            y_shunt = None
        elif hasattr(branch.equipment, "r_matrix") and hasattr(
            branch.equipment, "x_matrix"
        ):
            y_series = _matrix_branch_series_admittance(branch, active_idx)
            y_shunt = (
                _matrix_branch_shunt_admittance(branch, active_idx, frequency_hz)
                if include_shunt
                else None
            )
        else:
            continue

        _stamp_branch(
            ybus,
            label_to_index,
            branch.buses[0].name,
            branch.buses[1].name,
            phase_names,
            y_series,
            y_shunt,
        )

    if include_transformers:
        for transformer in working_system.get_components(DistributionTransformer):
            if transformer.in_service:
                _stamp_transformer(ybus, label_to_index, transformer, include_neutral)

    return YBusResult(
        ybus=_as_sparse_if_requested(ybus, sparse),
        index_to_label=index_to_label,
        label_to_index=label_to_index,
    )
