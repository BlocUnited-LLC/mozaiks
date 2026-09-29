"""Enforce declared platform and selected-pack ownership before design approval."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory_app.workflows._shared.hook_utils import workflow_context_path
from factory_app.workflows.AppGenerator.tools.app_backend_admin_contract import (
    APP_BACKEND_ADMIN_BUILTIN_PANELS,
    APP_BACKEND_ADMIN_SECTION_IDS,
)
from mozaiksai.core.runtime.app.auth_contract import AuthRoutes, validate_app_auth_contract
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    CANONICAL_FIELD_TYPES,
    STRUCTURED_FIELD_TYPES,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    CANONICAL_WRITE_OPERATIONS,
    managed_pack_contracts,
)

_MODULE_ACTION_ENDPOINT = re.compile(r"^/api/modules/([A-Za-z0-9_.-]+)/[A-Za-z0-9_.-]+$")
# Every canonical scalar is a bounded value; object/array state is bounded only
# where a rule declares that shape for the claim (structured_state_field_types).
_BOUNDED_FIELD_TYPES = frozenset(CANONICAL_FIELD_TYPES) - STRUCTURED_FIELD_TYPES
# Row keys any collection may carry; they say nothing about whose state it holds.
_BOOKKEEPING_FIELDS = frozenset({"_id", "id", "app_id", "user_id", "created_at", "updated_at"})
# Page primitives that list records through typed `columns`.
_LIST_PRIMITIVES = frozenset({"DataTable", "ResourceTable"})


class UserAdministration(BaseModel):
    """The platform's user administration: a built-in panel of the admin portal.

    It names the panel and the admin page it belongs to, never a route: until
    the panel is served end to end, a generated app must not link to it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    builtin_panel: str
    admin_page: str
    # The identity entities whose records the panel lists (users, not sessions).
    entity_names: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def declared_admin_panel(self) -> Self:
        if self.builtin_panel not in APP_BACKEND_ADMIN_BUILTIN_PANELS:
            raise ValueError(f"builtin_panel must be one of {list(APP_BACKEND_ADMIN_BUILTIN_PANELS)}")
        if self.admin_page not in APP_BACKEND_ADMIN_SECTION_IDS:
            raise ValueError(f"admin_page must be one of {list(APP_BACKEND_ADMIN_SECTION_IDS)}")
        return self


class SurfaceOwnershipRule(BaseModel):
    """Exact contract identifiers, never classification of free-form prose."""

    model_config = ConfigDict(extra="forbid", strict=True)

    owner: str = Field(min_length=1)
    facade_module: str | None = None
    # The platform capability whose own pages a matching surface duplicates.
    # `authentication` pages are served at the generated auth contract's routes.
    platform_capability: Literal["authentication"] | None = None
    # The admin portal panel that lists platform users.
    user_administration: UserAdministration | None = None
    surface_ids: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    collection_names: list[str] = Field(default_factory=list)
    surface_collection_names: list[str] = Field(default_factory=list)
    surface_entity_names: list[str] = Field(default_factory=list)
    surface_action_ids: list[str] = Field(default_factory=list)
    state_field_names: list[str] = Field(default_factory=list)
    identity_evidence_fields: list[str] = Field(default_factory=list)
    # Claims that identify an account: a collection unique on one is an account registry.
    account_key_fields: list[str] = Field(default_factory=list)
    # Operations the platform owns on its identity entities: an action
    # `<verb>_<entity>` or `<entity>_<verb>` (create_user, user_login) is one.
    identity_lifecycle_verbs: list[str] = Field(default_factory=list)
    # The lifecycle verbs that are sign-in: the platform's even where the entity carries app data.
    sign_in_verbs: list[str] = Field(default_factory=list)
    # Events `<entity>_<fact>` of an identity entity (user_created) are platform lifecycle events.
    identity_lifecycle_facts: list[str] = Field(default_factory=list)
    # The lifecycle facts that are sign-in (user_logged_in).
    sign_in_facts: list[str] = Field(default_factory=list)
    # Entities that are platform identity only where their records carry a sign-in credential.
    credential_entity_names: list[str] = Field(default_factory=list)
    sign_in_credential_fields: list[str] = Field(default_factory=list)
    # Credentials that make a user record its user's account: one row per user (tokens are not).
    account_credential_fields: list[str] = Field(default_factory=list)
    structured_state_field_types: dict[str, list[str]] = Field(default_factory=dict)
    # App action ids a facade action already serves (subscribe_user -> start_subscription_checkout).
    action_aliases: dict[str, str] = Field(default_factory=dict)
    # Facade reads that serve the rule's state to app pages, and the state fields each serves.
    state_readers: dict[str, list[str]] = Field(default_factory=dict)
    # Names designs give the facade's state on the platform account record
    # (users.subscription_status). While the pack is active they are account state
    # the facade serves; without the pack nothing serves them and they stay app data.
    account_state_field_names: list[str] = Field(default_factory=list)
    # Provider entity names that also name app records (a newsletter's or an alert's
    # Subscription). A surface matched only through one of them may stay app-owned by
    # naming its records for what they are; other provider state (token wallets) may not.
    homonym_entity_names: list[str] = Field(default_factory=list)

    @property
    def reserved_action_ids(self) -> list[str]:
        """Actions a surface the rule already matches may declare: its identifiers and the aliases its facade serves.

        Aliases never match a surface by themselves: an app's own subscribe_user
        (newsletter, price alerts) is not billing.
        """
        return [*self.action_ids, *self.action_aliases]

    @model_validator(mode="after")
    def declared_structured_fields(self) -> Self:
        if _identifiers(self.structured_state_field_types) - _identifiers(self.state_field_names):
            raise ValueError("structured_state_field_types must reference declared state_field_names")
        if {kind for kinds in self.structured_state_field_types.values() for kind in kinds} - STRUCTURED_FIELD_TYPES:
            raise ValueError(f"structured_state_field_types must use {sorted(STRUCTURED_FIELD_TYPES)}")
        if _identifiers(self.identity_evidence_fields) - _identifiers(self.state_field_names):
            raise ValueError("identity_evidence_fields must reference declared state_field_names")
        if _identifiers(self.account_key_fields) - _identifiers(self.state_field_names):
            raise ValueError("account_key_fields must reference declared state_field_names")
        if self.platform_capability and self.facade_module:
            raise ValueError("platform_capability describes platform ownership, not a managed facade")
        if (self.user_administration or self.identity_lifecycle_verbs) and self.platform_capability != "authentication":
            raise ValueError(
                "user_administration and identity_lifecycle_verbs belong to the platform authentication capability"
            )
        if self.identity_lifecycle_verbs and set(CANONICAL_WRITE_OPERATIONS) - _identifiers(self.identity_lifecycle_verbs):
            raise ValueError(f"identity_lifecycle_verbs must include the canonical writes {list(CANONICAL_WRITE_OPERATIONS)}")
        if self.user_administration and _keys(self.user_administration.entity_names) - _keys(
            [*self.entity_names, *self.surface_entity_names]
        ):
            raise ValueError("user_administration.entity_names must be declared identity entities")
        if (self.action_aliases or self.state_readers) and not self.facade_module:
            raise ValueError("action_aliases and state_readers map onto a managed facade's actions")
        if _identifiers(field for fields in self.state_readers.values() for field in fields) - _identifiers(
            self.state_field_names
        ):
            raise ValueError("state_readers must serve declared state_field_names")
        if (self.account_state_field_names or self.homonym_entity_names) and not self.facade_module:
            raise ValueError("account_state_field_names and homonym_entity_names describe a managed facade")
        if _identifiers(self.homonym_entity_names) - _identifiers(self.entity_names):
            raise ValueError("homonym_entity_names must be declared entity_names")
        if _identifiers(self.account_state_field_names) - _identifiers(self.state_field_names):
            raise ValueError("account_state_field_names must be declared state_field_names")
        if (self.sign_in_verbs or self.identity_lifecycle_facts or self.sign_in_facts) and (
            self.platform_capability != "authentication"
        ):
            raise ValueError("sign-in verbs and lifecycle facts belong to the platform authentication capability")
        if _identifiers(self.sign_in_verbs) - _identifiers(self.identity_lifecycle_verbs):
            raise ValueError("sign_in_verbs must be declared identity_lifecycle_verbs")
        if _identifiers(self.sign_in_facts) - _identifiers(self.identity_lifecycle_facts):
            raise ValueError("sign_in_facts must be declared identity_lifecycle_facts")
        if (self.credential_entity_names or self.sign_in_credential_fields) and (
            self.platform_capability != "authentication"
        ):
            raise ValueError("credential-gated identity entities belong to the platform authentication capability")
        if bool(self.credential_entity_names) != bool(self.sign_in_credential_fields):
            raise ValueError("credential_entity_names and sign_in_credential_fields are declared together")
        if _identifiers(self.sign_in_credential_fields) - _identifiers(self.identity_evidence_fields):
            raise ValueError("sign_in_credential_fields must be declared identity_evidence_fields")
        if _identifiers(self.account_credential_fields) - _identifiers(self.identity_evidence_fields):
            raise ValueError("account_credential_fields must be declared identity_evidence_fields")
        return self


