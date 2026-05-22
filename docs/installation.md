# Installation

## Requirements

- Python ≥ 3.11
- [grid-data-models](https://github.com/NLR-Distribution-Suite/grid-data-models)

## Install from Source

Clone the repository and install in editable mode:

```bash
git clone https://github.com/FirstGridConsulting/fgc-flow.git
cd fgc-flow
pip install -e .
```

## Optional Extras

FGC-Flow has optional dependency groups for different use cases:

```bash
# For AC OPF and DC OPF solvers (requires SciPy)
pip install -e ".[optimization]"

# For sparse Y-bus matrices
pip install -e ".[sparse]"

# For development and testing
pip install -e ".[dev]"

# For MCP server runtime and MCP tests
pip install -e ".[mcp,optimization]"

# Install everything
pip install -e ".[optimization,sparse,dev,mcp]"
```

## Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| `numpy` | Array operations, Y-bus matrices | Yes |
| `grid-data-models` | Distribution system data model | Yes |
| `typer` | CLI framework | Yes |
| `rich` | Terminal formatting | Yes |
| `scipy` | AC/DC optimization solvers | Optional |
| `mcp` | MCP server runtime and MCP tests | Optional |

## Testing Notes

For a consolidated local/CI testing reference, see `docs/guide/testing.md`.

Run all tests:

```bash
pytest -v --tb=short
```

Run MCP server tests directly:

```bash
pip install -e ".[mcp,optimization,dev]"
pytest -v --tb=short tests/test_mcp_server.py
```

In GitHub Actions, MCP tests are enforced by the dedicated `mcp-test` job in `.github/workflows/ci.yml`.

## Verify Installation

After installation, verify the CLI is available:

```bash
fgc-flow --help
```

You should see:

```
Usage: fgc-flow [OPTIONS] COMMAND [ARGS]...

 FGC-Flow — Power flow & optimal power flow for distribution systems

╭─ Commands ──────────────────────────────────────────────╮
│ info      Show system topology and component summary.   │
│ run       Run one or more OPF solvers.                  │
│ compare   Run all three solvers and compare results.    │
│ export    Run solvers and export results to SQLite.     │
╰─────────────────────────────────────────────────────────╯
```

Or verify in Python:

```python
import fgc_flow
print(dir(fgc_flow))
```
