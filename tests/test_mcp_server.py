import asyncio
import json
from pathlib import Path

import numpy as np

import pytest

pytest.importorskip("mcp")

from fgc_flow.mcp import server as mcp_server


def test_mcp_list_tools_includes_documentation_tools():
    tools = asyncio.run(mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}

    assert "opf_calculate_ybus" in tool_names
    assert "opf_run_ac" in tool_names
    assert "list_opf_documentation" in tool_names
    assert "search_opf_documentation" in tool_names
    assert "get_opf_documentation_page" in tool_names
    assert "list_opf_api_symbols" in tool_names
    assert "get_opf_api_reference" in tool_names


def test_mcp_documentation_tools_smoke():
    listing = asyncio.run(mcp_server._handle_list_opf_documentation({}))
    assert listing["count"] > 0
    assert "intro.md" in listing["files"]

    search = asyncio.run(
        mcp_server._handle_search_opf_documentation(
            {
                "query": "AC OPF",
                "max_results": 3,
            }
        )
    )
    assert search["count"] >= 1

    page = asyncio.run(
        mcp_server._handle_get_opf_documentation_page(
            {
                "doc_path": "intro.md",
                "start_line": 1,
                "max_lines": 20,
            }
        )
    )
    assert page["path"] == "intro.md"
    assert "FGC-Flow" in page["content"]


def test_mcp_api_reference_tools_smoke():
    symbol_listing = asyncio.run(mcp_server._handle_list_opf_api_symbols({}))
    assert symbol_listing["count"] > 0
    assert "calculate_ybus" in symbol_listing["symbols"]

    api_ref = asyncio.run(
        mcp_server._handle_get_opf_api_reference(
            {
                "symbol_name": "calculate_ybus",
            }
        )
    )
    assert api_ref["symbol"] == "calculate_ybus"
    assert api_ref["module"].startswith("fgc_flow")
    assert api_ref["signature"] is not None


def test_extract_snippet_and_source_bus_totals_helpers():
    text = "alpha beta gamma delta"
    assert mcp_server._extract_snippet(text, "beta")
    assert mcp_server._extract_snippet(text, "missing") == ""

    totals = mcp_server._source_bus_totals(
        [("source", "A"), ("load", "A"), ("source", "B")],
        np.array([1 + 2j, 5 + 6j, 3 + 4j], dtype=np.complex128),
    )
    assert totals == {"source_bus": "source", "p_w": 4.0, "q_var": 6.0}


def test_serialize_ybus_result_with_matrix_preview_and_sparse_like():
    class _SparseLike:
        def __init__(self, arr):
            self._arr = arr

        def toarray(self):
            return self._arr

    class _Result:
        ybus = _SparseLike(
            np.array([[1 + 1j, 0], [0, 2 + 0j]], dtype=np.complex128)
        )
        index_to_label = [("b1", "A"), ("b2", "A")]

    payload = mcp_server._serialize_ybus_result(
        _Result(), include_matrix=True, matrix_preview_limit=2
    )
    assert payload["n_nodes"] == 2
    assert payload["is_sparse"] is True
    assert payload["matrix_preview"]["rows"] == 2


def test_api_reference_unknown_symbol_raises():
    with pytest.raises(ValueError):
        mcp_server._api_reference_for_symbol("does_not_exist")


