from __future__ import annotations

import json

import pytest

from mozaiksai.core.app_context.context_graph import (
    build_context_graph_from_file_map,
    context_graph_parser_status,
    extract_file_context,
)
from mozaiksai.core.app_context.models import AppContextGraph, GraphEdgeType, GraphNodeType


def _require_grammar(*languages: str) -> None:
    status = context_graph_parser_status()["languages"]
    missing = [language for language in languages if not status[language]["tree_sitter_available"]]
    assert not missing, f"Required Tree-sitter grammars unavailable: {missing}"


def _graph(files: dict[str, str]) -> AppContextGraph:
    return build_context_graph_from_file_map(
        app_id="source-syntax",
        artifact_version_id="av_syntax",
        artifact_kind="app_bundle",
        file_map=files,
    )


def _edges(graph: AppContextGraph, edge_type: GraphEdgeType):
    nodes = {node.node_id: node for node in graph.nodes}
    return [
        (nodes[edge.source_node_id], nodes[edge.target_node_id], edge.metadata)
        for edge in graph.edges
        if edge.edge_type == edge_type
    ]


def _resolved_calls(graph: AppContextGraph) -> set[tuple[str | None, str | None, str | None]]:
    return {
        (
            source.metadata.get("qualified_name"),
            target.metadata.get("path"),
            target.metadata.get("qualified_name"),
        )
        for source, target, _ in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.SYMBOL
    }


