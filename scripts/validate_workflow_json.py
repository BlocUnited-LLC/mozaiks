from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path("workflows")
    failures: list[tuple[str, str]] = []
    count = 0

    for path in root.rglob("*.json"):
        if any(part in {"node_modules", "build", "dist", ".next"} for part in path.parts):
            continue
        count += 1
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            failures.append((path.as_posix(), str(exc)))

    print(f"JSON files checked: {count}")
    print(f"Failures: {len(failures)}")

    for p, err in failures:
        print(f"- {p}: {err}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
