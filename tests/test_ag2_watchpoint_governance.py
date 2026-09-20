"""High-signal governance checks for AG2 update watchpoints."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "architecture" / "workflows" / "ag2-watchpoints.yaml"
LEDGER_PATH = ROOT / "docs" / "architecture" / "workflows" / "ag2-update-watchpoints.md"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"

FINITE_STATUSES = {
    "ACTIVE",
    "WATCH",
    "RESOLVED_UPSTREAM",
    "RESOLVED_IN_MOZAIKS",
    "DEFERRED",
    "RETIRED",
}
FINITE_RISKS = {"LOW", "MEDIUM", "HIGH"}
RETIRED_PACKAGE = "auto" "gen"
RETIRED_AGENT_SYMBOLS = {
    "AssistantAgent",
    "Conversable" "Agent",
    "GroupChat",
    "GroupChat" "Manager",
    "UserProxyAgent",
}
PRODUCTION_ROOTS = (ROOT / "mozaiksai", ROOT / "factory_app", ROOT / "scripts")


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _production_python_files() -> list[Path]:
    return [
        path
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if not ("build_context" in path.parts and "templates" in path.parts)
    ]


def _imports_ag2(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "ag2" or alias.name.startswith("ag2.") for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "ag2" or node.module.startswith("ag2."):
                return True
    return False


def test_watchpoint_manifest_has_unique_ids_finite_statuses_and_current_baseline() -> None:
    manifest = _manifest()
    baseline = str(manifest["ag2_baseline"])
    watchpoints = manifest["watchpoints"]
    ledger = LEDGER_PATH.read_text(encoding="utf-8")

    ids = [item["watchpoint_id"] for item in watchpoints]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"AG2-WP-\d{3}", watchpoint_id) for watchpoint_id in ids)

    required_fields = {
        "ag2_surface",
        "mozaiks_surface",
        "status",
        "last_verified_version",
        "trigger",
        "replacement_condition",
        "verification_tests",
    }
    for item in watchpoints:
        assert required_fields <= item.keys(), item["watchpoint_id"]
        assert item["status"] in FINITE_STATUSES, item["watchpoint_id"]
        assert str(item["last_verified_version"]) == baseline, item["watchpoint_id"]
        assert (ROOT / item["mozaiks_surface"]).exists(), item["watchpoint_id"]
        assert item["watchpoint_id"] in ledger
        for test_path in item["verification_tests"]:
            assert (ROOT / test_path).is_file(), (item["watchpoint_id"], test_path)


def test_private_api_register_is_complete_and_protected() -> None:
    manifest = _manifest()
    baseline = str(manifest["ag2_baseline"])
    entries = manifest["private_api_register"]
    ledger = LEDGER_PATH.read_text(encoding="utf-8")

    ids = [item["private_api_id"] for item in entries]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"AG2-PRI-\d{3}", private_id) for private_id in ids)

    registered_symbols: set[str] = set()
    for item in entries:
        assert item["risk"] in FINITE_RISKS, item["private_api_id"]
        assert str(item["last_verified_version"]) == baseline, item["private_api_id"]
        assert item["private_api_id"] in ledger
        caller = ROOT / item["mozaiks_caller"]
        assert caller.is_file(), item["private_api_id"]
        caller_text = caller.read_text(encoding="utf-8")
        for symbol in item["symbols"]:
            registered_symbols.add(symbol)
            assert symbol.rsplit(".", 1)[-1] in caller_text, symbol
            assert symbol in ledger
        for test_path in item["verification_tests"]:
            assert (ROOT / test_path).is_file(), (item["private_api_id"], test_path)

    private_member_uses: set[str] = set()
    internal_imports: set[str] = set()
    for path in _production_python_files():
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        if not _imports_ag2(tree):
            continue
        for line in source.splitlines():
            if "SLF001" in line:
                private_member_uses.update(re.findall(r"\.(_[A-Za-z][A-Za-z0-9_]*)", line))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module in {"ag2.network.client.handlers", "ag2.network.policies"}:
                internal_imports.update(f"{node.module}.{alias.name}" for alias in node.names)

    registered_private_names = {
        symbol.rsplit(".", 1)[-1] for symbol in registered_symbols if symbol.rsplit(".", 1)[-1].startswith("_")
    }
    assert private_member_uses == registered_private_names
    assert internal_imports <= registered_symbols


def test_retired_classic_ag2_agent_apis_are_not_imported_by_production() -> None:
    violations: list[str] = []
    for path in _production_python_files():
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == RETIRED_PACKAGE or alias.name.startswith(f"{RETIRED_PACKAGE}."):
                        violations.append(f"{path.relative_to(ROOT)} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == RETIRED_PACKAGE or node.module.startswith(f"{RETIRED_PACKAGE}."):
                    violations.append(f"{path.relative_to(ROOT)} imports from {node.module}")
                if node.module == "ag2" or node.module.startswith("ag2."):
                    retired = RETIRED_AGENT_SYMBOLS.intersection(alias.name for alias in node.names)
                    if retired:
                        violations.append(f"{path.relative_to(ROOT)} imports {sorted(retired)}")

    assert violations == []


def test_dependabot_isolates_ag2_runtime_updates_from_general_python_group() -> None:
    config = yaml.safe_load(DEPENDABOT_PATH.read_text(encoding="utf-8"))
    pip_root = next(
        item
        for item in config["updates"]
        if item["package-ecosystem"] == "pip" and item["directory"] == "/"
    )
    groups = pip_root["groups"]
    assert list(groups).index("ag2-runtime") < list(groups).index("python-minor-patch")
    assert set(groups["ag2-runtime"]["patterns"]) == {"ag2", "agent-client-protocol"}
    assert {"ag2", "agent-client-protocol"} <= set(
        groups["python-minor-patch"]["exclude-patterns"]
    )
