#!/usr/bin/env python3
"""
Validate Fixture Example — Standalone Script
=============================================

Validates every event in a given NDJSON fixture file against the frozen
event_envelope.schema.json using jsonschema Draft2020-12.

Usage:
    python mozaiksai/core/contracts/schemas/v1/transport/validate_fixture_example.py [FIXTURE_PATH]

If FIXTURE_PATH is omitted, validates the default example_full_run.stream.ndjson.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema is required — pip install jsonschema", file=sys.stderr)
    raise SystemExit(2)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
_ENVELOPE_SCHEMA = _HERE / "event_envelope.schema.json"
_DEFAULT_FIXTURE = _HERE.parent / "fixtures" / "example_full_run.stream.ndjson"


def load_ndjson(path: Path) -> list[dict]:
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"  JSON parse error on line {lineno}: {exc}", file=sys.stderr)
    return events


def main() -> int:
    fixture_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_FIXTURE

    if not _ENVELOPE_SCHEMA.exists():
        print(f"Schema not found: {_ENVELOPE_SCHEMA}", file=sys.stderr)
        return 2
    if not fixture_path.exists():
        print(f"Fixture not found: {fixture_path}", file=sys.stderr)
        return 2

    schema = json.loads(_ENVELOPE_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    events = load_ndjson(fixture_path)
    print(f"Loaded {len(events)} events from {fixture_path.name}")

    errors_total = 0
    for i, event in enumerate(events):
        errs = list(validator.iter_errors(event))
        if errs:
            errors_total += len(errs)
            print(f"\n  Event[{i}] seq={event.get('seq')} type={event.get('event_type')}:")
            for e in errs:
                print(f"    • {e.json_path}: {e.message}")

    if errors_total:
        print(f"\n❌ {errors_total} validation error(s) in {len(events)} events.")
        return 1
    else:
        print(f"✅ All {len(events)} events valid against {_ENVELOPE_SCHEMA.name}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
