"""ACP-only workers can use an approved adapter without a structured model."""

from __future__ import annotations

import pytest

from mozaiksai.control_plane import CodingWorkerRequest, ControlPlaneACPProviderConfig
from mozaiksai.control_plane.implementations import acp_coding_provider


def test_isolated_worker_uses_approved_provider_without_loading_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_policy_load() -> None:
        pytest.fail("ACP-only worker loaded a structured-output refinement policy")

    monkeypatch.setattr(acp_coding_provider, "load_control_plane_config", unexpected_policy_load)
    approved = ControlPlaneACPProviderConfig.model_validate(
        {"enabled": True, "adapter": "codex", "budget": {"max_wall_seconds": 45}}
    )

    provider = acp_coding_provider.ACPCodingProvider(provider_config=approved)

    assert provider.provider_id == "acp_codex"
    assert provider._provider_config() is approved
    assert provider._provider_config().budget.max_wall_seconds == 45


def test_provider_rejects_ambiguous_or_untyped_configuration() -> None:
    approved = ControlPlaneACPProviderConfig(enabled=True)

    with pytest.raises(ValueError, match="either a policy loader or an approved provider config"):
        acp_coding_provider.ACPCodingProvider(
            config_loader=lambda: {}, provider_config=approved
        )
    with pytest.raises(TypeError, match="ControlPlaneACPProviderConfig"):
        acp_coding_provider.ACPCodingProvider(provider_config={"enabled": True})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_direct_configuration_still_honors_disabled_gate() -> None:
    provider = acp_coding_provider.ACPCodingProvider(
        provider_config=ControlPlaneACPProviderConfig(enabled=False)
    )
    request = CodingWorkerRequest(
        app_id="app_1",
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        artifact_version_id="av_1",
        raw_user_request="Change the dashboard",
        change_class="patch",
        files={"app/app.json": "{}"},
    )

    result = await provider.execute(request)

    assert result.status == "unavailable"
    assert result.error == "ACP coding provider is disabled by its approved configuration."
