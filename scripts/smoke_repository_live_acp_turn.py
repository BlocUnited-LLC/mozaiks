"""Operator-attended transport smoke: one live repository ACP turn.

This runs ONE real Codex or Claude Code coding turn through the opt-in live
repository Docker transport against a local, throwaway snapshot directory. It
is the "pre-gate-4 transport smoke" an operator runs by hand under ADR 0015:
no repository, pull request, database, or publication is involved. It is never
run in CI and never by pytest.

Usage (see infra/docker/acp_live/README.md for the full recipe):

    MOZAIKS_RUN_REPOSITORY_LIVE_ACP=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=<this checkout> \\
    python scripts/smoke_repository_live_acp_turn.py \\
        --snapshot-dir <throwaway copy> --baseline-sha <40-hex commit> \\
        --edit README.md --read-only docs/one.md \\
        --instruction "Add one sentence to the README introduction." \\
        --model <exact model id> \\
        --worker-image-id sha256:<id> --gateway-image-id sha256:<id> \\
        --output-dir <new empty directory>

The provider key is read once from a hidden prompt (``getpass``). It is never
read from the environment, the command line, or a file, and it reaches only
the model gateway container over stdin. The host stages and finalizes the
returned archive against the snapshot evidence built here, writes the
host-verified diff for a human to read, and writes a content-free JSON
receipt. Nothing is written to the snapshot directory.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import SecretStr

import mozaiksai
from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    CodingWorkerRequest,
    RepositoryDockerExecutionError,
    RepositoryLiveACPProfile,
    RepositorySnapshotEvidence,
    execute_repository_docker_turn,
    repository_live_docker,
)
from mozaiksai.control_plane.repository_acp_usage import record_isolated_acp_usage
from mozaiksai.control_plane.repository_patch import (
    finalize_repository_patch,
    repository_patch_digest,
    stage_repository_workspace_archive,
)
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.usage.ledger import RuntimeUsageLedger

ENABLE_ENV = "MOZAIKS_RUN_REPOSITORY_LIVE_ACP"
RECEIPT_SCHEMA = "mozaiks.refinement.repository_live_acp.transport_smoke_receipt.v1"
MIN_WALL_SECONDS = 90
MAX_WALL_SECONDS = 600
MAX_READ_ONLY_PATHS = 2
MAX_FILE_BYTES = 1_048_576
_REPO_ROOT = Path(__file__).resolve().parents[1]
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_LABEL = re.compile(r"throwaway/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_ERROR_CODE = re.compile(r"REPOSITORY_[A-Z0-9_]{2,80}(?=:|\Z)")
_GATES_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_GATES_RECORD_BYTES = 8_388_608
_IMPORT_ROOTS = frozenset({"mozaiksai", "scripts"})
_IMPORTABLE_SUFFIXES = (".py", ".pyc", ".pyd", ".so", ".pth")
# The executor's cleanup path raises one of these when it could not confirm a
# removal; any other outcome means its finally block removed everything it
# created. Values: (containers_removed, networks_removed), None = unconfirmed.
_CLEANUP_FAILURES: dict[str, tuple[bool | None, bool | None]] = {
    "REPOSITORY_DOCKER_CLEANUP_FAILED": (False, None),
    "REPOSITORY_LIVE_GATEWAY_CLI_CLEANUP_FAILED": (True, None),
    "REPOSITORY_LIVE_NETWORK_CLEANUP_FAILED": (True, False),
}
_CI_ENV_NAMES = ("CI", "GITHUB_ACTIONS", "TF_BUILD", "BUILDKITE", "GITLAB_CI", "JENKINS_URL")
_APP_ID = "transport-smoke"
_OPERATOR = "operator"


class SmokeRefused(Exception):
    """The smoke did not start; the message is safe to print."""


class _ReceiptLedger(RuntimeUsageLedger):
    """Keeps the advisory usage delta in memory; never opens a database."""

    def __init__(self) -> None:
        super().__init__()
        self.deltas: list[dict[str, Any]] = []

    async def record_usage_delta(self, payload: dict[str, Any], *, source: str = "usage_event") -> None:
        self.deltas.append({**payload, "source": source})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operator-attended live repository ACP transport smoke (never CI).",
    )
    parser.add_argument("--snapshot-dir", required=True, type=Path,
                        help="local throwaway copy of the target at --baseline-sha")
    parser.add_argument("--baseline-sha", required=True, help="40-hex commit the snapshot was taken from")
    parser.add_argument("--repository-label", default="throwaway/transport-smoke",
                        help="label recorded in approval and receipt; must start with throwaway/")
    parser.add_argument("--edit", required=True, help="the one relative path the agent may edit")
    parser.add_argument("--read-only", action="append", default=[],
                        help=f"relative read-only path (repeat up to {MAX_READ_ONLY_PATHS} times)")
    parser.add_argument("--instruction", required=True, help="the approved edit request")
    parser.add_argument("--adapter", choices=("claude_code", "codex"), default="claude_code")
    parser.add_argument("--model", required=True, help="exact provider model id")
    parser.add_argument("--worker-image-id", required=True, help="sha256 ID of the live worker image")
    parser.add_argument("--gateway-image-id", required=True, help="sha256 ID of the model gateway image")
    parser.add_argument("--max-wall-seconds", type=int, default=300,
                        help=f"turn budget, {MIN_WALL_SECONDS}-{MAX_WALL_SECONDS} seconds")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="new or empty directory for receipt.json and host-verified.diff")
    parser.add_argument("--gates-receipt", default=None,
                        help="saved record of the gates 1-3 run on these image IDs (a file path), "
                             "or that record's sha256:<64 hex> digest")
    return parser


def _attended() -> bool:
    return sys.stdin.isatty()


def _require_operator_context(environ: dict[str, str]) -> None:
    if environ.get(ENABLE_ENV) != "1":
        raise SmokeRefused(f"set {ENABLE_ENV}=1 to run the operator-only live transport smoke")
    if any(environ.get(name) for name in _CI_ENV_NAMES):
        raise SmokeRefused("refusing to run in CI")
    if environ.get("PYTEST_CURRENT_TEST"):
        raise SmokeRefused("refusing to run under pytest")
    if not _attended():
        raise SmokeRefused("an attended terminal is required for the hidden key prompt")


def _checkout_identity(root: Path = _REPO_ROOT) -> tuple[str, bool, tuple[str, ...]]:
    """Return HEAD, whether tracked files changed, and untracked non-ignored paths."""
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            encoding="utf-8", timeout=60, check=True,
        ).stdout

    fields = iter(git("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"))
    tracked_changes = False
    untracked: list[str] = []
    for entry in fields:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if status == "??":
            untracked.append(path)
            continue
        tracked_changes = True
        if "R" in status or "C" in status:
            next(fields, None)  # the rename/copy source path
    return git("rev-parse", "HEAD").strip(), tracked_changes, tuple(untracked)


def _imported_roots() -> set[str]:
    """Top-level checkout entries that hold code this process has imported."""
    roots = set(_IMPORT_ROOTS)
    for module in list(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            relative = Path(module_file).resolve().relative_to(_REPO_ROOT)
        except (OSError, ValueError):
            continue
        if len(relative.parts) > 1:
            roots.add(relative.parts[0])
    return roots


def _shadows_imports(path: str, roots: set[str]) -> bool:
    """Whether an untracked path could be imported in place of tracked code."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0].casefold() in {root.casefold() for root in roots}:
        return True
    if len(parts) == 1:
        return path.casefold().endswith(_IMPORTABLE_SUFFIXES)
    return len(parts) == 2 and parts[1].casefold().startswith("__init__.")


