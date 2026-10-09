"""Admission policy for app and workflow bytes in portable Android deliveries.

Exports are source distributions. Runtime credentials belong in the configured
secret backend; these checks reject recognizable literals, not arbitrary hidden
or obfuscated secrets. No submitted values appear in diagnostics.
"""

from __future__ import annotations

import ast
import json
import plistlib
import re
import tomllib
import xml.etree.ElementTree as ET
import zlib
from html import unescape
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote
from xml.parsers.expat import ExpatError

import yaml

from mozaiksai.core.secrets.contract import is_secret_contract_path, validate_secret_contract_text

from .deployment_contract import validate_deployment_secrets

ENV_EXAMPLES = frozenset({".env.example", ".env.staging.example", ".env.production.example"})
BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".mp3", ".mp4"})
_BRAND_ASSETS = BINARY_SUFFIXES.difference({".pdf", ".mp3", ".mp4"}) | {".svg"}
_PRIVATE_SUFFIXES = {".key", ".pem", ".jks", ".keystore", ".p12", ".pfx", ".p8"}
_PRIVATE_PARTS = {".aws", ".ssh", ".azure", ".kube", ".docker", ".idea", ".vscode", ".venv", ".mypy_cache"}
_PRIVATE_NAMES = {".npmrc", ".pypirc", ".netrc", ".git-credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "local.properties"}
_PRIVATE_STEMS = {"credential", "credentials", "developer", "secrets", "service-account", "service_account", "serviceaccount", "local_config"}
_REFERENCE = re.compile(r"(?:\$\{[A-Z][A-Z0-9_]*\}|\$\{\{\s*secrets\.[A-Z][A-Z0-9_]*\s*\}\}|<[A-Za-z][A-Za-z0-9_ -]*>)")
_ASSIGNMENT = re.compile(r'''(?<![\w$-])["']?(?P<key>[A-Za-z_$][\w$-]*)["']?\s*\]?\s*(?::\s*[A-Za-z_][\w.\[\] |]*\s*)?(?:=(?!=|>)|:)\s*''')
_QUOTED = re.compile(r'''^(?P<prefix>[rubfRUBF]{0,2})(?P<quote>["'`])(?P<value>(?:\\.|(?!\2).)*?)\2''', re.DOTALL)
_MARKERS = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|\bghp_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|\bAKIA[0-9A-Z]{16}\b")
_URI_USERINFO = re.compile(r'''\b[a-z][a-z0-9+.-]*://(?P<userinfo>[^\s/'"<>@?#]+)@''', re.IGNORECASE)
_URI_QUERY = re.compile(r'''\b[a-z][a-z0-9+.-]*://[^\s'"`<>?#]*\?(?P<query>[^\s'"`<>#]*)''', re.IGNORECASE)
_DOCUMENTATION_URI = "postgresql://user:pass@host:port/dbname"
_UNQUOTED_CONFIG = {".ini", ".conf", ".cfg", ".toml", ".properties", ".txt"}
_PNG_TEXT_LIMIT = 1024 * 1024
_PNG_TOTAL_TEXT_LIMIT = 2 * _PNG_TEXT_LIMIT
_CREDENTIAL_VALUE_FIELDS = frozenset({"value", "default", "default_value", "secret_value", "data", "content"})
_PUBLIC_CREDENTIAL_METADATA_FIELDS = frozenset({
    "description", "title", "label", "help", "type", "format", "required", "optional",
    "name", "env", "env_name", "secret_name", "secret_ref", "ref",
})
_PUBLIC_CREDENTIAL_LIST_FIELDS = _PUBLIC_CREDENTIAL_METADATA_FIELDS | {"metadata"}


def _normalized_key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _credential_key(value: str) -> bool:
    value = _normalized_key(value)
    if value.endswith(("_env", "_ref", "_name", "_names", "_type", "_url", "_uri")):
        return value in {"mongo_uri", "mongodb_uri", "database_url", "database_uri", "redis_url"}
    for suffix in ("_value", "_hash", "_b64", "_base64", "_hex", "_encoded"):
        value = value.removesuffix(suffix)
    parts = value.split("_")
    return (
        parts[-1] == "key" and bool(set(parts) & {"api", "secret", "private", "access", "signing"})
    ) or parts[-1] in {"token", "secret", "password", "passwd", "pwd", "credential", "credentials", "authorization"} or value in {
        "apikey", "privatekey", "connectionstring", "connection_string", "dsn",
    } or value.endswith("_connection_string") or value in {"auth_header", "authorization_header"}


def _literal_secret(value: object) -> bool:
    if value is None or isinstance(value, bool) or value == "":
        return False
    if isinstance(value, str):
        normalized = value.strip()
        if _REFERENCE.fullmatch(normalized):
            return False
        scheme = re.fullmatch(r"(?:Bearer|Basic)\s+(.+)", normalized, re.IGNORECASE)
        if scheme and _REFERENCE.fullmatch(scheme[1]):
            return False
    return True


def _structured_credentials(
    value: object, *, credential: bool = False, value_payload: bool = False, list_payload: bool = False,
    ancestors: frozenset[int] = frozenset(), visited: set[tuple[int, bool, bool, bool]] | None = None,
) -> bool:
    if not isinstance(value, (dict, list)):
        return (isinstance(value, str) and _uri_query_credentials(value)) or (
            value_payload and _literal_secret(value)
        )
    if id(value) in ancestors:
        raise ValueError("Recursive configuration is not supported in Android delivery")
    if visited is None:
        visited = set()
    identity = (id(value), credential, value_payload, list_payload)
    if identity in visited:
        return False
    visited.add(identity)
    ancestors = ancestors | {id(value)}
    if isinstance(value, list):
        return any(
            _structured_credentials(
                item, credential=credential,
                value_payload=value_payload or (list_payload and not isinstance(item, (dict, list))),
                list_payload=list_payload if isinstance(item, list) else False,
                ancestors=ancestors, visited=visited,
            )
            for item in value
        )
    for key, item in value.items():
        normalized = _normalized_key(str(key))
        key_is_credential = _credential_key(str(key))
        # Keep credential context through arbitrary containers. Only declared
        # descriptive fields and names-only references are public beneath it;
        # an unknown scalar label is not evidence that its value is harmless.
        sensitive = value_payload or (credential and normalized in _CREDENTIAL_VALUE_FIELDS) or (
            (credential and normalized not in _PUBLIC_CREDENTIAL_METADATA_FIELDS or key_is_credential)
            and not isinstance(item, (dict, list))
        )
        if _structured_credentials(
            item, credential=credential or key_is_credential, value_payload=sensitive,
            list_payload=(credential or key_is_credential) and normalized not in _PUBLIC_CREDENTIAL_LIST_FIELDS,
            ancestors=ancestors, visited=visited,
        ):
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


def _python_static_join(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _python_static_join(node.left, bindings)
        right = _python_static_join(node.right, bindings)
        if left is not None and right is not None:
            return left + right
    return None


def _python_credentials(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # General literal scanning still applies to unsupported source syntax.
        return False
    bindings = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
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
                joined = _python_static_join(node.value, bindings)
                if joined is not None and _literal_secret(joined):
                    return True
                if any(_literal_secret(value) for value in _python_literals(node.value)):
                    return True
    return False


def _text_credentials(text: str, *, unquoted_config: bool = False) -> bool:
    if _MARKERS.search(text) or _uri_query_credentials(text):
        return True
    for match in _URI_USERINFO.finditer(text):
        # URL userinfo is a credential even when its enclosing key is just "url".
        # This exact example has a nonnumeric port and a documentation hostname.
        example_end = match.start() + len(_DOCUMENTATION_URI)
        if (
            text[match.start():example_end] == _DOCUMENTATION_URI
            and (example_end == len(text) or text[example_end] in "\"'` )\n\r\t,;")
        ):
            continue
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


def _uri_query_credentials(text: str) -> bool:
    for match in _URI_QUERY.finditer(text):
        try:
            params = parse_qsl(unquote(unescape(match["query"])), keep_blank_values=True, max_num_fields=1024)
        except ValueError:
            return True
        if any(_credential_key(key) and _literal_secret(value) for key, value in params):
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


def _png_text(raw: bytes) -> list[tuple[str, str]]:
    """Read bounded standard PNG text chunks; fail closed on malformed chunks."""
    position = 8
    total_text = 0
    total_decoded = 0
    found_end = False
    metadata = []
    while position + 12 <= len(raw):
        length = int.from_bytes(raw[position:position + 4], "big")
        end = position + 12 + length
        if end > len(raw):
            raise ValueError("Invalid PNG chunk")
        kind = raw[position + 4:position + 8]
        payload = raw[position + 8:end - 4]
        checksum = int.from_bytes(raw[end - 4:end], "big")
        if zlib.crc32(payload, zlib.crc32(kind)) != checksum:
            raise ValueError("Invalid PNG checksum")
        if position == 8 and (kind != b"IHDR" or length != 13):
            raise ValueError("Invalid PNG header")
        if kind in {b"tEXt", b"zTXt", b"iTXt"}:
            total_text += length
            if length > _PNG_TEXT_LIMIT or total_text > _PNG_TOTAL_TEXT_LIMIT:
                raise ValueError("PNG metadata exceeds limit")
            keyword, separator, remainder = payload.partition(b"\0")
            if not separator or not keyword or len(keyword) > 79:
                raise ValueError("Invalid PNG text keyword")
            if kind == b"tEXt":
                value = remainder.decode("latin-1")
            elif kind == b"zTXt":
                if not remainder.startswith(b"\0"):
                    raise ValueError("Invalid compressed PNG text")
                value = _inflate_png_text(remainder[1:]).decode("latin-1")
            else:
                if len(remainder) < 2 or remainder[0] not in (0, 1) or remainder[1] != 0:
                    raise ValueError("Invalid international PNG text")
                compressed = remainder[0] == 1
                language, separator, remainder = remainder[2:].partition(b"\0")
                if not separator or any(byte > 127 for byte in language):
                    raise ValueError("Invalid international PNG language")
                translated, separator, value_bytes = remainder.partition(b"\0")
                if not separator:
                    raise ValueError("Invalid international PNG text")
                if compressed:
                    value_bytes = _inflate_png_text(value_bytes)
                value = value_bytes.decode("utf-8")
                if translated:
                    metadata.append((translated.decode("utf-8"), value))
            total_decoded += len(value)
            if total_decoded > _PNG_TOTAL_TEXT_LIMIT:
                raise ValueError("PNG text exceeds limit")
            metadata.append((keyword.decode("latin-1"), value))
        position = end
        if kind == b"IEND":
            if length or position != len(raw):
                raise ValueError("Invalid PNG end")
            found_end = True
            break
    if not found_end:
        raise ValueError("Missing PNG end")
    return metadata


def _inflate_png_text(raw: bytes) -> bytes:
    inflater = zlib.decompressobj()
    value = inflater.decompress(raw, _PNG_TEXT_LIMIT + 1)
    if len(value) > _PNG_TEXT_LIMIT or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError("Invalid compressed PNG text")
    return value


def _xml_credentials(text: str, *, require_svg: bool = False, nested_depth: int = 0) -> bool:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML entity declarations are not supported")
    root = ET.fromstring(text)
    if require_svg and root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
        raise ValueError("Not an SVG root")
    elements = [(root, False)]
    while elements:
        element, inherited_credential = elements.pop()
        children = list(element)
        for index, child in enumerate(children[:-1]):
            if child.tag.rpartition("}")[2].lower() in {"key", "name"} and _credential_key(
                "".join(child.itertext()).strip()
            ):
                next_child = children[index + 1]
                next_name = next_child.tag.rpartition("}")[2].lower()
                if next_name in {"value", "string", "default"} and _literal_secret(
                    "".join(next_child.itertext()).strip()
                ):
                    return True
        if any(
            _credential_key(key.rpartition("}")[2]) and _literal_secret(value)
            for key, value in element.attrib.items()
        ):
            return True
        attributes = {_normalized_key(key.rpartition("}")[2]): value for key, value in element.attrib.items()}
        declared_key = attributes.get("name") or attributes.get("key")
        element_name = _normalized_key(element.tag.rpartition("}")[2])
        credential_field = _credential_key(element_name) or bool(
            declared_key and _credential_key(declared_key)
        )
        public_field = element_name in _PUBLIC_CREDENTIAL_METADATA_FIELDS or bool(
            declared_key and _normalized_key(declared_key) in _PUBLIC_CREDENTIAL_METADATA_FIELDS
        )
        direct_text = (element.text or "") + "".join(child.tail or "" for child in children)
        if (credential_field or (inherited_credential and not public_field)) and (
            any(
                _literal_secret(attributes.get(key))
                for key in _CREDENTIAL_VALUE_FIELDS
            )
            or _literal_secret(direct_text.strip())
        ):
            return True
        fragment = (element.text or "").strip()
        if nested_depth < 2 and fragment.startswith("<") and ("</" in fragment or fragment.endswith("/>")):
            if _xml_credentials(fragment, nested_depth=nested_depth + 1):
                return True
        elements.extend((child, inherited_credential or credential_field) for child in reversed(children))
    return False


def _png_metadata_credentials(key: str, value: str) -> bool:
    if _credential_key(key) and _literal_secret(value):
        return True
    if _text_credentials(value, unquoted_config=True):
        return True
    if value.lstrip().startswith("<"):
        return _xml_credentials(value)
    return False


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
    if path.suffix in {".svg", ".xml", ".xmp", ".plist"}:
        try:
            forbidden = _xml_credentials(text, require_svg=path.suffix == ".svg") or forbidden
            if path.suffix == ".plist":
                forbidden = _structured_credentials(plistlib.loads(raw)) or forbidden
        except (ET.ParseError, ExpatError, ValueError):
            raise ValueError(f"Public asset content does not match its declared type: {name}") from None
    if path.suffix == ".png":
        try:
            metadata = _png_text(raw)
            forbidden = forbidden or any(
                _png_metadata_credentials(key, value)
                for key, value in metadata
            )
        except (ET.ParseError, UnicodeError, ValueError, zlib.error):
            raise ValueError(f"Public asset content does not match its declared type: {name}") from None
    if path.suffix == ".py":
        forbidden = forbidden or _python_credentials(text)
    if secret_contract:
        validate_secret_contract_text(text)
    if path.suffix in {".json", ".yaml", ".yml", ".toml"}:
        try:
            value = (
                json.loads(text) if path.suffix == ".json"
                else tomllib.loads(text) if path.suffix == ".toml"
                else yaml.safe_load(text)
            )
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
