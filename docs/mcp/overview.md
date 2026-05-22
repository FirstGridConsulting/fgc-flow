# MCP Overview

GDM-OPF includes an MCP (Model Context Protocol) server that exposes OPF and Y-bus workflows as callable tools for MCP-compatible clients.

## What It Provides

- Run solver workflows through structured tool calls
- Access Y-bus metadata for distribution models
- Export solver results to SQLite
- Search and read GDM-OPF documentation from MCP clients
- Inspect public GDM-OPF API symbols and docstrings

## Install

Install with MCP and optimization dependencies:

```bash
pip install -e ".[mcp,optimization]"
```

## Run the Server

```bash
fgc-flow-mcp-server
```

The server runs over stdio and is intended for MCP clients (for example, VS Code agent integrations) to start and manage.

## Testing

Run MCP server tests locally:

```bash
pip install -e ".[mcp,optimization,dev]"
pytest -v --tb=short tests/test_mcp_server.py
```

In CI, MCP server tests are run in the dedicated `mcp-test` job in `.github/workflows/ci.yml`.

## Tool Families

- Solver tools: run AC OPF, DC OPF, LinDistFlow, and cross-solver comparison
- Matrix tools: compute Y-bus metadata and optional preview values
- Export tools: persist selected solver outputs to SQLite
- Documentation tools: list/search/read docs and get API references

For complete tool details and parameters, see the MCP Tool Reference page.