def test_serialize_result_helpers_with_details():
    class _AC:
        success = True
        message = "ok"
        iterations = 2
        initial_objective = 3.0
        final_objective = 1.0
        voltage = np.array([230 + 0j, 229 - 1j], dtype=np.complex128)
        power_injection = np.array([100 + 10j, -100 - 10j], dtype=np.complex128)

        class _Y:
            index_to_label = [("source", "A"), ("load", "A")]

        ybus_result = _Y()

    ac_payload = mcp_server._serialize_ac_result(_AC(), include_details=True)
    assert ac_payload["success"] is True
    assert len(ac_payload["nodes"]) == 2

    class _DC:
        success = True
        message = "ok"
        objective = 2.5
        iterations = 4
        slack_injection_w = 50.0
        generator_dispatch_w = {"g": 10.0}
        theta_rad = {("b1", "A"): 0.1}
        nodal_balance_w = {("b1", "A"): 0.0}

    dc_payload = mcp_server._serialize_dc_result(_DC(), include_details=True)
    assert dc_payload["generator_count"] == 1
    assert len(dc_payload["theta_rad"]) == 1

    class _LDF:
        success = True
        message = "ok"
        source_bus = "source"
        voltage_v = {("b1", "A"): 230.0}
        p_flow_w = {("line", "A"): 100.0}
        q_flow_var = {("line", "A"): 20.0}

    ldf_payload = mcp_server._serialize_lindistflow_result(_LDF(), include_details=True)
    assert ldf_payload["modeled_nodes"] == 1
    assert len(ldf_payload["q_flow_var"]) == 1


def test_call_tool_unknown_and_handler_failure(monkeypatch):
    unknown = asyncio.run(mcp_server.call_tool("no_such_tool", {}))
    unknown_payload = json.loads(unknown[0].text)
    assert "Unknown tool" in unknown_payload["error"]

    async def _boom(_args):
        raise RuntimeError("boom")

    monkeypatch.setitem(mcp_server._TOOL_HANDLERS, "boom_tool", _boom)
    failure = asyncio.run(mcp_server.call_tool("boom_tool", {}))
    failure_payload = json.loads(failure[0].text)
    assert "RuntimeError" in failure_payload["error"]


def test_handle_get_documentation_page_validation_and_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "DOCS_ROOT", tmp_path)
    (tmp_path / "intro.md").write_text("line1\nline2\nline3\n", encoding="utf-8")

    page = asyncio.run(
        mcp_server._handle_get_opf_documentation_page(
            {"doc_path": "intro.md", "start_line": 2, "max_lines": 2}
        )
    )
    assert page["start_line"] == 2
    assert "line2" in page["content"]

    with pytest.raises(ValueError):
        asyncio.run(
            mcp_server._handle_get_opf_documentation_page(
                {"doc_path": "../escape.md"}
            )
        )

    with pytest.raises(FileNotFoundError):
        asyncio.run(
            mcp_server._handle_get_opf_documentation_page(
                {"doc_path": "missing.md"}
            )
        )


def test_iter_doc_files_and_search_docs(monkeypatch, tmp_path):
    docs_root = tmp_path / "docs"
    (docs_root / "guide").mkdir(parents=True)
    (docs_root / "_build" / "html").mkdir(parents=True)
    (docs_root / "intro.md").write_text("AC OPF intro", encoding="utf-8")
    (docs_root / "guide" / "notes.ipynb").write_text("AC OPF notebook", encoding="utf-8")
    (docs_root / "_build" / "html" / "skip.md").write_text("skip", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "DOCS_ROOT", docs_root)
    files = mcp_server._iter_doc_files()
    rel = {str(p.relative_to(docs_root)) for p in files}
    assert "intro.md" in rel
    assert "guide/notes.ipynb" in rel
    assert "_build/html/skip.md" not in rel

    search = asyncio.run(
        mcp_server._handle_search_opf_documentation({"query": "AC OPF", "max_results": 1})
    )
    assert search["count"] == 1


