"""In-memory stand-ins for connected-accounts tests: Mongo collections, the vault, and Google.

Only the provider's HTTP endpoints, the database and the secret store are
faked; the connected-accounts service, store, routes and runtime are real.
"""

from __future__ import annotations

import base64
import copy
import json
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from mozaiksai.core.connected_accounts.store import ConnectedAccountStore

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------

def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
            for op, value in expected.items():
                if op == "$gt" and not (actual is not None and actual > value):
                    return False
                if op == "$in" and actual not in value:
                    return False
                if op == "$ne" and actual == value:
                    return False
        elif actual != expected:
            return False
    return True


@dataclass
class _Result:
    deleted_count: int = 0
    matched_count: int = 0
    modified_count: int = 0
    upserted_id: Any = None


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows if length is None else self._rows[:length]


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.documents: list[dict[str, Any]] = []
        self.indexes: list[dict[str, Any]] = []

    async def create_index(self, keys: Any, **options: Any) -> str:
        self.indexes.append({"keys": keys, **options})
        return str(options.get("name") or keys)

    async def insert_one(self, document: dict[str, Any]) -> _Result:
        self.documents.append(copy.deepcopy(document))
        return _Result()

    async def find_one(self, query: dict[str, Any], projection: Any = None) -> dict[str, Any] | None:
        for document in self.documents:
            if _matches(document, query):
                return copy.deepcopy(document)
        return None

    def find(self, query: dict[str, Any], projection: Any = None) -> _Cursor:
        return _Cursor([copy.deepcopy(document) for document in self.documents if _matches(document, query)])

    @staticmethod
    def _apply(document: dict[str, Any], update: dict[str, Any], *, inserting: bool) -> None:
        document.update(copy.deepcopy(update.get("$set") or {}))
        if inserting:
            document.update(copy.deepcopy(update.get("$setOnInsert") or {}))
        for key in update.get("$unset") or {}:
            document.pop(key, None)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> _Result:
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update, inserting=False)
                return _Result(matched_count=1, modified_count=1)
        if upsert:
            document = {key: value for key, value in query.items() if not isinstance(value, dict)}
            self._apply(document, update, inserting=True)
            self.documents.append(document)
            return _Result(upserted_id=len(self.documents))
        return _Result()

    async def find_one_and_update(self, query: dict[str, Any], update: dict[str, Any], **_: Any) -> dict[str, Any] | None:
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update, inserting=False)
                return copy.deepcopy(document)
        return None

    async def delete_one(self, query: dict[str, Any]) -> _Result:
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                del self.documents[index]
                return _Result(deleted_count=1)
        return _Result()

    async def delete_many(self, query: dict[str, Any]) -> _Result:
        before = len(self.documents)
        self.documents = [document for document in self.documents if not _matches(document, query)]
        return _Result(deleted_count=before - len(self.documents))


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))

    async def collection(self, name: str) -> FakeCollection:
        return self[name]

    def store(self) -> ConnectedAccountStore:
        return ConnectedAccountStore(collection_factory=self.collection)


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class FakeVault:
    def __init__(self) -> None:
        self.secrets: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.fail_writes = False
        self.fail_reads = False

    async def describe(self) -> dict[str, Any]:
        return {"provider": "fake", "configured": True}

    async def store_secret(
        self, *, scope: str, scope_id: str, service: str, secret_value: str,
        display_name: str | None = None, ttl_days: int = 30,
    ) -> dict[str, Any]:
        if self.fail_writes:
            return {"success": False, "error": "write refused"}
        expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
        self.secrets[(scope, scope_id, service)] = {"value": secret_value, "expires_at": expires_at.isoformat()}
        return {"success": True, "expires_at": expires_at.isoformat()}

    async def get_secret(self, *, scope: str, scope_id: str, service: str) -> dict[str, Any]:
        if self.fail_reads:
            return {"success": False, "status": "error", "secret_value": None}
        found = self.secrets.get((scope, scope_id, service))
        if found is None:
            return {"success": False, "status": "not_found", "secret_value": None}
        return {"success": True, "status": "found", "secret_value": found["value"], "expires_at": found["expires_at"]}

    async def delete_secret(self, *, scope: str, scope_id: str, service: str) -> dict[str, Any]:
        removed = self.secrets.pop((scope, scope_id, service), None)
        return {"success": removed is not None}

    def refresh_tokens(self) -> list[str]:
        return [json.loads(entry["value"])["refresh_token"] for entry in self.secrets.values()]


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