@pytest.mark.parametrize("fallback", [False, True], ids=["tree-sitter", "python-ast"])
def test_python_dependency_endpoints_and_qualified_methods(
    monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    if fallback:
        from mozaiksai.core.app_context import _source_syntax

        monkeypatch.setattr(_source_syntax, "_tree_sitter_parser", lambda language: None)
    else:
        _require_grammar("python")
    path = "pkg/main.py"
    files = {
        path: (
            "from . import utils\n"
            "from .services import load as fetch\n"
            "from pkg.helpers import boot\n"
            "import os, sys\n"
            "import pkg.services, pkg.utils\n"
            "class One:\n"
            "    @staticmethod\n"
            "    async def run(self):\n"
            "        self.work()\n"
            "        fetch()\n"
            "        boot()\n"
            "        utils.work()\n"
            "        pkg.services.load()\n"
            "        unrelated.work()\n"
            "    def work(self): pass\n"
            "class Two:\n"
            "    def work(self): pass\n"
        ),
        "pkg/utils.py": "def work(): pass\n",
        "pkg/services.py": "def load(): pass\n",
        "pkg/helpers/__init__.py": "def boot(): pass\n",
    }
    graph = _graph(files)
    parser = "python_ast" if fallback else "tree_sitter"
    file_node = next(
        node for node in graph.nodes if node.node_type == GraphNodeType.FILE and node.label == path
    )
    assert file_node.metadata["parser"] == parser
    assert file_node.metadata["parse_status"] == "parsed"
    symbols = {
        node.metadata["qualified_name"]: node
        for node in graph.nodes
        if node.node_type == GraphNodeType.SYMBOL and node.metadata["path"] == path
    }
    assert symbols["One.work"].node_id != symbols["Two.work"].node_id
    assert symbols["One.run"].metadata["symbol_kind"] == "method"
    assert symbols["One.run"].metadata["async"] is True
    assert symbols["One.run"].metadata["decorators"] == ["staticmethod"]
    assert {
        (target.label, target.node_type)
        for source, target, _ in _edges(graph, GraphEdgeType.IMPORTS)
        if source.label == path
    } == {
        ("pkg/utils.py", GraphNodeType.FILE),
        ("pkg/services.py", GraphNodeType.FILE),
        ("pkg/helpers/__init__.py", GraphNodeType.FILE),
        ("os", GraphNodeType.UNKNOWN),
        ("sys", GraphNodeType.UNKNOWN),
    }
    assert _resolved_calls(graph) == {
        ("One.run", path, "One.work"),
        ("One.run", "pkg/services.py", "load"),
        ("One.run", "pkg/helpers/__init__.py", "boot"),
        ("One.run", "pkg/utils.py", "work"),
    }
    assert [
        (source.metadata["qualified_name"], target.label, edge["resolved"])
        for source, target, edge in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.UNKNOWN
    ] == [("One.run", "unrelated.work", False)]


def test_javascript_and_jsx_use_syntax_and_bindings() -> None:
    _require_grammar("javascript", "css")
    path = "ui/Main.jsx"
    graph = _graph(
        {
            path: (
                "import './styles.css';\n"
                "import Widget, { load as fetch } from './data';\n"
                "import { load } from './other';\n"
                "export { helper } from './helpers';\n"
                "const { load: fetchAgain } = require('./data');\n"
                "export function Main() {\n"
                "  const nested = () => fetch();\n"
                "  import(`./lazy`);\n"
                "  fetchAgain();\n"
                "  const literal = 'phantom()'; // ghost()\n"
                "  return <Widget data={nested()} />;\n"
                "}\n"
                "class One { run() { this.work(); } work() {} }\n"
                "class Two { work() {} }\n"
            ),
            "ui/data.js": "export function load() {}\nexport default function ActualWidget() {}\n",
            "ui/other.js": "export function load() {}\n",
            "ui/helpers.js": "export function helper() {}\n",
            "ui/lazy.js": "export function lazy() {}\n",
            "ui/styles.css": "@import './theme.css';\n",
            "ui/theme.css": ".button { color: red; }\n",
        }
    )
    assert {
        (source.label, target.label) for source, target, _ in _edges(graph, GraphEdgeType.IMPORTS)
    } == {
        (path, "ui/styles.css"),
        (path, "ui/data.js"),
        (path, "ui/other.js"),
        (path, "ui/helpers.js"),
        (path, "ui/lazy.js"),
        ("ui/styles.css", "ui/theme.css"),
    }
    assert _resolved_calls(graph) == {
        ("Main.nested", "ui/data.js", "load"),
        ("Main", "ui/data.js", "load"),
        ("Main", path, "Main.nested"),
        ("One.run", path, "One.work"),
    }
    assert not [
        target
        for _, target, _ in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.UNKNOWN
    ]
    assert [
        (source.metadata["qualified_name"], target.label)
        for source, target, _ in _edges(graph, GraphEdgeType.REFERENCES)
    ] == [("Main", "ActualWidget")]


def test_native_typescript_and_tsx_extract_typed_symbols_and_component_references() -> None:
    _require_grammar("typescript", "tsx")
    graph = _graph(
        {
            "src/Main.tsx": (
                "import Card from './Card';\n"
                "import { fetchLabel as label } from './data';\n"
                "export interface Props { title: string }\n"
                "export function Main({ title }: Props) {\n"
                "  const caption: string = label(title);\n"
                "  return <Card>{caption}</Card>;\n"
                "}\n"
            ),
            "src/data.ts": "export function fetchLabel(title: string): string { return title; }\n",
            "src/Card.tsx": "export default ({ children }: { children: string }) => <p>{children}</p>;\n",
        }
    )
    files = {
        node.label: node.metadata for node in graph.nodes if node.node_type == GraphNodeType.FILE
    }
    assert files["src/Main.tsx"]["language"] == "tsx"
    assert files["src/data.ts"]["language"] == "typescript"
    assert all(
        item["parser"] == "tree_sitter" and item["parse_error_count"] == 0
        for item in files.values()
    )
    assert any(
        node.label == "Props" and node.metadata.get("symbol_kind") == "interface"
        for node in graph.nodes
    )
    assert _resolved_calls(graph) == {("Main", "src/data.ts", "fetchLabel")}
    assert [
        (source.label, target.label, edge["resolved"])
        for source, target, edge in _edges(graph, GraphEdgeType.REFERENCES)
    ] == [("Main", "default", True)]


def test_unknown_calls_do_not_borrow_symbols_from_unrelated_imports() -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "main.js": "import { a } from './a';\nimport { b } from './b';\nexport function run() { other.load(); }",
            "a.js": "export function load() {}\nexport function a() {}",
            "b.js": "export function load() {}\nexport function b() {}",
        }
    )
    assert [
        (source.label, target.label, edge["resolved"])
        for source, target, edge in _edges(graph, GraphEdgeType.CALLS)
    ] == [("run", "other.load", False)]


