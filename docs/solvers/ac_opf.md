# AC OPF

The AC OPF solver finds complex nodal voltages that minimize power mismatch across the network. It operates in per-unit internally for numerical stability and converts back to SI for output.

## Formulation

Given the Y-bus equation $S(V) = V \cdot \overline{Y_{bus} \cdot V}$, the solver minimizes:

$$\min_{V_m, \theta} \sum_{i \notin \text{slack}} \left| \frac{S_i(V) - S_i^{spec}}{S_i^{scale}} \right|^2 + w_{reg} \sum_i (V_{m,i} - 1)^2 + w_{tgt} \sum_{j \in \text{reg}} (V_{m,j} - V_j^{tgt})^2$$

where:
- $S_i^{spec}$ is the specified net power injection (positive = generation)
- $S_i^{scale}$ normalizes each residual for conditioning
- $w_{reg}$ is a voltage regularization weight
- $w_{tgt}$ penalizes deviation from regulator voltage targets

### Decision Variables

The solver optimizes voltage magnitude $V_m$ (per-unit) and angle $\theta$ (radians) at each non-slack node. Slack nodes are held at nominal voltage and zero angle.

### Per-Unit System

Internally, the solver converts to per-unit:
- **Voltage base**: Nominal phase voltage at each bus
- **Power base**: $S_{base} = 1\text{ MW}$
- **Y-bus conversion**: $Y_{pu} = Y_{SI} \cdot (V_{base}^{(i)} \cdot V_{base}^{(j)}) / S_{base}$

This absorbs transformer turns ratios into the voltage bases and improves solver conditioning.

## Newton-Raphson Warm Start

For large systems (> 1000 non-slack nodes), the solver uses a Newton-Raphson (NR) power flow as a warm start before the least-squares optimizer. The NR solver:

1. Builds a sparse analytical Jacobian of the power balance equations
2. Solves the linear system with sparse LU factorization (via `spsolve`)
3. Applies a damped line search (backtracking) to ensure monotonic convergence
4. Runs without voltage bounds — the physical power-flow solution is returned directly when NR converges

When NR converges (typical: 5–10 iterations), the expensive `least_squares` / LSMR refinement is skipped entirely. This reduces solve time from minutes to seconds for large distribution systems.

## Center-Tapped Transformer Support

The AC solver correctly handles center-tapped (split-phase) service transformers. The S2 winding is antiphase (180°) relative to the primary due to the neutral center tap. The solver:

- Initializes S2-phase node angles at $\theta = \pi$ for correct flat start
- Uses the polarity-aware Y-bus from `calculate_ybus` (see [Y-Bus Construction](ybus.md#center-tapped-split-phase-transformer-model))

Without the 180° initialization, Newton-Raphson diverges because the flat start (all angles at 0°) is far from the physical operating point for antiphase nodes.

## Multi-Phase Slack

The solver supports multi-phase slack bus operation. When using `optimize_ac_power_flow_from_components`, all non-neutral phases of the source bus are automatically designated as slack nodes.

## Usage

### High-Level (Recommended)

```python
from gdm_opf import optimize_ac_power_flow_from_components

result = optimize_ac_power_flow_from_components(
    system,
    include_loads=True,
    include_solar=True,
    include_capacitor=True,
    include_regulator_targets=True,
    include_regulator_limits=True,
)

print(f"Success:    {result.success}")
print(f"Iterations: {result.iterations}")
```

### Low-Level

```python
from gdm_opf import optimize_ac_power_flow

result = optimize_ac_power_flow(
    system,
    p_spec_w={("bus_1", "A"): -1000.0},   # 1 kW load
    q_spec_var={("bus_1", "A"): -200.0},   # 200 var load
    slack_label=[("source", "A"), ("source", "B"), ("source", "C")],
    vm_min_pu=0.95,
    vm_max_pu=1.05,
)
```

### Extracting Source Power

```python
import numpy as np

v = result.voltage
ybus = result.ybus_result.ybus
s = v * np.conj(ybus @ v)

source_bus = "my_source_bus"
idx_map = result.ybus_result.index_to_label
src_idx = [i for i, lbl in enumerate(idx_map) if lbl[0] == source_bus]

source_p = sum(s[i].real for i in src_idx)
source_q = sum(s[i].imag for i in src_idx)
print(f"Source: P={source_p:.1f} W, Q={source_q:.1f} var")
```

## Result Object

`PowerFlowOptimizationResult` contains:

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the solver converged |
| `message` | `str` | Solver status message |
| `ybus_result` | `YBusResult` | Y-bus matrix and node indexing |
| `voltage` | `np.ndarray` | Complex nodal voltages (SI, volts) |
| `power_injection` | `np.ndarray` | Complex power injections (SI, watts + j·vars) |
| `iterations` | `int` | Number of solver iterations |
| `initial_objective` | `float` | Objective value at start |
| `final_objective` | `float` | Objective value at convergence |

## Tuning Parameters

| Parameter | Default | Effect |
|-----------|---------|--------|
| `vm_min_pu` | 0.95 | Lower voltage bound (per-unit) |
| `vm_max_pu` | 1.05 | Upper voltage bound (per-unit) |
| `voltage_reg_weight` | 1e-3 | Strength of voltage regularization |
| `voltage_target_weight` | 1.0 | Strength of regulator target penalty |
| `mismatch_scale_floor_w` | 1e3 | Minimum normalization for power mismatch |
| `max_nfev` | 300 | Maximum number of function evaluations |

## Assumptions

- **Balanced slack voltage.** Slack bus phases are held at nominal voltage magnitude and reference angle (0° for A/B/C phases). No unbalanced source voltage support.
- **S2 phase offset.** All bus-phase nodes named `S2` are assumed to be center-tapped transformer secondaries and initialized at $\theta = \pi$. Systems with `S2`-named phases that are not center-tapped secondaries would get incorrect initialization.
- **NR warm start is unconstrained.** The Newton-Raphson warm start runs without voltage bounds (`vm_min_pu` / `vm_max_pu`). The converged physical power-flow solution may have voltages outside the specified bounds, particularly on long LV feeders downstream of center-tapped transformers.
- **Connectivity filtering.** Nodes unreachable from the slack bus via Y-bus adjacency are treated as fixed at flat start and excluded from the solve. This prevents infeasibility from isolated sub-networks but means those nodes will show zero power injection.
- **Sign convention.** Positive power spec = generation/injection; negative = load/demand.
