"""
AppGenerator capability pack selection integration tests.

Validates that the messaging capability pack is:
  - Registered in capability_routing.yaml under capability_pack.packs
  - Registered in capability_directory.yaml with complete metadata
  - Self-consistent between context.yaml, contract.yaml, and module.yaml
  - Producing the correct required and optional output families
  - Declaring profile panels that bind to actions declared in module.yaml
  - Including a contacts module with complete contract files

These are static YAML-parse tests — no live LLM calls.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKSPACE = Path(__file__).resolve().parents[1]
_BUILD_CONTEXT = _WORKSPACE / "factory_app" / "build_context"


def _read_yaml(relative_path: str):
    return yaml.safe_load((_WORKSPACE / relative_path).read_text(encoding="utf-8"))


def _pack_path(pack_id: str) -> Path:
    return _BUILD_CONTEXT / pack_id


def _module_path(pack_id: str, module_id: str) -> Path:
    return _pack_path(pack_id) / "templates" / "modules" / module_id


# ---------------------------------------------------------------------------
# capability_routing.yaml — pack registration
# ---------------------------------------------------------------------------


class TestCapabilityRoutingRegistration:
    """messaging pack must be listed under capability_pack.packs."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.routing = _read_yaml(
            "factory_app/build_context/AppGenerator/capability_routing.yaml"
        )
        self.packs = {
            p["id"]: p
            for p in self.routing["layers"]["capability_pack"]["packs"]
        }

    def test_messaging_registered(self) -> None:
        assert "messaging" in self.packs

    def test_messaging_has_capability_kind_operator_pack(self) -> None:
        assert self.packs["messaging"]["capability_kind"] == "operator_pack"

    def test_messaging_covers_dm_threads(self) -> None:
        covers = self.packs["messaging"]["covers"].lower()
        assert "dm" in covers or "thread" in covers

    def test_messaging_has_use_when(self) -> None:
        assert "use_when" in self.packs["messaging"]
        assert self.packs["messaging"]["use_when"]

    def test_messaging_has_avoid_when(self) -> None:
        assert "avoid_when" in self.packs["messaging"]

    def test_social_graph_not_registered(self) -> None:
        assert "social_graph" not in self.packs


# ---------------------------------------------------------------------------
# capability_directory.yaml — full metadata registration
# ---------------------------------------------------------------------------


