"""Private record evidence and atomic result delivery for a host-admitted workflow."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, model_validator

from .adapter import PersistenceScopeError
from .request_scope import _active_scope

if TYPE_CHECKING:
    from .mongo import MongoPersistenceCollection, MongoPersistenceContext

_MARKER = "_mz_workflow_result"
_IDENTITY = frozenset({"_id", "id", "app_id", "user_id", "tenant_id", "workspace_id", "created_at", "updated_at", _MARKER})
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


class ResultRecordSubject(BaseModel):
    """Immutable, JSON-storable subject for server-owned session metadata."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    app_id: str
    module_id: str
    source_action: str
    collection: str
    database_name: str
    physical_collection: str
    native_id_kind: Literal["string", "object_id"]
    native_id: str
    record_key: str
    record_id: str
    owner_field: str
    user_id: str
    tenant_id: str | None
    workspace_id: str | None
    row_nonce: str

    @model_validator(mode="after")
    def validate_identities(self) -> ResultRecordSubject:
        if (
            not self.native_id or not self.record_id or not self.user_id
            or not re.fullmatch(r"[0-9a-f]{32}", self.row_nonce)
            or (self.native_id_kind == "object_id" and not ObjectId.is_valid(self.native_id))
            or (self.record_key != "_id" and not _FIELD.fullmatch(self.record_key))
            or (self.record_key == "_id" and self.record_id != self.native_id)
        ):
            raise ValueError("Invalid stored record identity")
        return self


def _upsert_seeds_marker(value: Any) -> bool:
    # Mongo can synthesize an upsert document from equality predicates in the filter.
    if isinstance(value, Mapping):
        return any(
            str(key).split(".", 1)[0] == _MARKER or _upsert_seeds_marker(item)
            for key, item in value.items()
        )
    return isinstance(value, (list, tuple)) and any(_upsert_seeds_marker(item) for item in value)


async def _insert_with_evidence(collection: MongoPersistenceCollection, document: dict[str, Any]) -> Any:
    scope = _active_scope(collection._app_id)
    if scope is None or scope.result_record_key is None:
        return await collection._collection.insert_one(document)
    if scope.subject_issued:
        raise PersistenceScopeError("An issued result subject requires exactly one source insert")
    scope.insert_attempts += 1
    policy = collection._ownership
    principal = scope.principal
    collection_principal = collection._principal() if callable(collection._principal) else collection._principal
    key = scope.result_record_key
    if (
        scope.insert_attempts != 1 or principal is None or principal.source != "authenticated" or principal != collection_principal
        or not scope.action_id or not scope.module_id or scope.module_id != collection._module_id
        or not collection._logical_collection or policy is None or policy.tenancy != "per_user"
        or (key != "_id" and not _FIELD.fullmatch(key))
        or key in {"app_id", "user_id", "tenant_id", "workspace_id", policy.owner_field}
        or document.get(policy.owner_field) != principal.user_id or document.get("user_id") != principal.user_id
        or document.get("tenant_id") != principal.tenant_id or document.get("workspace_id") != principal.workspace_id
    ):
        return await collection._collection.insert_one(document)

    nonce = uuid4().hex
    document[_MARKER] = {"origin": nonce}
    result = await collection._collection.insert_one(document)
    if getattr(result, "acknowledged", None) is not True or not scope.active:
        return result
    native_id = getattr(result, "inserted_id", None)
    record_id = str(native_id) if key == "_id" else document.get(key)
    if type(native_id) not in (str, ObjectId) or not native_id or type(record_id) is not str or not record_id:
        return result
    scope.inserted_subject = ResultRecordSubject(
        app_id=scope.app_id, module_id=scope.module_id, source_action=scope.action_id,
        collection=collection._logical_collection, physical_collection=collection._collection.name,
        database_name=collection._collection.database.name, native_id=str(native_id),
        native_id_kind="object_id" if isinstance(native_id, ObjectId) else "string",
        record_key=key, record_id=record_id, owner_field=policy.owner_field, user_id=principal.user_id,
        tenant_id=principal.tenant_id, workspace_id=principal.workspace_id, row_nonce=nonce,
    )
    return result


