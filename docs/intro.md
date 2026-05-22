# FGC-Flow

**Power Flow & Optimal Power Flow for Distribution Systems**

FGC-Flow provides three complementary solvers for analyzing distribution power systems built on [grid-data-models](https://github.com/NLR-Distribution-Suite/grid-data-models):

| Solver | Method | Strengths |
|--------|--------|-----------|
| **AC OPF** | Nonlinear least-squares on Y-bus | Full voltage & power accuracy including losses |
| **DC OPF** | Quadratic programming with linearized constraints | Economic dispatch with generation cost optimization |
| **LinDistFlow** | Backward/forward sweep on radial tree | Fast, lightweight voltage drop analysis |

## Key Features

- **Y-Bus Construction** — Phase-domain admittance matrices from GDM components (branches, transformers, switches) with matrix and sequence impedance support
- **Three OPF Solvers** — AC nonlinear, DC linearized, and LinDistFlow radial approximation
- **Multi-Phase Support** — Full three-phase modeling with per-phase power injection and voltage tracking
- **Component Integration** — Direct integration with GDM loads, solar PV, batteries, capacitors, and regulators
- **Modern CLI** — Rich terminal interface with formatted tables, progress indicators, and solver comparison
- **SQLite Export** — Structured database output for post-processing and archival
- **Per-Unit Internals** — Numerically robust per-unit formulation for AC OPF with automatic base conversion

## Architecture

```
DistributionSystem (GDM JSON)
        │
        ▼
   ┌─────────┐
   │  Y-Bus  │  ← Phase-domain admittance matrix
   └────┬────┘
        │
   ┌────┴───────────────────────┐
   │            │               │
   ▼            ▼               ▼
┌───────┐  ┌────────┐  ┌─────────────┐
│AC OPF │  │DC OPF  │  │ LinDistFlow │
└───┬───┘  └───┬────┘  └──────┬──────┘
    │          │               │
    ▼          ▼               ▼
 Results    Results         Results
    │          │               │
    └──────────┴───────────────┘
               │
         ┌─────┴─────┐
         │  CLI / DB  │
         └────────────┘
```

## Quick Example

```python
from gdm.distribution import DistributionSystem
from fgc_flow import optimize_ac_power_flow_from_components

system = DistributionSystem.from_json("model.json")
result = optimize_ac_power_flow_from_components(system)

print(f"Success: {result.success}")
print(f"Iterations: {result.iterations}")
```

## Navigation

Use the sidebar to explore:

- **Getting Started** — Installation and a hands-on quickstart notebook
- **Solvers** — Theory and implementation details for each solver
- **User Guide** — CLI usage, SQLite export, testing workflows, and result comparison
- **API Reference** — Complete function and class documentation