def _id_token(claims: dict[str, Any]) -> str:
    def encode(part: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


@dataclass(eq=False)
class _Grant:
    """One user's grant to the app's client. Like Google's, it is shared by every
    refresh token issued for it, and revoking any of its tokens ends all of it."""

    user: str
    scopes: set[str]
    revoked: bool = False


@dataclass
class FakeGoogle:
    """Google's token, revoke, Calendar and Gmail endpoints, keyed by authorization code.

    A test approves a consent with :meth:`approve`, which returns the code the
    provider would put on the redirect. Tokens are opaque strings that name the
    grant and the scopes they carry, so API handlers can check them. As with
    Google, a user has one grant per client: a later consent extends it
    (incremental authorization) and issues another refresh token for it.
    """

    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    grants: dict[str, _Grant] = field(default_factory=dict)  # refresh token -> grant (several tokens, one grant)
    access_tokens: dict[str, tuple[_Grant, frozenset[str]]] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    api_requests: list[httpx.Request] = field(default_factory=list)
    sent_messages: list[str] = field(default_factory=list)
    issue_refresh_token: bool = True
    reject_access_tokens: set[str] = field(default_factory=set)
    _counter: int = 0

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def active_grant(self, user: str) -> _Grant | None:
        return next((grant for grant in self.grants.values() if grant.user == user and not grant.revoked), None)

    def unique_grants(self) -> list[_Grant]:
        return list({id(grant): grant for grant in self.grants.values()}.values())

    def approve(self, authorize_url: str, *, user: str = "person", granted: set[str] | None = None) -> tuple[str, str]:
        """Consent on *authorize_url*; returns (code, state). *granted* defaults to every requested scope."""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url).query)
        requested = set(query["scope"][0].split())
        scopes = requested if granted is None else set(granted)
        existing = self.active_grant(user)
        if existing is not None and query.get("include_granted_scopes") == ["true"]:
            # Incremental authorization: the new tokens also cover what this user granted before.
            scopes |= existing.scopes
        code = self._next("code")
        self.codes[code] = {
            "user": user,
            "scopes": scopes,
            "challenge": query["code_challenge"][0],
            "redirect_uri": query["redirect_uri"][0],
            "client_id": query["client_id"][0],
        }
        return code, query["state"][0]

    def _issue(self, grant: _Grant, scopes: frozenset[str], *, refresh_token: str | None = None) -> dict[str, Any]:
        access_token = self._next(f"access-{grant.user}")
        self.access_tokens[access_token] = (grant, scopes)
        body: dict[str, Any] = {"access_token": access_token, "expires_in": 3600, "scope": " ".join(sorted(scopes))}
        if refresh_token:
            body["refresh_token"] = refresh_token
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/token":
            form = {key: values[0] for key, values in urllib.parse.parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                entry = self.codes.pop(form["code"], None)
                if entry is None or entry["redirect_uri"] != form.get("redirect_uri"):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                import hashlib
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"].encode()).digest()
                ).decode().rstrip("=")
                if challenge != entry["challenge"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                grant = self.active_grant(entry["user"])
                if grant is None:
                    grant = _Grant(entry["user"], set())
                grant.scopes |= set(entry["scopes"])
                refresh_token = self._next(f"refresh-{entry['user']}")
                self.grants[refresh_token] = grant
                body = self._issue(
                    grant, frozenset(entry["scopes"]), refresh_token=refresh_token if self.issue_refresh_token else None,
                )
                body["id_token"] = _id_token({"sub": f"sub-{grant.user}", "email": f"{grant.user}@gmail.test", "email_verified": True})
                return httpx.Response(200, json=body)
            if form["grant_type"] == "refresh_token":
                grant = self.grants.get(form["refresh_token"])
                if grant is None or grant.revoked:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                requested = set(form["scope"].split()) if form.get("scope") else set(grant.scopes)
                if not requested <= grant.scopes:
                    return httpx.Response(400, json={"error": "invalid_scope"})
                return httpx.Response(200, json=self._issue(grant, frozenset(requested)))
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        if path == "/revoke":
            token = urllib.parse.parse_qs(request.content.decode())["token"][0]
            self.revoked.append(token)
            grant = self.grants.get(token) or (self.access_tokens.get(token) or (None,))[0]
            if grant is not None:
                grant.revoked = True
            return httpx.Response(200)
        return self._api(request)

    def _authorized(self, request: httpx.Request, scope: str) -> _Grant | httpx.Response:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        found = self.access_tokens.get(token)
        if found is None or token in self.reject_access_tokens or found[0].revoked:
            return httpx.Response(401, json={"error": {"code": 401}})
        if scope not in found[1]:
            return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED"}})
        return found[0]

    def _api(self, request: httpx.Request) -> httpx.Response:
        self.api_requests.append(request)
        path = request.url.path
        if path == "/calendar/v3/calendars/primary/events":
            grant = self._authorized(request, CALENDAR_SCOPE)
            if isinstance(grant, httpx.Response):
                return grant
            if request.method == "GET":
                return httpx.Response(200, json={"items": [{
                    "id": "evt-1",
                    "summary": f"{grant.user}'s standup",
                    "start": {"dateTime": "2026-11-09T09:00:00+00:00"},
                    "end": {"dateTime": "2026-11-09T09:15:00+00:00"},
                    "attendees": [{"email": "someone@example.test"}],
                    "htmlLink": "https://calendar.google.test/evt-1",
                    "status": "confirmed",
                }]})
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": "evt-new", **body, "status": "confirmed"})
        if path == "/gmail/v1/users/me/messages/send":
            grant = self._authorized(request, GMAIL_SEND_SCOPE)
            if isinstance(grant, httpx.Response):
                return grant
            raw = json.loads(request.content)["raw"]
            self.sent_messages.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
            return httpx.Response(200, json={"id": "msg-1", "threadId": "thread-1"})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), base_url="https://google.test")


