"""Source export security applies before either Android archive is emitted."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from factory_app.workflows.AppGenerator.tools import android_delivery as delivery
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    archive_digest,
    build_deterministic_archive,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_TOKEN = "SYNTHETIC_NOT_A_REAL_TOKEN_1234567890"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4"
    "DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
)


def _png_with_text(kind: bytes, payload: bytes) -> bytes:
    assert PNG[-8:-4] == b"IEND"
    chunk = len(payload).to_bytes(4, "big") + kind + payload
    chunk += zlib.crc32(kind + payload).to_bytes(4, "big")
    return PNG[:-12] + chunk + PNG[-12:]


def _write(root: Path, name: str, content: str | bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def _xml_meta(value: str) -> bytes:
    return f'<configuration><meta name="access_token" content="{value}"/></configuration>'.encode()


def _svg_meta(value: str) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        f'<meta name="access_token" content="{value}"/>'
        f'</metadata></svg>'
    ).encode()


def _png_xmp_meta(value: str) -> bytes:
    return _png_with_text(
        b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0" +
        f'<metadata><meta name="access_token" content="{value}"/></metadata>'.encode(),
    )


def _xml_nested_meta(value: str) -> bytes:
    return (
        f'<configuration><meta name="access_token">'
        f'<item content="{value}"/></meta></configuration>'
    ).encode()


def _svg_nested_meta(value: str) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        f'<meta name="access_token"><item content="{value}"/></meta>'
        f'</metadata></svg>'
    ).encode()


def _png_nested_xmp_meta(value: str) -> bytes:
    return _png_with_text(
        b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0" +
        f'<metadata><meta name="access_token"><item content="{value}"/></meta></metadata>'.encode(),
    )


def _query_url(value: str, key: str) -> str:
    return f"https://api.example.invalid/lookup?view=public&{key}={value}"


@pytest.fixture
def export_input(tmp_path, monkeypatch):
    workspace = tmp_path / "source"
    _write(workspace, "app/app.json", json.dumps({
        "appId": "source-export-test", "appName": "Source export", "authRequired": False,
    }))
    _write(workspace, "app/ui/index.js", "export function register() {}\n")
    framework_files = {
        "web_shell/package.json": b'{"name":"shell"}',
        "chat-ui/package.json": b'{"name":"ui"}',
    }
    framework = {
        "commit": "a" * 40,
        "resource_digest": "sha256:" + "b" * 64,
        "files": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
            for name, raw in sorted(framework_files.items())
        ],
    }
    monkeypatch.setattr(delivery, "_framework_snapshot", lambda: (framework_files, framework))
    monkeypatch.setattr(delivery.resources, "resolve_factory_app_root", lambda: ROOT / "factory_app")
    spec = {
        "schema_version": "mozaiks.android_delivery.v1",
        "package_id": "org.example.exporttest", "display_name": "Source export",
        "version_name": "1.0.0", "version_code": 1,
        "backend_origin": "https://api.example.invalid", "build_type": "debug",
    }
    return workspace, spec, tmp_path / "delivery"


@pytest.mark.parametrize("name,content", [
    (
        "app/brand/developer.json",
        json.dumps({
            "access_token": SYNTHETIC_TOKEN,
            "backend": "https://10.31.41.59:8443", "debug": True,
        }),
    ),
    ("app/services/config.py", f'API_TOKEN = "{SYNTHETIC_TOKEN}"\n'),
    ("workflows/Fixture/tools/local_config.py", f'API_TOKEN = "{SYNTHETIC_TOKEN}"\n'),
], ids=["developer-brand-file", "app-service-token", "workflow-tool-token"])
def test_demonstrated_leaks_are_rejected_independently_before_either_archive(export_input, name, content):
    workspace, spec, output = export_input
    _write(workspace, name, content)

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert not output.exists()
    assert not list(workspace.parent.rglob("*.zip"))
    assert (workspace / name).read_text() == content


@pytest.mark.parametrize("name,make_content", [
    ("app/config/provider.xml", _xml_meta),
    ("app/brand/assets/metadata.svg", _svg_meta),
    ("app/brand/assets/xmp.png", _png_xmp_meta),
    ("app/config/provider.toml", lambda value: f'API_TOKEN = """{value}"""\n'.encode()),
    ("app/config/runtime.txt", lambda value: f'export API_TOKEN={value}\n'.encode()),
    ("app/config/provider.toml", lambda value: f'[access_token]\ndefaultValue = "{value}"\n'.encode()),
    ("app/config/provider.toml", lambda value: f'[access_token]\ndefault_value = "{value}"\n'.encode()),
    ("app/config/provider.toml", lambda value: f'[access_token]\n"default-value" = "{value}"\n'.encode()),
    ("app/config/provider.json", lambda value: json.dumps({"access_token": {"defaultValue": value}}).encode()),
    ("app/config/provider.json", lambda value: json.dumps({"access_token": {"default_value": value}}).encode()),
    ("app/config/provider.json", lambda value: json.dumps({"access_token": {"default-value": value}}).encode()),
    ("app/config/provider.json", lambda value: json.dumps({"access_token": {"metadata": {"defaultValue": value}}}).encode()),
    ("app/config/provider.json", lambda value: json.dumps({"access_token": {"metadata": [{"defaultValue": value}]}}).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": {"metadata": {"entries": [{"defaultValue": value}]}},
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": {"metadata": {"entries": [{"literalValue": value}]}},
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": {"metadata": {"entries": [{"futurePayloadField": value}]}},
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": [{"metadata": {"entries": [{"futurePayloadField": value}]}}],
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": {"metadata": {"entries": [value]}},
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "access_token": {"metadata": {"collection": [{"settings": {"defaultValue": value}}]}},
    }).encode()),
    ("app/config/provider.toml", lambda value: f'[access_token.metadata]\ndefaultValue = "{value}"\n'.encode()),
    ("app/config/provider.toml", lambda value: (
        f'[access_token.metadata]\nentries = [{{ defaultValue = "{value}" }}]\n'
    ).encode()),
    ("app/config/provider.toml", lambda value: (
        f'[access_token.metadata]\nentries = [{{ literalValue = "{value}" }}]\n'
    ).encode()),
    ("app/config/provider.yaml", lambda value: (
        f'access_token:\n  metadata:\n    entries:\n      - defaultValue: "{value}"\n'
    ).encode()),
    ("app/config/provider.yaml", lambda value: (
        f'access_token:\n  metadata:\n    entries:\n      - literalValue: "{value}"\n'
    ).encode()),
    ("app/config/provider.xml", _xml_nested_meta),
    ("app/brand/assets/metadata.svg", _svg_nested_meta),
    ("app/brand/assets/xmp.png", _png_nested_xmp_meta),
    ("app/brand/theme_config.json", lambda value: json.dumps({
        "identity": {"name": "Source export"}, "url": _query_url(value, "access_token"),
    }).encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "url": _query_url(value, "api_key"),
    }).encode()),
    ("app/ui/provider.js", lambda value: f'export const providerUrl = "{_query_url(value, "api_key")}";\n'.encode()),
    ("app/config/provider.json", lambda value: f'{{"url":"https://api.example.invalid/lookup\\u003fapi_key={value}"}}'.encode()),
    ("app/config/provider.json", lambda value: json.dumps({
        "url": f"https://api.example.invalid/lookup?api_key%3D{value}",
    }).encode()),
    ("app/config/provider.xml", lambda value: (
        f'<configuration><entry url="https://api.example.invalid/lookup?view=public&amp;api_key={value}"/>'
        f'</configuration>'
    ).encode()),
], ids=[
    "xml-meta-content", "svg-meta-content", "png-xmp-meta-content",
    "toml-triple-double", "env-style-text",
    "toml-camel-default", "toml-snake-default", "toml-kebab-default",
    "json-camel-default", "json-snake-default", "json-kebab-default",
    "json-nested-metadata-default", "json-metadata-list-default",
    "json-metadata-entries-default", "json-metadata-entries-literal",
    "json-metadata-entries-future-payload", "json-direct-list-future-payload",
    "json-metadata-entries-scalar", "json-arbitrary-wrappers-default",
    "toml-nested-metadata-default", "toml-metadata-entries-default", "toml-metadata-entries-literal",
    "yaml-metadata-entries-default", "yaml-metadata-entries-literal",
    "xml-descendant-content", "svg-descendant-content", "png-xmp-descendant-content",
    "theme-json-query-token", "provider-json-query-key", "ui-js-query-key",
    "json-escaped-question-query-key", "json-encoded-equals-query-key", "xml-escaped-ampersand-query-key",
])
def test_credential_metadata_literals_fail_but_runtime_references_remain_portable(
    export_input, name, make_content,
):
    workspace, spec, output = export_input
    unsafe = make_content(SYNTHETIC_TOKEN)
    _write(workspace, name, unsafe)

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert not output.exists()
    assert not list(workspace.parent.rglob("*.zip"))

    safe = make_content("${INTEGRATION_API_TOKEN}")
    _write(workspace, name, safe)
    result = delivery.materialize_android_workspace(workspace, spec, output)
    delivery.verify_android_delivery(Path(result["mobile_dir"]))
    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            assert archive.read(name) == safe


@pytest.mark.parametrize("name,safe", [
    ("app/config/provider.xml", (
        b'<configuration><meta name="access_token" '
        b'content="&lt;INTEGRATION_API_TOKEN&gt;"/></configuration>'
    )),
    ("app/brand/assets/metadata.svg", (
        b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        b'<meta name="access_token" content="&lt;INTEGRATION_API_TOKEN&gt;"/>'
        b'</metadata></svg>'
    )),
    ("app/brand/assets/xmp.png", _png_xmp_meta("&lt;INTEGRATION_API_TOKEN&gt;")),
    ("app/config/provider.toml", b'[access_token]\ndefaultValue = "<INTEGRATION_API_TOKEN>"\n'),
    ("app/config/provider.json", b'{"access_token":{"defaultValue":"<INTEGRATION_API_TOKEN>"}}'),
])
def test_documented_placeholders_keep_exact_source_bytes_in_both_archives(export_input, name, safe):
    workspace, spec, output = export_input
    _write(workspace, name, safe)

    result = delivery.materialize_android_workspace(workspace, spec, output)
    delivery.verify_android_delivery(Path(result["mobile_dir"]))
    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            assert archive.read(name) == safe


def test_ordinary_xml_content_metadata_is_public(export_input):
    workspace, spec, output = export_input
    name = "app/config/provider.xml"
    content = b'<configuration><meta name="title" content="Public title"/></configuration>'
    _write(workspace, name, content)

    result = delivery.materialize_android_workspace(workspace, spec, output)
    delivery.verify_android_delivery(Path(result["mobile_dir"]))
    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            assert archive.read(name) == content


@pytest.mark.parametrize("name,content", [
    ("app/config/provider.json", b'{"access_token":{"metadata":{"description":"Public description"}}}'),
    ("app/config/provider.json", b'{"access_token":{"metadata":["Public description",{"type":"string"}]}}'),
    ("app/config/provider.json", b'{"access_token":{"metadata":{"entries":[{"description":"Public note"}]}}}'),
    ("app/config/provider.json", b'{"access_token":{"metadata":{"entries":[{"description":"Public note","type":"string","required":true,"env_name":"INTEGRATION_API_TOKEN"}]}}}'),
    ("app/config/provider.json", b'{"access_token":[{"metadata":{"description":"Public description"}}]}'),
    ("app/config/provider.xml", b'<configuration><meta name="title"><item content="Public title"/></meta></configuration>'),
    ("app/config/provider.xml", (
        b'<configuration><access_token><description>Public description</description>'
        b'<env_name>INTEGRATION_API_TOKEN</env_name></access_token></configuration>'
    )),
    ("app/config/provider.json", b'{"url":"https://api.example.invalid/lookup?view=public"}'),
])
def test_public_nested_metadata_and_noncredential_queries_remain_portable(export_input, name, content):
    workspace, spec, output = export_input
    _write(workspace, name, content)

    result = delivery.materialize_android_workspace(workspace, spec, output)
    delivery.verify_android_delivery(Path(result["mobile_dir"]))
    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            assert archive.read(name) == content


@pytest.mark.parametrize("name", [
    "app/config/nested/credentials.json",
    "workflows/Fixture/tools/CREDENTIALS.JSON",
    "app/services/nested/Developer.json",
    "workflows/Fixture/tools/developer.yaml",
    "app/.env.local",
    "workflows/Fixture/tools/.ENV.local",
    "app/.npmrc",
    "workflows/Fixture/tools/.NPMRC",
    "app/services/.git-credentials",
    "app/.venv/lib/site-packages/cache.py",
    "workflows/Fixture/.mypy_cache/state.json",
    "app/services/id_rsa",
    "workflows/Fixture/tools/ID_ED25519",
    "app/config/release.pem",
    "workflows/Fixture/tools/RELEASE.KEYSTORE",
    "app/config/client.p12",
    "workflows/Fixture/tools/client.PFX",
])
def test_known_private_paths_are_rejected_even_without_a_recognizable_token(export_input, name):
    workspace, spec, output = export_input
    _write(workspace, name, "public: true\n")

    with pytest.raises(delivery.AndroidDeliveryError):
        delivery.materialize_android_workspace(workspace, spec, output)

    assert not output.exists()


@pytest.mark.parametrize("name,content", [
    ("app/.env.example", f"STORAGE_KEY={SYNTHETIC_TOKEN}\n"),
    ("app/services/config.py", f'SECRET_KEY = "{SYNTHETIC_TOKEN}"\n'),
    ("workflows/Fixture/tools/config.py", f'AWS_SECRET_ACCESS_KEY = "{SYNTHETIC_TOKEN}"\n'),
    ("app/config/integration.json", json.dumps({"access_token": SYNTHETIC_TOKEN})),
    ("app/config/integration.json", json.dumps({"accessToken": SYNTHETIC_TOKEN})),
    ("app/config/integration.json", json.dumps({"client": {"client_secret": SYNTHETIC_TOKEN}})),
    ("app/config/integration.yaml", f"ACCESS_TOKEN: {SYNTHETIC_TOKEN}\n"),
    ("app/config/integration.yaml", f"'access_token': '{SYNTHETIC_TOKEN}'\n"),
    ("app/config/integration.yaml", f"access_token: |-\n  {SYNTHETIC_TOKEN}\n"),
    ("app/config/integration.ini", f"[provider]\nAPI_TOKEN={SYNTHETIC_TOKEN}\n"),
    ("workflows/Fixture/tools/provider.conf", f"API_TOKEN={SYNTHETIC_TOKEN}\n"),
    ("app/config/integration.toml", f"API_TOKEN={SYNTHETIC_TOKEN}\n"),
    ("app/config/provider.toml", f"API_TOKEN = '''{SYNTHETIC_TOKEN}'''\n"),
    ("app/config/runtime.txt", f"API_TOKEN={SYNTHETIC_TOKEN}\n"),
    ("app/config/provider.json", json.dumps({"authHeader": f"Bearer {SYNTHETIC_TOKEN}"})),
    ("app/config/provider.json", json.dumps({"authorizationHeader": f"Basic {SYNTHETIC_TOKEN}"})),
    ("app/config/provider.json", json.dumps({"access_token_b64": base64.b64encode(SYNTHETIC_TOKEN.encode()).decode()})),
    ("app/config/provider.xml", f"<configuration><access_token>{SYNTHETIC_TOKEN}</access_token></configuration>"),
    ("workflows/Fixture/tools/provider.xml", f"<configuration><access_token>{SYNTHETIC_TOKEN}</access_token></configuration>"),
    ("app/config/provider.xml", f'<configuration><access_token value="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", f'<configuration><access_token default="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", f'<configuration><access_token defaultValue="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", (
        '<configuration><access_token><description>Public description</description>'
        + SYNTHETIC_TOKEN + '</access_token></configuration>'
    )),
    ("app/config/provider.xml", f'<configuration><access_token data="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", f'<configuration><access_token secretValue="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", f'<configuration><entry name="access_token" value="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", f'<configuration><property name="access_token" defaultValue="{SYNTHETIC_TOKEN}"/></configuration>'),
    ("app/config/provider.xml", (
        "<configuration><entry><key>access_token</key><value>"
        + SYNTHETIC_TOKEN + "</value></entry></configuration>"
    )),
    ("app/config/provider.plist", (
        '<plist version="1.0"><dict><key>access_token</key><string>'
        + SYNTHETIC_TOKEN + "</string></dict></plist>"
    )),
    ("app/services/provider.py", (
        'PART_ONE = "SYNTHETIC_NOT_A_REAL_"\n'
        'PART_TWO = "TOKEN_1234567890"\n'
        'API_TOKEN = PART_ONE + PART_TWO\n'
    )),
    ("workflows/Fixture/tools/provider.properties", f"API_TOKEN={SYNTHETIC_TOKEN}\n"),
    ("app/config/integration.json", json.dumps({
        "url": f"https://user:{SYNTHETIC_TOKEN}@api.example.invalid",
    })),
    ("app/services/integration_catalog.py", 'POSTGRES_EXAMPLE = "postgresql://user:pass@db.example.net:5432/dbname"\n'),
    ("app/services/config.py", f'API_TOKEN: str = "{SYNTHETIC_TOKEN}"\n'),
    ("app/services/config.py", f'API_TOKEN = (\n    "{SYNTHETIC_TOKEN}"\n)\n'),
    ("app/services/config.py", f'CONFIG = {{"token": "{SYNTHETIC_TOKEN}"}}\n'),
    ("app/services/config.py", f'API_KEY = os.getenv("SERVICE_API_KEY", "{SYNTHETIC_TOKEN}")\n'),
    ("workflows/Fixture/tools/config.py", f'API_TOKEN = os.environ.get("API_TOKEN", "{SYNTHETIC_TOKEN}")\n'),
    ("workflows/Fixture/tools/config.py", f'ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN") or "{SYNTHETIC_TOKEN}"\n'),
    ("app/ui/config.js", f"export const accessToken = '{SYNTHETIC_TOKEN}';\n"),
    ("app/ui/config.js", f'export const apiToken =\n  "{SYNTHETIC_TOKEN}";\n'),
    ("app/ui/config.js", f'export const API_TOKEN = (\n  "{SYNTHETIC_TOKEN}"\n);\n'),
    ("app/ui/config.js", f'export const config = {{"apiKey": "{SYNTHETIC_TOKEN}"}};\n'),
    ("app/ui/config.js", f'const config = {{}}; config.accessToken = "{SYNTHETIC_TOKEN}";\n'),
    ("app/ui/config.js", f'const headers = {{Authorization: "Bearer {SYNTHETIC_TOKEN}"}};\n'),
    ("app/ui/config.js", f'export const API_TOKEN = process.env.API_TOKEN || "{SYNTHETIC_TOKEN}";\n'),
    ("app/ui/config.js", f'export const API_TOKEN = process.env.API_TOKEN ?? "{SYNTHETIC_TOKEN}";\n'),
    ("workflows/Fixture/tools/provider.json", json.dumps({"refresh_token": SYNTHETIC_TOKEN})),
    ("workflows/Fixture/agents.yaml", f"provider:\n  api_token: {SYNTHETIC_TOKEN}\n"),
    ("app/brand/theme_config.json", json.dumps({"identity": {"access_token": SYNTHETIC_TOKEN}})),
])
def test_obvious_credential_values_in_app_and_workflow_inputs_fail_closed(export_input, name, content):
    workspace, spec, output = export_input
    _write(workspace, name, content)
    # Callers may provide an existing empty output directory; it must stay empty.
    output.mkdir()

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("name,content", [
    ("app/brand/settings.json", '{"debug": true}'),
    ("app/brand/assets/config.js", "export const debug = true;\n"),
    ("app/brand/assets/help.html", "<h1>Help</h1>"),
    ("app/brand/assets/app.js.map", '{}'),
    ("app/brand/assets/manual.pdf", b"%PDF-1.7\n"),
    ("app/brand/assets/icon.png", json.dumps({"access_token": SYNTHETIC_TOKEN})),
    ("app/brand/assets/icon.png", PNG + f'\nAPI_TOKEN="{SYNTHETIC_TOKEN}"\n'.encode()),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        + json.dumps({"access_token": SYNTHETIC_TOKEN}) + "</metadata></svg>"
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata><access_token>'
        + SYNTHETIC_TOKEN + "</access_token></metadata></svg>"
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata><access_token '
        + f'value="{SYNTHETIC_TOKEN}"/></metadata></svg>'
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        + 'data-access-token="' + SYNTHETIC_TOKEN + '" />'
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata><![CDATA['
        + f"<access_token>{SYNTHETIC_TOKEN}</access_token>" + "]]></metadata></svg>"
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>&lt;access_token&gt;'
        + SYNTHETIC_TOKEN + "&lt;/access_token&gt;</metadata></svg>"
    )),
    ("app/brand/assets/logo.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>&lt;access_token '
        + f'value="{SYNTHETIC_TOKEN}"/&gt;</metadata></svg>'
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"tEXt", b"Access Token\0" + SYNTHETIC_TOKEN.encode(),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"zTXt", b"access_token\0\0" + zlib.compress(SYNTHETIC_TOKEN.encode()),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"iTXt", b"Description\0\x01\0en\0Access Token\0"
        + zlib.compress(SYNTHETIC_TOKEN.encode()),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0"
        + f"<access_token>{SYNTHETIC_TOKEN}</access_token>".encode(),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0"
        + f'<access_token defaultValue="{SYNTHETIC_TOKEN}"/>'.encode(),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"zTXt", b"XML:com.adobe.xmp\0\0"
        + zlib.compress(f"<access_token>{SYNTHETIC_TOKEN}</access_token>".encode()),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"tEXt", b"XML:com.adobe.xmp\0"
        + f"<access_token>{SYNTHETIC_TOKEN}</access_token>".encode(),
    )),
    ("app/brand/assets/icon.png", _png_with_text(
        b"zTXt", b"Title\0\0" + zlib.compress(b"x" * (1024 * 1024 + 1)),
    )),
    ("app/brand/assets/icon.png", PNG + b"trailing bytes"),
    ("app/brand/assets/logo.svg", '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>'),
])
def test_public_brand_surface_accepts_only_declared_asset_types(export_input, name, content):
    workspace, spec, output = export_input
    _write(workspace, name, content)

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert not output.exists()


def test_names_only_secret_contract_cannot_smuggle_a_value(export_input):
    workspace, spec, output = export_input
    _write(workspace, "app/security/secrets.yaml", (
        "version: 1\nsecrets:\n  - env: INTEGRATION_API_TOKEN\n"
        f"    value: {SYNTHETIC_TOKEN}\n"
    ))

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert not output.exists()


@pytest.mark.parametrize("secret_contract", [
    (
        b"version: 1\nprovider:\n  type: env\nsecrets:\n"
        b"  - env: INTEGRATION_API_TOKEN\n    required: true\n"
    ),
    (
        b"version: 1\nprovider:\n  type: azure_key_vault\nsecrets:\n"
        b"  - env: INTEGRATION_API_TOKEN\n    required: true\n"
        b"    azure_key_vault:\n      secret_name: integration-api-token\n"
    ),
], ids=["environment-handle", "named-vault-reference"])
def test_both_archives_preserve_public_assets_and_names_only_secret_inputs(export_input, secret_contract):
    workspace, spec, output = export_input
    safe_inputs = {
        "app/brand/icon.png": PNG,
        "app/brand/assets/metadata.png": _png_with_text(
            b"tEXt", b"Access Token\0${INTEGRATION_API_TOKEN}",
        ),
        "app/brand/assets/title.png": _png_with_text(b"tEXt", b"Title\0Public logo"),
        "app/brand/assets/xmp.png": _png_with_text(
            b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0"
            b"<metadata><title>Public logo</title></metadata>",
        ),
        "app/brand/assets/xmp_reference.png": _png_with_text(
            b"iTXt", b"XML:com.adobe.xmp\0\x00\0\0\0"
            b'<access_token defaultValue="${INTEGRATION_API_TOKEN}"/>',
        ),
        "app/brand/assets/logo.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
            b'<path d="M0 0h1v1H0z"/></svg>'
        ),
        "app/brand/assets/metadata.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
            b'<access_token>${INTEGRATION_API_TOKEN}</access_token>'
            b'</metadata></svg>'
        ),
        "app/brand/assets/metadata_reference.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
            b'<access_token value="${INTEGRATION_API_TOKEN}"/>'
            b'</metadata></svg>'
        ),
        "app/brand/assets/escaped_metadata_reference.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
            b'&lt;access_token value="${INTEGRATION_API_TOKEN}"/&gt;'
            b'</metadata></svg>'
        ),
        "app/brand/theme_config.json": json.dumps({
            "identity": {"name": "Source export"},
            "assets": {"logo": "assets/logo.svg"},
        }).encode(),
        "app/security/secrets.yaml": secret_contract,
        "app/services/config.py": (
            b"import os\nfrom mozaiksai.core.secrets import resolve_secret\n"
            b'API_TOKEN = resolve_secret("INTEGRATION_API_TOKEN")\n'
            b'ACCESS_TOKEN = os.environ["INTEGRATION_API_TOKEN"]\n'
            b'REFRESH_TOKEN = os.getenv("INTEGRATION_REFRESH_TOKEN")\n'
            b'def authorization_for(access_token):\n'
            b'    authorization = f"Bearer {access_token}"\n    return authorization\n'
            b'def joined_authorization_for(access_token):\n'
            b'    authorization = "Bearer " + access_token\n    return authorization\n'
        ),
        "workflows/Fixture/tools/integration.py": (
            b"import os\n"
            b'API_TOKEN = os.environ.get("INTEGRATION_API_TOKEN")\n'
            b'def from_response(response):\n    return response["access_token"]\n'
        ),
        "app/ui/runtime-token.js": (
            b"export function tokenFrom(response) { return response.access_token; }\n"
            b"export const API_TOKEN = process.env.INTEGRATION_API_TOKEN;\n"
            b'export const grant = { grant_type: "refresh_token" };\n'
            b'export function authorizationFor(accessToken) {\n'
            b'  const authorization = `Bearer ${accessToken}`;\n  return authorization;\n}\n'
            b'export function joinedAuthorizationFor(accessToken) {\n'
            b'  const authorization = "Bearer " + accessToken;\n  return authorization;\n}\n'
        ),
        "app/config/integration.yaml": (
            b"api_token_env: INTEGRATION_API_TOKEN\n"
            b"access_token: ${INTEGRATION_API_TOKEN}\n"
            b"token_endpoint_auth_method: none\ntoken_timeout_seconds: 30\n"
        ),
        "app/.env.example": b"INTEGRATION_API_TOKEN=\nINTEGRATION_REFRESH_TOKEN=\n",
        "app/config/provider.json": (
            b'{"url":"https://user:${INTEGRATION_PASSWORD}@api.example.invalid"}'
        ),
        "app/config/header.json": b'{"authHeader":"Bearer ${INTEGRATION_API_TOKEN}"}',
        "app/config/provider.toml": b'[provider]\napi_token_env = "INTEGRATION_API_TOKEN"\napi_token = "${INTEGRATION_API_TOKEN}"\n',
        "app/config/runtime.txt": b'API_TOKEN=${INTEGRATION_API_TOKEN}\n',
        "app/config/provider.xml": b'<configuration><access_token>${INTEGRATION_API_TOKEN}</access_token></configuration>',
        "app/config/provider_direct_attributes.xml": (
            b'<configuration><access_token value="${INTEGRATION_API_TOKEN}" '
            b'default="${INTEGRATION_API_TOKEN}" defaultValue="${INTEGRATION_API_TOKEN}" '
            b'data="${INTEGRATION_API_TOKEN}" secretValue="${INTEGRATION_API_TOKEN}"/>'
            b'</configuration>'
        ),
        "app/config/provider_defaults.xml": (
            b'<configuration><property name="access_token" '
            b'defaultValue="${INTEGRATION_API_TOKEN}"/></configuration>'
        ),
        "app/config/provider_entries.xml": (
            b'<configuration><entry><key>access_token</key>'
            b'<value>${INTEGRATION_API_TOKEN}</value></entry></configuration>'
        ),
        "app/config/provider.plist": (
            b'<plist version="1.0"><dict><key>access_token</key>'
            b'<string>${INTEGRATION_API_TOKEN}</string></dict></plist>'
        ),
        "app/services/integration_catalog.py": (
            b'POSTGRES_EXAMPLE = "postgresql://user:pass@host:port/dbname"\n'
        ),
    }
    for name, content in safe_inputs.items():
        _write(workspace, name, content)
    # Workspace/operator inputs outside app/ and workflows/ are not exported.
    _write(workspace, ".env", f"ACCESS_TOKEN={SYNTHETIC_TOKEN}\n")

    result = delivery.materialize_android_workspace(workspace, spec, output)
    manifest = delivery.verify_android_delivery(Path(result["mobile_dir"]))
    source_paths = {entry["path"] for entry in manifest["source_files"]}

    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            members = set(archive.namelist())
            assert source_paths.issubset(members)
            assert ".env" not in members
            for name, content in safe_inputs.items():
                assert archive.read(name) == content
            assert all(SYNTHETIC_TOKEN.encode() not in archive.read(name) for name in members)
            if field == "source_archive":
                assert members == source_paths
            else:
                assert "mobile/delivery.manifest.json" in members
                assert "app/ui/auth/capacitor/index.js" in members


def test_reused_yaml_aliases_preserve_ordinary_configuration(export_input):
    workspace, spec, output = export_input
    name = "app/config/layout.yaml"
    content = (
        "leaf: &leaf {layout: compact, retries: 3}\n"
        "pair: &pair [*leaf, *leaf]\n"
        "row: &row [*pair, *pair]\n"
        "tables: [*row, *row]\n"
    )
    _write(workspace, name, content)

    result = delivery.materialize_android_workspace(workspace, spec, output)
    delivery.verify_android_delivery(Path(result["mobile_dir"]))

    for field in ("source_archive", "archive_path"):
        with ZipFile(result[field]) as archive:
            assert archive.read(name) == content.encode()


def test_yaml_alias_reused_in_credential_context_does_not_inherit_a_safe_verdict(export_input):
    workspace, spec, output = export_input
    _write(workspace, "app/config/integration.yaml", (
        "ordinary_defaults: &defaults\n"
        f"  default: {SYNTHETIC_TOKEN}\n"
        "public_options: *defaults\n"
        "access_token: *defaults\n"
    ))

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.materialize_android_workspace(workspace, spec, output)

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert not output.exists()


@pytest.mark.parametrize("name,safe,unsafe", [
    (
        "workflows/Fixture/tools/integration.py",
        b'import os\nAPI_TOKEN = os.environ["INTEGRATION_API_TOKEN"]\n',
        f'API_TOKEN = "{SYNTHETIC_TOKEN}"\n'.encode(),
    ),
    (
        "app/config/runtime.txt",
        b'export API_TOKEN=${INTEGRATION_API_TOKEN}\n',
        f'export API_TOKEN={SYNTHETIC_TOKEN}\n'.encode(),
    ),
    (
        "app/config/provider.toml",
        b'API_TOKEN = """${INTEGRATION_API_TOKEN}"""\n',
        f'API_TOKEN = """{SYNTHETIC_TOKEN}"""\n'.encode(),
    ),
    (
        "app/config/provider.xml",
        _xml_meta("${INTEGRATION_API_TOKEN}"),
        _xml_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/brand/assets/metadata.svg",
        _svg_meta("${INTEGRATION_API_TOKEN}"),
        _svg_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/brand/assets/xmp.png",
        _png_xmp_meta("${INTEGRATION_API_TOKEN}"),
        _png_xmp_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/config/provider.xml",
        b'<configuration><access_token value="${INTEGRATION_API_TOKEN}"/></configuration>',
        f'<configuration><access_token value="{SYNTHETIC_TOKEN}"/></configuration>'.encode(),
    ),
    (
        "app/brand/assets/metadata.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
        b'<access_token defaultValue="${INTEGRATION_API_TOKEN}"/></metadata></svg>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg"><metadata>'
            f'<access_token defaultValue="{SYNTHETIC_TOKEN}"/></metadata></svg>'
        ).encode(),
    ),
    (
        "app/config/provider.xml",
        _xml_meta("${INTEGRATION_API_TOKEN}"),
        _xml_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/brand/assets/xmp.png",
        _png_xmp_meta("${INTEGRATION_API_TOKEN}"),
        _png_xmp_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/config/provider.toml",
        b'[access_token]\ndefaultValue = "${INTEGRATION_API_TOKEN}"\n',
        f'[access_token]\ndefaultValue = "{SYNTHETIC_TOKEN}"\n'.encode(),
    ),
    (
        "app/config/provider.json",
        b'{"access_token":{"defaultValue":"${INTEGRATION_API_TOKEN}"}}',
        json.dumps({"access_token": {"defaultValue": SYNTHETIC_TOKEN}}).encode(),
    ),
    (
        "app/config/provider.json",
        json.dumps({"access_token": {"metadata": {"defaultValue": "${INTEGRATION_API_TOKEN}"}}}).encode(),
        json.dumps({"access_token": {"metadata": {"defaultValue": SYNTHETIC_TOKEN}}}).encode(),
    ),
    (
        "app/config/provider.json",
        json.dumps({"access_token": {"metadata": {"entries": [{"defaultValue": "${INTEGRATION_API_TOKEN}"}]}}}).encode(),
        json.dumps({"access_token": {"metadata": {"entries": [{"defaultValue": SYNTHETIC_TOKEN}]}}}).encode(),
    ),
    (
        "app/config/provider.json",
        json.dumps({"access_token": {"metadata": {"entries": [{"literalValue": "${INTEGRATION_API_TOKEN}"}]}}}).encode(),
        json.dumps({"access_token": {"metadata": {"entries": [{"literalValue": SYNTHETIC_TOKEN}]}}}).encode(),
    ),
    (
        "app/config/provider.json",
        json.dumps({"access_token": {"metadata": {"entries": [{"futurePayloadField": "${INTEGRATION_API_TOKEN}"}]}}}).encode(),
        json.dumps({"access_token": {"metadata": {"entries": [{"futurePayloadField": SYNTHETIC_TOKEN}]}}}).encode(),
    ),
    (
        "app/config/provider.toml",
        b'[access_token.metadata]\nentries = [{ literalValue = "${INTEGRATION_API_TOKEN}" }]\n',
        f'[access_token.metadata]\nentries = [{{ literalValue = "{SYNTHETIC_TOKEN}" }}]\n'.encode(),
    ),
    (
        "app/config/provider.yaml",
        b'access_token:\n  metadata:\n    entries:\n      - literalValue: "${INTEGRATION_API_TOKEN}"\n',
        f'access_token:\n  metadata:\n    entries:\n      - literalValue: "{SYNTHETIC_TOKEN}"\n'.encode(),
    ),
    (
        "app/brand/assets/xmp.png",
        _png_nested_xmp_meta("${INTEGRATION_API_TOKEN}"),
        _png_nested_xmp_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/config/provider.xml",
        _xml_nested_meta("${INTEGRATION_API_TOKEN}"),
        _xml_nested_meta(SYNTHETIC_TOKEN),
    ),
    (
        "app/brand/theme_config.json",
        json.dumps({"identity": {"name": "Source export"},
                    "url": _query_url("${INTEGRATION_API_TOKEN}", "access_token")}).encode(),
        json.dumps({"identity": {"name": "Source export"},
                    "url": _query_url(SYNTHETIC_TOKEN, "access_token")}).encode(),
    ),
    (
        "app/ui/provider.js",
        f'export const providerUrl = "{_query_url("${INTEGRATION_API_TOKEN}", "api_key")}";\n'.encode(),
        f'export const providerUrl = "{_query_url(SYNTHETIC_TOKEN, "api_key")}";\n'.encode(),
    ),
    (
        "app/config/provider.json",
        json.dumps({"url": _query_url("${INTEGRATION_API_TOKEN}", "api_key")}).encode(),
        json.dumps({"url": _query_url(SYNTHETIC_TOKEN, "api_key")}).encode(),
    ),
])
def test_delivery_verification_rejects_secret_source_even_with_matching_inventory_hashes(
    export_input, name, safe, unsafe,
):
    workspace, spec, output = export_input
    _write(workspace, name, safe)
    result = delivery.materialize_android_workspace(workspace, spec, output)
    manifest_path = Path(result["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    exported_root = Path(result["workspace_dir"])
    _write(exported_root, name, unsafe)
    for inventory in (manifest["source_files"], manifest["files"]):
        for entry in inventory:
            if entry["path"] == name:
                entry.update(sha256=hashlib.sha256(unsafe).hexdigest(), size_bytes=len(unsafe))
    source_archive = build_deterministic_archive(
        ArchiveEntry(path=entry["path"], content=(exported_root / entry["path"]).read_bytes())
        for entry in manifest["source_files"]
    )
    manifest["source_digest"] = archive_digest(source_archive)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(delivery.AndroidDeliveryError) as caught:
        delivery.verify_android_delivery(Path(result["mobile_dir"]))

    assert SYNTHETIC_TOKEN not in str(caught.value)
    assert "manifest differs" not in str(caught.value)
