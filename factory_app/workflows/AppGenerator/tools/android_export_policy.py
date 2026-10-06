"""Admission policy for app and workflow bytes in portable Android deliveries.

Exports are source distributions. Runtime credentials belong in the configured
secret backend; these checks reject recognizable literals, not arbitrary hidden
or obfuscated secrets. No submitted values appear in diagnostics.
"""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import yaml

from mozaiksai.core.secrets.contract import is_secret_contract_path, validate_secret_contract_text

from .deployment_contract import validate_deployment_secrets

ENV_EXAMPLES = frozenset({".env.example", ".env.staging.example", ".env.production.example"})
BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".mp3", ".mp4"})
_BRAND_ASSETS = BINARY_SUFFIXES.difference({".pdf", ".mp3", ".mp4"}) | {".svg"}
_PRIVATE_SUFFIXES = {".key", ".pem", ".jks", ".keystore", ".p12", ".pfx", ".p8"}
_PRIVATE_PARTS = {".aws", ".ssh", ".azure", ".kube", ".docker", ".idea", ".vscode"}
_PRIVATE_NAMES = {".npmrc", ".pypirc", ".netrc", ".git-credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "local.properties"}
_PRIVATE_STEMS = {"credential", "credentials", "developer", "secrets", "service-account", "service_account", "serviceaccount", "local_config"}
_REFERENCE = re.compile(r"(?:\$\{[A-Z][A-Z0-9_]*\}|\$\{\{\s*secrets\.[A-Z][A-Z0-9_]*\s*\}\}|<[A-Za-z][A-Za-z0-9_ -]*>)")
_ASSIGNMENT = re.compile(r'''(?<![\w$-])["']?(?P<key>[A-Za-z_$][\w$-]*)["']?\s*\]?\s*(?::\s*[A-Za-z_][\w.\[\] |]*\s*)?(?:=(?!=|>)|:)\s*''')
_QUOTED = re.compile(r'''^(?P<prefix>[rubfRUBF]{0,2})(?P<quote>["'`])(?P<value>(?:\\.|(?!\2).)*?)\2''', re.DOTALL)
_MARKERS = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|\bAKIA[0-9A-Z]{16}\b")
_URI_USERINFO = re.compile(r'''\b[a-z][a-z0-9+.-]*://(?P<userinfo>[^\s/'"<>@?#]+)@''', re.IGNORECASE)
_UNQUOTED_CONFIG = {".ini", ".conf", ".cfg", ".toml", ".properties"}


def _credential_key(value: str) -> bool:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower().replace("-", "_")
    if value.endswith(("_env", "_ref", "_name", "_names", "_type", "_url", "_uri")):
        return value in {"mongo_uri", "mongodb_uri", "database_url", "database_uri", "redis_url"}
    value = value.removesuffix("_value").removesuffix("_hash")
    return value.split("_")[-1] in {"token", "secret", "password", "passwd", "pwd", "credential", "credentials", "authorization"} or value in {
        "apikey", "api_key", "privatekey", "private_key", "connectionstring", "connection_string", "dsn",
    } or value.endswith(("_api_key", "_private_key", "_secret_key", "_connection_string"))


def _literal_secret(value: object) -> bool:
    if value is None or isinstance(value, bool) or value == "":
        return False
    return not (isinstance(value, str) and _REFERENCE.fullmatch(value.strip()))


def _structured_credentials(
    value: object, *, credential: bool = False, ancestors: frozenset[int] = frozenset(),
    visited: set[tuple[int, bool]] | None = None,
) -> bool:
    if not isinstance(value, (dict, list)):
        return credential and _literal_secret(value)
    if id(value) in ancestors:
        raise ValueError("Recursive configuration is not supported in Android delivery")
    if visited is None:
        visited = set()
    identity = (id(value), credential)
    if identity in visited:
        return False
    visited.add(identity)
    ancestors = ancestors | {id(value)}
    if isinstance(value, list):
        return any(_structured_credentials(item, credential=credential, ancestors=ancestors, visited=visited) for item in value)
    for key, item in value.items():
        sensitive = _credential_key(str(key)) or (credential and key in {"value", "default"})
        if _structured_credentials(item, credential=sensitive, ancestors=ancestors, visited=visited):
            return True
    return False


def _python_literals(node: ast.AST):
    """Only literal assignments and literal credential defaults, not name lookups."""
    if isinstance(node, ast.Constant):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        # A runtime interpolation is a lookup; a literal f-string is still a literal.
        if all(isinstance(part, ast.Constant) for part in node.values):
            yield "".join(str(part.value) for part in node.values if isinstance(part, ast.Constant))
    elif isinstance(node, (ast.BoolOp, ast.IfExp)):
        for child in (node.values if isinstance(node, ast.BoolOp) else (node.body, node.orelse)):
            yield from _python_literals(child)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "getenv"}:
        for child in node.args[1:]:
            yield from _python_literals(child)
        for keyword in node.keywords:
            if keyword.arg == "default":
                yield from _python_literals(keyword.value)


