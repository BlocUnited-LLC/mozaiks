from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_contracts = import_module_directly("mozaiksai.core.orchestration.planning_contracts")


def _decomposition_payload() -> dict:
    return {
        "app_spec": {
            "name": "CampusMarket",
            "summary": "Student marketplace",
            "user_personas": ["student", "seller", "admin"],
            "bounded_contexts": ["marketplace", "messaging"],
            "core_jobs": ["browse listings", "save listing", "request assistance"],
        },
        "capabilities": [
            {
                "capability_id": "browse_listings",
                "label": "Browse listings",
                "primary_surface": "module",
                "actor": "student",
                "module_refs": ["marketplace_home"],
                "view_refs": ["listings_list"],
                "entity_refs": ["Listing"],
            },
            {
                "capability_id": "assistant_flow",
                "label": "Assistant flow",
                "primary_surface": "workflow",
                "actor": "student",
                "workflow_refs": ["Concierge"],
                "event_refs": ["listing.saved"],
                "automation_route_refs": ["route_saved_listing_followup"],
            },
        ],
        "entities": [
            {
                "name": "Listing",
                "purpose": "Sell items",
                "key_fields": [{"name": "title", "field_type": "string", "required": True}],
            }
        ],
        "views": [
            {
                "name": "listings_list",
                "view_type": "list",
                "entity": "Listing",
                "module": "marketplace_home",
                "fields": ["title"],
            }
        ],
        "actions": [],
        "modules": [
            {
                "name": "marketplace_home",
                "purpose": "Main marketplace page",
                "route": "/marketplace",
                "primary_views": ["listings_list"],
            }
        ],
        "events": [
            {
                "event_type": "listing.saved",
                "producer": "mozaikscore",
                "source_event": "listing_saved",
                "correlation_keys": ["listing_id", "user_id"],
                "post_commit_only": True,
            }
        ],
        "automation_routes": [
            {
                "route_id": "route_saved_listing_followup",
                "event_type": "listing.saved",
                "effect": {
                    "kind": "workflow.run",
                    "workflow": "Concierge",
                    "surface": "background",
                },
                "bindings": {
                    "app_id": "tenant.app_id",
                    "user_id": "tenant.user_id",
                },
            }
        ],
        "workflows": [
            {
                "name": "Concierge",
                "purpose": "Buyer assistance workflow",
                "entry_reason": "Needs conversational discovery",
            }
        ],
        "policies": [],
        "bundle_plan": {
            "manifest_paths": ["platform/app.json"],
            "shell_paths": [
                "platform/shell/navigation.json",
                "platform/shell/theme.json",
            ],
            "substrate_paths": ["platform/data/entities/listing.json"],
            "module_paths": ["platform/modules/marketplace_home/module.json"],
            "automation_paths": [
                "platform/automations/event_catalog.json",
                "platform/automations/routes.json",
            ],
            "workflow_paths": ["platform/workflows/Concierge/orchestrator.yaml"],
        },
    }