def test_javascript_imports_resolve_exported_names_only() -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "main.js": (
                "import { run as load, hidden } from './esm';\n"
                "const runCommon = require('./common');\n"
                "function main() { load(); hidden(); runCommon(); }\n"
            ),
            "esm.js": "function runImpl() {}\nfunction hidden() {}\nexport { runImpl as run };\n",
            "common.js": "function runImpl() {}\nmodule.exports = runImpl;\n",
        }
    )
    assert _resolved_calls(graph) == {
        ("main", "esm.js", "runImpl"),
        ("main", "common.js", "runImpl"),
    }
    assert [
        target.label
        for _, target, _ in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.UNKNOWN
    ] == ["hidden"]


def test_distinct_paths_with_same_slug_keep_independent_file_edges() -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "src/App.js": "import './foo-bar';\nimport './foo_bar';\n",
            "src/foo-bar.js": "export function first() {}",
            "src/foo_bar.js": "export function second() {}",
        }
    )
    targets = [target for _, target, _ in _edges(graph, GraphEdgeType.IMPORTS)]
    assert {node.label for node in targets} == {"src/foo-bar.js", "src/foo_bar.js"}
    assert len({node.node_id for node in targets}) == 2


def test_anonymous_default_class_keeps_method_scope_and_import_binding() -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "src/App.jsx": "import Card from './Card';\nexport function App() { return <Card />; }",
            "src/Card.jsx": "export default class { render() { return this.label(); } label() { return 'hi'; } }",
        }
    )
    assert _resolved_calls(graph) == {("default.render", "src/Card.jsx", "default.label")}
    assert [
        (source.label, target.label, edge["resolved"])
        for source, target, edge in _edges(graph, GraphEdgeType.REFERENCES)
    ] == [("App", "default", True)]


@pytest.mark.parametrize("fallback", [False, True], ids=["tree-sitter", "python-ast"])
def test_python_local_bindings_shadow_imported_callables(
    monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    if fallback:
        from mozaiksai.core.app_context import _source_syntax

        monkeypatch.setattr(_source_syntax, "_tree_sitter_parser", lambda language: None)
    else:
        _require_grammar("python")
    graph = _graph(
        {
            "app.py": (
                "from lib import load\n"
                "def one(load):\n    return load()\n"
                "def two():\n    load = lambda: 1\n    return load()\n"
                "def three():\n    return load()\n"
            ),
            "lib.py": "def load(): pass\n",
        }
    )
    assert _resolved_calls(graph) == {("three", "lib.py", "load")}
    assert {
        (source.label, target.label)
        for source, target, _ in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.UNKNOWN
    } == {("one", "load"), ("two", "load")}


def test_javascript_local_bindings_shadow_imported_callables() -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "app.js": (
                "import { load } from './lib';\n"
                "const one = load => load();\n"
                "function two() { const { load } = dynamic; return load(); }\n"
                "function three() { return load(); }\n"
            ),
            "lib.js": "export function load() {}\n",
        }
    )
    assert _resolved_calls(graph) == {("three", "lib.js", "load")}
    assert {
        (source.label, target.label)
        for source, target, _ in _edges(graph, GraphEdgeType.CALLS)
        if target.node_type == GraphNodeType.UNKNOWN
    } == {("one", "load"), ("two", "load")}