def _python_credentials(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # General literal scanning still applies to unsupported source syntax.
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            keys = []
            for target in targets:
                if isinstance(target, ast.Name):
                    keys.append(target.id)
                elif isinstance(target, ast.Attribute):
                    keys.append(target.attr)
                elif isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    keys.append(str(target.slice.value))
            if node.value is not None and any(_credential_key(key) for key in keys):
                if any(_literal_secret(value) for value in _python_literals(node.value)):
                    return True
    return False


def _text_credentials(text: str, *, unquoted_config: bool = False) -> bool:
    if _MARKERS.search(text):
        return True
    for match in _URI_USERINFO.finditer(text):
        # URL userinfo is a credential even when its enclosing key is just "url".
        password = match["userinfo"].partition(":")[2] if ":" in match["userinfo"] else match["userinfo"]
        if _literal_secret(password):
            return True
    for match in _ASSIGNMENT.finditer(text):
        if not _credential_key(match["key"]):
            continue
        start = match.end()
        while start < len(text) and (text[start].isspace() or text[start] == "("):
            start += 1
        end = text.find("\n", start)
        expression = text[start:end if end >= 0 else len(text)].strip()
        literal = _QUOTED.match(expression)
        if literal and _quoted_secret(literal) and not _runtime_auth_prefix(literal, expression):
            return True
        if unquoted_config and not literal:
            value = re.split(r"\s+[;#]", expression, maxsplit=1)[0].strip()
            if _literal_secret(value):
                return True
        # JavaScript environment lookups may contain hardcoded fallback values.
        for fallback in re.finditer(r"(?:\|\||\?\?)\s*", expression):
            literal = _QUOTED.match(expression[fallback.end():])
            if literal and _quoted_secret(literal):
                return True
    return False


def _quoted_secret(literal: re.Match[str]) -> bool:
    value = literal["value"]
    if literal["quote"] == "`" and re.search(r"\$\{[^{}]+\}", value):
        return False
    if "f" in literal["prefix"].lower() and re.search(r"\{[^{}]+\}", value):
        return False
    return _literal_secret(value)


def _runtime_auth_prefix(literal: re.Match[str], expression: str) -> bool:
    # A scheme-only prefix contains no credential. Preserve ordinary runtime
    # header assembly, but still reject concatenation with a literal token.
    tail = expression[literal.end():].lstrip()
    return literal["value"].lower() in {"bearer ", "basic "} and bool(
        re.match(r"\+\s*[A-Za-z_$][\w$]*(?:[.\[(;)}\s]|$)", tail)
    )


def _valid_binary(suffix: str, raw: bytes) -> bool:
    signatures = {
        ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".gif": (b"GIF87a", b"GIF89a"), ".ico": (b"\x00\x00\x01\x00",), ".woff": (b"wOFF",),
        ".woff2": (b"wOF2",), ".ttf": (b"\x00\x01\x00\x00", b"true"), ".pdf": (b"%PDF-",),
        ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    }
    if suffix == ".webp":
        return raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    if suffix == ".mp4":
        return raw[4:8] == b"ftyp"
    return raw.startswith(signatures[suffix])


def validate_android_export_file(name: str, raw: bytes) -> None:
    """Apply the same admission rule to every captured app or workflow file."""
    path = PurePosixPath(name.lower())
    secret_contract = is_secret_contract_path(name)
    if (
        (path.name.startswith(".env") and path.name not in ENV_EXAMPLES)
        or path.suffix in _PRIVATE_SUFFIXES or _PRIVATE_PARTS.intersection(path.parts)
        or path.name in _PRIVATE_NAMES
        or (path.stem in _PRIVATE_STEMS and not secret_contract)
    ):
        raise ValueError(f"Private environment, credential or developer files must not be exported: {name}")
    if path.parts[:2] == ("app", "brand") and not (
        name == "app/brand/theme_config.json" or path.suffix in _BRAND_ASSETS
    ):
        raise ValueError(f"Only theme_config.json and public image/font assets may be exported from app/brand: {name}")
    if path.suffix in BINARY_SUFFIXES:
        if not _valid_binary(path.suffix, raw):
            raise ValueError(f"Public asset content does not match its declared type: {name}")
        # Do not let an image suffix hide recognizable plaintext credentials.
        text = raw.decode("utf-8", errors="replace")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError(f"Unsupported binary source: {name}") from None
        if "\x00" in text:
            raise ValueError(f"Unsupported binary source: {name}")
    forbidden = _text_credentials(text, unquoted_config=path.suffix in _UNQUOTED_CONFIG)
    if path.suffix == ".svg":
        try:
            if ET.fromstring(text).tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
                raise ValueError("Not an SVG root")
        except (ET.ParseError, ValueError):
            raise ValueError(f"Public asset content does not match its declared type: {name}") from None
    if path.suffix == ".py":
        forbidden = forbidden or _python_credentials(text)
    if secret_contract:
        validate_secret_contract_text(text)
    if path.suffix in {".json", ".yaml", ".yml"}:
        try:
            value = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
            forbidden = forbidden or _structured_credentials(value)
        except (ValueError, yaml.YAMLError, RecursionError):
            raise ValueError(f"Invalid export configuration: {name}") from None
    if path.name in ENV_EXAMPLES:
        # Preserve the existing deployment env-example obligations (including
        # provider-specific keys) alongside the shared token-literal checks.
        forbidden = forbidden or bool(validate_deployment_secrets({path.name: text}))
        for line in text.splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and _credential_key(key.strip()) and _literal_secret(value.strip().strip("\"'")):
                forbidden = True
    if forbidden:
        raise ValueError(f"Literal secret variable or credential in {name}; use secret names and runtime references")