def _verify_checkout() -> str:
    """Return the OSS commit, after proving the imported transport is this clean checkout."""
    for module_file in (mozaiksai.__file__, repository_live_docker.__file__):
        if module_file is None or not Path(module_file).resolve().is_relative_to(_REPO_ROOT):
            raise SmokeRefused(
                "mozaiksai resolves outside this checkout; set PYTHONPATH to the checkout root",
            )
    sha, dirty, untracked = _checkout_identity()
    if not _COMMIT_SHA.fullmatch(sha):
        raise SmokeRefused("could not resolve this checkout's commit")
    if dirty:
        raise SmokeRefused("this checkout has uncommitted tracked changes; the receipt must name the code that ran")
    roots = _imported_roots()
    shadowing = sorted(path for path in untracked if _shadows_imports(path, roots))
    if shadowing:
        shown = ", ".join(shadowing[:3]) + (" ..." if len(shadowing) > 3 else "")
        raise SmokeRefused(
            f"this checkout has untracked files where imported code lives ({shown}); "
            "remove them so the receipt names the code that ran",
        )
    return sha


def _enclosing_work_tree(path: Path, *, ceiling: Path | None = None) -> Path | None:
    """Return the nearest directory at or above ``path`` holding a ``.git`` entry.

    ``ceiling`` stops the walk at that directory (it is still checked).
    """
    for candidate in (path, *path.parents):
        if ceiling is not None and not candidate.is_relative_to(ceiling):
            break
        if (candidate / ".git").exists():
            return candidate
    return None


