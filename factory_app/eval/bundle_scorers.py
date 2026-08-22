"""Deterministic scorers over a generated app bundle.

Generation-time evaluation: grade what the Factory produced, before anything is
hosted. This needs no customers, no traffic and no LLM — only bundles — which is
why it is buildable ahead of launch while runtime outcome collection is not.

Scorers return `ag2.eval.Feedback` so they migrate cleanly to `@scorer` if and
when the Factory itself is evaluated through `run_agent`. The runner here is
bespoke because a bundle is a directory of artifacts, not an agent trace.

Return-type convention follows AG2: a `score` of 1.0/0.0 aggregates as a pass
rate, a `value` aggregates as a distribution. One question per scorer.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

try:  # ag2 is the source of truth for the feedback shape.
    from ag2.eval import Feedback
except Exception:  # pragma: no cover - exercised only without ag2 installed
    from dataclasses import dataclass, field

    @dataclass
    class Feedback:  # type: ignore[no-redef]
        key: str
        score: float | None = None
        value: Any = None
        comment: str | None = None
        detail: dict[str, Any] = field(default_factory=dict)


PASS = 1.0
FAIL = 0.0


# ── bundle reading ────────────────────────────────────────────────────────


class Bundle:
    """A generated app bundle on disk, parsed once and shared across scorers."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.errors: list[str] = []
        self.manifest = self._json("app.json")
        self.subscriptions = self._yaml("config/subscriptions.yaml")
        self.modules = self._load_modules()
        self.ui_pages = self._load_ui_pages()

    @property
    def name(self) -> str:
        # Generated app manifests use appName/appId; hosted-intake bundles
        # historically used name. Accept both dialects.
        return str(
            self.manifest.get("name")
            or self.manifest.get("appName")
            or self.root.name
        )

    def _read(self, rel: str) -> str | None:
        path = self.root / rel
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            self.errors.append(f"{rel}: {exc}")
            return None

    def _json(self, rel: str) -> dict[str, Any]:
        raw = self._read(rel)
        if raw is None:
            return {}
        try:
            return json.loads(raw) or {}
        except json.JSONDecodeError as exc:
            self.errors.append(f"{rel}: {exc}")
            return {}

    def _yaml(self, rel: str) -> dict[str, Any]:
        raw = self._read(rel)
        if raw is None:
            return {}
        try:
            return yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            self.errors.append(f"{rel}: {exc}")
            return {}

    def _load_modules(self) -> dict[str, dict[str, Any]]:
        modules: dict[str, dict[str, Any]] = {}
        modules_dir = self.root / "modules"
        if not modules_dir.is_dir():
            return modules
        for child in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
            contract = self._yaml(f"modules/{child.name}/module.yaml")
            modules[child.name] = contract
        return modules

    def _load_ui_pages(self) -> dict[str, dict[str, Any]]:
        pages: dict[str, dict[str, Any]] = {}
        pages_dir = self.root / "ui" / "pages"
        if not pages_dir.is_dir():
            return pages
        for path in sorted(pages_dir.glob("*.yaml")):
            pages[path.stem] = self._yaml(f"ui/pages/{path.name}")
        return pages

    # ── derived views ─────────────────────────────────────────────────

    def actions(self) -> list[tuple[str, dict[str, Any]]]:
        """(module_id, action) for every declared action in the bundle."""
        out: list[tuple[str, dict[str, Any]]] = []
        for module_id, contract in self.modules.items():
            for action in contract.get("actions") or []:
                if isinstance(action, dict):
                    out.append((module_id, action))
        return out

    def gated_capabilities(self) -> set[str]:
        return {
            str(a["entitlement_gate"]).strip()
            for _, a in self.actions()
            if str(a.get("entitlement_gate") or "").strip()
        }

    def plan_capabilities(self) -> set[str]:
        caps: set[str] = set()
        for plan in self.subscriptions.get("plans") or []:
            if isinstance(plan, dict):
                for cap in plan.get("capabilities") or []:
                    if str(cap).strip():
                        caps.add(str(cap).strip())
        return caps

    def ui_endpoints(self) -> list[tuple[str, str]]:
        """(page, api_endpoint) for every section that declares one."""
        found: list[tuple[str, str]] = []
        for page, doc in self.ui_pages.items():
            for section in (doc or {}).get("sections") or []:
                if not isinstance(section, dict):
                    continue
                endpoint = ((section.get("config") or {}) or {}).get("api_endpoint")
                if endpoint:
                    found.append((page, str(endpoint)))
        return found


# ── scorers ───────────────────────────────────────────────────────────────

Scorer = Callable[[Bundle], Feedback]
_REGISTRY: list[Scorer] = []


def scorer(fn: Scorer) -> Scorer:
    _REGISTRY.append(fn)
    return fn


def all_scorers() -> list[Scorer]:
    return list(_REGISTRY)


@scorer
def bundle_parses(bundle: Bundle) -> Feedback:
    """Every artifact in the bundle is readable and well-formed."""
    ok = not bundle.errors
    return Feedback(
        key="bundle_parses",
        score=PASS if ok else FAIL,
        comment=None if ok else "; ".join(bundle.errors[:5]),
    )


@scorer
def has_app_manifest(bundle: Bundle) -> Feedback:
    named = bundle.manifest.get("name") or bundle.manifest.get("appName")
    return Feedback(
        key="has_app_manifest",
        score=PASS if named else FAIL,
    )


