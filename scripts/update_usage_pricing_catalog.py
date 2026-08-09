from __future__ import annotations

"""Refresh the Mozaiks provider usage-pricing catalog.

The generated catalog is cost-basis data only. App/customer markups belong in
``app/config/subscriptions.yaml`` usage charge policies, not in this file.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mozaiksai.core.usage.pricing_catalog import normalize_litellm_pricing_catalog

DEFAULT_LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
DEFAULT_OUTPUT_PATH = Path("ai-pricing") / "catalogs" / "usage-pricing.generated.json"
DEFAULT_PACKAGE_OUTPUT_PATH = (
    Path("mozaiksai") / "core" / "usage" / "catalogs" / "usage-pricing.generated.json"
)
DEFAULT_MIN_NORMALIZED_MODEL_COUNT = 1_000
DEFAULT_MIN_NORMALIZED_RATIO = 0.50
DEFAULT_MAX_ROW_DROP_PERCENT = 20.0


def _load_json_url(url: str) -> tuple[dict[str, Any], str | None, str]:
    request = Request(url, headers={"User-Agent": "mozaiks-usage-pricing-updater"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            revision = response.headers.get("ETag") or response.headers.get("Last-Modified")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"failed to fetch pricing source {url}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pricing source {url} did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"pricing source {url} did not return a JSON object")
    return payload, revision, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_catalog(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(payload), encoding="utf-8")


def _existing_catalog(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _preserve_metadata_when_models_unchanged(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    existing = _existing_catalog(path)
    if not isinstance(existing, dict):
        return payload
    if existing.get("models") != payload.get("models"):
        return payload

    existing_source = existing.get("source")
    if not isinstance(existing_source, dict):
        return payload
    next_payload = dict(payload)
    next_source = dict(next_payload.get("source") or {})
    for key in ("content_sha256", "fetched_at", "source_revision"):
        if existing_source.get(key):
            next_source[key] = existing_source[key]
    next_payload["source"] = next_source
    return next_payload


def _model_count(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    models = payload.get("models")
    return len(models) if isinstance(models, dict) else 0


def _raw_model_count(raw_catalog: dict[str, Any]) -> int:
    return sum(
        1
        for raw_model_name, raw_model in raw_catalog.items()
        if raw_model_name != "sample_spec" and isinstance(raw_model, dict)
    )


def _catalog_change_summary(
    existing: dict[str, Any] | None,
    payload: dict[str, Any],
    *,
    sample_size: int = 5,
) -> dict[str, Any]:
    existing_models = existing.get("models") if isinstance(existing, dict) else None
    next_models = payload.get("models")
    if not isinstance(existing_models, dict) or not isinstance(next_models, dict):
        return {
            "existing_models": _model_count(existing),
            "next_models": _model_count(payload),
            "added": 0,
            "removed": 0,
            "changed": 0,
            "added_sample": [],
            "removed_sample": [],
            "changed_sample": [],
        }

    existing_keys = set(existing_models)
    next_keys = set(next_models)
    changed = sorted(
        key
        for key in existing_keys & next_keys
        if existing_models.get(key) != next_models.get(key)
    )
    added = sorted(next_keys - existing_keys)
    removed = sorted(existing_keys - next_keys)
    return {
        "existing_models": len(existing_models),
        "next_models": len(next_models),
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "added_sample": added[:sample_size],
        "removed_sample": removed[:sample_size],
        "changed_sample": changed[:sample_size],
    }


def _validate_catalog_refresh(
    *,
    raw_catalog: dict[str, Any],
    payload: dict[str, Any],
    existing: dict[str, Any] | None,
    min_normalized_model_count: int,
    min_normalized_ratio: float,
    max_row_drop_percent: float,
    allow_large_row_drop: bool,
) -> None:
    raw_count = _raw_model_count(raw_catalog)
    model_count = _model_count(payload)
    if model_count < min_normalized_model_count:
        raise RuntimeError(
            "normalized pricing catalog is unexpectedly small: "
            f"{model_count} model rows; minimum is {min_normalized_model_count}"
        )

    if raw_count > 0:
        normalized_ratio = model_count / raw_count
        if normalized_ratio < min_normalized_ratio:
            raise RuntimeError(
                "normalized pricing catalog coverage is unexpectedly low: "
                f"{model_count}/{raw_count} rows ({normalized_ratio:.1%}); "
                f"minimum ratio is {min_normalized_ratio:.1%}"
            )

    existing_count = _model_count(existing)
    if allow_large_row_drop or existing_count <= 0 or model_count >= existing_count:
        return

    drop_percent = ((existing_count - model_count) / existing_count) * 100.0
    if drop_percent > max_row_drop_percent:
        raise RuntimeError(
            "normalized pricing catalog row count dropped too much: "
            f"{existing_count} -> {model_count} ({drop_percent:.1f}% drop); "
            "use --allow-large-row-drop only after verifying the upstream schema changed intentionally"
        )


def _print_summary(
    *,
    raw_catalog: dict[str, Any],
    payload: dict[str, Any],
    source_revision: str | None,
    source_content_sha256: str,
    summary: dict[str, Any],
) -> None:
    print(f"source_revision={source_revision or 'unknown'}")
    print(f"source_content_sha256={source_content_sha256}")
    print(f"source_rows={_raw_model_count(raw_catalog)}")
    print(f"normalized_rows={_model_count(payload)}")
    print(
        "model_diff="
        f"added:{summary['added']} removed:{summary['removed']} changed:{summary['changed']}"
    )
    for key in ("added_sample", "removed_sample", "changed_sample"):
        sample = summary.get(key) or []
        if sample:
            print(f"{key}={', '.join(sample)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-url",
        default=DEFAULT_LITELLM_PRICES_URL,
        help="LiteLLM model_prices_and_context_window.json URL.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Generated Mozaiks usage pricing catalog path.",
    )
    parser.add_argument(
        "--package-output",
        default=None,
        help=(
            "Optional packaged catalog copy. Defaults to the bundled runtime catalog "
            "when --output is the default ai-pricing/catalogs path."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when the generated output differs from the existing file.",
    )
    parser.add_argument(
        "--min-normalized-model-count",
        type=int,
        default=DEFAULT_MIN_NORMALIZED_MODEL_COUNT,
        help=(
            "Fail when the normalized catalog has fewer model rows than this. "
            "This catches upstream schema changes that would silently erase pricing."
        ),
    )
    parser.add_argument(
        "--min-normalized-ratio",
        type=float,
        default=DEFAULT_MIN_NORMALIZED_RATIO,
        help="Fail when normalized rows divided by source rows is below this ratio.",
    )
    parser.add_argument(
        "--max-row-drop-percent",
        type=float,
        default=DEFAULT_MAX_ROW_DROP_PERCENT,
        help="Fail when normalized model count drops more than this percent from the existing catalog.",
    )
    parser.add_argument(
        "--allow-large-row-drop",
        action="store_true",
        help="Allow a large normalized row-count drop after manual upstream schema review.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the normalized catalog to stdout instead of writing it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)
    package_output_path = (
        Path(args.package_output)
        if args.package_output
        else DEFAULT_PACKAGE_OUTPUT_PATH
        if output_path == DEFAULT_OUTPUT_PATH
        else None
    )
    output_paths = [output_path]
    if package_output_path is not None and package_output_path != output_path:
        output_paths.append(package_output_path)

    raw_catalog, source_revision, source_content_sha256 = _load_json_url(args.source_url)
    payload = normalize_litellm_pricing_catalog(
        raw_catalog,
        source_url=args.source_url,
        source_revision=source_revision,
        source_content_sha256=source_content_sha256,
    )
    existing = _existing_catalog(output_path)
    _validate_catalog_refresh(
        raw_catalog=raw_catalog,
        payload=payload,
        existing=existing,
        min_normalized_model_count=max(0, args.min_normalized_model_count),
        min_normalized_ratio=max(0.0, args.min_normalized_ratio),
        max_row_drop_percent=max(0.0, args.max_row_drop_percent),
        allow_large_row_drop=bool(args.allow_large_row_drop),
    )
    summary = _catalog_change_summary(existing, payload)
    payload = _preserve_metadata_when_models_unchanged(output_path, payload)
    rendered = _canonical_json(payload)

    if args.dry_run:
        print(rendered, end="")
        return 0

    if args.check:
        stale_paths = []
        for path in output_paths:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing != rendered:
                stale_paths.append(path)
        if stale_paths:
            for path in stale_paths:
                print(f"{path} is not up to date", file=sys.stderr)
            _print_summary(
                raw_catalog=raw_catalog,
                payload=payload,
                source_revision=source_revision,
                source_content_sha256=source_content_sha256,
                summary=summary,
            )
            return 1
        print("usage pricing catalogs are up to date")
        return 0

    _print_summary(
        raw_catalog=raw_catalog,
        payload=payload,
        source_revision=source_revision,
        source_content_sha256=source_content_sha256,
        summary=summary,
    )
    for path in output_paths:
        _write_catalog(path, payload)
        print(f"wrote {len(payload.get('models', {}))} model price rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