def capture_result_subject(
    *, app_id: str, module_id: str, action_id: str, collection: str, record_key: str, record_id: str,
) -> ResultRecordSubject:
    scope = _active_scope(app_id)
    subject = scope.inserted_subject if scope is not None else None
    if (
        scope is None or scope.insert_attempts != 1 or subject is None
        or (subject.module_id, subject.source_action, subject.collection, subject.record_key, subject.record_id)
        != (module_id, action_id, collection, record_key, record_id)
    ):
        raise PersistenceScopeError("Event does not identify the acknowledged source insert")
    scope.subject_issued = True
    return subject


async def commit_workflow_result(
    *, persistence: MongoPersistenceContext, subject: ResultRecordSubject, module_id: str,
    action_id: str, collection: str, invocation_id: str, allowed_fields: Sequence[str],
    values: Mapping[str, Any],
) -> Literal["applied", "already_applied"]:
    """Apply a host-validated result and its replay receipt in one matched update."""
    principal = persistence.principal
    if (
        principal is None or principal.source != "authenticated"
        or (persistence.app_id, principal.user_id, principal.tenant_id, principal.workspace_id,
            persistence.database_name, module_id, collection)
        != (subject.app_id, subject.user_id, subject.tenant_id, subject.workspace_id,
            subject.database_name, subject.module_id, subject.collection)
        or not action_id or not invocation_id
    ):
        raise PersistenceScopeError("Workflow result scope differs from the source insert")
    target = persistence.collection(module_id, collection)
    policy = target._ownership
    if (target._collection.name != subject.physical_collection or policy is None or policy.tenancy != "per_user"
            or policy.owner_field != subject.owner_field):
        raise PersistenceScopeError("Workflow result target no longer has its original ownership contract")
    if not target._collection.write_concern.acknowledged:
        raise PersistenceScopeError("Workflow result requires acknowledged storage")
    try:
        encoded = json.dumps(dict(values), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PersistenceScopeError("Workflow result must contain JSON values") from exc
    result_values = json.loads(encoded)
    fields = set(allowed_fields)
    if (
        not fields or len(fields) != len(allowed_fields) or set(result_values) != fields
        or fields.intersection(_IDENTITY | {subject.owner_field, subject.record_key})
        or any(type(field) is not str or not _FIELD.fullmatch(field) for field in fields)
    ):
        raise PersistenceScopeError("Workflow result must set exactly the declared mutable fields")
    receipt = {"action_id": action_id, "invocation_id": invocation_id, "digest": hashlib.sha256(encoded.encode()).hexdigest()}
    native_id = ObjectId(subject.native_id) if subject.native_id_kind == "object_id" else subject.native_id
    identities = {
        "_id": native_id, subject.record_key: native_id if subject.record_key == "_id" else subject.record_id,
        "user_id": subject.user_id, f"{_MARKER}.origin": subject.row_nonce,
        "tenant_id": subject.tenant_id if subject.tenant_id is not None else {"$exists": False},
        "workspace_id": subject.workspace_id if subject.workspace_id is not None else {"$exists": False},
    }
    query = target._scoped_query({**identities, "$nor": [{field: {"$type": "array"}} for field in identities]})
    receipt_path = f"{_MARKER}.receipt"
    update = {"$set": {**result_values, receipt_path: receipt}}
    result = await target._collection.update_one(
        {"$and": [query, {receipt_path: {"$exists": False}}]}, update, upsert=False, **target._options,
    )
    if result.acknowledged and result.matched_count == 1:
        return "applied"
    if result.acknowledged and await target._collection.find_one(
        {"$and": [query, {receipt_path: receipt}]}, {"_id": 1}, **target._options,
    ):
        return "already_applied"
    raise PersistenceScopeError("Workflow result subject is unavailable or already consumed")