def test_tree_sitter_reports_malformed_source_and_keeps_intact_facts() -> None:
    _require_grammar("javascript")
    result = extract_file_context(
        path="example.js", content="import './intact';\nfunction ready() {}\nconst = ;\n"
    )
    assert result.parser == "tree_sitter"
    assert result.parse_status == "partial"
    assert result.parse_error_count and result.parse_error_count > 0
    assert result.imports == ["./intact"]
    assert [item.name for item in result.symbols] == ["ready"]


def test_javascript_fallback_keeps_partial_coverage_without_fabricating_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.app_context import _source_syntax

    monkeypatch.setattr(_source_syntax, "_tree_sitter_parser", lambda language: None)
    result = extract_file_context(
        path="example.tsx",
        content="import './style.css';\nexport function ready() { return <Card />; }\n// ghost()\n",
    )
    assert result.parser == "regex"
    assert result.parse_status == "partial"
    assert result.imports == ["./style.css"]
    assert [item.name for item in result.symbols] == ["ready"]
    assert result.references == []


@pytest.mark.parametrize("fallback", [False, True], ids=["tree-sitter", "python-ast"])
def test_python_lambda_and_exception_bindings_do_not_capture_module_import(
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    if fallback:
        from mozaiksai.core.app_context import _source_syntax

        monkeypatch.setattr(_source_syntax, "_tree_sitter_parser", lambda language: None)
    else:
        _require_grammar("python")
    graph = _graph(
        {
            "app.py": (
                "from lib import load\n"
                "def run():\n"
                "    callback = lambda load: load()\n"
                "    return load()\n"
                "def recover():\n"
                "    try:\n"
                "        pass\n"
                "    except Exception as load:\n"
                "        load()\n"
            ),
            "lib.py": "def load(): pass\n",
        }
    )
    assert _resolved_calls(graph) == {("run", "lib.py", "load")}
    shadowed = [
        source
        for source, target, metadata in _edges(graph, GraphEdgeType.CALLS)
        if target.label == "load" and metadata["resolved"] is False
    ]
    assert len(shadowed) == 2
    assert all(source.node_type == GraphNodeType.SYMBOL for source in shadowed)
    assert all(source.metadata.get("qualified_name") != "run" for source in shadowed)
    assert any(source.metadata.get("qualified_name") == "recover" for source in shadowed)


@pytest.mark.parametrize("callback", ["load => load()", "function(load) { return load(); }"])
def test_anonymous_javascript_callback_owns_its_shadowed_calls(callback: str) -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "app.js": (
                "import {load} from './lib';\n"
                f"export function run(values) {{ values.forEach({callback}); return load(); }}"
            ),
            "lib.js": "export function load() {}",
        }
    )
    assert _resolved_calls(graph) == {("run", "lib.js", "load")}
    shadowed = [
        source
        for source, target, metadata in _edges(graph, GraphEdgeType.CALLS)
        if target.label == "load" and metadata["resolved"] is False
    ]
    assert len(shadowed) == 1
    assert shadowed[0].node_type == GraphNodeType.SYMBOL
    assert shadowed[0].metadata.get("qualified_name") != "run"


