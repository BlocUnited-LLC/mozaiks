"""Verify generated contract provenance against a pinned source commit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mozaiksai.core.contract_identity import ContractIdentityError, verify_contract_identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="generated bundle root")
    parser.add_argument("--metadata", type=Path, required=True, help="identity JSON file")
    parser.add_argument("--source-commit", required=True, help="expected immutable source commit")
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        verify_contract_identity(metadata, args.root, expected_source_commit=args.source_commit)
    except (OSError, json.JSONDecodeError, ContractIdentityError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps({"status": "verified", "source_commit": args.source_commit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