def test_handle_calculate_run_compare_and_export_paths(monkeypatch, tmp_path):
    fake_system = object()
    monkeypatch.setattr(mcp_server, "_load_system", lambda _p: fake_system)

    class _YResult:
        ybus = np.eye(1, dtype=np.complex128)
        index_to_label = [("b", "A")]

    monkeypatch.setattr(mcp_server, "calculate_ybus", lambda *a, **k: _YResult())
    ybus_payload = asyncio.run(
        mcp_server._handle_calculate_ybus({"system_path": "x.json", "include_matrix": True})
    )
    assert ybus_payload["n_nodes"] == 1

    class _AC:
        success = True
        message = "ok"
        iterations = 1
        initial_objective = 1.0
        final_objective = 0.5
        voltage = np.array([1 + 0j], dtype=np.complex128)
        power_injection = np.array([2 + 3j], dtype=np.complex128)

        class _Y:
            index_to_label = [("source", "A")]

        ybus_result = _Y()

    class _DC:
        success = True
        message = "ok"
        objective = 1.0
        iterations = 1
        slack_injection_w = 2.0
        generator_dispatch_w = {"g": 2.0}
        theta_rad = {("source", "A"): 0.0}
        nodal_balance_w = {("source", "A"): 0.0}

    class _LDF:
        success = True
        message = "ok"
        source_bus = "source"
        voltage_v = {("source", "A"): 1.0}
        p_flow_w = {("line", "A"): 1.0}
        q_flow_var = {("line", "A"): 0.0}

    monkeypatch.setattr(mcp_server, "optimize_ac_power_flow_from_components", lambda *a, **k: _AC())
    monkeypatch.setattr(mcp_server, "solve_dc_opf_from_components", lambda *a, **k: _DC())
    monkeypatch.setattr(mcp_server, "build_lindistflow_net_injections_from_components", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(mcp_server, "solve_lindistflow", lambda *a, **k: _LDF())

    ac_payload = asyncio.run(mcp_server._handle_run_ac({"system_path": "x.json", "include_details": True}))
    dc_payload = asyncio.run(mcp_server._handle_run_dc({"system_path": "x.json", "include_details": True}))
    ldf_payload = asyncio.run(mcp_server._handle_run_lindistflow({"system_path": "x.json", "include_details": True}))
    cmp_payload = asyncio.run(mcp_server._handle_compare_solvers({"system_path": "x.json", "include_details": True}))

    assert ac_payload["success"] is True
    assert dc_payload["success"] is True
    assert ldf_payload["success"] is True
    assert cmp_payload["summary"]["ac_success"] is True

    captured = {}

    def _fake_export(db_path, ac_result, dc_result, lindistflow_result):
        captured["db"] = db_path
        captured["ac"] = ac_result is not None
        captured["dc"] = dc_result is not None
        captured["ldf"] = lindistflow_result is not None
        return {"ac": 1}

    monkeypatch.setattr(mcp_server, "export_all_results_to_sqlite", _fake_export)

    export_payload = asyncio.run(
        mcp_server._handle_export_sqlite(
            {
                "system_path": "x.json",
                "db_path": str(tmp_path / "out.sqlite"),
                "run_ac": True,
                "run_dc": False,
                "run_lindistflow": True,
            }
        )
    )
    assert export_payload["exported"] == {"ac": True, "dc": False, "lindistflow": True}
    assert captured["ac"] is True and captured["dc"] is False and captured["ldf"] is True

    with pytest.raises(ValueError):
        asyncio.run(
            mcp_server._handle_export_sqlite(
                {
                    "system_path": "x.json",
                    "db_path": str(tmp_path / "out.sqlite"),
                    "run_ac": False,
                    "run_dc": False,
                    "run_lindistflow": False,
                }
            )
        )


def test_load_system_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        mcp_server._load_system(str(Path(tmp_path) / "missing.json"))


def test_run_server_and_main_wrapper(monkeypatch):
    class _Ctx:
        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(mcp_server, "stdio_server", lambda: _Ctx())

    calls = {"run": 0, "main": 0}

    async def _fake_run(read_stream, write_stream, init_opts):
        calls["run"] += 1
        assert read_stream == "read"
        assert write_stream == "write"
        assert init_opts is not None

    monkeypatch.setattr(mcp_server.app, "run", _fake_run)
    monkeypatch.setattr(mcp_server.app, "create_initialization_options", lambda: {"x": 1})

    mcp_server._run_server("INFO")
    assert calls["run"] == 1

    monkeypatch.setattr(mcp_server.typer, "run", lambda fn: calls.__setitem__("main", calls["main"] + 1))
    mcp_server.main()
    assert calls["main"] == 1