# ---------------------------------------------------------------------------
# A plugin app on disk
# ---------------------------------------------------------------------------

def write_plugin_module(app_root: Path, module_id: str, connections: list[dict[str, Any]]) -> Path:
    module_dir = app_root / "modules" / module_id
    (module_dir / "contracts").mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(f"schema_version: mozaiks.module.v1\nmodule:\n  id: {module_id}\n", encoding="utf-8")
    import yaml

    (module_dir / "contracts" / "account_connections.yaml").write_text(
        yaml.safe_dump({"schema_version": "mozaiks.account_connections.v1", "connections": connections}),
        encoding="utf-8",
    )
    return module_dir


CALENDAR_CONNECTION = {
    "id": "google_calendar",
    "provider": "google",
    "label": "Google Calendar",
    "description": "See and add events on your Google Calendar.",
    "scopes": [CALENDAR_SCOPE],
}
GMAIL_CONNECTION = {
    "id": "gmail_send",
    "provider": "google",
    "label": "Gmail",
    "description": "Send email from your Gmail address.",
    "scopes": [GMAIL_SEND_SCOPE],
}


def plugin_app(app_root: Path) -> Path:
    write_plugin_module(app_root, "google_calendar", [CALENDAR_CONNECTION])
    write_plugin_module(app_root, "gmail", [GMAIL_CONNECTION])
    return app_root


def iter_cookie_values(set_cookie_headers: list[str], name: str) -> Iterator[str]:
    for header in set_cookie_headers:
        key, _, rest = header.partition("=")
        if key.strip() == name:
            yield rest.split(";", 1)[0]
