from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools import android_delivery as delivery


def _spec(origin: str, **extra):
    return {
        "schema_version": "mozaiks.android_delivery.v1",
        "package_id": "org.example.alpine",
        "display_name": "Alpine Club",
        "version_name": "1.2.3",
        "version_code": 7,
        "backend_origin": origin,
        "build_type": "debug",
        **extra,
    }


@pytest.mark.parametrize("origin", [
    "https://127.0.0.1", "https://127.255.255.254", "https://0.0.0.0",
    "https://10.31.41.59:8443", "https://172.16.0.1", "https://192.168.1.1",
    "https://169.254.169.254", "https://100.64.0.1", "https://192.0.0.8", "https://192.0.2.1",
    "https://198.18.0.1", "https://224.0.0.1", "https://240.0.0.1",
    "https://255.255.255.255", "https://[::]", "https://[::1]",
    "https://[fc00::1]", "https://[fd12::1]", "https://[fe80::1]",
    "https://[ff02::1]", "https://[2001:db8::1]", "https://[::ffff:7f00:1]",
    "https://[::ffff:a1f:293b]", "https://[::ffff:808:808]",
    "https://[100::1]", "https://[2002:7f00:1::]",
    "https://[64:ff9b:1::1]",
    "https://localhost", "https://backend.localhost", "https://backend.internal",
    "https://backend.local", "https://backend.localdomain", "https://backend.intranet",
    "https://backend.lan", "https://backend.home", "https://backend.corp",
    "https://backend.home.arpa", "https://api.backend.internal", "https://backend",
    "https://backend.internal.", "https://api.example.com.",
    "https://backend_internal.example.com", "https://-backend.example.com",
    "https://backend-.example.com", "https://api..example.com",
    "https://127.1", "https://2130706433", "https://0177.0.0.1",
    "https://0x7f000001", "https://%6cocalhost", "https://backend%2einternal",
    "https://[fe80::1%25eth0]", "https://LOCALHOST", "https://BACKEND.INTERNAL",
])
def test_nonpublic_or_noncanonical_backend_fails_before_either_archive(tmp_path, origin):
    source = tmp_path / "source"
    output = tmp_path / "delivery"
    with pytest.raises(ValidationError):
        delivery.materialize_android_workspace(source, _spec(origin), output)
    assert not output.exists()
    assert not (output / "source.zip").exists()
    assert not (output / "android-workspace.zip").exists()


@pytest.mark.parametrize("origin", [
    "https://api.example.com", "https://api.example.com:8443",
    "https://localhost.example.com", "https://backend.internal.example.com",
    "https://xn--bcher-kva.example.com", "https://8.8.8.8",
    "https://1.1.1.1:8443", "https://[2606:4700:4700::1111]",
    "https://[2001:4860:4860::8888]:8443",
])
def test_public_backend_origin_is_preserved(origin):
    assert delivery.AndroidDeliverySpec.model_validate(_spec(origin)).backend_origin == origin


@pytest.mark.parametrize("extra", [{"acceptance": True}, {"allow_private": True}])
def test_manifest_has_no_local_acceptance_override(tmp_path, extra):
    with pytest.raises(ValidationError):
        delivery.materialize_android_workspace(
            tmp_path / "source", _spec("https://127.0.0.1", **extra), tmp_path / "delivery",
        )
    assert not (tmp_path / "delivery").exists()


def test_preconstructed_manifest_is_revalidated_before_export(tmp_path):
    spec = delivery.AndroidDeliverySpec.model_construct(**_spec("https://backend.internal"))
    with pytest.raises(ValidationError):
        delivery.materialize_android_workspace(tmp_path / "source", spec, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()