def _builder_blueprint_payload() -> dict:
    return {
        "concept_blueprint": {
            "intent_mode": "create",
            "app_name": "CampusMarket",
            "product_summary": "Students browse listings and can invoke an assistant for help.",
            "value_proposition": "Launch a useful campus marketplace on top of core platform primitives.",
            "primary_users": ["student", "seller"],
            "approved_scope": ["browse listings", "save listings", "assistant follow-up"],
            "core_outcomes": ["usable marketplace", "assistant-driven follow-up"],
            "success_signals": ["students can browse and save listings"],
            "approval_notes": ["Prefer core platform capabilities over bespoke runtime invention"],
        },
        "intent_brief": {
            "source_request": "Build a student marketplace with an AI buyer assistant.",
            "product_summary": "Students browse listings and can invoke an assistant for help.",
            "user_personas": ["student", "seller"],
            "bounded_contexts": ["marketplace", "assistant"],
            "business_entities": ["Listing"],
            "constraints": ["mobile first"],
            "success_criteria": ["students can browse and save listings"],
        },
        "capability_map": {
            "capabilities": [
                {
                    "capability_id": "browse_listings",
                    "label": "Browse listings",
                    "summary": "Students explore marketplace inventory.",
                    "actor": "student",
                    "primary_surface": "module",
                    "requires_durable_state": True,
                    "module_candidates": ["marketplace_home"],
                    "entity_candidates": ["Listing"],
                },
                {
                    "capability_id": "assistant_flow",
                    "label": "Assistant flow",
                    "summary": "Students can ask for buying help.",
                    "actor": "student",
                    "primary_surface": "workflow",
                    "requires_reasoning": True,
                    "can_be_event_triggered": True,
                    "workflow_candidates": ["Concierge"],
                    "notes": ["Can be launched directly or from listing.saved"],
                },
            ]
        },
        "platform_provision_plan": {
            "provisions": [
                {
                    "provision_id": "auth-runtime",
                    "label": "Auth runtime",
                    "category": "auth",
                    "runtime_owner": "mozaikscore",
                    "mode": "core_configured",
                    "summary": "Use the core auth system and app manifest auth metadata.",
                    "config_paths": ["platform/app.json"],
                },
                {
                    "provision_id": "shell-surface",
                    "label": "Shell surface",
                    "category": "shell",
                    "runtime_owner": "shared",
                    "mode": "core_configured",
                    "summary": "Use the platform shell primitives for navigation and theme.",
                    "config_paths": [
                        "platform/shell/navigation.json",
                        "platform/shell/theme.json",
                    ],
                },
                {
                    "provision_id": "automation-transport",
                    "label": "Automation transport",
                    "category": "automation_transport",
                    "runtime_owner": "shared",
                    "mode": "core_provided",
                    "summary": "Use the core domain-event transport for automation delivery.",
                    "notes": ["No app-authored broker implementation is required."],
                },
            ]
        },
        "decomposition": _decomposition_payload(),
        "build_graph": {
            "tasks": [
                {
                    "task_id": "intent",
                    "title": "Normalize user request",
                    "builder_workflow": "IntentModeler",
                    "produces": ["intent_brief"],
                },
                {
                    "task_id": "architecture",
                    "title": "Finalize app and capability models",
                    "builder_workflow": "ArchitecturePlanner",
                    "depends_on": ["intent"],
                    "consumes": ["intent_brief"],
                    "produces": ["decomposition"],
                },
                {
                    "task_id": "compile_manifest",
                    "title": "Write app manifest",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "provision_refs": ["auth-runtime"],
                    "declarative_families": ["app_manifest"],
                    "bundle_paths": ["platform/app.json"],
                },
                {
                    "task_id": "compile_shell",
                    "title": "Write shell declaratives",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "provision_refs": ["shell-surface"],
                    "declarative_families": ["shell"],
                    "bundle_paths": [
                        "platform/shell/navigation.json",
                        "platform/shell/theme.json",
                    ],
                },
                {
                    "task_id": "compile_substrate",
                    "title": "Write substrate declaratives",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "capability_refs": ["browse_listings"],
                    "declarative_families": ["app_substrate"],
                    "bundle_paths": ["platform/data/entities/listing.json"],
                },
                {
                    "task_id": "compile_modules",
                    "title": "Write module declaratives",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "capability_refs": ["browse_listings"],
                    "declarative_families": ["modules"],
                    "bundle_paths": ["platform/modules/marketplace_home/module.json"],
                },
                {
                    "task_id": "compile_automation",
                    "title": "Write automation declaratives",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "capability_refs": ["assistant_flow"],
                    "provision_refs": ["automation-transport"],
                    "declarative_families": ["automation"],
                    "bundle_paths": [
                        "platform/automations/event_catalog.json",
                        "platform/automations/routes.json",
                    ],
                },
                {
                    "task_id": "author_workflow",
                    "title": "Author concierge workflow",
                    "builder_workflow": "WorkflowAuthor",
                    "depends_on": ["architecture"],
                    "capability_refs": ["assistant_flow"],
                    "declarative_families": ["workflows"],
                    "bundle_paths": ["platform/workflows/Concierge/orchestrator.yaml"],
                },
                {
                    "task_id": "validate",
                    "title": "Validate bundle",
                    "builder_workflow": "Validator",
                    "depends_on": [
                        "compile_manifest",
                        "compile_shell",
                        "compile_substrate",
                        "compile_modules",
                        "compile_automation",
                        "author_workflow",
                    ],
                    "report_paths": ["build/reports/validation.json"],
                },
            ],
            "entry_tasks": ["intent"],
            "terminal_tasks": ["validate"],
        },
    }


