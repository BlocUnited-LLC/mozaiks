"""Token accounting must be importable before the runtime is initialized."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "source",
    (
        "from mozaiksai.core.tokens.wallet import get_token_wallet_ledger",
        "from mozaiksai.core.tokens.guard import TokenUsageDenied",
    ),
)
def test_token_modules_can_be_first_import_in_fresh_interpreter(source: str) -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