def _relative_path(value: str) -> str:
    posix = PurePosixPath(value)
    if (
        not value or "\\" in value or value != str(posix) or posix.is_absolute()
        or any(part in ("", ".", "..") for part in posix.parts)
        or unicodedata.normalize("NFC", value) != value
        or re.match(r"[A-Za-z]:", value)
    ):
        raise SmokeRefused(f"not a canonical relative path: {value!r}")
    return value


def _read_snapshot_file(root: Path, relative: str) -> str:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SmokeRefused(f"snapshot path crosses a link: {relative!r}")
    if not current.is_file() or not current.resolve().is_relative_to(root):
        raise SmokeRefused(f"snapshot file is missing or outside the snapshot: {relative!r}")
    raw = current.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise SmokeRefused(f"snapshot file exceeds {MAX_FILE_BYTES} bytes: {relative!r}")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SmokeRefused(f"snapshot file is not UTF-8 text: {relative!r}") from None


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
    ).hexdigest()


class SmokePlan:
    """Validated operator inputs plus the approval, snapshot, and request built from them."""

    def __init__(self, args: argparse.Namespace) -> None:
        if not MIN_WALL_SECONDS <= args.max_wall_seconds <= MAX_WALL_SECONDS:
            raise SmokeRefused(f"--max-wall-seconds must be between {MIN_WALL_SECONDS} and {MAX_WALL_SECONDS}")
        for name in ("worker_image_id", "gateway_image_id"):
            if not _IMAGE_ID.fullmatch(getattr(args, name)):
                raise SmokeRefused(f"--{name.replace('_', '-')} must be a local image ID (sha256:<64 hex>)")
        if not _COMMIT_SHA.fullmatch(args.baseline_sha):
            raise SmokeRefused("--baseline-sha must be a 40-character lowercase hex commit")
        if not _LABEL.fullmatch(args.repository_label):
            raise SmokeRefused("--repository-label must start with throwaway/")
        if not _MODEL.fullmatch(args.model):
            raise SmokeRefused("--model is not a valid model identifier")
        if not args.instruction.strip():
            raise SmokeRefused("--instruction must not be empty")
        edit = _relative_path(args.edit)
        read_only = [_relative_path(path) for path in args.read_only]
        if len(read_only) > MAX_READ_ONLY_PATHS or len(set(read_only)) != len(read_only):
            raise SmokeRefused(f"use at most {MAX_READ_ONLY_PATHS} distinct --read-only paths")
        for path in read_only:
            if path == edit or path.startswith(f"{edit}/") or edit.startswith(f"{path}/"):
                raise SmokeRefused("--read-only paths must not overlap --edit")
        snapshot_dir: Path = args.snapshot_dir.resolve()
        if not snapshot_dir.is_dir():
            raise SmokeRefused("--snapshot-dir must be an existing directory")
        if snapshot_dir.is_relative_to(_REPO_ROOT) or _REPO_ROOT.is_relative_to(snapshot_dir):
            raise SmokeRefused("--snapshot-dir must be outside this checkout")
        work_tree = _enclosing_work_tree(snapshot_dir)
        if work_tree is not None:
            raise SmokeRefused(
                f"--snapshot-dir must be outside any git working checkout (found .git at {work_tree})",
            )
        output_dir: Path = args.output_dir.resolve()
        if output_dir.is_relative_to(snapshot_dir) or output_dir.is_relative_to(_REPO_ROOT):
            raise SmokeRefused("--output-dir must be outside the snapshot and this checkout")
        if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
            raise SmokeRefused("--output-dir must be a new or empty directory")

        self.gates_receipt = _gates_receipt(args)
        self.args = args
        self.edit = edit
        self.read_only = read_only
        self.output_dir = output_dir
        self.run_id = uuid.uuid4().hex[:16]
        self.files = {edit: _read_snapshot_file(snapshot_dir, edit)}
        self.read_only_files = {path: _read_snapshot_file(snapshot_dir, path) for path in read_only}
        manifest = {
            path: f"sha256:{_sha256_text(content)}"
            for path, content in {**self.files, **self.read_only_files}.items()
        }
        snapshot_digest = "sha256:" + _canonical_digest({
            "repository": args.repository_label, "baseline_commit_sha": args.baseline_sha, "manifest": manifest,
        })
        ids = {name: f"transport-smoke-{name}-{self.run_id}" for name in ("handoff", "request", "plan")}
        self.context = ApprovedExecutionContext(
            handoff_id=ids["handoff"], request_id=ids["request"], plan_id=ids["plan"],
            app_id=_APP_ID, build_registry_id=_APP_ID,
            repository_full_name=args.repository_label, baseline_commit_sha=args.baseline_sha,
            raw_request=args.instruction, request_type="patch",
            approved_plan_digest=_canonical_digest({
                "label": "transport smoke", "repository": args.repository_label,
                "baseline_commit_sha": args.baseline_sha, "edit": edit, "read_only": read_only,
                "instruction_sha256": _sha256_text(args.instruction), "adapter": args.adapter,
                "model": args.model, "worker_image_id": args.worker_image_id,
                "gateway_image_id": args.gateway_image_id, "snapshot_digest": snapshot_digest,
            }),
            snapshot_digest=snapshot_digest, graph_identity_digest="transport-smoke",
            impact_report_digest="transport-smoke", execution_strategy_id="transport_smoke",
            rationale="Operator-attended transport smoke under ADR 0015; no repository or PR.",
            allowed_paths=[edit, *read_only], read_only_paths=read_only,
            create_paths=[], delete_paths=[],
        )
        self.snapshot = RepositorySnapshotEvidence(
            plan_id=ids["plan"], request_id=ids["request"], app_id=_APP_ID,
            repository_full_name=args.repository_label, baseline_commit_sha=args.baseline_sha,
            snapshot_digest=snapshot_digest, file_manifest=manifest,
        )
        self.request = CodingWorkerRequest(
            app_id=_APP_ID, target_app_id=_APP_ID, user_id=_OPERATOR,
            usage_context=AuxiliaryUsageContext(app_id=_APP_ID, user_id=_OPERATOR),
            build_family="app_bundle", build_key=_APP_ID, build_record_id=f"transport-smoke-{self.run_id}",
            change_class="patch", raw_user_request=args.instruction,
            files=self.files, read_only_files=self.read_only_files,
        )

    def validate_path(self, path: str) -> None:
        if path != self.edit and path not in self.read_only:
            raise ValueError("REPOSITORY_SMOKE_PATH_NOT_APPROVED")


