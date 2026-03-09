# =============================================================================
# FILE: core/data/theme_validation.py
# DESCRIPTION: Shared utilities for validating app theme configurations
# =============================================================================
from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from mozaiksai.core.data.themes.theme_manager import DEFAULT_THEME, ThemeConfig, ThemeUpdateRequest


@dataclass
class ThemeValidationResult:
    """Result object returned by validation helpers."""

    mode: str
    theme: Optional[ThemeConfig]
    merged_data: Optional[Dict[str, Any]]
    errors: Optional[List[Dict[str, Any]]]

    @property
    def ok(self) -> bool:
        return self.errors is None


class ThemeValidationError(Exception):
    """Raised when theme validation fails."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []

    def as_result(self, mode: str) -> ThemeValidationResult:
        return ThemeValidationResult(mode=mode, theme=None, merged_data=None, errors=self.errors)


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _format_validation_errors(exc: ValidationError) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        formatted.append(
            {
                "location": loc or "root",
                "message": err.get("msg", "Invalid value"),
                "type": err.get("type", "value_error"),
                "context": err.get("ctx", {}),
            }
        )
    return formatted


def validate_theme_update(payload: Dict[str, Any]) -> ThemeValidationResult:
    """Validate a partial update payload and merge it with the default theme."""

    normalized = payload if "theme" in payload else {"theme": payload}

    try:
        update_request = ThemeUpdateRequest.parse_obj(normalized)
    except ValidationError as exc:  # pragma: no cover - pydantic formatting
        errors = _format_validation_errors(exc)
        raise ThemeValidationError("Theme update payload validation failed", errors=errors) from exc

    overrides = update_request.theme.model_dump(exclude_none=True)
    merged = _deep_merge(DEFAULT_THEME, overrides)

    try:
        theme = ThemeConfig.parse_obj(merged)
    except ValidationError as exc:  # pragma: no cover - defensive validation
        errors = _format_validation_errors(exc)
        raise ThemeValidationError("Merged theme configuration is invalid", errors=errors) from exc

    return ThemeValidationResult(mode="update", theme=theme, merged_data=merged, errors=None)


def validate_full_theme(theme_data: Dict[str, Any]) -> ThemeValidationResult:
    """Validate a full theme document (fonts/colors/shadows/branding)."""

    try:
        theme = ThemeConfig.parse_obj(theme_data)
    except ValidationError as exc:
        errors = _format_validation_errors(exc)
        raise ThemeValidationError("Theme configuration validation failed", errors=errors) from exc

    return ThemeValidationResult(mode="full", theme=theme, merged_data=theme.dict(), errors=None)


def auto_validate_theme(data: Dict[str, Any]) -> ThemeValidationResult:
    """Attempt to validate a payload by inferring whether it's an update or full theme."""

    if not isinstance(data, dict):
        raise ThemeValidationError("Theme payload must be a JSON object", errors=[{"location": "root", "message": "Expected object", "type": "type_error"}])

    if any(key in data for key in ("fonts", "colors", "shadows", "branding")) and "theme" not in data:
        return validate_full_theme(data)

    return validate_theme_update(data)


def validate_theme(data: Dict[str, Any], *, mode: str = "auto") -> ThemeValidationResult:
    """Backward-compatible dispatcher that normalizes the legacy entry point."""

    normalized_mode = (mode or "auto").lower()
    if normalized_mode == "auto":
        return auto_validate_theme(data)
    if normalized_mode == "update":
        return validate_theme_update(data)
    if normalized_mode == "full":
        return validate_full_theme(data)
    raise ValueError(f"Unsupported validation mode: {mode}")


def summarize_validation(result: ThemeValidationResult) -> Dict[str, Any]:
    """Return a human-friendly summary dictionary for CLI/UX integration."""

    if not result.ok:
        raise ThemeValidationError("Cannot summarize failed validation", errors=result.errors)

    theme = result.theme
    assert theme is not None  # for type checkers

    primary = theme.colors.primary
    branding = theme.branding

    return {
        "mode": result.mode,
        "branding": {
            "name": branding.name,
            "logo": branding.logo,
            "favicon": branding.favicon,
        },
        "primary_color": {
            "main": primary.main,
            "light": primary.light,
            "dark": primary.dark,
        },
        "fonts": {
            "body": theme.fonts.body.family,
            "heading": theme.fonts.heading.family,
            "logo": theme.fonts.logo.family,
        },
    }


__all__ = [
    "ThemeValidationResult",
    "ThemeValidationError",
    "validate_theme",
    "validate_theme_update",
    "validate_full_theme",
    "auto_validate_theme",
    "summarize_validation",
    "build_theme_validation_parser",
    "theme_validation_cli_main",
]


def _load_payload(path: str) -> Dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ThemeValidationError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(data, dict):
        raise ThemeValidationError(
            "Theme payload must be a JSON object",
            errors=[{"location": "root", "message": "Expected object", "type": "type_error"}],
        )

    return data


def _resolve_validation(args: argparse.Namespace) -> ThemeValidationResult:
    if args.use_default:
        payload: Dict[str, Any] = DEFAULT_THEME if args.mode != "update" else {}
    else:
        payload = _load_payload(args.input)

    if args.mode == "update":
        return validate_theme_update(payload)
    if args.mode == "full":
        return validate_full_theme(payload)
    return auto_validate_theme(payload)


def _print_summary(result: ThemeValidationResult, args: argparse.Namespace) -> None:
    if not args.summary:
        return
    summary = summarize_validation(result)
    print("Validation Summary:")
    print(json.dumps(summary, indent=args.indent or 2, sort_keys=True))


def _print_merged(result: ThemeValidationResult, args: argparse.Namespace) -> None:
    if not args.print_merged or not result.ok:
        return
    print("Merged Theme JSON:")
    print(json.dumps(result.merged_data, indent=args.indent or 2, sort_keys=True))


def build_theme_validation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate app theme payloads against the runtime schema",
    )
    parser.add_argument("--input", "-i", default="-", help="Path to JSON payload (use '-' for stdin)")
    parser.add_argument("--mode", choices=["auto", "update", "full"], default="auto", help="Validation mode")
    parser.add_argument("--summary", action="store_true", help="Print key branding/color/font information if validation succeeds")
    parser.add_argument("--print-merged", action="store_true", help="Print the merged theme JSON (default theme + overrides)")
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation for printed output")
    parser.add_argument("--use-default", action="store_true", help="Validate the built-in default theme instead of reading a file")
    return parser


def theme_validation_cli_main(argv: List[str] | None = None) -> int:
    parser = build_theme_validation_parser()
    args = parser.parse_args(argv)

    try:
        result = _resolve_validation(args)
    except ThemeValidationError as exc:
        print("Validation failed:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        if exc.errors:
            for err in exc.errors:
                loc = err.get("location", "root")
                msg = err.get("message", "Invalid value")
                print(f"  - {loc}: {msg}", file=sys.stderr)
        return 1

    print(f"Validation succeeded in {result.mode!r} mode.")
    _print_summary(result, args)
    _print_merged(result, args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(theme_validation_cli_main())

