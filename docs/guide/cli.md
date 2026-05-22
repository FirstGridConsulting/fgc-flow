# Command-Line Interface

GDM-OPF includes a modern CLI built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/) for colorful, readable terminal output.

## Installation

The CLI is installed automatically with the package:

```bash
pip install -e ".[optimization]"
```

The command `fgc-flow` becomes available in your terminal.

## Commands

### `fgc-flow info`

Display system topology, component counts, and power summary.

```bash
fgc-flow info examples/models/p5r.json
```

**Output includes:**
- Source bus name and phases
- Bus count, transformer count, load count, solar PV count
- Total load (P and Q), solar active/rated power, net demand
- Per-bus details: phases, rated voltage, bus type

### `fgc-flow run`

Run one or more solvers on a distribution system model.

```bash
# Run AC OPF only (default)
fgc-flow run examples/models/p5r.json

# Run multiple solvers
fgc-flow run examples/models/p5r.json -s ac -s dc -s ldf

# Verbose — show voltage table and dispatch details
fgc-flow run examples/models/p5r.json -s ac -s dc -v
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `-s`, `--solver` | `ac` | Solver(s) to run: `ac`, `dc`, `ldf` (repeatable) |
| `-v`, `--verbose` | `false` | Show detailed voltage and dispatch tables |

### `fgc-flow compare`

Run all three solvers and display a side-by-side comparison.

```bash
fgc-flow compare examples/models/p5r.json

# Also generate an HTML comparison plot
fgc-flow compare examples/models/p5r.json -o comparison.html
```

The comparison shows:
- Status (pass/fail) for each solver
- Source power (P and Q)
- Execution time and iteration count
- DC dispatch breakdown (grid, solar, battery)
- Agreement panel showing maximum disagreement in watts

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | None | Export comparison to interactive HTML (requires plotly) |

### `fgc-flow export`

Run solvers and export results to a SQLite database.

```bash
# Export all solvers
fgc-flow export examples/models/p5r.json --db results.db

# Export only AC and DC
fgc-flow export examples/models/p5r.json --db results.db -s ac -s dc
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ac dc ldf` | Solver(s) to export (repeatable) |

### `fgc-flow report-overvoltage`

Print voltage limit violations from exported AC OPF or LinDistFlow node results.

```bash
# Check latest AC run in database
fgc-flow report-overvoltage --db results.db

# Check latest LinDistFlow run
fgc-flow report-overvoltage --db results.db -s ldf

# Check a specific run id
fgc-flow report-overvoltage --db results.db -s ac --run-id ac_123456abcdef
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ac` | Solver result set: `ac` or `ldf` |
| `--run-id` | latest run | Specific run id to inspect |

### `fgc-flow report-overload`

Print branch loading violations from exported AC OPF, DC OPF, or LinDistFlow branch results.

> **DC note:** `-s dc` uses a post-processed DC approximation (angle-difference, P-only proxy), not full AC branch power flow.

```bash
# Check latest LinDistFlow run
fgc-flow report-overload --db results.db

# Check latest AC OPF run
fgc-flow report-overload --db results.db -s ac

# Check latest DC OPF run
fgc-flow report-overload --db results.db -s dc

# For DC, optionally print full percentage table instead of ranked severity
fgc-flow report-overload --db results.db -s dc --no-dc-severity-only

# Check a specific run id
fgc-flow report-overload --db results.db --run-id lindistflow_123456abcdef
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ldf` | Solver result set: `ac`, `dc`, or `ldf` |
| `--run-id` | latest solver run | Specific run id to inspect |
| `--dc-severity-only/--no-dc-severity-only` | `true` | For DC reports, show ranked severity instead of percent magnitudes |

### `fgc-flow db-schema`

Print the SQLite table/column schema for a database file.

```bash
# Show user tables and columns
fgc-flow db-schema --db results.db

# Include sqlite_* internal tables
fgc-flow db-schema --db results.db --include-internal
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `--include-internal` | `false` | Include SQLite internal tables |

## Examples

### Quick System Check

```bash
# What's in this model?
fgc-flow info examples/models/p5r.json

# Run all solvers and see if they agree
fgc-flow compare examples/models/p5r.json
```

### Full Analysis Pipeline

```bash
# 1. Inspect the system
fgc-flow info examples/models/p5r.json

# 2. Run solvers with detailed output
fgc-flow run examples/models/p5r.json -s ac -s dc -s ldf -v

# 3. Export to database for further analysis
fgc-flow export examples/models/p5r.json --db analysis.db

# 4. Generate comparison plot
fgc-flow compare examples/models/p5r.json -o comparison.html
```