@scorer
def has_subscription_catalog(bundle: Bundle) -> Feedback:
    """A monetised app needs a plan catalog for entitlement to resolve against."""
    return Feedback(
        key="has_subscription_catalog",
        score=PASS if bundle.subscriptions.get("plans") else FAIL,
    )


@scorer
def every_module_has_contract(bundle: Bundle) -> Feedback:
    missing = [m for m, c in bundle.modules.items() if not c.get("module")]
    return Feedback(
        key="every_module_has_contract",
        score=FAIL if missing else PASS,
        comment=f"missing module.yaml: {', '.join(missing)}" if missing else None,
    )


@scorer
def module_count(bundle: Bundle) -> Feedback:
    return Feedback(key="module_count", value=float(len(bundle.modules)))


@scorer
def action_count(bundle: Bundle) -> Feedback:
    return Feedback(key="action_count", value=float(len(bundle.actions())))


@scorer
def gated_capabilities_declared(bundle: Bundle) -> Feedback:
    """Every entitlement_gate resolves to a capability some plan grants.

    OSS states this as an invariant: capability_ids used in entitlement_gate
    MUST appear in subscriptions.yaml under at least one plan. A gate naming a
    capability no plan grants locks the action for everyone.

    This is the direction the framework actually asserts, which is why it is
    pass/fail while the inverse is only reported as a count.
    """
    orphaned = sorted(bundle.gated_capabilities() - bundle.plan_capabilities())
    return Feedback(
        key="gated_capabilities_declared",
        score=FAIL if orphaned else PASS,
        comment=f"gated but not in any plan: {', '.join(orphaned)}" if orphaned else None,
        detail={"orphaned": orphaned},
    )


@scorer
def ungated_plan_capabilities(bundle: Bundle) -> Feedback:
    """How many plan capabilities no action gates on. Informational, not pass/fail.

    A capability sold on a plan that nothing gates *may* mean the tier enforces
    nothing — but it is not a defect on its own, and OSS states no such
    invariant. The capability may be enforced outside module dispatch, or
    reserved for a module not yet generated.

    It is also easy to read a false positive here: `permissions[]` is
    auth-level access control and lives in a different namespace from
    `entitlement_gate`, so a permission id appearing in a plan's capability
    list is a mistake in the *catalog*, not evidence of a missing gate. OSS is
    explicit that the two must not be confused.

    Reported as a count so the trend across builds is visible without asserting
    a rule the framework does not make.
    """
    ungated = sorted(bundle.plan_capabilities() - bundle.gated_capabilities())
    return Feedback(
        key="ungated_plan_capabilities",
        value=float(len(ungated)),
        comment=f"not gated by any action: {', '.join(ungated)}" if ungated else None,
        detail={"ungated": ungated},
    )


@scorer
def action_gate_coverage(bundle: Bundle) -> Feedback:
    """Share of actions carrying an entitlement gate.

    Reported as a distribution rather than pass/fail: not every action should be
    gated, and admin_internal actions must not be. The useful signal is the
    trend across builds, not a threshold.
    """
    actions = bundle.actions()
    if not actions:
        return Feedback(key="action_gate_coverage", value=None, comment="no actions")
    gated = sum(1 for _, a in actions if str(a.get("entitlement_gate") or "").strip())
    return Feedback(key="action_gate_coverage", value=round(gated / len(actions), 3))


@scorer
def ui_pages_present(bundle: Bundle) -> Feedback:
    return Feedback(
        key="ui_pages_present",
        score=PASS if bundle.ui_pages else FAIL,
    )


@scorer
def ui_endpoints_resolve(bundle: Bundle) -> Feedback:
    """Every UI api_endpoint maps to an action the bundle actually declares.

    Catches UI wired to a nonexistent handler — a dead button that no
    generation-time syntax check would notice and no infrastructure metric
    would ever surface.
    """
    declared = {f"{m}/{a.get('id')}" for m, a in bundle.actions() if a.get("id")}
    broken: list[str] = []
    for page, endpoint in bundle.ui_endpoints():
        parts = [p for p in str(endpoint).split("/") if p]
        # /api/modules/<module_id>/<action_id>
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "modules":
            if f"{parts[2]}/{parts[3]}" not in declared:
                broken.append(f"{page}:{endpoint}")
        else:
            broken.append(f"{page}:{endpoint} (unrecognised shape)")
    return Feedback(
        key="ui_endpoints_resolve",
        score=FAIL if broken else PASS,
        comment=f"unresolved: {', '.join(broken[:5])}" if broken else None,
        detail={"broken": broken},
    )


@scorer
def has_dockerfile(bundle: Bundle) -> Feedback:
    return Feedback(
        key="has_dockerfile",
        score=PASS if (bundle.root / "Dockerfile").is_file() else FAIL,
    )


def score_bundle(root: Path) -> list[Feedback]:
    """Run every registered scorer against one bundle.

    A scorer that raises yields a Feedback with score=None and the error in
    detail, matching AG2's behaviour: one broken scorer must not void the run.
    """
    bundle = Bundle(root)
    results: list[Feedback] = []
    for fn in all_scorers():
        try:
            results.append(fn(bundle))
        except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
            results.append(
                Feedback(
                    key=getattr(fn, "__name__", "unknown"),
                    score=None,
                    comment=f"scorer raised: {type(exc).__name__}: {exc}",
                )
            )
    return results