def auth_contract_routes() -> AuthRoutes:
    """The routes where the generated auth contract serves sign-in.

    Generated apps receive this contract from the same template
    (render_auth_scaffold), so a design page duplicating sign-in is replaced by a
    reference to ``login`` rather than by an app-built page.
    """
    template = workflow_context_path("webapp_builder", "templates", "config", "auth.yaml")
    config = yaml.safe_load(template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    return validate_app_auth_contract(config).routes


def _route_key(route: Any) -> str:
    """One route identity for sign-in checks, removal, and redirects: '/auth/' is '/auth'."""
    return str(route or "").strip().rstrip("/") or "/"


def _sign_in_routes(routes: AuthRoutes) -> set[str]:
    """Sign-in routes only; post_login_default is where the app lands afterwards."""
    return {_route_key(route) for route in (routes.login, routes.callback, routes.logout)}


def _typed_form_fields(config_hint: Any) -> set[str]:
    """Field names a Form section declares through typed ``fields[].name`` entries."""
    if not isinstance(config_hint, str) or not config_hint.strip():
        return set()
    try:
        config = json.loads(config_hint)
    except ValueError:
        return set()
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            fields = node.get("fields")
            if isinstance(fields, list):
                names.update(
                    str(field.get("name")).strip().casefold()
                    for field in fields if isinstance(field, dict) and field.get("name")
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    return names


def _is_sign_in_page(page: dict[str, Any], *, sign_in_routes: set[str], credential_fields: set[str]) -> bool:
    """A page the auth contract already serves: one of its routes (or a route
    such as /auth that a contract route nests under), or a form collecting a
    credential field the ownership rule declares as identity evidence."""
    route = _route_key(page.get("route"))
    if route != "/" and any(
        candidate == route or candidate.startswith(route + "/") for candidate in sign_in_routes
    ):
        return True
    return any(
        section.get("primitive") == "Form"
        and _typed_form_fields(section.get("config_hint")) & credential_fields
        for section in page.get("sections") or []
    )


@dataclass(frozen=True)
class _SurfaceClaim:
    """How one design surface relates to the ownership rule it matched."""

    rule: SurfaceOwnershipRule
    facade: dict[str, Any]
    # Matched by its own identity (reserved surface id or facade module), not
    # only through a reserved entity or action it declares.
    name_match: bool
    declared_entities: list[str]
    declared_actions: list[str]
    unknown_entities: list[str]
    unknown_actions: list[str]
    remaining: list[str]
    triggers: list[str]
    split_out: list[str]


def _get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def default_subscription_contract(context: Any) -> dict[str, Any] | None:
    """The same default pack used to complete greenfield subscription pages."""
    if _get(context, "brownfield_build_path") or _get(context, "monetization_enabled") is not True:
        return None
    binding = _get(context, "run_build_binding", {}) or {}
    if binding.get("phase", "genesis") != "genesis":
        return None
    blueprint = _get(context, "concept_blueprint", {}) or {}
    intent = blueprint.get("monetization_intent") or {}
    if intent.get("monetized") is not True or intent.get("subscription_contract_likely") is not True:
        return None
    return dict(yaml.safe_load(workflow_context_path("mozaikspay", "contract.yaml").read_text(encoding="utf-8")))


def _identifiers(values: Any) -> set[str]:
    return {str(value).strip().casefold() for value in values or []}


def _key(name: Any) -> str:
    """One identifier however a declared name is cased: passwordHash, PasswordHash and password_hash."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", str(name or "").strip())
    return re.sub(r"_+", "_", snake).casefold()


def _pascal(name: str) -> str:
    """An identifier as a PascalCase entity name: newsletter_subscriptions -> NewsletterSubscriptions."""
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[_\s]+", _key(name)) if part)


def _keys(values: Any) -> set[str]:
    return {_key(value) for value in values or []}


def _declared_fields(collection: dict[str, Any]) -> list[dict[str, Any]]:
    return [field for field in collection.get("fields") or [] if isinstance(field, dict)]


def _unbounded_fields(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> list[Any]:
    """Claimed fields whose declared type is not the claim's shape."""
    claims = _keys(rule.state_field_names)
    shapes = {_key(name): set(kinds) for name, kinds in rule.structured_state_field_types.items()}
    return [
        field.get("name") for field in _declared_fields(collection)
        if _key(field.get("name")) in claims
        and field.get("type") not in _BOUNDED_FIELD_TYPES
        and field.get("type") not in shapes.get(_key(field.get("name")), set())
    ]


def _carries_sign_in_credential(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """A password or its hash: the app's own sign-in, never a third-party token."""
    return bool(
        _keys(field.get("name") for field in _declared_fields(collection)) & _keys(rule.sign_in_credential_fields)
    )


def _is_platform_entity_record(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """A record of one of the rule's platform identity entities.

    The declared identity entities count by name. A credential-gated entity
    (Account) counts only where the record carries a sign-in credential: a
    CRM, bank or linked social account is app data.
    """
    entity = _key(collection.get("entity"))
    if not entity:
        return False
    if entity in _keys([*rule.entity_names, *rule.surface_entity_names]):
        return True
    return entity in _keys(rule.credential_entity_names) and _carries_sign_in_credential(collection, rule)


def _identity_entities(rule: SurfaceOwnershipRule, collections: list[dict[str, Any]]) -> set[str]:
    """The rule's platform identity entities in this design, credential-gated ones where their records sign in."""
    return _keys([*rule.entity_names, *rule.surface_entity_names]) | {
        _key(collection.get("entity")) for collection in collections
        if _key(collection.get("entity")) in _keys(rule.credential_entity_names)
        and _carries_sign_in_credential(collection, rule)
    }


def _record_keys(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> frozenset[str]:
    """Row keys that say nothing about whose state a collection holds.

    Beside the shared bookkeeping keys, a record of one of the rule's own
    entities carries its generated record id (``account_id`` on an Account),
    unless that name is itself a declared claim (``session_id`` is session state).
    """
    entity = _key(collection.get("entity"))
    record_id = f"{entity}_id"
    if rule.facade_module:
        # A provider record's own id (user_plan_id on user_plans) names the row, not app data.
        name = _key(collection.get("name"))
        singular = name[:-3] + "y" if name.endswith("ies") else name[:-1] if name.endswith("s") else name
        return _BOOKKEEPING_FIELDS | ({record_id, f"{singular}_id"} - {"user_id"})
    if _is_platform_entity_record(collection, rule) and record_id not in _keys(rule.state_field_names):
        return _BOOKKEEPING_FIELDS | {record_id}
    return _BOOKKEEPING_FIELDS


def _is_identity_state(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """Every field is a claim the rule declares, in its bounded shape, beyond its row keys."""
    fields = _keys(field.get("name") for field in _declared_fields(collection))
    keys = _record_keys(collection, rule)
    return (
        bool(fields - keys) and fields - keys <= _keys(rule.state_field_names)
        and not _unbounded_fields(collection, rule)
    )


def _carries_credentials(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    return bool(
        _keys(field.get("name") for field in _declared_fields(collection)) & _keys(rule.identity_evidence_fields)
    )


def _keyed_by_account(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """A unique index on one account-key claim (email identity).

    Bookkeeping keys beside it (app_id, or the owner field the save path
    prefixes to unique indexes on owned collections) scope that uniqueness.
    """
    accounts = _keys(rule.account_key_fields)
    scoping = _BOOKKEEPING_FIELDS | ({_key(collection["owner_field"])} if collection.get("owner_field") else set())
    for index in collection.get("indexes") or []:
        if not isinstance(index, dict) or index.get("unique") is not True:
            continue
        keys = [_key(str((key or {}).get("field") or "").split(".", 1)[0]) for key in index.get("keys") or []]
        scoped = [key for key in keys if key not in scoping]
        if len(scoped) == 1 and scoped[0] in accounts:
            return True
    return False


def _one_row_per_user(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """An account registry: identity with an account credential, unique on an account key, or unique on user_id alone.

    Only such records survive a split into a user_id-keyed residual unchanged:
    a device, ledger, integration, membership or history collection holds many
    rows per user, whatever tokens it carries, and so does anything kept per
    workspace.
    """
    if collection.get("tenancy") == "per_workspace":
        return False
    fields = _keys(field.get("name") for field in _declared_fields(collection))
    if _keyed_by_account(collection, rule) or (
        _is_identity_state(collection, rule) and fields & _keys(rule.account_credential_fields)
    ):
        return True
    for index in collection.get("indexes") or []:
        if not isinstance(index, dict) or index.get("unique") is not True:
            continue
        keys = {_key(str((key or {}).get("field") or "").split(".", 1)[0]) for key in index.get("keys") or []}
        if keys - {"app_id"} == {"user_id"}:
            return True
    return False


def _is_account_collection(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    """A user-account registry: identity state carrying credentials or keyed by an account claim."""
    return _is_identity_state(collection, rule) and (
        _carries_credentials(collection, rule) or _keyed_by_account(collection, rule)
    )


def _is_lifecycle_action(action: Any, entity_keys: set[str], verbs: set[str]) -> bool:
    """``<verb>_<entity>`` or ``<entity>_<verb>``: create_user, list_users, user_login, logout_user.

    The words must be exactly a lifecycle verb and an identity entity (plural
    allowed); follow_user, update_user_theme or award_user_badge are not.
    """
    words = _key(action)
    for entity in entity_keys:
        for noun in {entity, f"{entity}s", f"{entity}es"}:
            for verb in verbs:
                if words in {f"{verb}_{noun}", f"{noun}_{verb}"}:
                    return True
    return False


def _related_entity(entity: str, keys: set[str]) -> bool:
    """Whether an entity key names one of ``keys``, singular or plural (session, sessions)."""
    forms = {entity, f"{entity}s", f"{entity}es"}
    return any(key in forms or entity in {key, f"{key}s", f"{key}es"} for key in keys)


def _is_lifecycle_event(event: Any, entity_keys: set[str], facts: set[str]) -> bool:
    """An event whose last segment is ``<entity>_<fact>``: domain.users.user_created, auth.user_logged_in.

    The words must be exactly an identity entity (plural allowed) and a declared
    lifecycle fact; user_invited or user_badge_awarded are not.
    """
    words = _key(str(event or "").rsplit(".", 1)[-1])
    return any(
        words == f"{noun}_{fact}"
        for entity in entity_keys for noun in {entity, f"{entity}s", f"{entity}es"} for fact in facts
    )


@dataclass(frozen=True)
class _IdentityClaim:
    """Platform identity a surface declares, recognized by what it is rather than by its surface_id.

    ``entities`` are declared platform identity entities with no app data behind
    them; ``actions`` are the surface's identity lifecycle actions the catalog
    does not already reserve for it; ``recognized`` says whether the surface's
    own data proves it (an account collection, or sign-in actions over its
    identity records) rather than a catalog identifier; ``account_fields`` are
    the declared fields of its user-account collections; ``domain`` is the
    identity entities that are the platform's on this surface.
    """

    entities: tuple[str, ...]
    actions: tuple[str, ...]
    recognized: bool
    account_fields: frozenset[str]
    # The catalog already identifies the surface (reserved surface id, entity or action).
    declared: bool = False
    domain: frozenset[str] = frozenset()


def _identity_claims(
    surfaces: list[dict[str, Any]],
    groups: list[tuple[str, list[dict[str, Any]]]],
    rule: SurfaceOwnershipRule,
    *,
    facades: set[str],
) -> dict[str, _IdentityClaim]:
    """Recognize platform identity by the entities, data and actions a surface declares.

    Evidence is required: a catalog identifier the surface declares (its reserved
    surface_id, entity or action); a user-account collection of a platform
    identity entity (every field a declared identity claim in its bounded shape,
    carrying credentials or unique on an account key); or a sign-in action (a
    sign-in verb and a platform identity entity, login_user) over identity
    records of an entity it declares, such as a session store.

    A platform identity entity (the rule's entity_names or surface_entity_names,
    whatever the casing) is the platform's on a surface when none of that
    surface's own records of it (singular or plural) hold app data, and either
    the surface stores identity records of it or no collection anywhere in the
    design holds app data for it: app fields beside a password make it app data,
    and a users collection of accounts stays the platform's even when another
    surface keeps app data about users. An action is identity lifecycle when it
    is exactly a lifecycle verb and one of those entities.
    """
    everything = [collection for _, collections in groups for collection in collections]
    identity = _identity_entities(rule, everything)
    verbs = _keys(rule.identity_lifecycle_verbs)
    sign_in = _keys(rule.sign_in_verbs)
    administered = (
        _keys(rule.user_administration.entity_names) | (identity - _keys([*rule.entity_names, *rule.surface_entity_names]))
        if rule.user_administration else set()
    )

    def entities_with(collections: list[dict[str, Any]], *, app_data: bool) -> set[str]:
        return {
            _key(collection.get("entity")) for collection in collections
            if _is_identity_state(collection, rule) is not app_data
        }

    # An identity-named entity with app data anywhere in the design is app data,
    # and its actions are the app's too, unless a surface stores identity records of it.
    anywhere = entities_with(everything, app_data=True)
    claims: dict[str, _IdentityClaim] = {}
    for surface in surfaces:
        surface_id = str(surface.get("surface_id") or "")
        if surface_id in facades:
            continue
        named = surface_id.strip().casefold() in _identifiers(rule.surface_ids)
        owned = [
            collection for group_id, collections in groups for collection in collections
            if surface_id in {group_id, (collection.get("ownership") or {}).get("surface_id")}
        ]
        own_app_data = entities_with(owned, app_data=True)
        own_identity = entities_with(owned, app_data=False)
        domain = {
            entity for entity in identity
            if not _related_entity(entity, own_app_data)
            and (_related_entity(entity, own_identity) or not _related_entity(entity, anywhere))
        }
        declared_entities = _keys(surface.get("primary_entities"))
        # App data under an entity the surface does not declare (or none) backs every entity it declares.
        orphaned = any(
            not _is_identity_state(collection, rule)
            and not _related_entity(_key(collection.get("entity")), declared_entities)
            for collection in owned
        )
        surface_domain = domain - declared_entities if orphaned else domain
        entities = tuple(
            str(entity) for entity in surface.get("primary_entities") or [] if _key(entity) in surface_domain
        )
        identity_records = [
            collection for collection in owned
            if _related_entity(_key(collection.get("entity")), _keys(entities)) and _is_identity_state(collection, rule)
        ]
        accounts = [collection for collection in identity_records if _is_account_collection(collection, rule)]
        declared_actions = [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
        signs_in = any(_is_lifecycle_action(action, surface_domain, sign_in) for action in declared_actions)
        recognized = bool(accounts) or (signs_in and bool(identity_records))
        declared = named or _matches_surface(surface, rule)
        if not (declared or recognized):
            continue
        # What the catalog already reserves for this surface is not a recognition.
        reserved = _keys([*rule.reserved_action_ids, *(rule.surface_action_ids if named else [])])
        actions = tuple(
            str(action) for action in declared_actions
            if _key(action) not in reserved and _is_lifecycle_action(action, surface_domain, verbs)
        )
        # A surface the catalog already identifies lists its accounts from any of its
        # identity records; one recognized by content, from its account collections.
        listed = identity_records if declared else accounts
        if entities or actions:
            claims[surface_id] = _IdentityClaim(
                entities=entities, actions=actions, recognized=recognized, declared=declared,
                domain=frozenset(surface_domain),
                account_fields=frozenset(
                    _key(field.get("name")) for collection in listed
                    if _related_entity(_key(collection.get("entity")), administered)
                    for field in _declared_fields(collection)
                ),
            )
    return claims


def _typed_record_fields(config_hint: Any, primitive: Any) -> tuple[set[str], set[str]]:
    """Record fields a section names through typed keys, and the columns it lists.

    Typed keys are list ``columns`` (validated downstream against the bound
    action's row fields), ``search_keys``, ``filters[].field``, ``sorts[].key``,
    form ``fields[].name`` and metric ``value_key``/``detail_key``/``trend_key``.
    Labels and intents are prose. Nested children are ``{primitive, config}`` sections.
    """
    if not isinstance(config_hint, str) or not config_hint.strip():
        return set(), set()
    try:
        config = json.loads(config_hint)
    except ValueError:
        return set(), set()
    names: set[str] = set()
    listed: set[str] = set()

    def entries(node: dict[str, Any], key: str, attribute: str | None) -> list[str]:
        values = node.get(key)
        found = [
            value.get(attribute) if attribute and isinstance(value, dict) else value
            for value in (values if isinstance(values, list) else [])
        ]
        return [value for value in found if isinstance(value, str) and value]

    def walk(node: Any, kind: Any) -> None:
        if isinstance(node, dict):
            columns = entries(node, "columns", "key")
            names.update(_key(column) for column in columns)
            if kind in _LIST_PRIMITIVES:
                listed.update(_key(column) for column in columns)
            names.update(_key(value) for value in entries(node, "search_keys", None))
            names.update(_key(value) for value in entries(node, "filters", "field"))
            names.update(_key(value) for value in entries(node, "sorts", "key"))
            names.update(_key(value) for value in entries(node, "fields", "name"))
            names.update(_key(node[key]) for key in ("value_key", "detail_key", "trend_key") if node.get(key))
            for key, value in node.items():
                walk(value, node.get("primitive") if key == "config" else kind)
        elif isinstance(node, list):
            for value in node:
                walk(value, kind)

    walk(config, primitive)
    return names, listed


def _account_listing(
    page: dict[str, Any], rule: SurfaceOwnershipRule, account_fields: frozenset[str],
) -> tuple[bool, list[str]]:
    """Whether a page lists the surface's user accounts, and the fields that keep it from being user administration.

    A page lists the accounts when a DataTable or ResourceTable's typed columns
    include a declared field of a user-account collection (``account_fields``,
    the administered entities' records) beyond bookkeeping keys; such a page is
    never a sign-in page, even with a create-user form collecting a password.
    It is user administration when every listed column is one of those fields
    or a bookkeeping key, and every other typed field on the page is an identity
    claim or a credential input. A form alone lists nobody; a list of sessions,
    teams or workspaces is not the users' records.
    """
    if rule.user_administration is None or not account_fields:
        return False, []
    fields: set[str] = set()
    listed: set[str] = set()
    for section in page.get("sections") or []:
        names, columns = _typed_record_fields(section.get("config_hint"), section.get("primitive"))
        fields |= names
        listed |= columns
    if not (listed - _BOOKKEEPING_FIELDS) & account_fields:
        return False, []
    claims = _keys(rule.state_field_names) | _keys(rule.identity_evidence_fields)
    extra = (listed - account_fields - _BOOKKEEPING_FIELDS) | (fields - listed - account_fields - _BOOKKEEPING_FIELDS - claims)
    return True, sorted(extra)



def _ownership_rules(
    context_variables: Any, *, include_default_subscription: bool,
) -> tuple[list[SurfaceOwnershipRule], dict[str, dict[str, Any]]]:
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    declarations = list(catalog["layers"]["runtime_provided"]["surface_ownership"])
    contracts = managed_pack_contracts(context_variables)
    default = default_subscription_contract(context_variables) if include_default_subscription else None
    if default and not any(contract.get("contract_id") == default["contract_id"] for contract in contracts):
        contracts.append(default)
    facades: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        ownership = contract.get("surface_ownership", [])
        if not isinstance(ownership, list):
            raise ValueError(f"{contract.get('contract_id')}: surface_ownership must be a list.")
        if any(not rule.get("facade_module") for rule in ownership):
            raise ValueError(
                f"{contract.get('contract_id')}: ambiguous managed ownership without a canonical facade_module."
            )
        declarations.extend(ownership)
        for facade in contract.get("facades") or []:
            declaration = {**facade, "pack_id": contract["contract_id"]}
            existing = facades.get(facade["module_id"])
            if existing is not None and existing != declaration:
                raise ValueError(f"Ambiguous ownership: competing declarations for facade {facade['module_id']!r}.")
            facades[facade["module_id"]] = declaration
    rules = [SurfaceOwnershipRule.model_validate(rule) for rule in declarations]
    blueprint = _get(context_variables, "concept_blueprint", {}) or {}
    for hint in blueprint.get("surface_candidate_hints") or []:
        if hint.get("owner_hint") == "platform":
            rules.append(SurfaceOwnershipRule(owner="Mozaiks platform", surface_ids=[hint["surface_id"]]))
    for rule in rules:
        if rule.facade_module and rule.facade_module not in facades:
            raise ValueError(f"{rule.owner}: surface_ownership references undeclared facade {rule.facade_module!r}.")
        served = set(_facade_actions(facades[rule.facade_module])) if rule.facade_module else set()
        if set(rule.action_aliases.values()) - served or set(rule.state_readers) - served:
            raise ValueError(
                f"{rule.owner}: action_aliases and state_readers must name actions of facade {rule.facade_module!r}."
            )
    # An active facade's account state (users.subscription_status) is the platform account's
    # projection of state the facade serves; without the pack it stays the app's own data.
    account_state = [name for rule in rules if rule.facade_module for name in rule.account_state_field_names]
    if account_state:
        rules = [
            rule.model_copy(update={"state_field_names": list(dict.fromkeys([*rule.state_field_names, *account_state]))})
            if rule.platform_capability == "authentication" else rule
            for rule in rules
        ]
    return rules, facades


def _facade_actions(facade: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        action for page in facade.get("pages") or [] for action in page.get("primary_actions") or []
    ))


def _facade_serves(
    module: str, action: str, rule: SurfaceOwnershipRule, facade: dict[str, Any], *, surface_id: str,
) -> bool:
    """A binding the facade serves once ``surface_id`` normalizes to it: its own action, or the surface's alias of one."""
    if module == rule.facade_module:
        return action in _facade_actions(facade)
    aliases = {alias.strip().casefold(): target for alias, target in rule.action_aliases.items()}
    return module == surface_id and aliases.get(action.strip().casefold(), action) in _facade_actions(facade)


def _facade_provides_page(
    name: Any, page: dict[str, Any] | None, rule: SurfaceOwnershipRule, facade: dict[str, Any], *, surface_id: str,
) -> bool:
    """A page the facade serves, which moves with a surface normalizing to it.

    One of the facade's own pages (by name or route), which the save completes; or a
    page whose every section is billing. A section is billing when it binds only the
    facade's own actions (``data_source`` or a module action URL), whatever fields it
    shows; when it binds only the normalizing surface's aliases of them and shows
    only the rule's state fields ("Upgrade" bound to subscribe_user); or, on a page
    named exactly for the reserved state ("Subscription", "Subscription Management"),
    when it binds nothing and shows only state fields. Wording alone never moves a
    page ("Alerts", "My Plans"), and neither does a reserved name over app fields.
    """
    def exact(value: Any) -> str:
        return re.sub(r"[^0-9a-z]+", "_", _key(value)).strip("_")

    pages = facade.get("pages") or []
    if exact(name) in {exact(item.get("name")) for item in pages}:
        return True
    if page is None:
        return False
    if _route_key(page.get("route")) in {_route_key(item.get("route")) for item in pages if item.get("route")}:
        return True
    reserved_name = exact(name) in {exact(value) for value in [*rule.surface_ids, *rule.entity_names, *rule.collection_names]}
    state = _keys(rule.state_field_names)

    def billing(section: dict[str, Any]) -> bool:
        bound = _typed_section_actions(section.get("config_hint"))
        fields, columns = _typed_record_fields(section.get("config_hint"), section.get("primitive"))
        shows_state = (fields | columns) <= state
        if not bound:
            return reserved_name and shows_state
        if not all(_facade_serves(module, action, rule, facade, surface_id=surface_id) for module, action in bound):
            return False
        return all(module == rule.facade_module for module, _action in bound) or shows_state

    sections = page.get("sections") or []
    return bool(sections) and all(billing(section) for section in sections)


def _app_record_names(surface_id: str, collection: str, entity: str, rule: SurfaceOwnershipRule) -> tuple[str, str]:
    """Example app names for records a design filed under provider names: alerts_subscriptions, AlertsSubscription.

    When the joined name is itself reserved (surface 'token' + 'wallets' is token_wallets),
    '_app_' separates the parts, so applying the example is never rejected again.
    """
    name, entity_name = f"{_key(surface_id)}_{_key(collection)}", f"{_pascal(surface_id)}{_pascal(entity or collection)}"
    if name.casefold() in _identifiers([*rule.collection_names, *rule.surface_ids, *rule.surface_collection_names]):
        name = f"{_key(surface_id)}_app_{_key(collection)}"
    if entity_name.casefold() in _identifiers(rule.entity_names):
        entity_name = f"{_pascal(surface_id)}App{_pascal(entity or collection)}"
    return name, entity_name


_FacadePageEntry = tuple[
    dict[str, Any], _SurfaceClaim, list[tuple[str, dict[str, Any] | None]], list[tuple[str, str]], list[tuple[str, str]],
]


def _unserved_bindings(
    pages: list[dict[str, Any]], rule: SurfaceOwnershipRule, facade: dict[str, Any], *, surface_id: str,
) -> list[tuple[str, str]]:
    """(page name, 'surface.action') for each section binding of ``surface_id`` the facade will not serve."""
    return [
        (str(page.get("name")), f"{module}.{action}")
        for page in pages for section in page.get("sections") or []
        for module, action in _typed_section_actions(section.get("config_hint"))
        if module == surface_id and not _facade_serves(module, action, rule, facade, surface_id=surface_id)
    ]


def _facade_app_page_message(
    entries: list[_FacadePageEntry], *, facades: dict[str, dict[str, Any]], app_surfaces: list[str], removable: bool,
) -> str:
    """Name every page a surface normalizing to a facade owns that the facade does not serve, and each change that saves.

    Each entry is (surface, claim, [(page name, page)], [(removed collection, its entity)],
    [(other page, binding)]): the last lists other pages' bindings the facade will not serve.
    """
    parts = []
    for surface, claim, pages, removed, elsewhere in entries:
        rule, surface_id = claim.rule, str(surface["surface_id"])
        facade_id = str(rule.facade_module)
        facade = facades[facade_id]
        declared = [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
        entities = [
            str(entity) for entity in surface.get("primary_entities") or []
            if _identifiers([entity]) & _identifiers(rule.entity_names)
        ]
        actions = [str(action) for action in declared if _identifiers([action]) & _identifiers(rule.action_ids)]
        aliases = [str(action) for action in declared if _identifiers([action]) & _identifiers(rule.action_aliases)]
        events = [str(event) for event in surface.get("events_emitted") or []]
        held = [
            f"{label} {values}" for label, values in (
                ("entities", entities), ("collections", [name for name, _entity in removed]),
                ("actions", [*actions, *aliases]), ("events", events),
            ) if values
        ]
        names = [name for name, _page in pages]
        one = len(names) == 1
        it, they, shows, its = ("it", "it", "shows", "its") if one else ("them", "they", "show", "their")
        unserved = [
            binding for _page_name, binding in _unserved_bindings(
                [page for _name, page in pages if page is not None], rule, facade, surface_id=surface_id,
            )
        ]
        text = (
            (f"Page {names[0]!r} is" if one else f"Pages {names} are") + f" owned by {surface_id!r}, which normalizes "
            f"to {facade_id}: its {', '.join(held) or 'claims'} are {rule.owner}. {facade_id} takes only its own pages "
            f"{[str(page.get('name')) for page in facade.get('pages') or []]} and pages whose every section binds its "
            f"actions, so {it} cannot move with {surface_id!r}"
            + (f" ({its} sections bind {unserved}, which {facade_id} does not serve)" if unserved else "") + ". "
            f"If {they} {shows} billing, bind every section to {facade_id}: add \"data_source\": "
            f"{{\"module_id\": \"{facade_id}\", \"action_id\": <one of {sorted(_facade_actions(facade))}>}} to each "
            f"section's config_hint" + (f", or remove {it}" if removable else "") + ". "
        )
        if elsewhere:
            text += (
                f"Other pages bind {surface_id!r} too: {[f'{binding} on {page!r}' for page, binding in elsewhere]}, "
                f"which {facade_id} does not serve; unless {surface_id!r} stays app-owned, bind those sections to "
                "actions the app surface owning each page declares. "
            )
        rebind = f" and bind {unserved} to actions that surface declares" if unserved else ""
        if not app_surfaces:
            options = [f"declare an app-owned module or ui_only surface and move {it} to its owned_pages{rebind}"]
        elif len(app_surfaces) == 1:
            options = [f"move {it} to owned_pages of {app_surfaces[0]!r}{rebind}"]
        else:
            options = [f"move {it} to owned_pages of one of {app_surfaces}{rebind}"]
        # Only a surface matched through a provider entity or action whose names also name app
        # records (Subscription) can stay app-owned; token wallets are runtime state whatever
        # they are called. Its records then need names of their own, or they are removed again.
        provider_entities = [*entities, *(entity for _name, entity in removed)]
        if not claim.name_match and (entities or actions or removed) and not any(
            _identifiers([entity]) & _identifiers(rule.entity_names)
            and not _identifiers([entity]) & _identifiers(rule.homonym_entity_names)
            for entity in provider_entities
        ):
            example = ""
            if removed or entities:
                collection, entity = removed[0] if removed else (_key(entities[0]), entities[0])
                example_collection, example_entity = _app_record_names(surface_id, collection, entity, rule)
                example = (
                    f" (for example {example_entity} in {example_collection})" if removed
                    else f" (for example {example_entity})"
                )
            renames = [
                *([f"replace {entities} in its primary_entities with an app entity of its own"] if entities else []),
                *(
                    f"rename collection {held_name!r} and its entity {held_entity!r} to match"
                    for held_name, held_entity in removed
                ),
                *([f"rename actions {actions} for what they do"] if actions else []),
            ]
            options.append(
                f"keep {surface_id!r} app-owned by naming its records for what they are: {', '.join(renames)}"
                f"{example}"
                + (
                    f", and {aliases} {'stays its own action' if len(aliases) == 1 else 'stay its own actions'}"
                    if aliases else ""
                )
            )
        parts.append(text + f"If {they} {shows} the app's own records, " + "; or ".join(options) + ".")
    return " ".join(parts)


def _app_behavior(
    surface: dict[str, Any], rule: SurfaceOwnershipRule, facade: dict[str, Any], *,
    collections: list[str], pages: dict[str, dict[str, Any]],
) -> list[str]:
    """What a surface declares beyond a managed facade's claims: behavior that keeps it app-owned."""
    served = _identifiers([*rule.action_ids, *rule.action_aliases, *_facade_actions(facade)])
    entities = [
        str(entity) for entity in surface.get("primary_entities") or []
        if not _identifiers([entity]) & _identifiers(rule.entity_names)
    ]
    actions = [
        str(action) for action in [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
        if str(action).strip().casefold() not in served
    ]
    owned_pages = [
        str(name) for name in surface.get("owned_pages") or []
        if not _facade_provides_page(
            name, pages.get(str(name).strip().casefold()), rule, facade, surface_id=str(surface.get("surface_id")),
        )
    ]
    return [
        f"{label} {values}" for label, values in (
            ("entities", entities), ("actions", actions), ("collections", collections), ("pages", owned_pages),
        ) if values
    ]


def _matches_surface(surface: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    return bool(
        _identifiers([surface.get("surface_id")]) & _identifiers(rule.surface_ids)
        or surface.get("surface_id") == rule.facade_module
        or _identifiers(surface.get("primary_entities")) & _identifiers(rule.entity_names)
        or _identifiers([
            *(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
        ]) & _identifiers(rule.action_ids)
    )


def _matches_collection(collection: dict[str, Any], group_id: str, rule: SurfaceOwnershipRule) -> bool:
    owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
    return bool(
        _identifiers([collection.get("name")]) & _identifiers(rule.collection_names)
        or _identifiers([owner_id, group_id]) & _identifiers(rule.surface_ids)
        or rule.facade_module and rule.facade_module in {owner_id, group_id}
        or rule.facade_module and _identifiers([collection.get("entity")]) & _identifiers(rule.entity_names)
        or (
            _identifiers([collection.get("name")]) & _identifiers(rule.surface_collection_names)
            and _carries_credentials(collection, rule)
        )
    )


def validate_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any] | None = None,
    include_default_subscription: bool = False,
) -> None:
    """Reject duplicate owners in approved designs; late planning never rewrites them."""
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    facade_actions = {module_id: _facade_actions(facade) for module_id, facade in facades.items()}

    surfaces = surface_map.get("surfaces") or []
    data = data_contract or {}
    collections = [
        (str(surface.get("surface_id") or ""), collection)
        for surface in data.get("surfaces") or [] for collection in surface.get("collections") or []
    ]
    collections.extend(("", collection) for collection in data.get("shared_collections") or [])

    for rule in rules:
        remedy = (
            f"Bind UI to {rule.facade_module} and its declared actions "
            f"{sorted(facade_actions[rule.facade_module])}; keep provider state out of app data_contract."
            if rule.facade_module else
            "Reference the platform capability with owner='platform'; use config/auth.yaml for auth "
            "and keep platform identity/session state out of app modules and data_contract."
        )
        reserved_entities = _identifiers(rule.entity_names)
        reserved_actions = _identifiers(rule.action_ids)
        reserved_surfaces = _identifiers(rule.surface_ids)
        for surface in surfaces:
            surface_id = str(surface.get("surface_id") or "")
            is_facade = surface_id == rule.facade_module
            if surface.get("owner") != "app" or surface.get("surface_kind") != "module":
                continue
            conflicts = _identifiers(surface.get("primary_entities")) & reserved_entities
            actions = _identifiers([
                *(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
            ])
            if is_facade:
                conflicts |= _identifiers(surface.get("primary_entities"))
                conflicts |= actions - _identifiers(facade_actions[surface_id])
            else:
                conflicts |= actions & reserved_actions
                conflicts |= _identifiers([surface_id]) & reserved_surfaces
            if conflicts:
                raise ValueError(
                    f"Surface {surface_id!r} claims {sorted(conflicts)}, owned by {rule.owner}. {remedy} "
                    "Revise DesignDocs ownership before app planning; do not generate a replacement module."
                )
        for group_id, collection in collections:
            owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
            name = str(collection.get("name") or "")
            if _matches_collection(collection, group_id, rule):
                raise ValueError(
                    f"Collection {name!r} on surface {owner_id!r} duplicates {rule.owner} state. {remedy} "
                    "Remove the app-owned state declaration before saving."
                )


def _typed_section_bindings(config_hint: Any) -> list[str]:
    """Module ids a section binds through typed references in its config hint.

    Only the two typed shapes downstream planning understands count:
    ``data_source: {module_id, action_id}`` and a canonical module action URL.
    Column names or labels are prose and never decide ownership.
    """
    if not isinstance(config_hint, str) or not config_hint.strip():
        return []
    try:
        config = json.loads(config_hint)
    except ValueError:
        return []
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source = node.get("data_source")
            if isinstance(source, dict) and source.get("module_id"):
                found.append(str(source["module_id"]))
            endpoint = node.get("api_endpoint")
            match = _MODULE_ACTION_ENDPOINT.match(endpoint) if isinstance(endpoint, str) else None
            if match:
                found.append(match.group(1))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    return list(dict.fromkeys(found))


def _rebind_sections(
    spec: dict[str, Any] | None, module_id: str, rebind: Any,
) -> list[dict[str, Any]]:
    """Apply ``rebind(action_id)`` to every typed binding of ``module_id``; return what changed.

    Typed bindings are ``data_source: {module_id, action_id}`` and a canonical
    module action URL. ``rebind`` returns the new ``(module_id, action_id)``, or
    None to drop the section that carries the binding. A section dropped is
    recorded with ``removed: section``.
    """
    changes: list[dict[str, Any]] = []
    for page in (spec or {}).get("pages") or []:
        for section in list(page.get("sections") or []):
            hint = section.get("config_hint")
            if not isinstance(hint, str) or not hint.strip():
                continue
            try:
                config = json.loads(hint)
            except ValueError:
                continue
            found: list[tuple[str, tuple[str, str] | None]] = []

            def walk(node: Any, found: list[tuple[str, tuple[str, str] | None]] = found) -> Any:
                if isinstance(node, dict):
                    node = {key: walk(value) for key, value in node.items()}
                    source = node.get("data_source")
                    if isinstance(source, dict) and source.get("module_id") == module_id:
                        target = rebind(str(source.get("action_id") or ""))
                        found.append((str(source.get("action_id") or ""), target))
                        if target is not None:
                            node["data_source"] = {**source, "module_id": target[0], "action_id": target[1]}
                    endpoint = node.get("api_endpoint")
                    match = _MODULE_ACTION_ENDPOINT.match(endpoint) if isinstance(endpoint, str) else None
                    if match and match.group(1) == module_id:
                        action = endpoint.rsplit("/", 1)[1]
                        target = rebind(action)
                        found.append((action, target))
                        if target is not None:
                            node["api_endpoint"] = f"/api/modules/{target[0]}/{target[1]}"
                    return node
                if isinstance(node, list):
                    return [walk(value) for value in node]
                return node

            rewritten = walk(config)
            if not found:
                continue
            if any(target is None for _, target in found):
                page["sections"].remove(section)
                changes.append({
                    "page": page.get("name"), "section": section.get("id"),
                    "binding": f"{module_id}.{next(action for action, target in found if target is None)}",
                    "removed": "section",
                })
                if not page["sections"]:
                    raise ValueError(
                        f"Page {page.get('name')!r} ({page.get('route')}) only binds {module_id!r} actions the "
                        "platform serves itself. Remove the page, or give it the app content it is for."
                    )
                continue
            if all(target == (module_id, action) for action, target in found):
                continue
            section["config_hint"] = json.dumps(rewritten)
            changes.extend(
                {"page": page.get("name"), "section": section.get("id"), "from": f"{module_id}.{action}",
                 "to": f"{target[0]}.{target[1]}"}
                for action, target in found if target is not None and (target[0], target[1]) != (module_id, action)
            )
    return changes


def _typed_section_actions(config_hint: Any) -> list[tuple[str, str]]:
    """(module_id, action_id) pairs a section binds through ``data_source`` or a module action URL."""
    if not isinstance(config_hint, str) or not config_hint.strip():
        return []
    try:
        config = json.loads(config_hint)
    except ValueError:
        return []
    found: list[tuple[str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source = node.get("data_source")
            if isinstance(source, dict) and source.get("module_id"):
                found.append((str(source["module_id"]), str(source.get("action_id") or "")))
            endpoint = node.get("api_endpoint")
            match = _MODULE_ACTION_ENDPOINT.match(endpoint) if isinstance(endpoint, str) else None
            if match and isinstance(endpoint, str):
                found.append((match.group(1), endpoint.rsplit("/", 1)[1]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    return found


def _admin_panel(administration: UserAdministration | None) -> str:
    if administration is None:
        return "the admin portal"
    return f"the admin portal's built-in {administration.builtin_panel!r} panel ({administration.admin_page} page)"


def _remove_platform_pages(
    surface: dict[str, Any],
    surfaces: list[dict[str, Any]],
    spec: dict[str, Any],
    *,
    rule: SurfaceOwnershipRule,
    sign_in_routes: set[str],
    account_fields: frozenset[str],
    platform_auth_ids: set[str],
    facade_ids: set[str],
    removed_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop the pages a platform surface owns that the platform already provides.

    Two kinds are determined: a page listing the surface's own user accounts
    (``_account_listing``), recorded with the admin portal panel that
    administers users, and a sign-in page, served by the auth contract. Any
    other page the surface owns is a user-designed page the app must build, so
    it is never dropped silently; the rejection names the app-owned surface it
    belongs to. A page is only removable when nothing app-owned depends on it:
    no app-owned surface lists it, and no section binds an app module through a
    typed reference. Sibling surfaces that normalize to the same platform
    capability are not app owners: the removal is constructed once and recorded
    on each of them.
    """
    surface_id = str(surface["surface_id"])
    owner = rule.owner
    names = _identifiers(surface.get("owned_pages"))
    if not names:
        return []
    app_owned = [
        str(other["surface_id"]) for other in surfaces
        if other is not surface and other.get("owner") == "app"
        and str(other["surface_id"]) not in platform_auth_ids and str(other["surface_id"]) not in facade_ids
    ]
    app_modules = {
        str(other["surface_id"]) for other in surfaces
        if str(other["surface_id"]) in app_owned and other.get("surface_kind") == "module"
    }
    app_targets = [
        str(other["surface_id"]) for other in surfaces
        if str(other["surface_id"]) in app_owned and other.get("surface_kind") in {"module", "ui_only"}
    ]
    credential_fields = _identifiers(rule.identity_evidence_fields)
    # Each sibling records its own copy, so the saved YAML carries no shared anchors.
    removed: list[dict[str, Any]] = [dict(removed_by_name[name]) for name in sorted(names) if name in removed_by_name]
    for page in list(spec.get("pages") or []):
        name = str(page.get("name") or "")
        key = name.strip().casefold()
        if key not in names:
            continue
        co_owners = [
            str(other["surface_id"]) for other in surfaces
            if other is not surface and str(other["surface_id"]) not in platform_auth_ids
            and key in _identifiers(other.get("owned_pages"))
        ]
        # Listing the accounts decides first: a create-user form collects a password too.
        lists, extra = _account_listing(page, rule, account_fields)
        administration = rule.user_administration if lists and not extra else None
        sign_in = not lists and _is_sign_in_page(
            page, sign_in_routes=sign_in_routes, credential_fields=credential_fields,
        )
        served = f"user administration in {_admin_panel(administration)}" if administration else "sign-in"
        if not sign_in and administration is None:
            if co_owners:
                target = f"it is already listed by {co_owners}: remove {name!r} from {surface_id!r}.owned_pages"
            elif len(app_targets) == 1:
                target = f"move {name!r} to owned_pages of {app_targets[0]!r}"
            elif app_targets:
                target = f"move {name!r} to owned_pages of one of {app_targets}"
            else:
                target = f"declare an app-owned module or ui_only surface and move {name!r} to its owned_pages"
            if lists:
                raise ValueError(
                    f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                    f"{owner}, and lists its user accounts, but it also names fields the accounts do not "
                    f"declare: {extra}. The platform administers users itself in "
                    f"{_admin_panel(rule.user_administration)}: drop the page, or keep only app data on it "
                    f"and {target}."
                )
            administered = (
                f" It does not list {surface_id!r}'s user accounts either, which {_admin_panel(rule.user_administration)}"
                f" administers."
                if rule.user_administration else ""
            )
            raise ValueError(
                f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                f"{owner}, but it is not a sign-in page: its route is not an auth contract route "
                f"{sorted(sign_in_routes)} and no Form section collects a credential field.{administered} "
                f"The platform provides only sign-in and user administration for {surface_id!r}, so the app "
                f"must own this page: {target}."
            )
        if co_owners:
            raise ValueError(
                f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                f"{owner}, and also by app-owned {co_owners}. The platform provides {served} itself, so the app "
                f"builds no page for {surface_id!r}: remove {name!r} from owned_pages of {co_owners} and drop "
                f"the page, or move its app-owned sections to a page owned only by {co_owners[0]!r} and remove "
                f"{name!r} from {surface_id!r}.owned_pages."
            )
        bound = [
            (str(section.get("id")), module)
            for section in page.get("sections") or []
            for module in _typed_section_bindings(section.get("config_hint")) if module in app_modules
        ]
        if bound:
            raise ValueError(
                f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                f"{owner}, but sections {sorted({section for section, _ in bound})} bind app-owned modules "
                f"{sorted({module for _, module in bound})}. The platform provides {served} itself: move those "
                f"sections to a page owned by the app module they bind, or drop the binding, then remove "
                f"{name!r} from {surface_id!r}.owned_pages."
            )
        spec["pages"].remove(page)
        entry: dict[str, Any] = {"name": name, "route": page.get("route")}
        if administration is not None:
            entry.update(builtin_panel=administration.builtin_panel, admin_page=administration.admin_page)
        removed.append(entry)
        removed_by_name[key] = entry
    if removed and not spec.get("pages"):
        raise ValueError(
            f"Removing the platform page(s) {[page['name'] for page in removed]} owned by "
            f"{surface_id!r} leaves no approved pages. Design the app's own pages under app-owned surfaces."
        )
    return removed


# The page schema's typed navigation references: action/link `href` and route
# fields. Labels, columns, and data values are never routes.
_NAVIGATION_KEYS = frozenset({"href", "route", "path", "fallbackPath"})
# Primitives whose purpose is the action they carry: without one they are dead controls.
_ACTION_PRIMITIVES = frozenset({"ActionButton", "Button"})
_ACTION_KEYS = ("actions", "action")
_DROPPED = object()


def _link_key(value: str) -> str:
    """A navigation reference's route identity: '/users/', '/users?tab=all' and '/users#top' are '/users'."""
    return _route_key(re.split(r"[?#]", value.strip(), maxsplit=1)[0])


def _is_link(key: Any, value: Any) -> bool:
    return key in _NAVIGATION_KEYS and isinstance(value, str) and value.strip().startswith("/")


def _navigation_target(node: dict[str, Any], routes: Any) -> str | None:
    return next((value for key, value in node.items() if _is_link(key, value) and _link_key(value) in routes), None)


def _rewrite_routes(node: Any, targets: dict[str, tuple[str, str]], hits: list[str]) -> Any:
    if isinstance(node, dict):
        rewritten: dict[str, Any] = {}
        for key, value in node.items():
            if _is_link(key, value) and _link_key(value) in targets:
                hits.append(value)
                rewritten[key] = targets[_link_key(value)][1]
            else:
                rewritten[key] = _rewrite_routes(value, targets, hits)
        return rewritten
    if isinstance(node, list):
        return [_rewrite_routes(value, targets, hits) for value in node]
    return node


def _drop_links(
    node: Any, routes: dict[str, str], path: str, hits: list[tuple[str, str]], primitive: Any = None,
) -> Any:
    """Remove every object whose typed navigation targets a removed route.

    The object carrying the reference (a link, an action) goes with it: a list
    loses the element, a mapping loses the key. What that leaves without a
    purpose goes too: a list or mapping all of whose entries went, a child
    section ``{primitive, config}`` whose config went, a Grid whose children all
    went (a Modal keeps its own content), an entry of an ``actions`` list whose
    action went, and the configuration of an ActionButton or Button left with
    no action at all (any other action keeps it). ``primitive`` is the
    primitive ``node`` configures; ``action_item`` marks an ``actions`` entry.
    ``_DROPPED`` means ``node`` itself goes, and its removal is recorded at its
    own path.
    """
    before = len(hits)

    def dropped() -> Any:
        # Record the removal where it happened: the outermost object that went.
        hits[before:] = list(dict.fromkeys((route, path) for route, _ in hits[before:]))
        return _DROPPED

    if isinstance(node, dict):
        target = _navigation_target(node, routes)
        if target is not None:
            hits.append((target, path))
            return _DROPPED
        kept: dict[str, Any] = {}
        for key, value in node.items():
            child = _drop_links(
                value, routes, f"{path}.{key}" if path else key, hits,
                node.get("primitive") if key == "config" else ("actions" if key == "actions" else None),
            )
            if child is not _DROPPED:
                kept[key] = child
        if len(hits) > before and (
            not kept
            or ("config" in node and "primitive" in node and "config" not in kept)
            or (primitive == "Grid" and "children" in node and "children" not in kept)
            or (primitive == "actions:item" and "action" in node and "action" not in kept)
            or (
                primitive in _ACTION_PRIMITIVES
                and any(key in node for key in _ACTION_KEYS) and not any(key in kept for key in _ACTION_KEYS)
            )
        ):
            return dropped()
        return kept
    if isinstance(node, list):
        items = [
            _drop_links(value, routes, f"{path}[{index}]", hits, "actions:item" if primitive == "actions" else None)
            for index, value in enumerate(node)
        ]
        kept_items = [item for item in items if item is not _DROPPED]
        if len(hits) > before and node and not kept_items:
            return dropped()
        return kept_items
    return node


def _redirect_navigation(
    spec: dict[str, Any],
    redirects: dict[str, tuple[str, str]],
    removals: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Point typed navigation at the platform route that serves a removed page, or remove it.

    ``redirects`` maps a removed sign-in route to (owning surface_id, the auth
    contract login route). ``removals`` maps a removed user-administration
    route to its owning surface_id: the admin portal does not serve user
    administration end to end yet, so navigation to it is removed rather than
    pointed at a route that does not work (see ``_drop_links``). A section left
    without a purpose is removed; a page left without sections is a design
    decision. Results are grouped by owning surface.
    """
    redirected: dict[str, list[dict[str, Any]]] = {}
    dropped: dict[str, list[dict[str, Any]]] = {}
    for page in spec.get("pages") or []:
        for section in list(page.get("sections") or []):
            hint = section.get("config_hint")
            if not isinstance(hint, str) or not hint.strip():
                continue
            try:
                config = json.loads(hint)
            except ValueError:
                continue
            links: list[tuple[str, str]] = []
            kept = _drop_links(config, removals, "", links, section.get("primitive")) if removals else config
            for route, path in (
                dict.fromkeys((route, "section") for route, _ in links) if kept is _DROPPED else links
            ):
                dropped.setdefault(removals[_link_key(route)], []).append({
                    "page": page.get("name"), "section": section.get("id"), "route": route, "removed": path,
                })
            if kept is _DROPPED:
                page["sections"].remove(section)
                if not page["sections"]:
                    raise ValueError(
                        f"Page {page.get('name')!r} ({page.get('route')}) only links to "
                        f"{sorted({route for route, _ in links})}, which the platform administers itself. "
                        "Remove the page, or give it the app content it is for."
                    )
                continue
            hits: list[str] = []
            rewritten = _rewrite_routes(kept, redirects, hits)
            if hits or links:
                section["config_hint"] = json.dumps(rewritten)
            for route in dict.fromkeys(hits):
                owner_id, target = redirects[_link_key(route)]
                redirected.setdefault(owner_id, []).append({
                    "page": page.get("name"), "section": section.get("id"), "from": route, "to": target,
                })
    return redirected, dropped


def entity_ownership_facts(
    context_variables: Any, *, include_default_subscription: bool = False,
) -> tuple[Any, set[str]]:
    """What feedback about an entity needs: a platform/provider-entity test and the managed facade ids.

    The test takes a collection and says whether its entity is platform
    identity (a credential-gated one only where the record signs in) or a
    selected provider's entity, which can never be an app entity.
    """
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    names = _keys(entity for rule in rules for entity in [*rule.entity_names, *rule.surface_entity_names])

    def reserved(collection: dict[str, Any]) -> bool:
        return _related_entity(_key(collection.get("entity")), names) or any(
            _is_platform_entity_record(collection, rule) for rule in rules
        )

    return reserved, set(facades)


def related_entity_name(entity: Any, names: Any) -> str | None:
    """The declared name that is ``entity`` in another spelling (casing, singular or plural), if any."""
    key = _key(entity)
    return next((str(name) for name in names or [] if _related_entity(key, {_key(name)})), None)


def declare_collection_entities(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any],
    include_default_subscription: bool = False,
) -> list[dict[str, Any]]:
    """Add a module-owned collection's explicit entity to the module's primary_entities.

    The data_contract names both the collection's entity and its owner, and a
    module that owns the records owns their entity, so listing it is determined.
    Left to the design: another surface already declares the entity (which
    surface owns it is a decision); the owner declares it under another
    spelling (no plural inference); the entity is platform identity or
    selected-provider state, or the owner is a surface the ownership catalog
    identifies (ownership normalization decides those); and a collection filed
    in one group but owned by another surface. Returns one record per completed
    surface.
    """
    rules, _ = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    reserved = _keys(entity for rule in rules for entity in [*rule.entity_names, *rule.surface_entity_names])
    surfaces = {str(surface.get("surface_id")): surface for surface in surface_map.get("surfaces") or []}
    groups = [(str(group.get("surface_id")), group.get("collections") or []) for group in data_contract.get("surfaces") or []]
    groups.append(("", data_contract.get("shared_collections") or []))
    candidates: list[tuple[str, str]] = []
    for group_id, collections in groups:
        for collection in collections:
            if not isinstance(collection, dict) or not isinstance(collection.get("ownership"), dict):
                continue
            entity = collection.get("entity")
            owner_id = str(collection["ownership"].get("surface_id") or "")
            owner = surfaces.get(owner_id)
            if (
                not isinstance(entity, str) or not entity.strip() or owner is None
                or owner.get("surface_kind") != "module" or (group_id and owner_id != group_id)
                # A differently spelled declared entity is the design's to reconcile: no plural inference.
                or _related_entity(_key(entity), _keys(owner.get("primary_entities")))
                # A surface the catalog identifies normalizes to its owner; its entities are not the app's.
                or any(_matches_surface(owner, rule) for rule in rules)
                or _related_entity(_key(entity), reserved)
                or any(_is_platform_entity_record(collection, rule) for rule in rules)
                # A reserved collection name (subscriptions) is ownership normalization's to judge.
                or any(
                    str(collection.get("name") or "").casefold() in _identifiers(rule.collection_names)
                    for rule in rules
                )
                or any(
                    _related_entity(_key(entity), _keys(other.get("primary_entities")))
                    for other_id, other in surfaces.items() if other_id != owner_id
                )
            ):
                continue
            candidates.append((owner_id, entity))
    # An entity whose records two modules own belongs to neither by construction: that is a decision.
    owners: dict[str, set[str]] = {}
    for owner_id, entity in candidates:
        owners.setdefault(_key(entity), set()).add(owner_id)
    added: dict[str, list[str]] = {}
    for owner_id, entity in candidates:
        if len(owners[_key(entity)]) != 1 or entity in added.get(owner_id, []):
            continue
        owner = surfaces[owner_id]
        owner["primary_entities"] = [*(owner.get("primary_entities") or []), entity]
        added.setdefault(owner_id, []).append(entity)
    return [
        {
            "surface_id": surface_id, "owner": str(surfaces[surface_id].get("owner") or "app"),
            "removed_collections": [], "added_entities": entities,
        }
        for surface_id, entities in added.items()
    ]


def normalize_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any],
    experience_spec: dict[str, Any] | None = None,
    include_default_subscription: bool = False,
) -> list[dict[str, Any]]:
    """Apply only contract-determined corrections, atomically, before design approval.

    A reserved surface name proves the owner, not the meaning of every field it
    contains. Residual auth data moves only to one determined existing app module;
    unresolved owners or mixed app behavior require a design decision.

    Platform identity is recognized by what a surface declares, whatever its
    surface_id: a user-account collection of a platform identity entity with no
    app data behind it (see ``_identity_claims``). Such a surface normalizes to
    the platform as a whole: its identity collections are removed, its identity
    lifecycle actions are removed and recorded (``removed_mutations``/
    ``removed_reads``), and it keeps nothing app-owned. When it also declares
    app-owned entities, actions, collections or pages, the rejection names what
    stays app-owned on it.

    Events on a surface that normalizes to platform or provider ownership are that
    owner's lifecycle events, which the app cannot emit: they are removed and
    recorded, unless another surface's workflow_triggers consume one. Pages owned
    by a platform authentication surface that the platform already provides are
    removed from the approved inventory when ``experience_spec`` is supplied:
    sign-in pages (typed navigation to them points at the auth contract login
    route) and pages listing the surface's user accounts (typed navigation to
    them is removed, as the admin portal's users panel is not served end to end).
    """
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    normalized_map, normalized_data = deepcopy(surface_map), deepcopy(data_contract)
    normalized_spec = deepcopy(experience_spec) if experience_spec is not None else None
    records: dict[tuple[str, str], dict[str, Any]] = {}

    def record(surface_id: str, rule: SurfaceOwnershipRule, removed: str | None = None) -> dict[str, Any]:
        owner = rule.facade_module or "platform"
        entry = records.setdefault((surface_id, owner), {
            "surface_id": surface_id, "owner": owner, "removed_collections": [],
        })
        if removed is not None:
            entry["removed_collections"].append(removed)
        return entry

    def choose(matches: list[SurfaceOwnershipRule], subject: str) -> SurfaceOwnershipRule:
        owners = {rule.facade_module or "platform" for rule in matches}
        if len(owners) != 1:
            raise ValueError(f"Ambiguous ownership for {subject}: competing owners {sorted(owners)}.")
        # A platform hint supplies identity only; prefer the declared capability
        # rule with its bounded state schema when both describe that identity.
        return max(matches, key=lambda rule: len(rule.state_field_names))

    groups = [(group["surface_id"], group["collections"]) for group in normalized_data.get("surfaces") or []]
    groups.append(("", normalized_data.get("shared_collections") or []))
    # Each collection's entity before any is removed: a message names what a removed one held.
    entity_of = {
        (str((collection.get("ownership") or {}).get("surface_id") or group_id), str(collection.get("name") or "")):
            str(collection.get("entity") or "")
        for group_id, collections in groups for collection in collections
    }
    facade_page_entries: list[_FacadePageEntry] = []
    # Platform identity is recognized by what each surface declares, before any repair. A
    # collection a managed facade rule matches is provider state, removed or rejected by that
    # rule, never a surface's app data.
    facade_rules = [rule for rule in rules if rule.facade_module]
    identity_groups = [
        (group_id, [
            collection for collection in collections
            if not any(_matches_collection(collection, group_id, rule) for rule in facade_rules)
        ])
        for group_id, collections in groups
    ]
    identity = {
        id(rule): _identity_claims(normalized_map["surfaces"], identity_groups, rule, facades=set(facades))
        for rule in rules if rule.platform_capability == "authentication" and not rule.facade_module
    }

    def identity_claim(surface_id: str, rule: SurfaceOwnershipRule) -> _IdentityClaim | None:
        return identity.get(id(rule), {}).get(surface_id)

    def recognized(surface_id: str, rule: SurfaceOwnershipRule) -> bool:
        """Recognized by its own data: a user-account collection, whatever the surface is called."""
        claim = identity_claim(surface_id, rule)
        return bool(claim and claim.recognized)

    def matches_surface(surface: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
        return _matches_surface(surface, rule) or recognized(str(surface.get("surface_id") or ""), rule)

    def identity_collection(collection: dict[str, Any], owner_id: str, group_id: str, rule: SurfaceOwnershipRule) -> bool:
        """Identity state of a platform identity entity on a surface normalizing to platform identity."""
        return (
            rule.platform_capability == "authentication" and not rule.facade_module
            and _is_platform_entity_record(collection, rule)
            and _is_identity_state(collection, rule)
            and any(
                recognized(surface_id, rule) or surface_id.casefold() in _identifiers(rule.surface_ids)
                or bool((claim := identity_claim(surface_id, rule)) and claim.declared)
                for surface_id in {owner_id, group_id} if surface_id
            )
        )

    surfaces_by_id = {str(surface.get("surface_id") or ""): surface for surface in normalized_map["surfaces"]}
    declared_by_any = _keys(
        entity for surface in normalized_map["surfaces"] for entity in surface.get("primary_entities") or []
    )

    def unclaimed_account_records(collection: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
        """Records of a platform account entity (User, or an Account signing in) that no surface declares.

        No surface claimed the entity, so what the records hold decides, whether
        they are filed under a surface that does not declare the entity or under
        a data group with no surface at all. Identity state is the platform's.
        App fields beside identity split out like any mixed identity collection,
        but only from records the user_id-keyed residual keeps whole (one row per
        user); a device, ledger or history collection is left to the design, and
        so are records holding nothing beyond row keys.
        """
        if rule.user_administration is None or rule.facade_module:
            return False
        entity = _key(collection.get("entity"))
        fields = _keys(field.get("name") for field in _declared_fields(collection))
        account = _related_entity(entity, _keys(rule.user_administration.entity_names)) or (
            entity in _keys(rule.credential_entity_names) and _carries_sign_in_credential(collection, rule)
        )
        return (
            account and not _related_entity(entity, declared_by_any)
            and bool(fields - _record_keys(collection, rule))
            and _one_row_per_user(collection, rule)
        )

    def matches_collection(collection: dict[str, Any], group_id: str, rule: SurfaceOwnershipRule) -> bool:
        owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
        return (
            _matches_collection(collection, group_id, rule)
            or identity_collection(collection, owner_id, group_id, rule)
            or unclaimed_account_records(collection, rule)
        )

    # Selected facades cannot own residual app data, even without an ownership rule.
    app_modules = {
        surface["surface_id"] for surface in normalized_map["surfaces"]
        if surface.get("owner") == "app" and surface.get("surface_kind") == "module"
        and surface["surface_id"] not in facades
        and not any(matches_surface(surface, rule) for rule in rules)
    }
    splits: list[tuple[str, dict[str, Any]]] = []
    for group_id, collections in groups:
        for collection in list(collections):
            matches = [rule for rule in rules if matches_collection(collection, group_id, rule)]
            if not matches:
                continue
            name = str(collection.get("name") or "")
            owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
            rule = choose(matches, f"collection {name!r}")
            scoped = bool(_identifiers([owner_id, group_id]) & _identifiers(rule.surface_ids))
            # Identity state of a platform identity entity: its fields decide, not its name.
            content = identity_collection(collection, owner_id, group_id, rule) or unclaimed_account_records(
                collection, rule,
            )
            names = _identifiers(rule.collection_names)
            if scoped or content or _carries_credentials(collection, rule):
                names |= _identifiers(rule.surface_collection_names)
            fields = _keys(field.get("name") for field in _declared_fields(collection))
            identity_fields = _keys(rule.state_field_names)
            row_keys = _record_keys(collection, rule)
            unknown = fields - identity_fields - row_keys
            state_fields = fields - row_keys
            unbounded_fields = _unbounded_fields(collection, rule)
            def rule_side(surface_id: str, rule: SurfaceOwnershipRule = rule) -> bool:
                surface = surfaces_by_id.get(surface_id)
                return (
                    surface is None or surface_id.casefold() in _identifiers(rule.surface_ids)
                    or surface_id == rule.facade_module or recognized(surface_id, rule)
                    or _matches_surface(surface, rule)
                )

            # Nothing app-owned disputes the records: the declared owner is the rule's reserved
            # surface or facade, a surface recognized as platform identity, or no surface at all;
            # for a managed facade the group must agree too. Credentials and a reserved collection
            # name make the records the rule's wherever they are filed. Otherwise an existing app
            # surface on either side is a design decision.
            owner_agrees = (
                not group_id or owner_id == group_id
                or (rule_side(owner_id) and (not rule.facade_module or rule_side(group_id)))
                or _carries_credentials(collection, rule) or name.casefold() in _identifiers(rule.collection_names)
            )
            owner_surface = surfaces_by_id.get(owner_id) or {}
            app_entity = (
                bool(owner_surface) and not rule_side(owner_id)
                and collection.get("entity") in (owner_surface.get("primary_entities") or [])
                and not _identifiers([collection.get("entity")]) & _identifiers(rule.entity_names)
            )
            if rule.facade_module and (app_entity or not (
                name.casefold() in _identifiers(rule.collection_names) or rule_side(owner_id)
                or bool(group_id) and rule_side(group_id)
            )):
                # Matched only by a provider name (its entity, or its collection name beside an app
                # entity its app surface declares): billing records or the app's own subscriptions
                # (newsletter follows). That is the design's call.
                raise ValueError(
                    f"Collection {name!r} on app surface {owner_id!r} (entity {collection.get('entity')!r}) is "
                    f"named for {rule.owner}. If it holds subscription or wallet records, remove it: "
                    + "; ".join(
                        f"{rule.facade_module}.{action} serves {', '.join(served)}"
                        for action, served in rule.state_readers.items()
                    )
                    + ". If it is the app's own data, name it for what it is: "
                    + (
                        f"rename the collection (for example "
                        f"{_app_record_names(owner_id, name, str(collection.get('entity') or ''), rule)[0]})."
                        if app_entity else
                        f"give it an app entity of its own (for example "
                        f"{_app_record_names(owner_id, name, str(collection.get('entity') or ''), rule)[1]})."
                    )
                )
            # A managed facade owns no collections: a matched one holding only provider state
            # names goes whatever types it declares them with.
            provider_state = bool(rule.facade_module) and bool(state_fields) and not unknown
            identity_state = (
                (name.casefold() in names or content) and bool(state_fields) and not unknown and not unbounded_fields
            )
            if (provider_state or identity_state) and owner_agrees:
                # Entirely the owner's state: removed wherever the design filed it.
                collections.remove(collection)
                record(owner_id, rule, name)
                continue
            if (scoped or content) and group_id and owner_id != group_id:
                raise ValueError(
                    f"Ambiguous collection {name!r} conflicts with {rule.owner}: data_contract group "
                    f"{group_id!r} disagrees with its declared owner {owner_id!r}. Declare it once: set its "
                    f"ownership.surface_id to {group_id!r}, or file it under the {owner_id!r} group if it is "
                    f"{owner_id!r}'s app data"
                    + (
                        f", under an app entity of its own ({collection.get('entity')!r} names "
                        f"{'platform identity' if not rule.facade_module else rule.owner})"
                        if _is_platform_entity_record(collection, rule)
                        or _identifiers([collection.get("entity")]) & _identifiers(rule.entity_names) else ""
                    )
                    + "."
                )
            if unknown and not rule.facade_module and "user_id" in identity_fields and not unbounded_fields:
                candidates = app_modules & {owner_id, group_id} or app_modules
                if len(candidates) != 1:
                    raise ValueError(
                        f"Ambiguous app ownership for collection {name!r} on surface {owner_id!r} "
                        f"after separating {rule.owner}: expected one app module, "
                        f"candidates={sorted(candidates)}, residual fields={sorted(unknown)}."
                    )
                target = next(iter(candidates))
                residual = deepcopy(collection)
                # Globally reserved names still denote platform state after a move.
                if name.casefold() in _identifiers(r for item in rules for r in item.collection_names):
                    residual["name"] = f"{name}_app_data"
                retained = unknown | {"user_id", "app_id"}
                residual["fields"] = [
                    field for field in residual["fields"]
                    if _key(field.get("name")) in retained - {"user_id"}
                ]
                residual["fields"].insert(0, {
                    "name": "user_id", "type": "string", "required": True,
                    "default": None, "enum": None, "nullable": False,
                })
                # Indexes and the owner key reference the residual's own declared names.
                residual_names = {str(field.get("name")) for field in residual["fields"]}
                app_field = next((name for name in residual_names if _key(name) == "app_id"), None)
                keys = [app_field, "user_id"] if app_field else ["user_id"]
                indexes = [
                    index for index in residual.get("indexes") or []
                    if index.get("keys") and all(
                        str(key["field"]).split(".", 1)[0] in residual_names for key in index["keys"]
                    )
                    and [key["field"] for key in index["keys"]] != keys
                ]
                index_base = f"{residual['name']}_{'_'.join(keys)}_unique"
                index_name = index_base
                index_names = {index.get("name") for index in indexes}
                suffix = 2
                while index_name in index_names:
                    index_name = f"{index_base}_{suffix}"
                    suffix += 1
                indexes.append({
                    "keys": [{"field": key, "order": 1} for key in keys],
                    "unique": True, "sparse": False, "name": index_name,
                })
                residual.update(
                    ownership={"surface_id": target, "surface_kind": "module"},
                    scope="app", search_by="user_id", indexes=indexes,
                    tenancy="per_user", owner_field="user_id",
                    entity=(residual["name"] if residual["name"].endswith("_app_data") else f"{residual['name']}_app_data"),
                    lifecycle={**(residual.get("lifecycle") or {}), "write_mode": "module_action"},
                )
                collections.remove(collection)
                splits.append((target, residual))
                entry = record(owner_id, rule)
                entry.setdefault("split_collections", []).append({
                    "name": name, "target_surface_id": target, "target_collection": residual["name"],
                    "removed_fields": sorted(
                        str(field.get("name")) for field in _declared_fields(collection)
                        if _key(field.get("name")) not in retained
                    ),
                    "retained_fields": [field["name"] for field in residual["fields"]],
                })
                continue
            if rule.facade_module:
                # A managed facade owns no collections, so there is no residual to keep.
                facade_id = rule.facade_module
                served = "; ".join(
                    f"{facade_id}.{action} serves {', '.join(fields)}" for action, fields in rule.state_readers.items()
                )
                app_fields = sorted(unknown)
                # An app surface keeping other app behavior stays app-owned once the collection
                # goes: name its own provider claims too, so the design is changed once, and keep
                # its alias actions (subscribe_user), which are then its own.
                behavior: list[str] = []
                if owner_surface.get("owner") == "app" and not (
                    owner_id.casefold() in _identifiers(rule.surface_ids) or owner_id == facade_id
                ):
                    behavior = _app_behavior(
                        owner_surface, rule, facades[facade_id],
                        collections=[
                            str(item.get("name")) for other_group, items in groups for item in items
                            if item is not collection
                            and owner_id in {other_group, (item.get("ownership") or {}).get("surface_id")}
                            and not _matches_collection(item, other_group, rule)
                        ],
                        pages={
                            str(page.get("name") or "").strip().casefold(): page
                            for page in (normalized_spec or {}).get("pages") or []
                        },
                    )
                owner_actions = [
                    *(owner_surface.get("owned_mutations") or []), *(owner_surface.get("custom_reads") or []),
                ]
                claimed_entities = [
                    str(entity) for entity in owner_surface.get("primary_entities") or []
                    if _identifiers([entity]) & _identifiers(rule.entity_names)
                ]
                claimed_actions = [
                    str(action) for action in owner_actions if _identifiers([action]) & _identifiers(rule.action_ids)
                ]
                alias_actions = [
                    str(action) for action in owner_actions if _identifiers([action]) & _identifiers(rule.action_aliases)
                ]
                example_collection, example_entity = _app_record_names(
                    owner_id, name, str(collection.get("entity") or name), rule,
                )
                example = f"{example_collection} with entity {example_entity}"
                if behavior:
                    keep = (
                        f"Its fields {app_fields} are not provider state: if the app needs them, keep them on "
                        f"{owner_id!r} in a collection of their own keyed by user_id and named for what it holds "
                        f"(for example {example}). "
                        if app_fields else ""
                    )
                    changes = [
                        *([
                            f"replace {claimed_entities} in its primary_entities with "
                            f"{'that app entity' if app_fields else 'an app entity of its own'} or drop "
                            f"{'it' if len(claimed_entities) == 1 else 'them'}: {facade_id} serves "
                            f"{'it' if len(claimed_entities) == 1 else 'them'}"
                        ] if claimed_entities else []),
                        *([f"rename actions {claimed_actions} for what they do"] if claimed_actions else []),
                        *([
                            f"{alias_actions} "
                            f"{'stays its own action' if len(alias_actions) == 1 else 'stay its own actions'}"
                        ] if alias_actions and (claimed_entities or claimed_actions) else []),
                    ]
                    keep += (
                        f"{owner_id!r} stays app-owned ({'; '.join(behavior)})"
                        + (": " + "; ".join(changes) if changes else "") + ". "
                    )
                else:
                    # The surface itself normalizes to the facade, so the fields need another module.
                    modules = sorted(app_modules - {owner_id})
                    keep = (
                        f"Its fields {app_fields} are not provider state: if the app needs them, declare them in "
                        "a collection keyed by user_id of "
                        + (
                            f"the app-owned module {modules[0]!r}" if len(modules) == 1
                            else f"one of the app-owned modules {modules}" if modules
                            else "an app-owned module declared for them"
                        )
                        + ". "
                        if app_fields else ""
                    )
                    # Its pages the facade does not take need a place too, in the same revision.
                    spec_named = {
                        str(page.get("name") or "").strip().casefold(): page
                        for page in (normalized_spec or {}).get("pages") or []
                    }
                    co_owned = {
                        name for other in normalized_map["surfaces"]
                        if other is not owner_surface and other.get("owner") == "app"
                        and not any(matches_surface(other, item) for item in rules)
                        for name in _identifiers(other.get("owned_pages"))
                    }
                    stranded = [
                        str(page_name) for page_name in owner_surface.get("owned_pages") or []
                        if owner_id != facade_id and str(page_name).strip().casefold() in spec_named
                        and str(page_name).strip().casefold() not in co_owned
                        and not _facade_provides_page(
                            page_name, spec_named[str(page_name).strip().casefold()], rule, facades[facade_id],
                            surface_id=owner_id,
                        )
                    ]
                    if stranded:
                        written = set(spec_named) - _identifiers(stranded) - _identifiers(
                            page.get("name") for page in facades[facade_id].get("pages") or []
                        )
                        keep += (
                            f"{owner_id!r} normalizes to {facade_id}, which does not take its pages {stranded}: "
                            + (
                                f"move them to owned_pages of {modules[0]!r}" if len(modules) == 1
                                else f"move them to owned_pages of one of {modules}" if modules
                                else "declare an app-owned module or ui_only surface and move them to its owned_pages"
                            )
                            + (" or remove them" if written else "") + ". "
                        )
                raise ValueError(
                    f"Collection {name!r} on surface {owner_id!r} duplicates {rule.owner}: remove collection "
                    f"{name!r}" + (f"; {served}" if served else "") + ". " + keep
                    + f"Bind subscription UI to {facade_id} and its actions {sorted(_facade_actions(facades[facade_id]))}."
                )
            raise ValueError(
                f"Ambiguous collection {name!r} on surface {owner_id!r} conflicts with {rule.owner}; "
                f"unrecognized state name/fields={sorted(unknown)}, unbounded fields={unbounded_fields}, "
                f"state field evidence={sorted(state_fields)}. Separate app-specific data from "
                "platform state before saving: keep app fields in a collection of an app-owned module keyed "
                "by user_id, and reference the platform with owner=platform."
            )

    for target, residual in splits:
        group = next((g for g in normalized_data["surfaces"] if g["surface_id"] == target), None)
        if group is None:
            group = {"surface_id": target, "surface_kind": "module", "collections": []}
            normalized_data["surfaces"].append(group)
            groups.append((target, group["collections"]))
        if any(
            item["name"].casefold() == residual["name"].casefold()
            for group_id, items in groups for item in items
            if target in {group_id, (item.get("ownership") or {}).get("surface_id")}
        ):
            raise ValueError(f"Ambiguous split destination {target!r}/{residual['name']!r}: collection already exists.")
        group["collections"].append(residual)
        target_surface = next(surface for surface in normalized_map["surfaces"] if surface["surface_id"] == target)
        target_surface["primary_entities"] = list(dict.fromkeys([
            *(target_surface.get("primary_entities") or []), residual["entity"],
        ]))

    surfaces = normalized_map["surfaces"]
    targets: dict[str, str] = {}
    # A removed sign-in route -> (owning surface_id, login route); a removed
    # user-administration route -> owning surface_id (its navigation is removed).
    redirects: dict[str, tuple[str, str]] = {}
    removals: dict[str, str] = {}
    removed_by_name: dict[str, dict[str, Any]] = {}
    auth_rules = [rule for rule in rules if rule.platform_capability == "authentication" and not rule.facade_module]
    # Sign-in routes matter wherever a page may duplicate sign-in, including on
    # app-owned surfaces that keep app data beside platform identity.
    auth_routes = auth_contract_routes() if normalized_spec is not None and auth_rules else None
    sign_in_routes = _sign_in_routes(auth_routes) if auth_routes is not None else set()
    spec_pages = list((normalized_spec or {}).get("pages") or [])

    # The platform's user accounts, wherever the design files them: every page a
    # platform surface owns is judged against the same fields, whatever the order.
    page_accounts: dict[int, frozenset[str]] = {
        id(rule): frozenset(
            field for claim in identity.get(id(rule), {}).values() for field in claim.account_fields
        )
        for rule in auth_rules
    }

    def assess(surface: dict[str, Any]) -> _SurfaceClaim | None:
        surface_id = surface["surface_id"]
        matches = [rule for rule in rules if matches_surface(surface, rule)]
        if not matches:
            return None
        if len({rule.facade_module or "platform" for rule in matches}) > 1:
            # Name what each owner claims so the design can split them.
            declared = [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
            claimed = []
            for rule in matches:
                platform_identity = identity_claim(surface_id, rule)
                facade = facades.get(rule.facade_module or "", {})
                entities = _identifiers([
                    *rule.entity_names, *(platform_identity.entities if platform_identity else []),
                ])
                actions = _identifiers([
                    *rule.reserved_action_ids, *_facade_actions(facade),
                    *(platform_identity.actions if platform_identity else []),
                ])
                claimed.append(
                    f"{rule.facade_module or 'platform'} ({rule.owner}): entities "
                    f"{[str(e) for e in surface.get('primary_entities') or [] if str(e).strip().casefold() in entities]}, "
                    f"actions {[str(a) for a in declared if str(a).strip().casefold() in actions]}"
                )
            raise ValueError(
                f"Ambiguous ownership for surface {surface_id!r}: competing owners "
                f"{sorted({rule.facade_module or 'platform' for rule in matches})}. It claims "
                f"{'; '.join(claimed)}. Declare each owner's state on its own surface, and keep app "
                "behavior on an app-owned module."
            )
        rule = choose(matches, f"surface {surface_id!r}")
        facade = facades.get(rule.facade_module or "", {})
        facade_actions = _facade_actions(facade)
        name_match = bool(_identifiers([surface_id]) & _identifiers(rule.surface_ids)) or surface_id == rule.facade_module
        entities = _identifiers(rule.entity_names)
        allowed_actions = _identifiers([*rule.reserved_action_ids, *facade_actions])
        if name_match:
            entities |= _identifiers(rule.surface_entity_names)
            allowed_actions |= _identifiers(rule.surface_action_ids)
        platform_identity = identity_claim(surface_id, rule)
        if platform_identity is not None:
            entities |= _identifiers(platform_identity.entities)
            allowed_actions |= _identifiers(platform_identity.actions)
        declared_entities = [str(entity) for entity in surface.get("primary_entities") or []]
        declared_actions = [
            str(action) for action in [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
        ]
        unknown_entities = [entity for entity in declared_entities if entity.strip().casefold() not in entities]
        unknown_actions = [action for action in declared_actions if action.strip().casefold() not in allowed_actions]
        remaining = [
            collection["name"] for group_id, collections in groups for collection in collections
            if surface_id in {group_id, (collection.get("ownership") or {}).get("surface_id")}
        ]
        # Each item names the app-owned behavior to split out and where it goes.
        split_out: list[str] = []
        if unknown_entities:
            split_out.append(f"move entities {unknown_entities} to an app-owned module surface")
        if unknown_actions:
            split_out.append(f"move actions {unknown_actions} to an app-owned module surface")
        if remaining:
            split_out.append(
                f"declare collections {remaining} under their app-owned module via ownership.surface_id"
            )
        triggers = [str(trigger) for trigger in surface.get("workflow_triggers") or []]
        if triggers:
            split_out.append(
                f"declare workflow_triggers {triggers} on the app-owned surface whose action starts them"
            )
        foreign_integrations = sorted(
            _identifiers(surface.get("integrations")) - _identifiers([facade["provider_module"]])
        ) if facade else []
        if foreign_integrations:
            split_out.append(
                f"move integrations {foreign_integrations} to an app-owned surface; "
                f"{rule.facade_module} integrates only {facade['provider_module']}"
            )
        return _SurfaceClaim(
            rule=rule, facade=facade, name_match=name_match,
            declared_entities=declared_entities, declared_actions=declared_actions,
            unknown_entities=unknown_entities, unknown_actions=unknown_actions,
            remaining=remaining, triggers=triggers, split_out=split_out,
        )

    claims = [(surface, assess(surface)) for surface in surfaces]
    # An app-named surface matched only through a reserved entity or action is an
    # app surface with a wrong claim when it also owns app behavior, including a
    # page that is not sign-in. The remedy is to drop the claim, not to become a
    # platform reference. Decide every such surface before any page moves, so the
    # outcome does not depend on surface order. A surface whose every claim is
    # reserved still normalizes to the canonical owner below.
    for surface, claim in claims:
        if claim is None or claim.name_match:
            continue
        rule = claim.rule
        owned = _identifiers(surface.get("owned_pages"))
        pages = [page for page in spec_pages if str(page.get("name") or "").strip().casefold() in owned]
        kept_pages = [str(page.get("name")) for page in pages]
        sign_in_pages: list[str] = []
        admin_pages: list[str] = []
        # Pages listing the platform's accounts beside fields the accounts do not declare.
        mixed_listings: dict[str, list[str]] = {}
        # Only a platform-auth claim makes a page app evidence: the platform
        # serves sign-in and user administration, nothing else. A managed
        # facade absorbs an entity-matched surface together with its pages.
        page_evidence = False
        if rule.platform_capability == "authentication" and auth_routes is not None:
            credential_fields = _identifiers(rule.identity_evidence_fields)
            accounts = page_accounts.get(id(rule), frozenset())
            listings = {str(page.get("name")): _account_listing(page, rule, accounts) for page in pages}
            admin_pages = [name for name, (lists, extra) in listings.items() if lists and not extra]
            mixed_listings = {name: extra for name, (lists, extra) in listings.items() if lists and extra}
            sign_in_pages = [
                str(page.get("name")) for page in pages
                if not listings[str(page.get("name"))][0]
                and _is_sign_in_page(page, sign_in_routes=sign_in_routes, credential_fields=credential_fields)
            ]
            kept_pages = [
                name for name in kept_pages
                if name not in sign_in_pages and name not in admin_pages and name not in mixed_listings
            ]
            # A page listing the accounts is never app evidence: with extra fields it
            # is judged where the surface normalizes, which names those fields.
            page_evidence = bool(kept_pages)
        if not claim.split_out and not page_evidence:
            continue
        surface_id = surface["surface_id"]
        reserved_entities = [entity for entity in claim.declared_entities if entity not in claim.unknown_entities]
        # An alias (subscribe_user) is the facade's only on a surface that normalizes to it;
        # this surface stays app-owned, so its alias stays its own action.
        kept_actions = [
            action for action in claim.declared_actions
            if action in claim.unknown_actions or action.strip().casefold() in _identifiers(rule.action_aliases)
        ]
        reserved_actions = [action for action in claim.declared_actions if action not in kept_actions]
        platform_identity = identity_claim(surface_id, rule)
        # What the save removes as the owner's state is part of the claim the design must drop.
        claimed_collections = records.get((surface_id, rule.facade_module or "platform"), {}).get(
            "removed_collections", [],
        )
        claimed_events = [
            str(event) for event in surface.get("events_emitted") or []
            if platform_identity is not None
            and _is_lifecycle_event(event, set(platform_identity.domain), _keys(rule.identity_lifecycle_facts))
        ]
        provides = (
            f"bind that UI to {rule.facade_module} and its actions {sorted(_facade_actions(claim.facade))}"
            if claim.facade else "the platform provides them; reference it with owner=platform and config/auth.yaml"
        )
        drop_sign_in = (
            f" Also remove its sign-in pages {sign_in_pages}: the platform serves sign-in."
            if sign_in_pages else ""
        )
        drop_admin = (
            f" Also remove its user-administration pages {admin_pages}: the platform administers users in "
            f"{_admin_panel(rule.user_administration)}."
            if admin_pages else ""
        )
        drop_listings = "".join(
            f" Page {name!r} lists {surface_id!r}'s user accounts but also names fields the accounts do not "
            f"declare: {extra}. The platform administers users in {_admin_panel(rule.user_administration)}: "
            f"drop the page, or keep only those app fields on it."
            for name, extra in mixed_listings.items()
        )
        raise ValueError(
            f"App surface {surface_id!r} claims {rule.owner}: entities {reserved_entities}, actions "
            f"{reserved_actions}"
            + (f", collections {claimed_collections}" if claimed_collections else "")
            + (f", events {claimed_events}" if claimed_events else "")
            + f". Remove those claims from {surface_id!r} ({provides}) and keep it "
            f"app-owned with its entities {claim.unknown_entities}, actions {kept_actions}, "
            f"collections {claim.remaining}, pages {kept_pages}, and workflow_triggers {claim.triggers}."
            f"{drop_sign_in}{drop_admin}{drop_listings}"
        )
    # Every surface left that matches platform authentication normalizes to it,
    # so sibling surfaces are never each other's app-owned co-owners.
    platform_auth_ids = {
        str(surface["surface_id"]) for surface, claim in claims
        if claim is not None and claim.rule.platform_capability == "authentication" and not claim.rule.facade_module
    }
    for surface, claim in claims:
        if claim is None:
            continue
        surface_id = surface["surface_id"]
        rule = claim.rule
        facade = claim.facade
        owner = rule.facade_module or "platform"
        facade_actions = _facade_actions(facade)
        split_out = claim.split_out
        if split_out:
            keep = (
                f"owner=app, surface_id={rule.facade_module}, owned_mutations within {sorted(facade_actions)}"
                if facade else "owner=platform"
            )
            raise ValueError(
                f"Ambiguous surface {surface_id!r} conflicts with {rule.owner}. Split out the app-owned "
                f"behavior: {'; '.join(split_out)}. Then keep {surface_id!r} as a {owner} reference "
                f"({keep}) with no entities, collections, or triggers."
            )
        # Events declared here are the owner's lifecycle events; the app cannot
        # emit them. Only a declared consumer turns removal into a design decision.
        events = [str(event) for event in surface.get("events_emitted") or []]
        consumers = [
            (str(other["surface_id"]), sorted(_identifiers(other.get("workflow_triggers")) & _identifiers(events)))
            for other in surfaces if other is not surface
        ]
        consumers = [(other_id, shared) for other_id, shared in consumers if shared]
        if consumers:
            raise ValueError(
                f"Events {events} on {surface_id!r} are {rule.owner} lifecycle events the app cannot emit, "
                f"but {'; '.join(f'surface {other!r} lists {shared} in workflow_triggers' for other, shared in consumers)}. "
                f"Start that workflow from an app-owned surface's action or domain event, or remove the "
                f"trigger; then {surface_id!r} normalizes to a {owner} reference without events."
            )
        removes_pages = rule.platform_capability == "authentication" and not facade and normalized_spec is not None
        if (
            surface.get("owner") == ("app" if facade else "platform")
            and (not facade or surface_id == rule.facade_module)
            and not surface.get("primary_entities")
            and not (_identifiers(surface.get("owned_mutations")) - _identifiers(facade_actions))
            and not (_identifiers(surface.get("custom_reads")) - _identifiers(facade_actions))
            and not events
            and not (removes_pages and surface.get("owned_pages"))
        ):
            continue
        removed_pages: list[dict[str, Any]] = []
        if removes_pages and normalized_spec is not None and auth_routes is not None:
            removed_pages = _remove_platform_pages(
                surface, surfaces, normalized_spec, rule=rule, sign_in_routes=sign_in_routes,
                account_fields=page_accounts.get(id(rule), frozenset()),
                platform_auth_ids=platform_auth_ids, facade_ids=set(facades), removed_by_name=removed_by_name,
            )
        corrected = deepcopy(surface)
        corrected.update(
            owner="app" if facade else "platform", primary_entities=[], owned_mutations=[], custom_reads=[],
            events_emitted=[],
        )
        if removes_pages:
            corrected["owned_pages"] = []
        released: list[dict[str, Any]] = []
        mapped: list[dict[str, str]] = []
        rebound: list[dict[str, Any]] = []
        if facade:
            corrected.update(
                surface_id=rule.facade_module, surface_kind="module",
                source_capability_packs=[facade["pack_id"]],
                owned_mutations=facade_actions, integrations=[facade["provider_module"]],
            )
            # A page an app-owned surface also owns stays the app's; the facade keeps the rest.
            for name in surface.get("owned_pages") or []:
                co_owners = [
                    str(other["surface_id"]) for other, other_claim in claims
                    if other is not surface and other_claim is None and other.get("owner") == "app"
                    and str(name).strip().casefold() in _identifiers(other.get("owned_pages"))
                ]
                if co_owners:
                    released.append({"name": str(name), "owners": co_owners})
            # The facade takes only pages it serves. Any other page stays with an app surface; with
            # none left to own it, where it goes is the design's call, named for every such surface
            # at once after this loop.
            # Pages the design put on the facade itself are not moving; an owned_pages name with
            # no page is no page at all.
            spec_by_name = {str(page.get("name") or "").strip().casefold(): page for page in spec_pages}
            unserved_pages = [
                (str(name), spec_by_name[str(name).strip().casefold()])
                for name in surface.get("owned_pages") or []
                if surface_id != rule.facade_module and str(name).strip().casefold() in spec_by_name
                and str(name) not in {page["name"] for page in released}
                and not _facade_provides_page(
                    name, spec_by_name[str(name).strip().casefold()], rule, facade, surface_id=str(surface_id),
                )
            ]
            if unserved_pages:
                moving = {name.casefold() for name, _page in unserved_pages}
                facade_page_entries.append((
                    surface, claim, unserved_pages,
                    [
                        (name, entity_of.get((str(surface_id), name), ""))
                        for name in records.get((surface_id, owner), {}).get("removed_collections", [])
                    ],
                    _unserved_bindings(
                        [page for page in spec_pages if str(page.get("name") or "").strip().casefold() not in moving],
                        rule, facade, surface_id=str(surface_id),
                    ),
                ))
                continue
            corrected["owned_pages"] = [
                name for name in surface.get("owned_pages") or []
                if str(name) not in {page["name"] for page in released}
            ]
            aliases = {alias.strip().casefold(): action for alias, action in rule.action_aliases.items()}
            mapped = [
                {"from": str(action), "to": aliases[str(action).strip().casefold()]}
                for action in [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
                if str(action).strip().casefold() in aliases
            ]
            facade_id = str(rule.facade_module)

            def facade_binding(
                action: str, facade_id: str = facade_id, aliases: dict[str, str] = aliases,
                served: list[str] = facade_actions, source: str = str(surface_id),
            ) -> tuple[str, str]:
                target = aliases.get(action.strip().casefold(), action)
                if target not in served:
                    raise ValueError(
                        f"A page section binds {source}.{action}, but {source!r} normalizes to "
                        f"{facade_id}, which does not serve {action!r}. Bind that section to one of "
                        f"{facade_id}'s actions {sorted(served)}."
                    )
                return facade_id, target

            rebound = _rebind_sections(normalized_spec, surface_id, facade_binding) if surface_id != facade_id else []
        if corrected != surface:
            entry = record(surface_id, rule)
            if released:
                entry["released_pages"] = released
            if mapped:
                entry["mapped_actions"] = mapped
            if facade and rebound:
                entry["rebound_sections"] = rebound
            # Actions the catalog reserves are the owner's own identifiers; actions
            # recognized as identity lifecycle are a correction and are recorded.
            platform_identity = identity_claim(surface_id, rule)
            for key, declared in (("removed_mutations", "owned_mutations"), ("removed_reads", "custom_reads")):
                removed_actions = [
                    str(action) for action in surface.get(declared) or []
                    if platform_identity is not None and str(action) in platform_identity.actions
                ]
                if removed_actions:
                    entry[key] = removed_actions
            if events:
                entry["removed_events"] = events
            if removed_pages:
                entry["removed_pages"] = removed_pages
                for page in removed_pages:
                    route = _route_key(page["route"])
                    if page.get("builtin_panel"):
                        # Sibling platform surfaces share the removal; attribute it to one, whatever the order.
                        removals[route] = min(removals.get(route, surface_id), surface_id)
                    elif auth_routes is not None and route != _route_key(auth_routes.login):
                        # A sign-in page designed at the login route itself needs no redirect.
                        redirects.setdefault(route, (surface_id, auth_routes.login))
            targets[surface_id] = corrected["surface_id"]
            surface.update(corrected)
    if facade_page_entries:
        named = {
            name.strip().casefold() for _surface, _claim, pages, _removed, _elsewhere in facade_page_entries
            for name, _page in pages
        }
        # The facade's own pages are completed at save; removal must leave a page the design wrote.
        completed = {
            str(page.get("name")).strip().casefold() for facade in facades.values() for page in facade.get("pages") or []
        }
        raise ValueError(_facade_app_page_message(
            facade_page_entries, facades=facades,
            app_surfaces=[
                str(other["surface_id"]) for other, other_claim in claims
                if other_claim is None and other.get("owner") == "app"
            ],
            removable=bool({str(page.get("name")).strip().casefold() for page in spec_pages} - named - completed),
        ))
    # Sign-in is the platform's even on a surface that stays app-owned: one that
    # declares a platform identity entity, or whose identity records were removed
    # or split out, loses its sign-in actions (login_user) and sign-in events
    # (user_logged_in), and a page section bound to such an action goes with it.
    # Its app data and other actions stay.
    everything = [collection for _, collections in groups for collection in collections]
    for rule in auth_rules:
        identity_entities = _identity_entities(rule, everything)
        account_entities = (
            _keys(rule.user_administration.entity_names) if rule.user_administration else set()
        ) | (identity_entities - _keys([*rule.entity_names, *rule.surface_entity_names]))
        sign_in, facts = _keys(rule.sign_in_verbs), _keys(rule.sign_in_facts)
        for surface, claim in claims:
            surface_id = str(surface["surface_id"])
            if claim is not None or surface.get("owner") != "app" or not (
                any(_related_entity(_key(entity), identity_entities) for entity in surface.get("primary_entities") or [])
                or (surface_id, "platform") in records
            ):
                continue
            declared_by = {"removed_mutations": "owned_mutations", "removed_reads": "custom_reads"}
            sign_in_actions = {
                key: [
                    str(action) for action in surface.get(declared) or []
                    if _is_lifecycle_action(action, account_entities, sign_in)
                ]
                for key, declared in declared_by.items()
            }
            removed_events = [
                str(event) for event in surface.get("events_emitted") or []
                if _is_lifecycle_event(event, account_entities, facts)
            ]
            # Where the surface gave up sign-in (its sign-in actions, or identity records the save
            # removed), a page that is only sign-in is the auth contract's, as on a platform
            # surface: removed, and typed navigation to it points at the login route. Only sign-in:
            # an auth contract route, or every section a credential form or bound to a removed
            # sign-in action. A page with app content beside a password field stays the app's, and
            # a page another surface also owns is left to that surface.
            credential_fields = _identifiers(rule.identity_evidence_fields)
            removed_sign_in = {action for actions in sign_in_actions.values() for action in actions}

            def only_sign_in(page: dict[str, Any], removed_sign_in: set[str] = removed_sign_in,
                             surface_id: str = surface_id, credential_fields: set[str] = credential_fields) -> bool:
                route = _route_key(page.get("route"))
                if route != "/" and any(
                    candidate == route or candidate.startswith(route + "/") for candidate in sign_in_routes
                ):
                    return True
                sections = page.get("sections") or []
                return bool(sections) and all(
                    (section.get("primitive") == "Form"
                     and bool(_typed_form_fields(section.get("config_hint")) & credential_fields))
                    or any(
                        binding == surface_id and action in removed_sign_in
                        for binding, action in _typed_section_actions(section.get("config_hint"))
                    )
                    for section in sections
                )

            gave_up_sign_in = bool(removed_sign_in) or (surface_id, "platform") in records
            owned_sign_in_pages = [
                page for page in (normalized_spec or {}).get("pages") or []
                if gave_up_sign_in
                and str(page.get("name") or "").strip().casefold() in _identifiers(surface.get("owned_pages"))
                and only_sign_in(page)
                and not any(
                    other is not surface
                    and str(page.get("name") or "").strip().casefold() in _identifiers(other.get("owned_pages"))
                    for other in surfaces
                )
            ]
            if not (removed_events or removed_sign_in or owned_sign_in_pages):
                continue
            # The emitting surface's own triggers count too: nothing in the app emits the event any more.
            consumers = [
                (str(other["surface_id"]), sorted(_identifiers(other.get("workflow_triggers")) & _identifiers(removed_events)))
                for other in surfaces
            ]
            consumers = [(other_id, shared) for other_id, shared in consumers if shared]
            if consumers:
                raise ValueError(
                    f"Events {removed_events} on {surface_id!r} are {rule.owner} sign-in events the app cannot "
                    f"emit, but {'; '.join(f'surface {other!r} lists {shared} in workflow_triggers' for other, shared in consumers)}. "
                    "Start that workflow from an app-owned action or domain event, or remove the trigger."
                )
            entry = record(surface_id, rule)
            for sign_in_page in owned_sign_in_pages:
                if normalized_spec is not None:
                    normalized_spec["pages"].remove(sign_in_page)
                surface["owned_pages"] = [
                    name for name in surface.get("owned_pages") or [] if name != sign_in_page.get("name")
                ]
                entry.setdefault("removed_pages", []).append(
                    {"name": sign_in_page.get("name"), "route": sign_in_page.get("route")},
                )
                route = _route_key(sign_in_page.get("route"))
                if auth_routes is not None and route != _route_key(auth_routes.login):
                    redirects.setdefault(route, (surface_id, auth_routes.login))
            if owned_sign_in_pages and not (normalized_spec or {}).get("pages"):
                raise ValueError(
                    f"Removing the sign-in page(s) {[item.get('name') for item in owned_sign_in_pages]} owned by "
                    f"{surface_id!r} leaves no approved pages. Design the app's own pages under app-owned surfaces."
                )
            removed = {action for actions in sign_in_actions.values() for action in actions}
            unbound = _rebind_sections(
                normalized_spec, surface_id,
                lambda action, removed=removed, surface_id=surface_id: None if action in removed else (surface_id, action),
            )
            if unbound:
                entry["removed_sections"] = [*entry.get("removed_sections", []), *unbound]
            for key, actions in sign_in_actions.items():
                if actions:
                    surface[declared_by[key]] = [
                        action for action in surface.get(declared_by[key]) or [] if str(action) not in actions
                    ]
                    entry[key] = [*entry.get(key, []), *actions]
            if removed_events:
                surface["events_emitted"] = [
                    event for event in surface.get("events_emitted") or [] if str(event) not in removed_events
                ]
                entry["removed_events"] = [*entry.get("removed_events", []), *removed_events]
    if redirects or removals:
        redirected, dropped = _redirect_navigation(normalized_spec or {}, redirects, removals)
        for surface_id, entries in redirected.items():
            records[(surface_id, "platform")]["redirected_navigation"] = entries
        for surface_id, entries in dropped.items():
            records[(surface_id, "platform")]["removed_navigation"] = entries

    # Several duplicate provider surfaces may map to one already materialized
    # facade. Its approved pages are a union, never replaced by pack defaults.
    merged: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        surface_id = surface["surface_id"]
        if surface_id in merged:
            if surface_id not in facades:
                raise ValueError(f"Ambiguous duplicate surface {surface_id!r}.")
            merged[surface_id]["owned_pages"] = list(dict.fromkeys([
                *(merged[surface_id].get("owned_pages") or []), *(surface.get("owned_pages") or []),
            ]))
        else:
            merged[surface_id] = surface
    normalized_map["surfaces"] = list(merged.values())
    data_groups: dict[str, dict[str, Any]] = {}
    for group in normalized_data.get("surfaces") or []:
        group["surface_id"] = targets.get(group["surface_id"], group["surface_id"])
        if group["surface_id"] in facades:
            group["surface_kind"] = "module"
        if group["surface_id"] in data_groups:
            data_groups[group["surface_id"]]["collections"].extend(group["collections"])
        else:
            data_groups[group["surface_id"]] = group
    # A data group left empty for a surface the map does not declare carries nothing.
    declared_ids = set(merged)
    normalized_data["surfaces"] = [
        group for group in data_groups.values() if group["collections"] or group["surface_id"] in declared_ids
    ]
    validate_surface_ownership(
        normalized_map, context_variables=context_variables, data_contract=normalized_data,
        include_default_subscription=include_default_subscription,
    )
    surface_map.update(normalized_map)
    data_contract.update(normalized_data)
    if experience_spec is not None and normalized_spec is not None:
        experience_spec.update(normalized_spec)
    return list(records.values())