def _gates_receipt(args: argparse.Namespace) -> dict[str, str] | None:
    """Digest the gates 1-3 run record so the smoke receipt can link to it."""
    value = args.gates_receipt
    if value is None:
        return None
    if _GATES_DIGEST.fullmatch(value):
        return {"source": "digest", "sha256": value}
    path = Path(value)
    if path.is_symlink() or not path.is_file():
        raise SmokeRefused("--gates-receipt must be a regular file or a sha256:<64 hex> digest")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_GATES_RECORD_BYTES:
        raise SmokeRefused(f"--gates-receipt must be a non-empty file of at most {MAX_GATES_RECORD_BYTES} bytes")
    for image_id in (args.worker_image_id, args.gateway_image_id):
        if image_id.encode("ascii") not in raw:
            raise SmokeRefused("--gates-receipt does not name both image IDs; run the gates on these images")
    return {"source": "file", "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}"}


def _error_code(exc: BaseException) -> str:
    """Keep only a repository error code; never free text from an exception."""
    if isinstance(exc, (RepositoryDockerExecutionError, ValueError)):
        match = _ERROR_CODE.match(str(exc))
        if match:
            return match.group(0)
    return type(exc).__name__


def _cleanup(turn_error: BaseException | None) -> dict[str, Any]:
    """What the executor's cleanup path confirmed about this turn's containers and networks."""
    containers: bool | None
    networks: bool | None
    if turn_error is None:
        containers = networks = True
    else:
        code = _error_code(turn_error)
        if code in _CLEANUP_FAILURES:
            containers, networks = _CLEANUP_FAILURES[code]
        elif isinstance(turn_error, (RepositoryDockerExecutionError, ValueError)):
            containers = networks = True
        else:
            containers = networks = None
    return {
        "verified_by": "executor_cleanup_path",
        "containers_removed": containers,
        "networks_removed": networks,
    }