def _flagship_platform_blueprint_payload() -> dict:
    return {
        "concept_blueprint": {
            "intent_mode": "create",
            "app_name": "My App",
            "product_summary": "A flagship backstage experience with workflow orchestration and durable modules.",
            "value_proposition": "Show the full runtime stack with a demo app built mostly from core primitives.",
            "primary_users": ["creator", "operator"],
            "approved_scope": ["platform admin module", "GreenRoom workflow trigger"],
            "core_outcomes": ["show shell, modules, automation, and workflows together"],
            "success_signals": ["user can reach platform admin and GreenRoom surfaces"],
        },
        "intent_brief": {
            "source_request": "Build the flagship runtime demo with admin surfaces and comedy workflows.",
            "product_summary": "The app should showcase durable modules plus workflow-triggered AI surfaces.",
            "user_personas": ["creator", "operator"],
            "bounded_contexts": ["platform", "comedy"],
            "business_entities": ["SetBrief"],
            "constraints": ["reuse core platform capabilities"],
            "success_criteria": ["navigation loads platform surfaces", "automation can launch GreenRoom"],
        },
        "capability_map": {
            "capabilities": [
                {
                    "capability_id": "platform_admin",
                    "label": "Platform admin",
                    "summary": "Operators can enter a durable admin surface from the shell.",
                    "actor": "operator",
                    "primary_surface": "module",
                    "requires_durable_state": True,
                    "module_candidates": ["admin_portal"],
                },
                {
                    "capability_id": "greenroom_automation",
                    "label": "GreenRoom automation",
                    "summary": "Automation can launch GreenRoom from substrate-side facts.",
                    "actor": "creator",
                    "primary_surface": "workflow",
                    "requires_reasoning": True,
                    "can_be_event_triggered": True,
                    "workflow_candidates": ["GreenRoom"],
                    "notes": ["Triggered from report.requested in the flagship bundle."],
                },
            ]
        },
        "platform_provision_plan": {
            "provisions": [
                {
                    "provision_id": "app-auth",
                    "label": "App auth",
                    "category": "auth",
                    "runtime_owner": "mozaikscore",
                    "mode": "core_configured",
                    "summary": "Use core auth and mobile/web platform metadata from the app manifest.",
                    "config_paths": ["platform/app.json"],
                },
                {
                    "provision_id": "shell-config",
                    "label": "Shell config",
                    "category": "shell",
                    "runtime_owner": "shared",
                    "mode": "core_configured",
                    "summary": "Use the current flagship shell projections in platform/config.",
                    "config_paths": [
                        "platform/config/navigation_config.json",
                        "platform/config/theme_config.json",
                    ],
                },
                {
                    "provision_id": "automation-transport",
                    "label": "Automation transport",
                    "category": "automation_transport",
                    "runtime_owner": "shared",
                    "mode": "core_provided",
                    "summary": "Use core substrate-to-AI transport without app-authored broker code.",
                },
            ]
        },
        "decomposition": {
            "app_spec": {
                "name": "My App",
                "summary": "Flagship runtime demo",
                "user_personas": ["creator", "operator"],
                "bounded_contexts": ["platform", "comedy"],
                "core_jobs": ["open admin portal", "trigger GreenRoom automation"],
                "constraints": ["reuse existing platform output layout"],
            },
            "capabilities": [
                {
                    "capability_id": "platform_admin",
                    "label": "Platform admin",
                    "primary_surface": "module",
                    "actor": "operator",
                    "module_refs": ["admin_portal"],
                },
                {
                    "capability_id": "greenroom_automation",
                    "label": "GreenRoom automation",
                    "primary_surface": "workflow",
                    "actor": "creator",
                    "workflow_refs": ["GreenRoom"],
                    "event_refs": ["report.requested"],
                    "automation_route_refs": ["greenroom-report-request"],
                },
            ],
            "entities": [],
            "views": [],
            "actions": [],
            "modules": [
                {
                    "name": "admin_portal",
                    "purpose": "Flagship platform overview",
                    "route": "/platform",
                }
            ],
            "events": [
                {
                    "event_type": "report.requested",
                    "producer": "mozaikscore",
                    "source_event": "report_requested",
                    "correlation_keys": ["app_id", "user_id"],
                    "post_commit_only": True,
                }
            ],
            "automation_routes": [
                {
                    "route_id": "greenroom-report-request",
                    "event_type": "report.requested",
                    "when": {"payload.workflow": "GreenRoom"},
                    "effect": {
                        "kind": "workflow.run",
                        "workflow": "GreenRoom",
                        "surface": "background",
                    },
                    "bindings": {
                        "app_id": "tenant.app_id",
                        "user_id": "tenant.user_id",
                    },
                }
            ],
            "workflows": [
                {
                    "name": "GreenRoom",
                    "purpose": "Warm conversational intake for the backstage demo",
                    "entry_reason": "Needs reasoning, clarifying questions, and artifact setup",
                }
            ],
            "policies": [],
            "bundle_plan": {
                "manifest_paths": ["platform/app.json"],
                "shell_paths": [
                    "platform/config/navigation_config.json",
                    "platform/config/theme_config.json",
                ],
                "substrate_paths": [],
                "module_paths": ["platform/modules/admin_portal/module.json"],
                "automation_paths": [
                    "platform/automations/event_catalog.json",
                    "platform/automations/routes.json",
                ],
                "workflow_paths": ["platform/workflows/GreenRoom/orchestrator.yaml"],
            },
        },
        "build_graph": {
            "tasks": [
                {
                    "task_id": "intent",
                    "title": "Normalize flagship intent",
                    "builder_workflow": "IntentModeler",
                    "produces": ["concept_blueprint", "intent_brief"],
                },
                {
                    "task_id": "architecture",
                    "title": "Plan flagship architecture",
                    "builder_workflow": "ArchitecturePlanner",
                    "depends_on": ["intent"],
                    "consumes": ["concept_blueprint", "intent_brief"],
                    "produces": ["decomposition", "platform_provision_plan"],
                },
                {
                    "task_id": "compile_manifest",
                    "title": "Write app manifest",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "provision_refs": ["app-auth"],
                    "declarative_families": ["app_manifest"],
                    "bundle_paths": ["platform/app.json"],
                },
                {
                    "task_id": "compile_shell",
                    "title": "Write flagship shell config",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "provision_refs": ["shell-config"],
                    "declarative_families": ["shell"],
                    "bundle_paths": [
                        "platform/config/navigation_config.json",
                        "platform/config/theme_config.json",
                    ],
                },
                {
                    "task_id": "compile_modules",
                    "title": "Write admin module",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "capability_refs": ["platform_admin"],
                    "declarative_families": ["modules"],
                    "bundle_paths": ["platform/modules/admin_portal/module.json"],
                },
                {
                    "task_id": "compile_automation",
                    "title": "Write flagship automation",
                    "builder_workflow": "BundleCompiler",
                    "depends_on": ["architecture"],
                    "capability_refs": ["greenroom_automation"],
                    "provision_refs": ["automation-transport"],
                    "declarative_families": ["automation"],
                    "bundle_paths": [
                        "platform/automations/event_catalog.json",
                        "platform/automations/routes.json",
                    ],
                },
                {
                    "task_id": "author_greenroom",
                    "title": "Author GreenRoom workflow",
                    "builder_workflow": "WorkflowAuthor",
                    "depends_on": ["architecture"],
                    "capability_refs": ["greenroom_automation"],
                    "declarative_families": ["workflows"],
                    "bundle_paths": ["platform/workflows/GreenRoom/orchestrator.yaml"],
                },
                {
                    "task_id": "validate",
                    "title": "Validate flagship bundle",
                    "builder_workflow": "Validator",
                    "depends_on": [
                        "compile_manifest",
                        "compile_shell",
                        "compile_modules",
                        "compile_automation",
                        "author_greenroom",
                    ],
                    "report_paths": ["build/reports/flagship-validation.json"],
                },
            ],
            "entry_tasks": ["intent"],
            "terminal_tasks": ["validate"],
        },
    }


