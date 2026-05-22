# Solver Overview

FGC-Flow provides three power flow solvers, each suited to different analysis needs. All operate on `DistributionSystem` objects from grid-data-models and share the same Y-bus construction infrastructure.

## Solver Comparison

| Feature | AC OPF | DC OPF | LinDistFlow |
|---------|--------|--------|-------------|
| **Formulation** | Nonlinear least-squares | Quadratic program | Backward/forward sweep |
| **Variables** | $V_m$, $\theta$ (per-unit) | $P_g$, $\theta$ | $V^2$, $P$, $Q$ |
| **Losses** | Full $I^2R$ losses | Neglected | Neglected |
| **Reactive Power** | Full Q modeling | Neglected | Modeled |
| **Network Topology** | Meshed or radial | Meshed or radial | Radial only |
| **Economic Dispatch** | No | Yes (generation costs) | No |
| **Speed** | Moderate (~300 ms) | Moderate (~400 ms) | Fast (~2 ms) |
| **Accuracy** | Highest | Approximate | Approximate |
| **Center-Tapped Transformers** | Full support (polarity-aware) | Limited (small-angle violation) | Full support (directed graph) |

## When to Use Each Solver

### AC OPF
Use when you need **accurate voltages and losses**. The AC solver finds complex voltages that satisfy power balance at every node, including reactive power and $I^2R$ line losses. Best for:
- Voltage regulation studies
- Loss analysis
- Detailed power quality assessment

### DC OPF
Use when you need **economic dispatch with generation costs**. The DC solver minimizes total generation cost subject to linearized power balance constraints. Best for:
- DER dispatch optimization (solar, battery, grid import)
- Market-clearing simulations
- Generation scheduling

> **Note:** DC OPF uses the small-angle approximation ($\sin\Delta\theta \approx \Delta\theta$), which breaks down across center-tapped transformers where the S2 winding operates at 180° from the primary. On systems with significant split-phase residential load, DC OPF will underestimate total source power. Use AC OPF or LinDistFlow for accurate results on such systems.

### LinDistFlow
Use when you need **fast voltage drop estimates** on radial feeders. LinDistFlow performs a single backward/forward sweep without iteration. Best for:
- Screening studies and quick assessments
- Large-scale parametric sweeps
- Hosting capacity analysis

## Common Workflow

All solvers follow the same pattern:

```python
from gdm.distribution import DistributionSystem

# 1. Load the system
system = DistributionSystem.from_json("model.json")

# 2. Run a solver (each has a *_from_components convenience wrapper)
result = solver_from_components(system, ...)

# 3. Inspect results
print(result.success)
```

The `*_from_components` wrapper functions automatically extract loads, solar, batteries, and other components from the system. For fine-grained control, use the lower-level functions that accept explicit parameter dictionaries.
