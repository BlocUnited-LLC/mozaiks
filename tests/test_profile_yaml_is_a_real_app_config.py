"""config/profile.yaml is consumed by the runtime and must not be rejected.

Two of the two generated bundles that got far enough to be judged failed
bundle_scan on the same file:

    config/profile.yaml: outside the canonical app planes ... not registered
    Generated app bundle contains noncanonical app config files:
    ['config/profile.yaml']

Both sides of that contradiction were checked before changing either.

AppGenerator is told to write it: "Set profile_layout based on app domain and
brand intent - this IS written to the generated app as app/config/profile.yaml".

The platform reads it: hosts/platform.py resolves app_root / "config" /
"profile.yaml" in its profile-layout endpoint and falls back to "top_nav" when
absent.

So the prompt was right, the runtime was right, and only the canonical path set
disagreed - it omitted a file the platform loads. The scanner was failing
bundles for obeying their own instructions.
"""

from __future__ import annotations

from pathlib import Path

from mozaiksai.core.runtime.app.paths import (
    CANONICAL_APP_CONFIG_FILES,
    is_canonical_app_config_path,
    noncanonical_app_config_paths,
)

ROOT = Path(__file__).resolve().parents[1]


def test_profile_yaml_is_canonical() -> None:
    assert "config/profile.yaml" in CANONICAL_APP_CONFIG_FILES
    assert is_canonical_app_config_path("config/profile.yaml")


def test_a_bundle_that_followed_the_instruction_is_not_rejected() -> None:
    """The exact config set from the two failing bundles."""
    emitted = [
        "config/ai.json",
        "config/integrations.yaml",
        "config/profile.yaml",
        "config/targets.json",
    ]

    assert noncanonical_app_config_paths(emitted) == []


def test_an_unregistered_config_file_is_still_rejected() -> None:
    """Registering one file must not open the plane to anything."""
    assert noncanonical_app_config_paths(["config/whatever.yaml"]) == [
        "config/whatever.yaml"
    ]


def test_the_runtime_still_reads_the_file_this_registers() -> None:
    """If the endpoint stops reading it, this registration is stale."""
    platform = (ROOT / "mozaiksai/hosts/platform.py").read_text(encoding="utf-8")

    assert 'app_root / "config" / "profile.yaml"' in platform


def test_the_prompt_still_tells_the_agent_to_write_it() -> None:
    """The other half of the contradiction, pinned from its own side."""
    agents = (ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(
        encoding="utf-8"
    )

    assert "app/config/profile.yaml" in agents