class TestCapabilityDirectoryRegistration:
    """messaging pack must have a rich entry in capability_directory.yaml."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.directory = _read_yaml(
            "factory_app/build_context/AppGenerator/capability_directory.yaml"
        )
        self.by_id = {c["id"]: c for c in self.directory["capabilities"]}

    def test_messaging_pack_registered(self) -> None:
        assert "messaging_pack" in self.by_id

    def test_social_graph_pack_not_registered(self) -> None:
        assert "social_graph_pack" not in self.by_id

    def test_messaging_pack_capability_kind_is_operator_pack(self) -> None:
        assert self.by_id["messaging_pack"]["capability_kind"] == "operator_pack"

    def test_messaging_pack_has_intent_signals(self) -> None:
        signals = self.by_id["messaging_pack"].get("intent_signals", [])
        assert len(signals) >= 1

    def test_messaging_pack_intent_signals_mention_messaging(self) -> None:
        text = " ".join(self.by_id["messaging_pack"]["intent_signals"]).lower()
        assert "message" in text or "chat" in text or "thread" in text

    def test_messaging_pack_has_selection_aliases(self) -> None:
        aliases = self.by_id["messaging_pack"].get("selection_aliases", [])
        assert len(aliases) >= 1

    def test_messaging_pack_has_domains(self) -> None:
        domains = self.by_id["messaging_pack"].get("domains", [])
        assert len(domains) >= 1
        assert any("messag" in d or "chat" in d for d in domains)

    def test_messaging_pack_has_generator_notes(self) -> None:
        notes = self.by_id["messaging_pack"].get("generator_notes", [])
        assert len(notes) >= 1

    def test_messaging_pack_generator_notes_warn_against_regenerating_internals(self) -> None:
        text = " ".join(self.by_id["messaging_pack"]["generator_notes"]).lower()
        assert "intern" in text or "regenerat" in text or "never" in text

    def test_messaging_pack_lists_capabilities_provided(self) -> None:
        caps = self.by_id["messaging_pack"].get("capabilities_provided", [])
        assert len(caps) >= 1
        assert any("messaging" in c for c in caps)

    def test_messaging_pack_capabilities_include_contacts(self) -> None:
        caps = self.by_id["messaging_pack"].get("capabilities_provided", [])
        assert "messaging.contacts.list" in caps
        assert "messaging.contacts.add" in caps


# ---------------------------------------------------------------------------
# Pack context.yaml — applies_to_workflows
# ---------------------------------------------------------------------------


class TestPackContextFiles:
    """context.yaml for the messaging pack must declare AppGenerator in applies_to_workflows."""

    def test_messaging_context_applies_to_appgenerator(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        assert "AppGenerator" in ctx["applies_to_workflows"]

    def test_messaging_context_has_pack_descriptor(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        assert "pack" in ctx
        assert ctx["pack"]["id"] == "messaging"
        assert ctx["pack"]["status"] == "active"

    def test_messaging_context_declares_contract_asset(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        kinds = {a["kind"] for a in ctx["assets"]}
        assert "contract" in kinds

    def test_messaging_context_declares_templates_asset(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        kinds = {a["kind"] for a in ctx["assets"]}
        assert "templates" in kinds

    def test_messaging_context_capabilities_match_module_yaml(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        messages_module = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/module.yaml"
        )
        contacts_module = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/contacts/module.yaml"
        )
        ctx_cap_ids = {c["capability_id"] for c in ctx["capabilities"]}
        all_module_cap_ids = (
            {c["capability_id"] for c in messages_module["capabilities"]}
            | {c["capability_id"] for c in contacts_module["capabilities"]}
        )
        # All context capabilities must be declared in one of the pack modules
        assert ctx_cap_ids.issubset(all_module_cap_ids), (
            f"context.yaml capabilities not in any module.yaml: {ctx_cap_ids - all_module_cap_ids}"
        )

    def test_messaging_context_includes_contacts_capabilities(self) -> None:
        ctx = _read_yaml("factory_app/build_context/messaging/context.yaml")
        cap_ids = {c["capability_id"] for c in ctx["capabilities"]}
        assert "messaging.contacts.list" in cap_ids
        assert "messaging.contacts.add" in cap_ids


# ---------------------------------------------------------------------------
# Pack contract.yaml — required outputs
# ---------------------------------------------------------------------------


class TestPackContractFiles:
    """contract.yaml for the messaging pack must declare required outputs."""

    def test_messaging_contract_exists(self) -> None:
        path = _pack_path("messaging") / "contract.yaml"
        assert path.exists()

    def test_messaging_contract_has_required_outputs(self) -> None:
        c = _read_yaml("factory_app/build_context/messaging/contract.yaml")
        assert "required_outputs" in c
        assert len(c["required_outputs"]) >= 1

    def _output_paths(self, entries: list) -> list[str]:
        """Extract the canonical path string from each required_output entry."""
        return [
            o if isinstance(o, str) else o.get("path", o.get("id", ""))
            for o in entries
        ]

    def test_messaging_contract_requires_module_yaml(self) -> None:
        c = _read_yaml("factory_app/build_context/messaging/contract.yaml")
        paths = self._output_paths(c["required_outputs"])
        assert any("module" in r for r in paths)

    def test_messaging_contract_requires_contacts_module_yaml(self) -> None:
        c = _read_yaml("factory_app/build_context/messaging/contract.yaml")
        paths = self._output_paths(c["required_outputs"])
        assert any("contacts/module.yaml" in r for r in paths)

    def test_messaging_contract_requires_contacts_events_yaml(self) -> None:
        c = _read_yaml("factory_app/build_context/messaging/contract.yaml")
        paths = self._output_paths(c["required_outputs"])
        assert any("contacts/contracts/events.yaml" in r for r in paths)

    def test_messaging_contract_cross_pack_integrations_has_no_social_graph(self) -> None:
        c = _read_yaml("factory_app/build_context/messaging/contract.yaml")
        integrations = c.get("cross_pack_integrations", [])
        pack_ids = [
            i if isinstance(i, str) else i.get("pack", "")
            for i in integrations
        ]
        assert "social_graph" not in pack_ids


# ---------------------------------------------------------------------------
# Module contract files — messaging pack
# ---------------------------------------------------------------------------


class TestMessagingModuleContract:
    """messages module.yaml is self-consistent and complete."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.module = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/module.yaml"
        )
        self.action_ids = {a["id"] for a in self.module["actions"]}
        self.cap_targets = {c["target"] for c in self.module["capabilities"]}

    def test_module_id_is_messages(self) -> None:
        assert self.module["module"]["id"] == "messages"

    def test_list_threads_action_exists(self) -> None:
        assert "list_threads" in self.action_ids

    def test_send_message_action_exists(self) -> None:
        assert "send_message" in self.action_ids

    def test_get_unread_summary_action_exists(self) -> None:
        assert "get_unread_summary" in self.action_ids

    def test_edit_message_action_exists(self) -> None:
        assert "edit_message" in self.action_ids

    def test_delete_message_action_exists(self) -> None:
        assert "delete_message" in self.action_ids

    def test_capability_targets_are_valid_action_ids(self) -> None:
        invalid = self.cap_targets - self.action_ids
        assert not invalid, f"Capability targets not in actions: {invalid}"

    def test_list_threads_has_cursor_pagination(self) -> None:
        lt = next(a for a in self.module["actions"] if a["id"] == "list_threads")
        props = lt["input_schema"].get("properties", {})
        assert "before" in props
        out_props = lt["output_schema"].get("properties", {})
        assert "next_cursor" in out_props

    def test_send_message_emits_event(self) -> None:
        sm = next(a for a in self.module["actions"] if a["id"] == "send_message")
        assert "app.messages.message.sent" in sm.get("emits", [])


