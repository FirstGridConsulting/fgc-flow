# DC OPF

The DC OPF solver performs economic dispatch by minimizing total generation cost subject to linearized power balance constraints. It determines how much power each generator (solar, battery, grid import) should produce.

## Formulation

The DC approximation assumes:
1. Voltage magnitudes are close to nominal ($V_m \approx 1$ p.u.)
2. Angle differences are small ($\sin(\theta_i - \theta_j) \approx \theta_i - \theta_j$)
3. Reactive power and line losses are neglected

### Objective Function

$$\min_{P_g, \theta} \sum_k \left( c_k^{(2)} P_{g,k}^2 + c_k^{(1)} P_{g,k} + c_k^{(0)} \right) + \epsilon \sum_i \theta_i^2$$

where $c_k^{(2)}, c_k^{(1)}, c_k^{(0)}$ are quadratic, linear, and constant cost coefficients for generator $k$, and $\epsilon$ is a small angle regularization term.

### Power Balance Constraints

$$P_{g,i} - P_{d,i} = \sum_j B_{ij} \cdot \theta_j \quad \forall i \notin \text{slack}$$

where $B = -\text{Im}(Y_{bus}) \cdot V_{nom}^{(i)} \cdot V_{nom}^{(j)}$ is the voltage-scaled susceptance matrix.

### Slack Bus Handling

Slack nodes serve as the angle reference ($\theta_{slack} = 0$). When a slack node has an explicit generator, its power balance constraint is included so the grid import carries a cost. Without this, the optimizer would inject unlimited free power through the slack bus.

## Generator Model

Each `DCGenerator` specifies:

```python
from fgc_flow import DCGenerator

gen = DCGenerator(
    name="solar_pv_1",
    node=("load_bus", "A"),     # (bus_name, phase)
    p_min_w=0.0,                # Minimum output (W)
    p_max_w=5000.0,             # Maximum output (W)
    cost_quadratic=0.0,         # $/W²
    cost_linear=5.0,            # $/W
    cost_constant=0.0,          # $ fixed
)
```

## Usage

### High-Level (Recommended)

```python
from fgc_flow import solve_dc_opf_from_components

result = solve_dc_opf_from_components(
    system,
    include_solar_generators=True,
    include_battery_generators=True,
    include_loads=True,
)

print(f"Success: {result.success}")
for name, dispatch in result.generator_dispatch_w.items():
    print(f"  {name}: {dispatch:.1f} W")
```

The convenience wrapper automatically:
- Creates solar generators from `DistributionSolar` components (cost = 5.0)
- Creates battery generators from `DistributionBattery` components (cost = 15.0)
- Adds grid import generators at the source bus (cost = 50.0)
- Detects all source bus phases as slack

### Low-Level

```python
from fgc_flow import DCGenerator, solve_dc_opf

generators = [
    DCGenerator("solar", ("bus_2", "A"), 0.0, 5000.0, cost_linear=5.0),
    DCGenerator("grid",  ("bus_1", "A"), 0.0, 1e6,    cost_linear=50.0),
]

demand = {("bus_2", "A"): 3000.0}  # 3 kW load

result = solve_dc_opf(
    system,
    generators=generators,
    demand_w=demand,
    slack_label=[("bus_1", "A")],
)
```

## Result Object

`DCOPFResult` contains:

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the optimizer converged |
| `message` | `str` | Solver status message |
| `objective` | `float` | Minimized total generation cost |
| `iterations` | `int` | Number of optimizer iterations |
| `generator_dispatch_w` | `dict[str, float]` | Optimal dispatch per generator (W) |
| `theta_rad` | `dict[BusPhaseLabel, float]` | Voltage angles (radians) |
| `nodal_balance_w` | `dict[BusPhaseLabel, float]` | Net nodal power balance (W) |
| `slack_injection_w` | `float` | Total slack bus injection (W) |
| `ybus_result` | `YBusResult` | Y-bus and node indexing |

## Default Cost Structure

| Generator Type | `cost_linear` | Interpretation |
|----------------|---------------|----------------|
| Solar PV | 5.0 | Cheap — dispatch first |
| Battery | 15.0 | Medium — dispatch second |
| Grid Import | 50.0 | Expensive — dispatch last |

This cost hierarchy ensures the optimizer maximizes DER utilization before importing from the grid.

## Limitations and Assumptions

- **Small-angle assumption.** The DC linearization $\sin(\theta_i - \theta_j) \approx \theta_i - \theta_j$ is valid only when angle differences across branches are small. This holds well for MV/HV networks but may produce inaccurate power flows on heavily loaded feeders.
- **Center-tapped (split-phase) transformers.** The S2 winding of a center-tapped service transformer operates at a 180° phase offset from the primary. The DC small-angle approximation fundamentally breaks down across these transformers because $\Delta\theta \approx \pi$, violating $\sin(\pi) \approx \pi$. As a result, DC OPF **underestimates total source power** on systems with significant split-phase load — it accurately dispatches MV-connected loads but does not correctly model power flow through center-tapped transformers to LV loads.
- **No reactive power.** All reactive power flows and VAR sources (capacitors, inductive loads) are ignored.
- **No losses.** Line $I^2R$ losses are neglected; total generation equals total demand.
- **Connectivity filtering.** Like AC OPF, unreachable nodes are excluded from the LP. All reachable nodes participate in power balance constraints.