@pytest.mark.parametrize("fallback", [False, True], ids=["tree-sitter", "python-ast"])
def test_python_comprehension_binding_does_not_shadow_the_enclosing_function(
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    if fallback:
        from mozaiksai.core.app_context import _source_syntax

        monkeypatch.setattr(_source_syntax, "_tree_sitter_parser", lambda language: None)
    else:
        _require_grammar("python")
    graph = _graph(
        {
            "app.py": (
                "from lib import load\n"
                "def run(items):\n"
                "    results = [load() for load in items]\n"
                "    return load()\n"
            ),
            "lib.py": "def load(): pass\n",
        }
    )
    assert _resolved_calls(graph) == {("run", "lib.py", "load")}
    shadowed = [
        source
        for source, target, metadata in _edges(graph, GraphEdgeType.CALLS)
        if target.label == "load" and metadata["resolved"] is False
    ]
    assert len(shadowed) == 1
    assert shadowed[0].node_type == GraphNodeType.SYMBOL
    assert shadowed[0].metadata.get("qualified_name") != "run"


@pytest.mark.parametrize(
    "shadow",
    [
        "function run(require) { const load = require('./fake'); return load(); }",
        "function run() { const require = dynamic; const load = require('./fake'); return load(); }",
    ],
)
def test_shadowed_require_does_not_create_a_module_dependency(shadow: str) -> None:
    _require_grammar("javascript")
    graph = _graph(
        {
            "app.js": "const real = require('./real');\n" + shadow + "\nreal();",
            "real.js": "function load() {}\nmodule.exports = load;",
            "fake.js": "function load() {}\nmodule.exports = load;",
        }
    )
    assert {
        (source.label, target.label) for source, target, _ in _edges(graph, GraphEdgeType.IMPORTS)
    } == {
        ("app.js", "real.js"),
    }
    assert not any(
        target.metadata.get("path") == "fake.js"
        for _, target, _ in _edges(graph, GraphEdgeType.CALLS)
    )
    assert any(
        target.metadata.get("path") == "real.js"
        for _, target, _ in _edges(graph, GraphEdgeType.CALLS)
    )


@pytest.mark.parametrize("language,path", [("javascript", "app.js"), ("python", "app.py")])
def test_deep_member_expression_reports_partial_coverage_without_losing_intact_imports(
    language: str,
    path: str,
) -> None:
    _require_grammar(language)
    chain = "receiver" + ".member" * 1800 + ".finish()"
    source = (
        f"import {{load}} from './lib';\nexport function run() {{ load(); {chain}; }}"
        if language == "javascript"
        else f"from lib import load\ndef run():\n    load()\n    {chain}\n"
    )
    result = extract_file_context(path=path, content=source)
    assert result.parser == "tree_sitter"
    assert result.parse_status == "partial"
    assert result.parse_error_count > 0
    assert result.imports == (["./lib"] if language == "javascript" else ["lib"])
    assert any(symbol.name == "run" for symbol in result.symbols)


def test_pack_owned_tsx_file_keeps_parser_facts_and_both_dependency_kinds() -> None:
    _require_grammar("typescript", "tsx")
    path = "ui/Card.tsx"
    graph = _graph(
        {
            ".mozaiks/pack_provenance.json": json.dumps(
                {
                    "pack_id": "cards",
                    "materialized_owned_files": [{"path": path, "owner": "cards"}],
                }
            ),
            path: "import {label} from './labels';\nexport function Card() { return <h1>{label()}</h1>; }",
            "ui/labels.ts": "export function label(): string { return 'Card'; }",
        }
    )
    files = [
        node
        for node in graph.nodes
        if node.node_type == GraphNodeType.FILE and node.metadata.get("path") == path
    ]
    assert len(files) == 1
    file = files[0]
    assert file.checksum
    assert file.artifact_version_id == "av_syntax"
    assert file.metadata["language"] == "tsx"
    assert file.metadata["parser"] == "tree_sitter"
    assert file.metadata["parse_status"] == "parsed"
    assert file.metadata["ownership"] == "cards"
    assert file.metadata["authority"] == "authoritative"
    assert any(
        source.node_id == file.node_id and target.label == "Card"
        for source, target, _ in _edges(graph, GraphEdgeType.DEFINES)
    )
    assert any(
        source.node_id == file.node_id and target.label == "ui/labels.ts"
        for source, target, _ in _edges(graph, GraphEdgeType.IMPORTS)
    )
    assert any(
        source.label == "cards" and target.node_id == file.node_id
        for source, target, _ in _edges(graph, GraphEdgeType.PRODUCES)
    )
    assert any(
        source.node_id == file.node_id and target.label == "cards"
        for source, target, _ in _edges(graph, GraphEdgeType.GENERATED_FROM)
    )
    assert _resolved_calls(graph) == {("Card", "ui/labels.ts", "label")}