async def _execute(plan: SmokePlan, key: SecretStr, ledger: RuntimeUsageLedger) -> dict[str, Any]:
    args = plan.args
    result: dict[str, Any] = {
        "proposal_status": None, "repository_patch_digest": None, "usage": None,
        "usage_recorded": False, "error_code": None, "diff": None,
        "cleanup": _cleanup(None),
    }
    try:
        turn = await execute_repository_docker_turn(
            plan.request, image=args.worker_image_id, expected_image_id=args.worker_image_id,
            approved_context=plan.context, snapshot=plan.snapshot, validate_path=plan.validate_path,
            live_profile=RepositoryLiveACPProfile(
                adapter=args.adapter, model=args.model, gateway_image=args.gateway_image_id,
                gateway_expected_image_id=args.gateway_image_id, upstream_api_key=key,
            ),
            max_wall_seconds=args.max_wall_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - the receipt carries only a code
        result["error_code"] = _error_code(exc)
        result["cleanup"] = _cleanup(exc)
        return result
    result["proposal_status"] = turn.proposal.status
    result["usage"] = turn.usage.model_dump(mode="json") if turn.usage is not None else None
    result["usage_recorded"] = await record_isolated_acp_usage(
        turn.usage, request=plan.request, attempt_id=f"transport-smoke-{plan.run_id}",
        model_name=args.model, ledger=ledger,
    )
    if turn.proposal.status != "completed" or turn.workspace_archive is None:
        return result
    try:
        with tempfile.TemporaryDirectory(prefix="mozaiks-transport-smoke-") as staging:
            workspace = stage_repository_workspace_archive(
                plan.context, snapshot=plan.snapshot, selected_paths=[plan.edit],
                baseline_files=plan.files, archive_bytes=turn.workspace_archive,
                workspace_root=Path(staging) / "staged", validate_path=plan.validate_path,
                max_files=1, max_archive_bytes=16_777_216,
            )
            try:
                candidate = finalize_repository_patch(
                    plan.context, snapshot=plan.snapshot, selected_paths=[plan.edit],
                    baseline_files=plan.files, workspace=workspace, proposal=turn.proposal,
                    validate_path=plan.validate_path,
                )
            finally:
                workspace.cleanup()
    except Exception as exc:  # noqa: BLE001 - the receipt carries only a code
        result["error_code"] = _error_code(exc)
        return result
    result["repository_patch_digest"] = repository_patch_digest(candidate)
    result["diff"] = "".join(change.diff for change in candidate.changed_files)
    return result


def _write_outputs(plan: SmokePlan, oss_sha: str, started: str, result: dict[str, Any]) -> Path:
    args = plan.args
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    diff_name = None
    if result["diff"] is not None:
        diff_name = "host-verified.diff"
        (plan.output_dir / diff_name).write_text(result["diff"], encoding="utf-8", newline="\n")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "label": "transport smoke",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "oss_sha": oss_sha,
        "repository_label": args.repository_label,
        "baseline_commit_sha": args.baseline_sha,
        "worker_image_id": args.worker_image_id,
        "gateway_image_id": args.gateway_image_id,
        "adapter": args.adapter,
        "model": args.model,
        "approved_path": plan.edit,
        "read_only_paths": plan.read_only,
        "instruction_sha256": _sha256_text(args.instruction),
        "max_wall_seconds": args.max_wall_seconds,
        "proposal_status": result["proposal_status"],
        "repository_patch_digest": result["repository_patch_digest"],
        "usage": result["usage"],
        "usage_recorded": result["usage_recorded"],
        "error_code": result["error_code"],
        "cleanup": result["cleanup"],
        "gates_receipt": plan.gates_receipt,
        "host_verified_diff": diff_name,
    }
    path = plan.output_dir / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def run(
    argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None,
    read_key: Callable[[str], str] = getpass.getpass,
) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_operator_context(dict(os.environ) if environ is None else environ)
        oss_sha = _verify_checkout()
        plan = SmokePlan(args)
    except SmokeRefused as exc:
        print(f"transport smoke refused: {exc}", file=sys.stderr)
        return 2
    print("Transport smoke (ADR 0015 pre-gate-4): one live turn, no repository or PR.")
    print(f"  OSS commit      {oss_sha}")
    print(f"  baseline        {args.repository_label} @ {args.baseline_sha}")
    print(f"  editable path   {plan.edit}")
    print(f"  read-only paths {', '.join(plan.read_only) or '(none)'}")
    print(f"  adapter/model   {args.adapter} / {args.model}")
    print(f"  worker image    {args.worker_image_id}")
    print(f"  gateway image   {args.gateway_image_id}")
    print(f"  wall budget     {args.max_wall_seconds} s")
    gates = plan.gates_receipt["sha256"] if plan.gates_receipt else "(none; the receipt will not link a gate run)"
    print(f"  gates record    {gates}")
    print("Use a dedicated key with a verified hard spend limit, and revoke it afterwards.")
    entered = read_key("Paste the provider API key (input hidden), or press Enter to cancel: ").strip()
    if not entered:
        print("transport smoke cancelled: no key entered", file=sys.stderr)
        return 2
    key = SecretStr(entered)
    del entered
    started = datetime.now(UTC).isoformat()
    ledger = _ReceiptLedger()
    result = asyncio.run(_execute(plan, key, ledger))
    receipt = _write_outputs(plan, oss_sha, started, result)
    print(f"receipt: {receipt}")
    cleanup = result["cleanup"]
    if not (cleanup["containers_removed"] and cleanup["networks_removed"]):
        print("cleanup NOT confirmed: inspect and remove mozaiks-acp-* containers and networks", file=sys.stderr)
    if result["diff"] is not None:
        print(f"host-verified diff for review: {plan.output_dir / 'host-verified.diff'}")
    if result["error_code"] is not None:
        print(f"turn failed closed: {result['error_code']}", file=sys.stderr)
    print("Now revoke the key and compare the receipt usage with the provider console.")
    return 0 if result["repository_patch_digest"] is not None else 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