def test_build_builder_blueprint_valid_payload() -> None:
    blueprint = _contracts.build_builder_blueprint(_builder_blueprint_payload())
    assert blueprint.concept_blueprint.value_proposition.startswith("Launch a useful")
    assert blueprint.intent_brief.product_summary.startswith("Students browse")
    assert blueprint.build_graph.tasks[-1].builder_workflow == _contracts.BuilderWorkflowRole.VALIDATOR


def test_build_task_rejects_disallowed_family_for_bundle_compiler() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][2]["declarative_families"] = ["workflows"]
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_build_graph_rejects_duplicate_bundle_path_ownership() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][3]["bundle_paths"].append("platform/app.json")
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_builder_blueprint_requires_full_bundle_plan_ownership() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][6]["bundle_paths"] = ["platform/automations/event_catalog.json"]
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_capability_map_requires_candidate_for_primary_surface() -> None:
    payload = _builder_blueprint_payload()
    payload["capability_map"]["capabilities"][0]["module_candidates"] = []
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_build_task_requires_capability_or_provision_refs_for_bundle_paths() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][2]["provision_refs"] = []
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_builder_blueprint_requires_capability_coverage() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][4]["capability_refs"] = []
    payload["build_graph"]["tasks"][5]["capability_refs"] = []
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_builder_blueprint_requires_provision_coverage() -> None:
    payload = _builder_blueprint_payload()
    payload["build_graph"]["tasks"][6]["provision_refs"] = []
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_builder_blueprint_requires_impact_set_for_change_mode() -> None:
    payload = _builder_blueprint_payload()
    payload["concept_blueprint"]["intent_mode"] = "change"
    payload["concept_blueprint"]["change_summary"] = "Add subscription-based premium seller tools."
    with pytest.raises(ValueError):
        _contracts.build_builder_blueprint(payload)