class TestContactsModuleContract:
    """contacts module.yaml is self-consistent and complete."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.module = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/contacts/module.yaml"
        )
        self.action_ids = {a["id"] for a in self.module["actions"]}
        self.cap_targets = {c["target"] for c in self.module["capabilities"]}

    def test_module_id_is_contacts(self) -> None:
        assert self.module["module"]["id"] == "contacts"

    def test_list_contacts_action_exists(self) -> None:
        assert "list_contacts" in self.action_ids

    def test_add_contact_action_exists(self) -> None:
        assert "add_contact" in self.action_ids

    def test_remove_contact_action_exists(self) -> None:
        assert "remove_contact" in self.action_ids

    def test_get_contact_action_exists(self) -> None:
        assert "get_contact" in self.action_ids

    def test_capability_targets_are_valid_action_ids(self) -> None:
        invalid = self.cap_targets - self.action_ids
        assert not invalid, f"Capability targets not in actions: {invalid}"

    def test_list_contacts_has_cursor_pagination(self) -> None:
        lc = next(a for a in self.module["actions"] if a["id"] == "list_contacts")
        props = lc["input_schema"].get("properties", {})
        assert "before" in props
        out_props = lc["output_schema"].get("properties", {})
        assert "next_cursor" in out_props

    def test_add_contact_emits_event(self) -> None:
        ac = next(a for a in self.module["actions"] if a["id"] == "add_contact")
        assert "app.contacts.contact.added" in ac.get("emits", [])

    def test_remove_contact_emits_event(self) -> None:
        rc = next(a for a in self.module["actions"] if a["id"] == "remove_contact")
        assert "app.contacts.contact.removed" in rc.get("emits", [])


# ---------------------------------------------------------------------------
# Profile panel contracts
# ---------------------------------------------------------------------------


class TestProfilePanelContracts:
    """messaging and contacts packs declare valid profile.yaml panels that bind to real module actions."""

    def _module_action_ids(self, pack_id: str, module_id: str) -> set[str]:
        module = _read_yaml(
            f"factory_app/build_context/{pack_id}/templates/modules/{module_id}/module.yaml"
        )
        return {a["id"] for a in module["actions"]}

    def test_messaging_profile_yaml_exists(self) -> None:
        path = (
            _pack_path("messaging")
            / "templates" / "modules" / "messages" / "contracts" / "profile.yaml"
        )
        assert path.exists(), "messaging profile.yaml missing"

    def test_messaging_profile_schema_version(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        assert p["schema_version"] == "mozaiks.profile.v1"

    def test_messaging_profile_has_panels(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        assert len(p["panels"]) >= 1

    def test_messaging_profile_panels_bind_to_real_actions(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        action_ids = self._module_action_ids("messaging", "messages")
        for panel in p["panels"]:
            if "action" in panel:
                assert panel["action"] in action_ids, (
                    f"Profile panel '{panel['id']}' binds to undeclared action '{panel['action']}'"
                )

    def test_messaging_profile_panels_have_no_form_kind(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        for panel in p["panels"]:
            assert panel.get("kind") != "form", (
                f"Profile panel '{panel['id']}' uses kind=form which is reserved"
            )

    def test_messaging_profile_panel_has_valid_kind(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        valid_kinds = {"metrics", "list", "component"}
        for panel in p["panels"]:
            assert panel["kind"] in valid_kinds, (
                f"Invalid panel kind '{panel['kind']}' in messaging profile.yaml"
            )

    def test_messaging_profile_messaging_summary_has_unread_count(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/messages/contracts/profile.yaml"
        )
        field_ids = {
            f["id"]
            for panel in p["panels"]
            for f in panel.get("fields", [])
        }
        assert "unread_thread_count" in field_ids

    def test_contacts_profile_yaml_exists(self) -> None:
        path = (
            _pack_path("messaging")
            / "templates" / "modules" / "contacts" / "contracts" / "profile.yaml"
        )
        assert path.exists(), "contacts profile.yaml missing"

    def test_contacts_profile_schema_version(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/contacts/contracts/profile.yaml"
        )
        assert p["schema_version"] == "mozaiks.profile.v1"

    def test_contacts_profile_panels_bind_to_real_actions(self) -> None:
        p = _read_yaml(
            "factory_app/build_context/messaging/templates/modules/contacts/contracts/profile.yaml"
        )
        action_ids = self._module_action_ids("messaging", "contacts")
        for panel in p["panels"]:
            if "action" in panel:
                assert panel["action"] in action_ids


# ---------------------------------------------------------------------------
# Template file presence checks
# ---------------------------------------------------------------------------


class TestPackTemplatePresence:
    """Key template files must be present in the messaging pack."""

    _MESSAGING_MODULE = "factory_app/build_context/messaging/templates/modules/messages"
    _CONTACTS_MODULE = "factory_app/build_context/messaging/templates/modules/contacts"

    _MESSAGING_PAGES = "factory_app/build_context/messaging/templates/ui/pages"

    @pytest.mark.parametrize("relative_path", [
        f"{_MESSAGING_MODULE}/module.yaml",
        f"{_MESSAGING_MODULE}/backend/__init__.py",
        f"{_MESSAGING_MODULE}/backend/handler.py",
        f"{_MESSAGING_MODULE}/backend/service.py",
        f"{_MESSAGING_MODULE}/backend/repo.py",
        f"{_MESSAGING_MODULE}/backend/schemas.py",
        f"{_MESSAGING_MODULE}/backend/policy.py",
        f"{_MESSAGING_MODULE}/contracts/events.yaml",
        f"{_MESSAGING_MODULE}/contracts/admin.yaml",
        f"{_MESSAGING_MODULE}/contracts/settings.yaml",
        f"{_MESSAGING_MODULE}/contracts/profile.yaml",
        # custom_route_bundle — real-time split-panel messaging inbox
        f"{_MESSAGING_PAGES}/custom/Messages.jsx",
        # contacts module files
        f"{_CONTACTS_MODULE}/module.yaml",
        f"{_CONTACTS_MODULE}/backend/__init__.py",
        f"{_CONTACTS_MODULE}/backend/handler.py",
        f"{_CONTACTS_MODULE}/backend/service.py",
        f"{_CONTACTS_MODULE}/backend/repo.py",
        f"{_CONTACTS_MODULE}/backend/schemas.py",
        f"{_CONTACTS_MODULE}/backend/policy.py",
        f"{_CONTACTS_MODULE}/contracts/events.yaml",
        f"{_CONTACTS_MODULE}/contracts/profile.yaml",
        # declarative AppPageSchema pages
        f"{_MESSAGING_PAGES}/contacts.yaml",
        # route manifest for custom bundle registration
        "factory_app/build_context/messaging/templates/ui/route_manifest.json",
    ])
    def test_messaging_template_file_exists(self, relative_path: str) -> None:
        assert (_WORKSPACE / relative_path).exists(), f"Missing: {relative_path}"
