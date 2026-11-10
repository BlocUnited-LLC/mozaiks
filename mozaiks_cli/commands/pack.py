"""Install, upgrade, verify and inspect capability packs in an existing app workspace.

``mozaiks pack`` is the operator-facing entry to the Community Component
lifecycle for apps that already exist. Exit codes are part of its contract:
0 means applied, unchanged or verified clean; 1 means a plan is blocked or
verification found drift, and nothing was written; 2 means the input was
invalid (a bad pack, an unknown pack, or a held lock).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_INVALID = 2


def add_parser(subparsers: Any) -> None:
    pack_parser = subparsers.add_parser(
        "pack",
        help="Install, upgrade and verify capability packs in an existing app",
        description=(
            "Install a versioned capability pack into an existing app, upgrade it while keeping the app's "
            "own files and entries, and verify that pack-owned files are unedited."
        ),
    )
    actions = pack_parser.add_subparsers(dest="pack_action", required=True)
    for name, help_text in (
        ("install", "Install a pack into the app"),
        ("upgrade", "Upgrade an installed pack to another version"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("pack_dir", nargs="?", help="Local pack directory (or use --git/--rev/--path)")
        action.add_argument("--git", dest="git_repository", help="Local git repository that holds the pack")
        action.add_argument("--rev", dest="git_revision", help="Full commit id naming the pack version")
        action.add_argument("--path", dest="git_path", help="Pack folder inside the repository")
        if name == "install":
            action.add_argument(
                "--adopt", action="store_true",
                help="Take ownership of existing app files and entries that are identical to the pack's",
            )
        action.add_argument("--dry-run", action="store_true", help="Print the plan without writing anything")
        _add_common_arguments(action)
    for name, help_text in (
        ("verify", "Check that pack-owned files and entries are unedited"),
        ("status", "List installed packs and what each owns"),
    ):
        _add_common_arguments(actions.add_parser(name, help=help_text))


def _add_common_arguments(parser: Any) -> None:
    parser.add_argument("--workspace", default=".", help="App workspace holding app/ and workflows/ (default: .)")
    parser.add_argument("--app-root", help="App bundle root (default: <workspace>/app)")
    parser.add_argument("--workflows-root", help="Workflow root (default: <workspace>/workflows)")
    parser.add_argument("--build-context-root", help="Build context root (default: <workspace>/build_context)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")


def _source(args: Any) -> Any:
    from factory_app.workflows.AppGenerator.tools.community_component_sources import (
        PackSource,
        PackSourceError,
    )

    git_fields = (args.git_repository, args.git_revision, args.git_path)
    if args.pack_dir and any(git_fields):
        raise PackSourceError("Pass a pack directory or --git/--rev/--path, not both")
    if args.pack_dir:
        return PackSource(directory=Path(args.pack_dir))
    if not all(git_fields):
        raise PackSourceError("Pass a pack directory, or all of --git, --rev and --path")
    return PackSource(
        git_repository=Path(args.git_repository), git_revision=args.git_revision, git_path=args.git_path,
    )


def _print_plan(plan: dict[str, Any]) -> None:
    target = plan["to"]
    print(f"{plan['operation']} {plan['pack_id']} {target['version']}: {plan['status']}")
    if plan.get("from"):
        print(f"  from {plan['from']['version']}")
    for label, group in (("app", plan["files"]), ("workflows", plan["workflow_files"])):
        for action, paths in group.items():
            for path in paths:
                print(f"  {action:<9} {label}/{path}")
    for row in plan["composed"]:
        print(f"  {row['action']:<9} {row['root']} {row['container']}:{row['key']}")
    for conflict in plan["conflicts"]:
        where = conflict.get("path") or conflict.get("root") or ""
        key = f" {conflict['key']}" if conflict.get("key") else ""
        print(f"  CONFLICT  {conflict['kind']} {where}{key}".rstrip())
    for warning in plan["warnings"]:
        print(f"  warning   {warning['kind']}: {warning.get('detail', '')}")


def _emit(payload: dict[str, Any], *, as_json: bool, printer: Any) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        printer(payload)


def _print_verify(report: dict[str, Any]) -> None:
    print(f"verify: {report['status']}")
    for pack in report["packs"]:
        note = "" if pack.get("verifiable", True) else " (AppGenerator provenance; not hash-verified)"
        print(f"  {pack['pack_id']} {pack.get('version')}{note}")
        for finding in pack["findings"]:
            where = finding.get("path") or " ".join(filter(None, (finding.get("root"), finding.get("key"))))
            print(f"    {finding['kind']} {where}".rstrip())


def _print_status(status: dict[str, Any]) -> None:
    if not status["packs"]:
        print("No packs installed.")
    for pack in status["packs"]:
        print(
            f"{pack['pack_id']} {pack['version']} [{pack['ownership']}] {pack['source']}: "
            f"{pack['owned_files']} files, {pack['seeded_files']} seeds, "
            f"{pack['workflow_files']} workflow files, {pack['composed_entries']} entries"
        )


def run(args: Any) -> int:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        CommunityComponentConflictError,
        CommunityComponentLifecycleError,
    )
    from factory_app.workflows.AppGenerator.tools.community_component_sources import PackSourceError
    from factory_app.workflows.AppGenerator.tools.community_component_state import (
        InstalledComponentStateError,
    )
    from factory_app.workflows.AppGenerator.tools.community_component_workspace import (
        apply_component_install,
        apply_workspace_upgrade,
        installed_component_status,
        plan_component_install,
        plan_workspace_upgrade,
        verify_installed_components,
        workspace_roots,
    )
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    try:
        roots = workspace_roots(
            args.workspace,
            app_root=args.app_root,
            workflows_root=args.workflows_root,
            build_context_root=args.build_context_root,
        )
        if args.pack_action == "verify":
            report = verify_installed_components(roots=roots)
            _emit(report, as_json=args.json, printer=_print_verify)
            return EXIT_OK if report["status"] == "clean" else EXIT_BLOCKED
        if args.pack_action == "status":
            _emit(installed_component_status(roots=roots), as_json=args.json, printer=_print_status)
            return EXIT_OK
        source = _source(args)
        try:
            if args.pack_action == "install" and args.dry_run:
                plan = plan_component_install(source=source, roots=roots, adopt=args.adopt)
            elif args.pack_action == "install":
                plan = apply_component_install(source=source, roots=roots, adopt=args.adopt)
            elif args.dry_run:
                plan = plan_workspace_upgrade(source=source, roots=roots)
            else:
                plan = apply_workspace_upgrade(source=source, roots=roots)
        except CommunityComponentConflictError as exc:
            blocked = getattr(exc, "plan", None)
            if blocked is None:
                raise
            plan = blocked
        payload = plan.to_dict()
        _emit(payload, as_json=args.json, printer=_print_plan)
        return EXIT_BLOCKED if payload["status"] == "blocked" else EXIT_OK
    except (
        CommunityComponentLifecycleError,
        ManagedCapabilityTemplateError,
        PackSourceError,
        InstalledComponentStateError,
    ) as exc:
        error = "\n".join([str(exc), *getattr(exc, "__notes__", [])])
        if getattr(args, "json", False):
            print(json.dumps({"status": "invalid", "error": error}, indent=2))
        else:
            print(f"Error: {error}")
        return EXIT_INVALID