def test_builder_blueprint_validates_flagship_platform_example() -> None:
    blueprint = _contracts.build_builder_blueprint(_flagship_platform_blueprint_payload())
    assert blueprint.decomposition.bundle_plan is not None
    assert blueprint.decomposition.bundle_plan.shell_paths == [
        "platform/config/navigation_config.json",
        "platform/config/theme_config.json",
    ]


def test_builder_blueprint_change_mode_accepts_valid_impact_set() -> None:
    payload = _builder_blueprint_payload()
    payload["concept_blueprint"]["intent_mode"] = "change"
    payload["concept_blueprint"]["change_summary"] = "Add notification-aware seller follow-up."
    payload["impact_set"] = {
        "change_summary": "Adjust the marketplace assistant and automation follow-up.",
        "affected_capability_ids": ["assistant_flow"],
        "affected_provision_ids": ["automation-transport"],
        "affected_workflows": ["Concierge"],
        "affected_bundle_paths": [
            "platform/automations/routes.json",
            "platform/workflows/Concierge/orchestrator.yaml",
        ],
        "affected_declarative_families": ["automation", "workflows"],
        "requires_concept_revision": False,
        "requires_replan": True,
        "requires_rebuild": True,
    }
    blueprint = _contracts.build_builder_blueprint(payload)
    assert blueprint.impact_set is not None
    assert blueprint.impact_set.requires_replan is True
