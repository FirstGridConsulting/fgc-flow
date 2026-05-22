# Command-Line Interface

GDM-OPF includes a modern CLI built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/) for colorful, readable terminal output.

## Installation

The CLI is installed automatically with the package:

```bash
pip install -e ".[optimization]"
```

The command `gdm-opf` becomes available in your terminal.

## Commands

### `gdm-opf info`

Display system topology, component counts, and power summary.

```bash
gdm-opf info examples/models/p5r.json
```

**Output includes:**
- Source bus name and phases
- Bus count, transformer count, load count, solar PV count
- Total load (P and Q), solar active/rated power, net demand
- Per-bus details: phases, rated voltage, bus type

### `gdm-opf run`

Run one or more solvers on a distribution system model.

```bash
# Run AC OPF only (default)
gdm-opf run examples/models/p5r.json

# Run multiple solvers
gdm-opf run examples/models/p5r.json -s ac -s dc -s ldf

# Verbose — show voltage table and dispatch details
gdm-opf run examples/models/p5r.json -s ac -s dc -v
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `-s`, `--solver` | `ac` | Solver(s) to run: `ac`, `dc`, `ldf` (repeatable) |
| `-v`, `--verbose` | `false` | Show detailed voltage and dispatch tables |

### `gdm-opf compare`

Run all three solvers and display a side-by-side comparison.

```bash
gdm-opf compare examples/models/p5r.json

# Also generate an HTML comparison plot
gdm-opf compare examples/models/p5r.json -o comparison.html
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

### `gdm-opf export`

Run solvers and export results to a SQLite database.

```bash
# Export all solvers
gdm-opf export examples/models/p5r.json --db results.db

# Export only AC and DC
gdm-opf export examples/models/p5r.json --db results.db -s ac -s dc
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ac dc ldf` | Solver(s) to export (repeatable) |

### `gdm-opf report-overvoltage`

Print voltage limit violations from exported AC OPF or LinDistFlow node results.

```bash
# Check latest AC run in database
gdm-opf report-overvoltage --db results.db

# Check latest LinDistFlow run
gdm-opf report-overvoltage --db results.db -s ldf

# Check a specific run id
gdm-opf report-overvoltage --db results.db -s ac --run-id ac_123456abcdef
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ac` | Solver result set: `ac` or `ldf` |
| `--run-id` | latest run | Specific run id to inspect |

### `gdm-opf report-overload`

Print branch loading violations from exported AC OPF, DC OPF, or LinDistFlow branch results.

> **DC note:** `-s dc` uses a post-processed DC approximation (angle-difference, P-only proxy), not full AC branch power flow.

```bash
# Check latest LinDistFlow run
gdm-opf report-overload --db results.db

# Check latest AC OPF run
gdm-opf report-overload --db results.db -s ac

# Check latest DC OPF run
gdm-opf report-overload --db results.db -s dc

# For DC, optionally print full percentage table instead of ranked severity
gdm-opf report-overload --db results.db -s dc --no-dc-severity-only

# Check a specific run id
gdm-opf report-overload --db results.db --run-id lindistflow_123456abcdef
```

**Options:**
| Flag | Default | Description |
|------|---------|-------------|
| `--db` | *required* | Path to SQLite database file |
| `-s`, `--solver` | `ldf` | Solver result set: `ac`, `dc`, or `ldf` |
| `--run-id` | latest solver run | Specific run id to inspect |
| `--dc-severity-only/--no-dc-severity-only` | `true` | For DC reports, show ranked severity instead of percent magnitudes |

### `gdm-opf db-schema`

Print the SQLite table/column schema for a database file.

```bash
# Show user tables and columns
gdm-opf db-schema --db results.db

# Include sqlite_* internal tables
gdm-opf db-schema --db results.db --include-internal
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
gdm-opf info examples/models/p5r.json

# Run all solvers and see if they agree
gdm-opf compare examples/models/p5r.json
```

### Full Analysis Pipeline

```bash
# 1. Inspect the system
gdm-opf info examples/models/p5r.json

# 2. Run solvers with detailed output
gdm-opf run examples/models/p5r.json -s ac -s dc -s ldf -v

# 3. Export to database for further analysis
gdm-opf export examples/models/p5r.json --db analysis.db

# 4. Generate comparison plot
gdm-opf compare examples/models/p5r.json -o comparison.html
```
