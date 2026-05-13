import json
import logging
from pathlib import Path
from typing import Any, List, Dict, Optional

import yaml

from mozaiksai.core.workflow.agents.factory import _compose_prompt_sections

logger = logging.getLogger(__name__)


# Simplified pattern registry: remove external taxonomy dependency
# Canonical order maps to examples/guidance blocks across this module
PATTERN_ID_BY_NAME = {
    "contextawarerouting": 1,
    "escalation": 2,
    "feedbackloop": 3,
    "hierarchical": 4,
    "organic": 5,
    "pipeline": 6,
    "redundant": 7,
    "star": 8,
    "triagewithtasks": 9,
}

PATTERN_NAME_BY_ID = {v: k for k, v in PATTERN_ID_BY_NAME.items()}

PATTERN_DISPLAY_NAME_BY_ID = {
  1: "Context-Aware Routing",
  2: "Escalation",
  3: "Feedback Loop",
  4: "Hierarchical",
  5: "Organic",
  6: "Pipeline",
  7: "Redundant",
  8: "Star",
  9: "Triage with Tasks",
}

PATTERN_GUIDANCE_PLACEHOLDER = "{{PATTERN_GUIDANCE_AND_EXAMPLES}}"
PATTERN_GUIDANCE_SECTION_IDS = {"pattern_guidance_and_examples"}
PATTERN_GUIDANCE_SECTION_HEADING = "[PATTERN GUIDANCE AND EXAMPLES]"

REPO_ROOT = Path(__file__).resolve().parents[3]
PATTERN_GUIDANCE_PATH = REPO_ROOT / "docs" / "pattern_guidance.md"
PATTERN_EXAMPLE_DIR = REPO_ROOT / "docs" / "pattern_examples"
PATTERN_EXAMPLE_FILENAMES = {
    1: "pattern_1_context_aware_routing.yaml",
    2: "pattern_2_escalation.yaml",
    3: "pattern_3_feedback_loop.yaml",
    4: "pattern_4_hierarchical.yaml",
    5: "pattern_5_organic.yaml",
    6: "pattern_6_pipeline.yaml",
    7: "pattern_7_redundant.yaml",
    8: "pattern_8_star.yaml",
    9: "pattern_9_triage_with_tasks.yaml",
}


def _load_pattern_guidance_text() -> str:
    """Load consolidated pattern guidance from docs/pattern_guidance.md."""
    try:
        return PATTERN_GUIDANCE_PATH.read_text(encoding="utf-8")
    except Exception as err:  # pragma: no cover - defensive logging
        logger.debug(f"Unable to read pattern guidance doc: {err}")
        return ""


def _load_pattern_example_str(pattern_id: int, section_key: str = "WorkflowStrategy") -> Optional[str]:
    """Load a pattern example from docs/pattern_examples.

    Supports YAML multi-doc examples (preferred) and JSON examples. For YAML, this
    returns the first document containing `section_key` dumped as JSON so it can
    be embedded into prompt examples consistently.
    """
    filename = PATTERN_EXAMPLE_FILENAMES.get(pattern_id)
    if not filename:
        return None
    path = PATTERN_EXAMPLE_DIR / filename
    try:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            for doc in yaml.safe_load_all(raw):
                if not isinstance(doc, dict):
                    continue
                if section_key in doc:
                    return json.dumps(doc, indent=2)
            return None

        data = json.loads(raw)
        return json.dumps(data, indent=2)
    except Exception as err:  # pragma: no cover - defensive logging
        logger.debug(f"Unable to load pattern example for id={pattern_id} at {path}: {err}")
        return None


def _apply_pattern_guidance(agent, guidance: str) -> bool:
    """Insert pattern guidance into the agent's PATTERN GUIDANCE AND EXAMPLES section."""
    try:
        normalized = (guidance or "").strip()
        if not normalized:
            logger.debug(f"No guidance content supplied for {getattr(agent, 'name', 'unknown')}" )
            return False

        sections = getattr(agent, "_mozaiks_prompt_sections", None)
        placeholder = PATTERN_GUIDANCE_PLACEHOLDER
        section_updated = False

        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section_id = section.get("id")
                heading = section.get("heading")
                if section_id in PATTERN_GUIDANCE_SECTION_IDS or heading == PATTERN_GUIDANCE_SECTION_HEADING:
                    content = section.get("content") or ""
                    if placeholder in content:
                        section["content"] = content.replace(placeholder, normalized)
                    else:
                        section["content"] = normalized
                    section_updated = True

            if section_updated:
                try:
                    recomposed = _compose_prompt_sections(sections)
                    if hasattr(agent, "_system_message"):
                        agent._system_message = recomposed
                    elif hasattr(agent, "update_system_message") and callable(agent.update_system_message):
                        agent.update_system_message(recomposed)
                    setattr(agent, "_mozaiks_prompt_sections", sections)
                    setattr(agent, "_mozaiks_base_system_message", recomposed)
                    logger.debug(f"Applied pattern guidance via prompt section for {getattr(agent, 'name', 'unknown')}")
                    return True
                except Exception as compose_err:
                    logger.error(
                        f"Failed to recompose prompt sections after inserting pattern guidance for {getattr(agent, 'name', 'unknown')}: {compose_err}",
                        exc_info=True,
                    )

        current_message = getattr(agent, "_system_message", "") or ""
        if placeholder and placeholder in current_message:
            updated = current_message.replace(placeholder, normalized)
            if hasattr(agent, "_system_message"):
                agent._system_message = updated
            elif hasattr(agent, "update_system_message") and callable(agent.update_system_message):
                agent.update_system_message(updated)
            setattr(agent, "_mozaiks_base_system_message", updated)
            logger.debug(f"Applied pattern guidance via string replacement for {getattr(agent, 'name', 'unknown')}")
            return True

        if hasattr(agent, "_system_message"):
            separator = "\n\n" if current_message else ""
            agent._system_message = f"{current_message}{separator}{normalized}".strip()
            setattr(agent, "_mozaiks_base_system_message", agent._system_message)
            logger.debug(f"Appended pattern guidance to system_message for {getattr(agent, 'name', 'unknown')} (placeholder missing)" )
            return True

        logger.warning(f"Unable to apply pattern guidance for {getattr(agent, 'name', 'unknown')}: no accessible system message")
        return False

    except Exception as err:
        logger.error(f"Unhandled error applying pattern guidance for {getattr(agent, 'name', 'unknown')}: {err}", exc_info=True)
        return False

def _get_pattern_from_context(agent) -> Dict[str, Any]:
    """Extract the active pattern from cached PatternSelection.

    PatternSelection is produced by PatternAgent and cached via the `pattern_selection`
    tool in `context_variables` under the key `PatternSelection`.

    For multi-workflow packs, the runtime should set `current_workflow_index` while
    iterating generation so downstream agents receive the correct per-workflow pattern.

    Returns a minimal dict: {"id": int, "name": str, "display_name": str}
    """
    try:
        if not hasattr(agent, "_context_variables") and not hasattr(agent, "context_variables"):
            logger.debug("Agent has no context_variables attribute")
            return {}

        context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
        if context is None:
            logger.debug("Agent context_variables is None")
            return {}

        if hasattr(context, "data"):
            data = context.data
        elif isinstance(context, dict):
            data = context
        else:
            logger.debug("Unexpected context type: %s", type(context))
            return {}

        pattern_selection = data.get("PatternSelection", {})
        if not isinstance(pattern_selection, dict) or not pattern_selection:
            logger.debug("No PatternSelection found in context")
            return {}

        workflows = pattern_selection.get("workflows")
        if not isinstance(workflows, list) or not workflows:
            logger.debug("PatternSelection missing workflows list")
            return {}

        current_index = data.get("current_workflow_index")
        index = current_index if isinstance(current_index, int) and current_index >= 0 else 0

        selected_workflow: Optional[Dict[str, Any]] = None
        if index < len(workflows) and isinstance(workflows[index], dict):
            selected_workflow = workflows[index]
        if selected_workflow is None:
            for wf in workflows:
                if isinstance(wf, dict) and wf.get("role") == "primary":
                    selected_workflow = wf
                    break
        if selected_workflow is None:
            return {}

        raw_id = selected_workflow.get("pattern_id")
        raw_name = selected_workflow.get("pattern_name")

        pattern_id: int | None = raw_id if isinstance(raw_id, int) else None
        if pattern_id is None and isinstance(raw_name, str):
            norm = raw_name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
            pattern_id = PATTERN_ID_BY_NAME.get(norm)

        if pattern_id is None or pattern_id not in PATTERN_NAME_BY_ID:
            logger.warning(
                "Unknown pattern selection provided: id=%r name=%r (workflow=%r)",
                raw_id,
                raw_name,
                selected_workflow.get("name"),
            )
            return {}

        pattern_name = PATTERN_NAME_BY_ID[pattern_id]
        display_name = PATTERN_DISPLAY_NAME_BY_ID.get(pattern_id) or (
            raw_name if isinstance(raw_name, str) and raw_name.strip() else pattern_name.replace("_", " ").title()
        )

        result = {"id": pattern_id, "name": pattern_name, "display_name": display_name}
        logger.info(f"✓ Pattern resolved for {agent.name}: id={pattern_id}, name={pattern_name}")
        return result

    except Exception as e:
        logger.error(f"Error extracting pattern from context: {e}", exc_info=True)
        return {}


def _get_upstream_context(agent, key: str) -> Dict[str, Any]:
    """Retrieve a specific upstream output from context variables."""
    try:
        if not hasattr(agent, '_context_variables') and not hasattr(agent, 'context_variables'):
            return {}
        
        context = getattr(agent, 'context_variables', None) or getattr(agent, '_context_variables', None)
        if context is None:
            return {}
            
        # Handle both dict and ContextVariables object
        data = context.data if hasattr(context, 'data') else context
        if not isinstance(data, dict):
            return {}
            
        return data.get(key, {})
    except Exception as e:
        logger.error(f"Error retrieving upstream context '{key}': {e}")
        return {}


def inject_workflow_strategy_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for WorkflowStrategyAgent.
    Injects comprehensive pattern-specific guidance into system message.
    
    WorkflowStrategyAgent OUTPUT FORMAT (WorkflowStrategyCall JSON):
    {
      "WorkflowStrategy": {
        "workflow_name": "<string>",
        "workflow_description": "<string>",
        "human_in_loop": true|false,
        "trigger": "chat|form_submit|schedule|database_condition|webhook",
        "initiated_by": "user|system|external_event",
        "pattern": ["<string>"],
        "workflow_stages": [
          {
            "stage_index": <int>,
            "stage_name": "<string>",
            "stage_description": "<string>",
            "agents_needed": ["<agent_name>", ...]
          }
        ]
      }
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)

        # Interaction Matrix Rules for Strategy
        matrix_rules = """
[INTERACTION MATRIX RULES]
You MUST align your GLOBAL `human_in_loop` decision with the following matrix:

| Workflow Nature | Human in Loop? |
| :--- | :--- |
| Intake / Concierge / Interview | true |
| Review / Approval / Decision | true |
| Clarification / Co-Pilot | true |
| Fully Automated Processing | false |
| Background Analysis / ETL | false |

IF you set `human_in_loop: true`, downstream agents will create UI components for user interaction.
IF you set `human_in_loop: false`, the workflow will run autonomously.
"""

        # Pattern-specific WorkflowStrategy examples (complete JSON payloads)
        strategy_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "WorkflowStrategy": {
    "workflow_name": "SaaS Support Domain Router",
    "workflow_description": "When a customer opens a support chat, the workflow classifies the request by product surface and routes the highest-confidence specialist so issues reach the right expert on the first try.",
    "human_in_loop": false,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Context-Aware Routing"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Automated Intake & Signal Capture",
        "stage_description": "Router agent gathers account metadata, parses the first message for domain cues, and records confidence scores.",
        "agents_needed": ["IntakeRouterAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Specialist Routing & Engagement",
        "stage_description": "Orchestrator selects the best specialist queue, invites the right agent, and hands off the enriched context payload.",
        "agents_needed": ["RoutingOrchestratorAgent", "SpecialistDispatcherAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Resolution & Post-Chat Summary",
        "stage_description": "Specialist resolves the issue, Router agent validates satisfaction, and final disposition is synced to CRM.",
        "agents_needed": ["ResolutionSpecialistAgent"]
      }
    ]
  }
}

// EXAMPLE 2: Internal IT Helpdesk
{
  "WorkflowStrategy": {
    "workflow_name": "Internal IT Helpdesk Concierge",
    "workflow_description": "When an employee requests IT assistance, the workflow classifies the issue (Hardware, Software, Access) and routes to the correct support tier, resulting in streamlined ticket assignment.",
    "human_in_loop": true,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Context-Aware Routing"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Employee Request Intake",
        "stage_description": "Concierge agent identifies the employee, verifies department, and captures the details of the IT issue.",
        "agents_needed": ["ITConciergeAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Issue Classification",
        "stage_description": "Classifier agent analyzes the request to categorize it as Hardware, Software, or Access Control.",
        "agents_needed": ["IssueClassifierAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Support Execution",
        "stage_description": "The specific IT specialist (Hardware Tech, Software Admin, Security Ops) handles the request and provides a solution.",
        "agents_needed": ["ITSupportCoordinatorAgent", "HardwareTechAgent", "SoftwareAdminAgent"]
      }
    ]
  }
}""",
            2: """{
  "WorkflowStrategy": {
    "workflow_name": "Cloud Incident Escalation Ladder",
    "workflow_description": "When monitoring detects a P1 outage, the workflow applies confidence thresholds and escalates the investigation through tiered responders so the right expert owns remediation without losing context.",
    "human_in_loop": true,
    "trigger": "webhook",
    "initiated_by": "system",
    "pattern": ["Escalation"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Alert Intake & Baseline Diagnostics",
        "stage_description": "Automated triage agent ingests the alert, correlates recent deployments, and attempts scripted remediation steps.",
        "agents_needed": ["AlertTriageAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Tier Promotion & Context Packaging",
        "stage_description": "Escalation coordinator assesses recovery confidence; if under 0.85 it bundles findings and pages the next responder tier.",
        "agents_needed": ["EscalationCoordinatorAgent", "TierPromotionAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Expert Mitigation & Stakeholder Updates",
        "stage_description": "Site reliability lead executes advanced playbooks, involves human commander as needed, and publishes status to leadership.",
        "agents_needed": ["SRELeadAgent"]
      }
    ]
  }
}""",
            3: """{
  "WorkflowStrategy": {
    "workflow_name": "Product Launch Copy Refinement",
    "workflow_description": "When marketing requests launch copy, the workflow drafts messaging, gathers structured stakeholder feedback, and iterates until approval so content quality steadily improves.",
    "human_in_loop": true,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Feedback Loop"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Brief Capture & Acceptance Criteria",
        "stage_description": "Facilitator agent collects campaign goals, tone, audience data, and defines done criteria with stakeholders.",
        "agents_needed": ["BriefFacilitatorAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Draft Creation",
        "stage_description": "Authoring agent generates initial announcement copy and attaches rationale mapped to the brief.",
        "agents_needed": ["CopyAuthoringAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Structured Review",
        "stage_description": "Review agent (or PMM) scores messaging pillars, leaves line-level comments, and flags blockers or minor tweaks.",
        "agents_needed": ["ContentReviewAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Revision & Approval",
        "stage_description": "Authoring agent applies accepted feedback, rechecks criteria, and loops until reviewers sign off.",
        "agents_needed": ["RevisionAgent", "ApprovalGateAgent"]
      }
    ]
  }
}""",
            4: """{
  "WorkflowStrategy": {
    "workflow_name": "Market Entry Intelligence Stack",
    "workflow_description": "When an executive team explores a new market, the workflow cascades research tasks through managers and specialists so each layer tackles the right depth of analysis.",
    "human_in_loop": true,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Hierarchical"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Executive Briefing & Workstream Plan",
        "stage_description": "Strategy lead clarifies objectives, splits work into demand, competitor, and regulatory streams, and assigns managers.",
        "agents_needed": ["StrategyLeadAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Manager Task Framing",
        "stage_description": "Each manager designs research backlogs, defines success metrics, and syncs expectations with their specialist pods.",
        "agents_needed": ["ResearchManagerCoordinator", "DemandManagerAgent", "CompetitorManagerAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Specialist Deep Dives",
        "stage_description": "Specialists execute assigned analyses, share interim findings upward, and surface blockers requiring executive decisions.",
        "agents_needed": ["AnalysisCoordinatorAgent", "MarketAnalystAgent", "RegulatoryAnalystAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Executive Synthesis & Go/No-Go",
        "stage_description": "Executive aggregates insights, prepares the narrative deck, and secures leadership approval on the market decision.",
        "agents_needed": ["ExecutiveSynthesisAgent"]
      }
    ]
  }
}""",
            5: """{
  "WorkflowStrategy": {
    "workflow_name": "Omnichannel Campaign Content Studio",
    "workflow_description": "When marketing launches a campaign sprint, the workflow orchestrates collaborative idea generation, automated draft creation, and cross-channel packaging so content is ready for every surface in one pass.",
    "human_in_loop": true,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Organic"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Brief Alignment & Inspiration",
        "stage_description": "Facilitator agent gathers campaign goals, target personas, and product messaging while seeding the room with prior high-performing assets.",
        "agents_needed": ["CampaignFacilitatorAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Collaborative Concept Jam",
        "stage_description": "Copy, design, and growth contributors brainstorm in an open thread while ideation agents capture hooks, tag emerging themes, and surface gaps to the group.",
        "agents_needed": ["IdeationAgent", "ThemeCapturerAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Asset Assembly & Channel Packaging",
        "stage_description": "Workflow compiles the strongest concepts into draft emails, social copy, and landing page variants, then routes them for stakeholder preview and scheduling.",
        "agents_needed": ["AssetAssemblyAgent"]
      }
    ]
  }
}""",
            6: """{
  "WorkflowStrategy": {
    "workflow_name": "Digital Loan Application Pipeline",
    "workflow_description": "When a borrower submits an online loan form, the workflow performs sequential validation, risk checks, underwriting, and customer notifications so decisions are consistent and auditable.",
    "human_in_loop": true,
    "trigger": "form_submit",
    "initiated_by": "user",
    "pattern": ["Pipeline"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Intake Validation",
        "stage_description": "Intake agent verifies required documents, normalizes applicant data, and halts if mandatory fields are missing.",
        "agents_needed": ["LoanIntakeAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Risk & Compliance Screening",
        "stage_description": "Workflow runs credit, fraud, and KYC checks sequentially, annotating the application with risk scores.",
        "agents_needed": ["CreditCheckAgent", "FraudScreeningAgent", "KYCVerificationAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Underwriting Decision",
        "stage_description": "Underwriting agent evaluates policy rules, calculates terms, and flags edge cases for manual review.",
        "agents_needed": ["UnderwritingAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Offer & Fulfillment",
        "stage_description": "Fulfillment agent generates the offer packet, notifies the borrower, and syncs status back to servicing systems.",
        "agents_needed": ["LoanFulfillmentAgent"]
      }
    ]
  }
}""",
            7: """{
  "WorkflowStrategy": {
    "workflow_name": "Demand Forecast Comparison",
    "workflow_description": "When the weekly planning cycle runs, the workflow commissions multiple forecasting approaches and compares them so planners adopt the most reliable projection.",
    "human_in_loop": true,
    "trigger": "schedule",
    "initiated_by": "system",
    "pattern": ["Redundant"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Scenario Brief",
        "stage_description": "Coordinator agent summarizes the upcoming sales window, constraints, and evaluation metrics for downstream models.",
        "agents_needed": ["ForecastCoordinatorAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Parallel Forecast Generation",
        "stage_description": "Distinct specialist agents build statistical, causal, and heuristic forecasts in parallel with documented assumptions.",
        "agents_needed": ["ForecastOrchestratorAgent", "StatisticalModelAgent", "CausalModelAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Comparative Evaluation",
        "stage_description": "Evaluator agent scores each forecast against hold-out accuracy, volatility, and narrative fit, involving planner review when diverging.",
        "agents_needed": ["ForecastEvaluatorAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Recommendation Delivery",
        "stage_description": "Coordinator selects the preferred forecast, documents rationale, and distributes the planning brief to stakeholders.",
        "agents_needed": ["RecommendationDeliveryAgent"]
      }
    ]
  }
}""",
            8: """{
  "WorkflowStrategy": {
    "workflow_name": "Vendor Onboarding Hub",
    "workflow_description": "When a new vendor submits onboarding forms, the workflow routes required checks to finance, security, and legal spokes so every team completes their review while the hub tracks status.",
    "human_in_loop": true,
    "trigger": "form_submit",
    "initiated_by": "user",
    "pattern": ["Star"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Hub Intake",
        "stage_description": "Coordinator agent validates vendor details, determines which spokes must be engaged, and packages briefing packets.",
        "agents_needed": ["VendorIntakeCoordinatorAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Spoke Reviews",
        "stage_description": "Finance, security, and legal spokes perform their assessments independently while posting status updates to the hub.",
        "agents_needed": ["SpokeCoordinatorAgent", "FinanceReviewAgent", "SecurityReviewAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Risk Alignment",
        "stage_description": "Coordinator monitors spoke progress, resolves conflicts, and summarizes outstanding blockers or additional requirements.",
        "agents_needed": ["RiskAlignmentAgent", "ConflictResolutionAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Hub Approval & Handoff",
        "stage_description": "Coordinator compiles approvals, triggers account provisioning, and delivers the final onboarding summary to the requester.",
        "agents_needed": ["ApprovalHandoffAgent"]
      }
    ]
  }
}""",
            9: """{
  "WorkflowStrategy": {
    "workflow_name": "Dream Weaver",
    "workflow_description": "When a user describes a dream, the workflow captures the narrative, generates a cinematic video visualization using Veo3, performs psychological analysis, and presents both artifacts with freemium monetization gates for premium interpretation content.",
    "human_in_loop": true,
    "trigger": "chat",
    "initiated_by": "user",
    "pattern": ["Triage with Tasks"],
    "workflow_stages": [
      {
        "stage_index": 0,
        "stage_name": "Stage 1: Dream Intake",
        "stage_description": "Interview agent conducts empathetic conversation to capture dream narrative, visual details, emotions, and sensory experiences. User confirms captured details via inline summary card.",
        "agents_needed": ["DreamInterviewAgent"]
      },
      {
        "stage_index": 1,
        "stage_name": "Stage 2: Prompt Engineering",
        "stage_description": "Prompt architect translates dream narrative into structured Veo3 video prompts with scene segmentation, camera angles, lighting, and mood specifications.",
        "agents_needed": ["PromptArchitectAgent"]
      },
      {
        "stage_index": 2,
        "stage_name": "Stage 3: Video Generation",
        "stage_description": "Video generator interfaces with Veo3 API to create cinematic dream visualization, handling polling, retries, and error cases until video URL is obtained.",
        "agents_needed": ["VideoGeneratorAgent"]
      },
      {
        "stage_index": 3,
        "stage_name": "Stage 4: Video Review & Approval",
        "stage_description": "User reviews the generated video in an artifact player. If satisfied, approves to proceed. If not, provides specific feedback for regeneration (adjust lighting, change camera angle, fix scene composition).",
        "agents_needed": ["VideoReviewAgent"]
      },
      {
        "stage_index": 4,
        "stage_name": "Stage 5: Psychological Analysis",
        "stage_description": "Psychoanalyst agent performs deep Jungian and Freudian analysis of dream symbols, archetypes, and subconscious themes, generating tiered interpretation report.",
        "agents_needed": ["PsychoanalystAgent"]
      },
      {
        "stage_index": 5,
        "stage_name": "Stage 6: Final Presentation",
        "stage_description": "Presenter displays the approved video alongside psychological analysis report with subscription-based content gating (preview for free, full for premium).",
        "agents_needed": ["FinalPresenterAgent"]
      }
    ]
  }
}"""
        }

        example_json = _load_pattern_example_str(pattern_id) or strategy_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No strategy example found for pattern_id {pattern_id}")
            return

        guidance_text = _load_pattern_guidance_text()
        guidance_prefix = matrix_rules if not guidance_text else f"{matrix_rules}\n\n{guidance_text}"

        guidance = (
            f"{guidance_prefix}\n\n"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete WorkflowStrategy JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )

        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected WorkflowStrategy example for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_workflow_strategy_guidance: {e}", exc_info=True)

def inject_workflow_architect_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
  AG2 update_agent_state hook for WorkflowArchitectAgent.
  Injects pattern-specific workflow-wide context variables and lifecycle hooks.

    WorkflowArchitectAgent OUTPUT FORMAT (TechnicalBlueprint JSON):
    {
      "TechnicalBlueprint": {
        "global_context_variables": [
          {
            "name": "<string>",
            "type": "config|data_reference|data_entity|computed|state|external",
            "purpose": "<string>",
            "trigger_hint": "<string|null>"
          }
        ],
        "ui_components": [
          {
            "phase_name": "<string>",
            "agent": "<PascalCaseAgentName>",
            "tool": "<snake_case_tool>",
            "label": "<CTA or heading>",
            "component": "<PascalCaseComponent>",
            "display": "inline|artifact",
            "ui_pattern": "single_step|two_step_confirmation|multi_step",
            "summary": "<<=200 char narrative>"
          }
        ],
        "before_chat_lifecycle": {
          "name": "<string>",
          "purpose": "<string>",
          "trigger": "before_chat",
          "integration": "<string|null>"
        },
        "after_chat_lifecycle": {
          "name": "<string>",
          "purpose": "<string>",
          "trigger": "after_chat",
          "integration": "<string|null>"
        }
      }
    }

    Set ui_components to [] when the workflow exposes no UI tools.
    Set before_chat_lifecycle or after_chat_lifecycle to null when the workflow does not require that hook.
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)

        # Pattern-specific complete JSON examples matching TechnicalBlueprintCall schema
        architect_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "customer_profile",
        "type": "external",
        "purpose": "Stores customer tier and product usage data fetched from CRM",
        "trigger_hint": "Loaded by before_chat lifecycle hook"
      },
      {
        "name": "intent_classification",
        "type": "computed",
        "purpose": "Stores the detected intent and confidence score from the initial user message",
        "trigger_hint": "Set when RouterAgent analyzes the first message"
      },
      {
        "name": "assigned_specialist_queue",
        "type": "state",
        "purpose": "Tracks which specialist team (Billing, Tech, Account) owns the active session",
        "trigger_hint": "Updated when RouterAgent completes routing logic"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1: Automated Intake & Signal Capture",
        "agent": "RouterAgent",
        "tool": "verify_account_details",
        "label": "Verify Account",
        "component": "AccountVerificationCard",
        "display": "inline",
        "ui_pattern": "single_step",
        "summary": "RouterAgent presents a card showing the detected account associated with the user. User confirms it is the correct account context for this support request."
      },
      {
        "phase_name": "Phase 3: Resolution & Post-Chat Summary",
        "agent": "RouterAgent",
        "tool": "submit_feedback",
        "label": "Rate Support Experience",
        "component": "FeedbackForm",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "A feedback form opens in the side panel allowing the user to rate the specialist's helpfulness and leave optional text comments before closing the session."
      }
    ],
    "before_chat_lifecycle": {
      "name": "load_customer_profile",
      "purpose": "Fetch customer metadata (tier, active products) from CRM to inform routing priority",
      "trigger": "before_chat",
      "integration": "Salesforce"
    },
    "after_chat_lifecycle": {
      "name": "sync_transcript_to_crm",
      "purpose": "Save the full conversation transcript and final resolution status to the customer's CRM record",
      "trigger": "after_chat",
      "integration": "Salesforce"
    }
  }
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "employee_context",
        "type": "config",
        "purpose": "Contains authenticated employee ID, department, and location",
        "trigger_hint": "Injected by platform authentication context"
      },
      {
        "name": "ticket_id",
        "type": "data_entity",
        "purpose": "The unique ID of the support ticket created for this session",
        "trigger_hint": "Created when ConciergeAgent initializes the request"
      },
      {
        "name": "outage_alert_active",
        "type": "external",
        "purpose": "Boolean flag indicating if a known system outage matches the user's keywords",
        "trigger_hint": "Set by before_chat system check"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1: Employee Request Intake",
        "agent": "ConciergeAgent",
        "tool": "confirm_issue_details",
        "label": "Confirm Issue Details",
        "component": "IssueSummaryCard",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "ConciergeAgent summarizes the understood issue (e.g., 'Laptop screen flickering'). User reviews the summary and clicks Confirm to generate the ticket or Edit to refine."
      },
      {
        "phase_name": "Phase 3: Support Execution",
        "agent": "HardwareSpecialist",
        "tool": "request_remote_access",
        "label": "Grant Remote Access",
        "component": "RemoteAccessPrompt",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "Specialist requests permission to remotely control the user's machine. User sees a security warning and must explicitly click 'Allow Access' to proceed."
      }
    ],
    "before_chat_lifecycle": {
      "name": "check_system_status",
      "purpose": "Query status page for active incidents to preemptively warn users about known outages",
      "trigger": "before_chat",
      "integration": "StatusPage"
    },
    "after_chat_lifecycle": {
      "name": "create_servicenow_ticket",
      "purpose": "Finalize the temporary ticket draft and push it to the ServiceNow queue for tracking",
      "trigger": "after_chat",
      "integration": "ServiceNow"
    }
  }
}""",
            2: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "incident_severity",
        "type": "state",
        "purpose": "Tracks live severity rating for the outage response",
        "trigger_hint": "Transitions when TriageAgent posts SEVERITY:<level>"
      },
      {
        "name": "active_response_tier",
        "type": "state",
        "purpose": "Identifies which escalation tier currently owns remediation",
        "trigger_hint": "Transitions when EscalationCoordinator announces TIER_OWNER:<group>"
      },
      {
        "name": "remediation_status",
        "type": "computed",
        "purpose": "Aggregates mitigation steps, ETA, and rollback decisions",
        "trigger_hint": "Calculated from mitigation_steps and rollback_decision inputs"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 0 - Intake & Severity Check",
        "agent": "TriageAgent",
        "tool": "acknowledge_incident_brief",
        "label": "Confirm incident details",
        "component": "IncidentIntakeInline",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "TriageAgent posts an inline card summarizing alerts and requests a quick confirmation before escalation tiering begins."
      },
      {
        "phase_name": "Phase 3 - Resolution Review",
        "agent": "SRELead",
        "tool": "publish_postmortem_outline",
        "label": "Review incident wrap-up",
        "component": "PostmortemSummaryArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "SRELead delivers an artifact outlining remediation, open follow-ups, and next actions for stakeholder sign-off."
      }
    ],
    "before_chat_lifecycle": {
      "name": "initialize_incident_context",
      "purpose": "Seed incident timeline, clear stale responders, and load alert metadata",
      "trigger": "before_chat",
      "integration": null
    },
    "after_chat_lifecycle": {
      "name": "publish_incident_postmortem_stub",
      "purpose": "Archive incident summary and auto-draft postmortem notes",
      "trigger": "after_chat",
      "integration": "StatusPage"
    }
  }
}""",
            3: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "campaign_brief_snapshot",
        "type": "state",
        "purpose": "Normalized brief containing personas, tone, and acceptance criteria",
        "trigger_hint": "Set when FacilitatorAgent outputs BRIEF_FINALIZED"
      },
      {
        "name": "feedback_log",
        "type": "data_entity",
        "purpose": "Structured array of review comments with severity tags",
        "trigger_hint": "Appended when ReviewAgent emits FEEDBACK_BUNDLE"
      },
      {
        "name": "approval_gate_status",
        "type": "state",
        "purpose": "Tracks stakeholder approval state for the launch copy",
        "trigger_hint": "Set when StakeholderTool returns APPROVAL_STATUS:<value>"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1 - Draft Creation",
        "agent": "CreatorAgent",
        "tool": "collect_structured_feedback",
        "label": "Submit revision feedback",
        "component": "FeedbackForm",
        "display": "artifact",
        "ui_pattern": "multi_step",
        "summary": "A side tray opens showing the complete draft copy with an interactive feedback form overlay. Step 1: User scores messaging pillars (Value Prop, Differentiation, CTA) on 1-5 scales with real-time validation. Step 2: User highlights specific sections and adds inline revision comments. Step 3: User reviews feedback summary and clicks Submit Feedback to trigger revision cycle."
      },
      {
        "phase_name": "Phase 3 - Approval Gate",
        "agent": "StakeholderAgent",
        "tool": "approve_final_copy",
        "label": "Approve launch copy",
        "component": "ApprovalDecisionInline",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "An inline approval card appears in chat displaying the revised launch copy summary with key changes highlighted in yellow. User sees before/after comparison snippets for major edits, then clicks either Approve for Launch (green button) or Request Final Revisions (amber button) to proceed."
      }
    ],
    "before_chat_lifecycle": {
      "name": "prime_campaign_workspace",
      "purpose": "Provision shared folders and ingest reference assets for the sprint",
      "trigger": "before_chat",
      "integration": "ContentHub"
    },
    "after_chat_lifecycle": {
      "name": "archive_campaign_package",
      "purpose": "Export approved copy, feedback history, and metrics to CMS",
      "trigger": "after_chat",
      "integration": "ContentHub"
    }
  }
}""",
            4: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "workstream_assignments",
        "type": "state",
        "purpose": "Mapping of demand, competitor, and regulatory owners",
        "trigger_hint": "Set when ExecutiveAgent issues WORKSTREAM_PLAN"
      },
      {
        "name": "manager_status_updates",
        "type": "data_entity",
        "purpose": "Rolling status objects from each manager with risk flags",
        "trigger_hint": "Appended when ManagerAgent sends STATUS_BULLETIN"
      },
      {
        "name": "go_no_go_recommendation",
        "type": "computed",
        "purpose": "Executive decision package combining insights and rationale",
        "trigger_hint": "Set when ExecutiveAgent outputs DECISION_BRIEF"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1 - Executive Briefing",
        "agent": "ExecutiveAgent",
        "tool": "share_strategy_overview",
        "label": "Review market entry brief",
        "component": "StrategyBriefArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "ExecutiveAgent publishes an artifact summarizing objectives and assigns managers before downstream workstreams begin."
      },
      {
        "phase_name": "Phase 2 - Manager Updates",
        "agent": "ManagerAgent",
        "tool": "capture_risk_update",
        "label": "Submit risk update",
        "component": "ManagerStatusInline",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "Managers log risk updates inline so the executive hub can escalate blockers in real time."
      }
    ],
    "before_chat_lifecycle": {
      "name": "distribute_strategy_brief",
      "purpose": "Share objective summary and research templates with managers",
      "trigger": "before_chat",
      "integration": null
    },
    "after_chat_lifecycle": {
      "name": "store_market_entry_decision",
      "purpose": "Persist recommendation, supporting data, and follow-up actions",
      "trigger": "after_chat",
      "integration": "StrategyVault"
    }
  }
}""",
            5: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "idea_pool",
        "type": "state",
        "purpose": "Live collection of campaign hooks with contributor attributions",
        "trigger_hint": "Updated when IdeationAgent records IDEA_CAPTURE"
      },
      {
        "name": "asset_draft_registry",
        "type": "state",
        "purpose": "Status map of draft emails, social posts, and landing variants",
        "trigger_hint": "Set when ContentAssembler outputs ASSET_DRAFT:<channel>"
      },
      {
        "name": "stakeholder_notes",
        "type": "data_entity",
        "purpose": "Aggregated reviewer reactions and launch readiness flags",
        "trigger_hint": "Appended when ReviewerTool sends NOTE_ENTRY"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1 - Ideation Jam",
        "agent": "IdeationAgent",
        "tool": "submit_brainstorm_ideas",
        "label": "Add campaign idea",
        "component": "IdeaCaptureInline",
        "display": "inline",
        "ui_pattern": "multi_step",
        "summary": "IdeationAgent opens an inline capture panel so participants can log ideas, tags, and inspiration without leaving the flow."
      },
      {
        "phase_name": "Phase 3 - Asset Review",
        "agent": "ReviewerAgent",
        "tool": "review_asset_variants",
        "label": "Review creative variants",
        "component": "CreativeBoardArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "ReviewerAgent posts an artifact of drafted assets so the user can skim highlights and decide which to advance."
      }
    ],
    "before_chat_lifecycle": {
      "name": "open_campaign_workspace",
      "purpose": "Create collaboration space and preload prior performance benchmarks",
      "trigger": "before_chat",
      "integration": "MarketingNotion"
    },
    "after_chat_lifecycle": {
      "name": "push_campaign_package",
      "purpose": "Deliver approved assets and notes to scheduling automations",
      "trigger": "after_chat",
      "integration": "CampaignScheduler"
    }
  }
}""",
            6: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "application_status",
        "type": "state",
        "purpose": "Current stage marker for the applicant journey",
        "trigger_hint": "Updated when PipelineAgent emits STAGE_ADVANCE:<stage>"
      },
      {
        "name": "risk_flags",
        "type": "state",
        "purpose": "Consolidated fraud, compliance, and credit findings",
        "trigger_hint": "Appended when RiskScreening tool posts FLAG_PAYLOAD"
      },
      {
        "name": "underwriting_result",
        "type": "computed",
        "purpose": "Decision payload including terms, APR, and decline reasons",
        "trigger_hint": "Set when UnderwritingAgent outputs DECISION_PACKAGE"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 0 - Intake",
        "agent": "PipelineAgent",
        "tool": "collect_supporting_documents",
        "label": "Upload financial documents",
        "component": "DocumentChecklistInline",
        "display": "inline",
        "ui_pattern": "multi_step",
        "summary": "PipelineAgent walks the applicant through an inline checklist to upload identity, income, and banking statements before downstream reviews."
      },
      {
        "phase_name": "Phase 2 - Decision Review",
        "agent": "UnderwritingAgent",
        "tool": "share_underwriting_package",
        "label": "Review underwriting decision",
        "component": "DecisionSummaryArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "UnderwritingAgent posts an artifact summarizing approval terms or decline reasons so the applicant and banker can finalize next steps."
      }
    ],
    "before_chat_lifecycle": {
      "name": "initialize_application_record",
      "purpose": "Create applicant shell record and attach submission documents",
      "trigger": "before_chat",
      "integration": "LoanCRM"
    },
    "after_chat_lifecycle": {
      "name": "finalize_decision_export",
      "purpose": "Sync decision, funding status, and notifications to core banking",
      "trigger": "after_chat",
      "integration": "LoanCore"
    }
  }
}""",
            7: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "forecast_submissions",
        "type": "state",
        "purpose": "Dictionary of submitted forecasts keyed by modeling approach",
        "trigger_hint": "Set when SpecialistAgent emits FORECAST_READY"
      },
      {
        "name": "evaluation_matrix",
        "type": "computed",
        "purpose": "Scoring table with accuracy, volatility, and narrative fit columns",
        "trigger_hint": "Updated when EvaluatorAgent posts SCORE_UPDATE"
      },
      {
        "name": "selected_forecast_summary",
        "type": "state",
        "purpose": "Chosen forecast metadata and rationale for planners",
        "trigger_hint": "Set when CoordinatorAgent issues FORECAST_SELECTION"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1 - Model Submission",
        "agent": "SpecialistAgent",
        "tool": "submit_forecast_bundle",
        "label": "Upload forecast bundle",
        "component": "ForecastUploadInline",
        "display": "inline",
        "ui_pattern": "multi_step",
        "summary": "SpecialistAgent opens an inline uploader so each modeling approach can attach projections, assumptions, and diagnostics side by side."
      },
      {
        "phase_name": "Phase 2 - Cross-Evaluation",
        "agent": "EvaluatorAgent",
        "tool": "compare_forecasts",
        "label": "Compare forecast scenarios",
        "component": "ForecastComparisonArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "EvaluatorAgent publishes an artifact ranking submissions on accuracy and resilience, helping coordinators pick the strongest outlook."
      }
    ],
    "before_chat_lifecycle": {
      "name": "prepare_forecast_cycle",
      "purpose": "Reset prior submissions and load upcoming planning window parameters",
      "trigger": "before_chat",
      "integration": null
    },
    "after_chat_lifecycle": {
      "name": "publish_forecast_summary",
      "purpose": "Distribute selected forecast package to planning stakeholders",
      "trigger": "after_chat",
      "integration": "PlanningHub"
    }
  }
}""",
            8: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "spoke_status_registry",
        "type": "state",
        "purpose": "Tracks completion state for finance, security, and legal reviews",
        "trigger_hint": "Updated when SpokeAgent emits REVIEW_STATUS:<state>"
      },
      {
        "name": "risk_exception_notes",
        "type": "data_entity",
        "purpose": "Catalog of open blockers or requested mitigations",
        "trigger_hint": "Appended when any spoke posts RISK_EXCEPTION"
      },
      {
        "name": "required_document_matrix",
        "type": "state",
        "purpose": "Checklist of documents received versus outstanding",
        "trigger_hint": "Set when CoordinatorAgent updates DOCUMENT_STATUS"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 0 - Vendor Intake",
        "agent": "CoordinatorAgent",
        "tool": "capture_vendor_profile",
        "label": "Enter vendor profile",
        "component": "VendorProfileInline",
        "display": "inline",
        "ui_pattern": "multi_step",
        "summary": "CoordinatorAgent uses an inline wizard to capture company basics and route downstream review requests to each spoke."
      },
      {
        "phase_name": "Phase 2 - Risk Decision",
        "agent": "RiskLeadAgent",
        "tool": "publish_risk_clearance",
        "label": "Review consolidated findings",
        "component": "RiskClearanceArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "RiskLeadAgent shares an artifact aggregating finance, security, and legal determinations so stakeholders can approve onboarding."
      }
    ],
    "before_chat_lifecycle": {
      "name": "seed_spoke_registry",
      "purpose": "Initialize branch tracking and clear previous vendor context",
      "trigger": "before_chat",
      "integration": null
    },
    "after_chat_lifecycle": {
      "name": "archive_vendor_package",
      "purpose": "Bundle approvals, risk notes, and onboarding summary for handoff",
      "trigger": "after_chat",
      "integration": "VendorPortal"
    }
  }
}""",
            9: """{
  "TechnicalBlueprint": {
    "global_context_variables": [
      {
        "name": "dream_narrative",
        "type": "state",
        "purpose": "Captured user description of the dream including visual details, emotions, and narrative flow",
        "trigger_hint": "Set when DreamInterviewerAgent completes interview and user confirms summary"
      },
      {
        "name": "video_prompts",
        "type": "computed",
        "purpose": "Structured scene-by-scene Veo3 prompts with camera angles, lighting, and mood specifications",
        "trigger_hint": "Generated by PromptArchitectAgent after analyzing dream_narrative"
      },
      {
        "name": "generated_video_url",
        "type": "data_reference",
        "purpose": "URL to the final rendered dream visualization video from Veo3 API",
        "trigger_hint": "Set when VideoGeneratorAgent completes generation and receives final video asset"
      },
      {
        "name": "psychoanalysis_report",
        "type": "data_entity",
        "purpose": "Structured psychological analysis with summary, symbols breakdown, and deep interpretation",
        "trigger_hint": "Generated by PsychoanalystAgent with tiered content based on subscription level"
      },
      {
        "name": "user_subscription_tier",
        "type": "config",
        "purpose": "User's subscription status (free/premium) for content gating decisions",
        "trigger_hint": "Loaded from Stripe at workflow start via before_chat lifecycle hook"
      },
      {
        "name": "video_approval_status",
        "type": "state",
        "purpose": "Tracks whether user approved the generated video or requested changes",
        "trigger_hint": "Set when user interacts with video review artifact (approved/revision_requested)"
      },
      {
        "name": "video_revision_feedback",
        "type": "data_entity",
        "purpose": "User's specific feedback for video regeneration (lighting, angles, composition changes)",
        "trigger_hint": "Captured when user requests video revision with detailed notes"
      }
    ],
    "ui_components": [
      {
        "phase_name": "Phase 1 - Dream Intake",
        "agent": "DreamInterviewerAgent",
        "tool": "confirm_dream_summary",
        "label": "Confirm Dream Details",
        "component": "DreamSummaryCard",
        "display": "inline",
        "ui_pattern": "two_step_confirmation",
        "summary": "After conversational interview, agent displays inline summary card highlighting captured visual elements, emotions, and narrative arc. User reviews the distilled dream details and clicks Confirm to proceed or Edit to refine specific aspects."
      },
      {
        "phase_name": "Phase 4 - Video Review & Approval",
        "agent": "VideoReviewAgent",
        "tool": "review_generated_video",
        "label": "Review Dream Video",
        "component": "VideoApprovalArtifact",
        "display": "artifact",
        "ui_pattern": "two_step_confirmation",
        "summary": "Artifact displays video player with the generated visualization. Below the player, user sees Approve button (green) to proceed or Request Changes button (amber) which opens feedback form for specific revision notes (adjust lighting, change camera angle, fix scene composition). If revisions requested, video regenerates with feedback applied."
      },
      {
        "phase_name": "Phase 6 - Final Presentation",
        "agent": "DreamPresenterAgent",
        "tool": "present_video_artifact",
        "label": "View Final Video",
        "component": "VideoCinemaArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "Side panel displays approved dream video with cinematic player, ambient audio, and playback controls. Premium users can download video."
      },
      {
        "phase_name": "Phase 6 - Final Presentation",
        "agent": "DreamPresenterAgent",
        "tool": "present_analysis_artifact",
        "label": "View Psychological Interpretation",
        "component": "AnalysisGateArtifact",
        "display": "artifact",
        "ui_pattern": "single_step",
        "summary": "Artifact displays psychological analysis report. Free users see summary section with key themes, plus blurred Deep Dive section showing premium content preview with Upgrade to Premium button. Premium users see full unblurred analysis with archetypes, symbolism, and subconscious insights."
      }
    ],
    "before_chat_lifecycle": {
      "name": "load_user_subscription_tier",
      "purpose": "Fetch user subscription status from Stripe to enable content gating decisions",
      "trigger": "before_chat",
      "integration": "Stripe"
    },
    "after_chat_lifecycle": {
      "name": "track_dream_analytics",
      "purpose": "Log dream visualization request, video generation metrics, and upgrade conversion events",
      "trigger": "after_chat",
      "integration": "Mixpanel"
    }
  }
}"""
        }
        
        example_json = architect_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No architect example found for pattern_id {pattern_id}")
            return

        # Semantic Context Injection
        strategy = _get_upstream_context(agent, 'WorkflowStrategy')
        semantic_context = ""
        if strategy:
            workflow_stages = strategy.get("workflow_stages") or []
            stage_summary = "\n".join(
                [
                    f"- Stage {m.get('stage_index', m.get('phase_index'))}: {m.get('stage_name', m.get('phase_name'))} ({m.get('stage_description', m.get('phase_description'))})"
                    for m in workflow_stages
                ]
            )
            semantic_context = (
                f"\n[UPSTREAM CONTEXT: WORKFLOW STRATEGY]\n"
                f"The WorkflowStrategyAgent has defined the following workflow stages. Your TechnicalBlueprint MUST align with these workflow stages:\n"
                f"{stage_summary}\n\n"
            )

        guidance = (
            f"{semantic_context}"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete TechnicalBlueprint JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )

        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected TechnicalBlueprint example for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_workflow_architect_guidance: {e}", exc_info=True)


def inject_agent_tools_file_generator_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for AgentToolsFileGenerator.
    Injects pattern-specific agent tool generation guidance.
    
    AgentToolsFileGenerator OUTPUT FORMAT (AgentToolsFileGeneratorOutput JSON):
    {
      "tools": [
        {
          "filename": "tools/<snake_case>.py",
          "content": "<complete_python_function>",
          "installRequirements": ["<package_name>"]
        }
      ]
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific agent tool examples (complete JSON payloads)
        agent_tool_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "tools": [
    {
      "filename": "tools/classify_intent.py",
      "content": "import os\\nimport httpx\\n\\nasync def classify_intent(message: str) -> dict:\\n    \\"\\"\\"Analyze message content to determine support domain\\"\\"\\"\\n    api_key = os.getenv(\\"OPENAI_API_KEY\\")\\n    if not api_key:\\n        return {\\"error\\": \\"Missing API key\\"}\\n        \\n    try:\\n        async with httpx.AsyncClient() as client:\\n            response = await client.post(\\n                \\"https://api.openai.com/v1/chat/completions\\",\\n                headers={\\"Authorization\\": f\\"Bearer {api_key}\\"},\\n                json={\\n                    \\"model\\": \\"gpt-4\\",\\n                    \\"messages\\": [\\n                        {\\"role\\": \\"system\\", \\"content\\": \\"Classify as Billing, Technical, or Account.\\"},\\n                        {\\"role\\": \\"user\\", \\"content\\": message}\\n                    ]\\n                }\\n            )\\n            response.raise_for_status()\\n            return response.json()\\n    except Exception as e:\\n        return {\\"error\\": str(e)}",
      "installRequirements": ["httpx"]
    },
    {
      "filename": "tools/check_queue_availability.py",
      "content": "import os\\nimport httpx\\n\\nasync def check_queue_availability(queue_name: str) -> dict:\\n    \\"\\"\\"Check wait times for specialist queues\\"\\"\\"\\n    # Mock Salesforce API call\\n    return {\\"queue\\": queue_name, \\"wait_time_minutes\\": 2, \\"available_agents\\": 3}",
      "installRequirements": ["httpx"]
    }
  ]
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "tools": [
    {
      "filename": "tools/create_draft_ticket.py",
      "content": "import os\\nimport httpx\\n\\nasync def create_draft_ticket(issue_summary: str, employee_id: str) -> dict:\\n    \\"\\"\\"Initialize ticket record\\"\\"\\"\\n    # Mock ServiceNow API call\\n    ticket_id = f\\"INC-{hash(issue_summary) % 10000}\\"\\n    return {\\"ticket_id\\": ticket_id, \\"status\\": \\"draft\\", \\"summary\\": issue_summary}",
      "installRequirements": ["httpx"]
    },
    {
      "filename": "tools/classify_ticket_category.py",
      "content": "import os\\nimport httpx\\n\\nasync def classify_ticket_category(description: str) -> dict:\\n    \\"\\"\\"Determine ticket category from description\\"\\"\\"\\n    # Simple keyword classifier for example\\n    desc_lower = description.lower()\\n    if \\"screen\\" in desc_lower or \\"laptop\\" in desc_lower:\\n        return {\\"category\\": \\"Hardware\\"}\\n    elif \\"login\\" in desc_lower or \\"password\\" in desc_lower:\\n        return {\\"category\\": \\"Access\\"}\\n    else:\\n        return {\\"category\\": \\"Software\\"}",
      "installRequirements": []
    }
  ]
}""",
            2: """{
  "tools": [
    {
      "filename": "tools/ingest_p1_alert.py",
      "content": "async def ingest_p1_alert(**kwargs) -> dict:\\n    \\"\\"\\"Pull alert metadata and context\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_ingest_p1_alert'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/run_baseline_diagnostics.py",
      "content": "async def run_baseline_diagnostics(**kwargs) -> dict:\\n    \\"\\"\\"Execute standard diagnostics\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_run_baseline_diagnostics'}",
      "installRequirements": ["psutil"]
    },
    {
      "filename": "tools/attempt_auto_remediation.py",
      "content": "async def attempt_auto_remediation(**kwargs) -> dict:\\n    \\"\\"\\"Run automated fixes\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_attempt_auto_remediation'}",
      "installRequirements": ["requests", "paramiko"]
    },
    {
      "filename": "tools/evaluate_recovery_confidence.py",
      "content": "async def evaluate_recovery_confidence(diagnostics: dict) -> dict:\\n    \\"\\"\\"Calculate confidence score for automated recovery based on diagnostic results.\\"\\"\\"\\n    score = 1.0\\n    factors = []\\n    \\n    if diagnostics.get('db_latency', 0) > 1000:\\n        score -= 0.3\\n        factors.append('high_db_latency')\\n        \\n    if diagnostics.get('error_rate', 0) > 0.05:\\n        score -= 0.4\\n        factors.append('high_error_rate')\\n        \\n    if not diagnostics.get('service_healthy', False):\\n        score -= 0.5\\n        factors.append('service_unhealthy')\\n        \\n    return {\\n        'confidence': max(0.0, score),\\n        'risk_factors': factors,\\n        'recommendation': 'escalate' if score < 0.85 else 'automate'\\n    }",
      "installRequirements": []
    },
    {
      "filename": "tools/package_incident_context.py",
      "content": "async def package_incident_context(**kwargs) -> dict:\\n    \\"\\"\\"Bundle incident details\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_package_incident_context'}",
      "installRequirements": []
    },
    {
      "filename": "tools/promote_response_tier.py",
      "content": "async def promote_response_tier(**kwargs) -> dict:\\n    \\"\\"\\"Escalate to next level\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_promote_response_tier'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/execute_mitigation_playbook.py",
      "content": "async def execute_mitigation_playbook(**kwargs) -> dict:\\n    \\"\\"\\"Run advanced remediation\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_execute_mitigation_playbook'}",
      "installRequirements": ["ansible-runner"]
    },
    {
      "filename": "tools/publish_status_update.py",
      "content": "async def publish_status_update(**kwargs) -> dict:\\n    \\"\\"\\"Broadcast incident status\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_publish_status_update'}",
      "installRequirements": ["requests"]
    }
  ]
}""",
            3: """{
  "tools": [
    {
      "filename": "tools/capture_campaign_brief.py",
      "content": "async def capture_campaign_brief(**kwargs) -> dict:\\n    \\"\\"\\"Gather campaign requirements\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_capture_campaign_brief'}",
      "installRequirements": []
    },
    {
      "filename": "tools/log_reference_assets.py",
      "content": "async def log_reference_assets(**kwargs) -> dict:\\n    \\"\\"\\"Store inspiration materials\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_log_reference_assets'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/generate_launch_copy.py",
      "content": "async def generate_launch_copy(**kwargs) -> dict:\\n    \\"\\"\\"Create campaign copy variants\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_generate_launch_copy'}",
      "installRequirements": ["openai"]
    },
    {
      "filename": "tools/record_generation_rationale.py",
      "content": "async def record_generation_rationale(**kwargs) -> dict:\\n    \\"\\"\\"Log creative decisions\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_record_generation_rationale'}",
      "installRequirements": []
    },
    {
      "filename": "tools/score_messaging_pillars.py",
      "content": "async def score_messaging_pillars(copy_draft: str, pillars: list) -> dict:\\n    \\"\\"\\"Score draft copy against defined messaging pillars.\\"\\"\\"\\n    scores = {}\\n    for pillar in pillars:\\n        # Mock scoring logic - in production use LLM evaluation\\n        presence = 1 if pillar.lower() in copy_draft.lower() else 0\\n        scores[pillar] = {\\n            'score': 3 + (presence * 2),  # 3 or 5\\n            'feedback': 'Pillar well represented' if presence else 'Pillar missing from draft'\\n        }\\n    return {'pillar_scores': scores, 'overall_average': sum(s['score'] for s in scores.values()) / len(scores) if scores else 0}",
      "installRequirements": []
    },
    {
      "filename": "tools/flag_campaign_blockers.py",
      "content": "async def flag_campaign_blockers(**kwargs) -> dict:\\n    \\"\\"\\"Surface approval issues\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_flag_campaign_blockers'}",
      "installRequirements": []
    },
    {
      "filename": "tools/apply_feedback_actions.py",
      "content": "async def apply_feedback_actions(**kwargs) -> dict:\\n    \\"\\"\\"Update content based on feedback\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_apply_feedback_actions'}",
      "installRequirements": []
    },
    {
      "filename": "tools/update_approval_status.py",
      "content": "async def update_approval_status(**kwargs) -> dict:\\n    \\"\\"\\"Track approval state\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_update_approval_status'}",
      "installRequirements": ["requests"]
    }
  ]
}""",
            4: """{
  "tools": [
    {
      "filename": "tools/decompose_market_objectives.py",
      "content": "async def decompose_market_objectives(objective: str) -> dict:\\n    \\"\\"\\"Break down high-level market objective into workstream tasks.\\"\\"\\"\\n    return {\\n        'demand_tasks': [f'Analyze TAM for {objective}', 'Identify key buyer personas'],\\n        'competitor_tasks': [f'Map competitive landscape for {objective}', 'Benchmark pricing'],\\n        'regulatory_tasks': ['Identify compliance hurdles', 'Review licensing requirements']\\n    }",
      "installRequirements": []
    },
    {
      "filename": "tools/assign_workstream_managers.py",
      "content": "async def assign_workstream_managers(**kwargs) -> dict:\\n    \\"\\"\\"Delegate research workstreams\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_assign_workstream_managers'}",
      "installRequirements": []
    },
    {
      "filename": "tools/publish_governance_brief.py",
      "content": "async def publish_governance_brief(**kwargs) -> dict:\\n    \\"\\"\\"Share strategy guidelines\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_publish_governance_brief'}",
      "installRequirements": []
    },
    {
      "filename": "tools/plan_demand_research.py",
      "content": "async def plan_demand_research(**kwargs) -> dict:\\n    \\"\\"\\"Define demand analysis scope\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_plan_demand_research'}",
      "installRequirements": []
    },
    {
      "filename": "tools/define_success_metrics.py",
      "content": "async def define_success_metrics(**kwargs) -> dict:\\n    \\"\\"\\"Set research KPIs\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_define_success_metrics'}",
      "installRequirements": []
    },
    {
      "filename": "tools/plan_regulatory_research.py",
      "content": "async def plan_regulatory_research(**kwargs) -> dict:\\n    \\"\\"\\"Structure compliance review\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_plan_regulatory_research'}",
      "installRequirements": []
    },
    {
      "filename": "tools/log_compliance_questions.py",
      "content": "async def log_compliance_questions(**kwargs) -> dict:\\n    \\"\\"\\"Track regulatory queries\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_log_compliance_questions'}",
      "installRequirements": []
    },
    {
      "filename": "tools/plan_competitive_analysis.py",
      "content": "async def plan_competitive_analysis(**kwargs) -> dict:\\n    \\"\\"\\"Define competitor research tasks\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_plan_competitive_analysis'}",
      "installRequirements": []
    },
    {
      "filename": "tools/track_specialist_updates.py",
      "content": "async def track_specialist_updates(**kwargs) -> dict:\\n    \\"\\"\\"Monitor research progress\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_track_specialist_updates'}",
      "installRequirements": []
    },
    {
      "filename": "tools/collect_demand_signals.py",
      "content": "async def collect_demand_signals(**kwargs) -> dict:\\n    \\"\\"\\"Gather market data\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_collect_demand_signals'}",
      "installRequirements": ["requests", "pandas"]
    },
    {
      "filename": "tools/benchmark_market_size.py",
      "content": "async def benchmark_market_size(**kwargs) -> dict:\\n    \\"\\"\\"Quantify TAM/SAM\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_benchmark_market_size'}",
      "installRequirements": ["pandas"]
    },
    {
      "filename": "tools/analyze_regulatory_climate.py",
      "content": "async def analyze_regulatory_climate(**kwargs) -> dict:\\n    \\"\\"\\"Review compliance landscape\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_analyze_regulatory_climate'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/log_license_requirements.py",
      "content": "async def log_license_requirements(**kwargs) -> dict:\\n    \\"\\"\\"Document licensing needs\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_log_license_requirements'}",
      "installRequirements": []
    },
    {
      "filename": "tools/profile_competitors.py",
      "content": "async def profile_competitors(**kwargs) -> dict:\\n    \\"\\"\\"Analyze competitor landscape\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_profile_competitors'}",
      "installRequirements": ["requests", "beautifulsoup4"]
    },
    {
      "filename": "tools/analyze_pricing_models.py",
      "content": "async def analyze_pricing_models(**kwargs) -> dict:\\n    \\"\\"\\"Study pricing strategies\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_analyze_pricing_models'}",
      "installRequirements": ["pandas"]
    },
    {
      "filename": "tools/aggregate_workstream_findings.py",
      "content": "async def aggregate_workstream_findings(**kwargs) -> dict:\\n    \\"\\"\\"Synthesize research outputs\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_aggregate_workstream_findings'}",
      "installRequirements": []
    },
    {
      "filename": "tools/prepare_go_no_go_brief.py",
      "content": "async def prepare_go_no_go_brief(**kwargs) -> dict:\\n    \\"\\"\\"Generate decision document\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_prepare_go_no_go_brief'}",
      "installRequirements": []
    }
  ]
}""",
            5: """{
  "tools": [
    {
      "filename": "tools/surface_reference_assets.py",
      "content": "async def surface_reference_assets(**kwargs) -> dict:\\n    \\"\\"\\"Pull previous successes\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_surface_reference_assets'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/generate_copy_hooks.py",
      "content": "async def generate_copy_hooks(theme: str, count: int = 3) -> list:\\n    \\"\\"\\"Generate creative copy hooks for a given theme.\\"\\"\\"\\n    # Placeholder for creative generation logic\\n    hooks = []\\n    for i in range(count):\\n        hooks.append({\\n            'hook_text': f'Experience {theme} like never before - Variant {i+1}',\\n            'tone': 'Exciting',\\n            'target_audience': 'General'\\n        })\\n    return hooks",
      "installRequirements": ["openai"]
    },
    {
      "filename": "tools/tag_emerging_themes.py",
      "content": "async def tag_emerging_themes(**kwargs) -> dict:\\n    \\"\\"\\"Categorize ideas\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_tag_emerging_themes'}",
      "installRequirements": ["scikit-learn"]
    },
    {
      "filename": "tools/update_idea_pool.py",
      "content": "async def update_idea_pool(**kwargs) -> dict:\\n    \\"\\"\\"Track brainstorm progress\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_update_idea_pool'}",
      "installRequirements": []
    },
    {
      "filename": "tools/pull_performance_signals.py",
      "content": "async def pull_performance_signals(**kwargs) -> dict:\\n    \\"\\"\\"Retrieve campaign metrics\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_pull_performance_signals'}",
      "installRequirements": ["requests", "pandas"]
    },
    {
      "filename": "tools/identify_theme_gaps.py",
      "content": "async def identify_theme_gaps(**kwargs) -> dict:\\n    \\"\\"\\"Detect missing coverage\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_identify_theme_gaps'}",
      "installRequirements": []
    },
    {
      "filename": "tools/queue_scheduling_metadata.py",
      "content": "async def queue_scheduling_metadata(**kwargs) -> dict:\\n    \\"\\"\\"Set publication timing\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_queue_scheduling_metadata'}",
      "installRequirements": []
    }
  ]
}""",
            6: """{
  "tools": [
    {
      "filename": "tools/validate_required_documents.py",
      "content": "async def validate_required_documents(uploaded_docs: list) -> dict:\\n    \\"\\"\\"Check if all mandatory documents are present.\\"\\"\\"\\n    required = {'id_proof', 'income_proof', 'bank_statement'}\\n    present = set(doc['type'] for doc in uploaded_docs)\\n    missing = list(required - present)\\n    \\n    return {\\n        'valid': len(missing) == 0,\\n        'missing_documents': missing,\\n        'verification_timestamp': '2023-10-27T10:00:00Z'\\n    }",
      "installRequirements": []
    },
    {
      "filename": "tools/normalize_applicant_profile.py",
      "content": "async def normalize_applicant_profile(**kwargs) -> dict:\\n    \\"\\"\\"Standardize applicant data\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_normalize_applicant_profile'}",
      "installRequirements": []
    },
    {
      "filename": "tools/run_credit_report.py",
      "content": "async def run_credit_report(**kwargs) -> dict:\\n    \\"\\"\\"Pull credit history\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_run_credit_report'}",
      "installRequirements": ["requests"]
    },
    {
      "filename": "tools/execute_fraud_screen.py",
      "content": "async def execute_fraud_screen(**kwargs) -> dict:\\n    \\"\\"\\"Detect fraud signals\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_execute_fraud_screen'}",
      "installRequirements": ["requests", "scikit-learn"]
    },
    {
      "filename": "tools/log_kyc_findings.py",
      "content": "async def log_kyc_findings(**kwargs) -> dict:\\n    \\"\\"\\"Document KYC results\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_log_kyc_findings'}",
      "installRequirements": []
    },
    {
      "filename": "tools/apply_underwriting_policy.py",
      "content": "async def apply_underwriting_policy(**kwargs) -> dict:\\n    \\"\\"\\"Evaluate loan eligibility\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_apply_underwriting_policy'}",
      "installRequirements": ["pandas"]
    },
    {
      "filename": "tools/calculate_offer_terms.py",
      "content": "async def calculate_offer_terms(**kwargs) -> dict:\\n    \\"\\"\\"Determine loan parameters\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_calculate_offer_terms'}",
      "installRequirements": ["numpy"]
    },
    {
      "filename": "tools/notify_borrower.py",
      "content": "async def notify_borrower(**kwargs) -> dict:\\n    \\"\\"\\"Send offer to applicant\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_notify_borrower'}",
      "installRequirements": ["requests", "sendgrid"]
    },
    {
      "filename": "tools/sync_fulfillment_status.py",
      "content": "async def sync_fulfillment_status(**kwargs) -> dict:\\n    \\"\\"\\"Update loan status\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_sync_fulfillment_status'}",
      "installRequirements": ["requests"]
    }
  ]
}""",
            7: """{
  "tools": [
    {
      "filename": "tools/compile_scenario_brief.py",
      "content": "async def compile_scenario_brief(**kwargs) -> dict:\\n    \\"\\"\\"Structure forecast requirements\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_compile_scenario_brief'}",
      "installRequirements": []
    },
    {
      "filename": "tools/lock_evaluation_metrics.py",
      "content": "async def lock_evaluation_metrics(**kwargs) -> dict:\\n    \\"\\"\\"Define comparison criteria\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_lock_evaluation_metrics'}",
      "installRequirements": []
    },
    {
      "filename": "tools/train_statistical_model.py",
      "content": "async def train_statistical_model(**kwargs) -> dict:\\n    \\"\\"\\"Build time-series model\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_train_statistical_model'}",
      "installRequirements": ["pandas", "statsmodels", "scikit-learn"]
    },
    {
      "filename": "tools/generate_statistical_projection.py",
      "content": "async def generate_statistical_projection(**kwargs) -> dict:\\n    \\"\\"\\"Create forecast output\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_generate_statistical_projection'}",
      "installRequirements": ["pandas", "numpy"]
    },
    {
      "filename": "tools/ingest_exogenous_signals.py",
      "content": "async def ingest_exogenous_signals(**kwargs) -> dict:\\n    \\"\\"\\"Load external variables\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_ingest_exogenous_signals'}",
      "installRequirements": ["requests", "pandas"]
    },
    {
      "filename": "tools/generate_causal_projection.py",
      "content": "async def generate_causal_projection(**kwargs) -> dict:\\n    \\"\\"\\"Build causal forecast\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_generate_causal_projection'}",
      "installRequirements": ["pandas", "scikit-learn"]
    },
    {
      "filename": "tools/apply_heuristic_rules.py",
      "content": "async def apply_heuristic_rules(**kwargs) -> dict:\\n    \\"\\"\\"Generate rule-based forecast\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_apply_heuristic_rules'}",
      "installRequirements": ["pandas"]
    },
    {
      "filename": "tools/stress_test_edge_cases.py",
      "content": "async def stress_test_edge_cases(**kwargs) -> dict:\\n    \\"\\"\\"Test extreme scenarios\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_stress_test_edge_cases'}",
      "installRequirements": ["numpy"]
    },
    {
      "filename": "tools/score_forecast_accuracy.py",
      "content": "async def score_forecast_accuracy(forecast: list, actuals: list) -> dict:\\n    \\"\\"\\"Calculate MAPE and RMSE for forecast vs actuals.\\"\\"\\"\\n    if len(forecast) != len(actuals):\\n        return {'error': 'Length mismatch'}\\n        \\n    errors = [abs(f - a) for f, a in zip(forecast, actuals)]\\n    mape = sum(e / a for e, a in zip(errors, actuals) if a != 0) / len(actuals)\\n    \\n    return {\\n        'mape': mape,\\n        'accuracy_score': max(0, 1 - mape),\\n        'bias': sum(errors) / len(errors)\\n    }",
      "installRequirements": ["numpy", "scikit-learn"]
    },
    {
      "filename": "tools/analyze_volatility.py",
      "content": "async def analyze_volatility(**kwargs) -> dict:\\n    \\"\\"\\"Assess forecast stability\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_analyze_volatility'}",
      "installRequirements": ["pandas", "numpy"]
    },
    {
      "filename": "tools/document_selection_rationale.py",
      "content": "async def document_selection_rationale(**kwargs) -> dict:\\n    \\"\\"\\"Explain choice\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_document_selection_rationale'}",
      "installRequirements": []
    }
  ]
}""",
            8: """{
  "tools": [
    {
      "filename": "tools/validate_vendor_submission.py",
      "content": "async def validate_vendor_submission(**kwargs) -> dict:\\n    \\"\\"\\"Check submission completeness\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_validate_vendor_submission'}",
      "installRequirements": []
    },
    {
      "filename": "tools/determine_required_checks.py",
      "content": "async def determine_required_checks(vendor_profile: dict) -> list:\\n    \\"\\"\\"Determine which spoke reviews are required based on vendor profile.\\"\\"\\"\\n    checks = ['finance']  # Always required\\n    \\n    if vendor_profile.get('access_sensitive_data', False):\\n        checks.append('security')\\n        \\n    if vendor_profile.get('contract_value', 0) > 50000:\\n        checks.append('legal')\\n        \\n    return checks",
      "installRequirements": []
    },
    {
      "filename": "tools/run_financial_due_diligence.py",
      "content": "async def run_financial_due_diligence(**kwargs) -> dict:\\n    \\"\\"\\"Assess financial risk\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_run_financial_due_diligence'}",
      "installRequirements": ["requests", "pandas"]
    },
    {
      "filename": "tools/verify_banking_details.py",
      "content": "async def verify_banking_details(**kwargs) -> dict:\\n    \\"\\"\\"Validate payment info\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_verify_banking_details'}",
      "installRequirements": ["requests", "stripe"]
    },
    {
      "filename": "tools/post_finance_status.py",
      "content": "async def post_finance_status(**kwargs) -> dict:\\n    \\"\\"\\"Update review status\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_post_finance_status'}",
      "installRequirements": []
    },
    {
      "filename": "tools/analyze_security_questionnaire.py",
      "content": "async def analyze_security_questionnaire(**kwargs) -> dict:\\n    \\"\\"\\"Review security posture\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_analyze_security_questionnaire'}",
      "installRequirements": ["scikit-learn"]
    },
    {
      "filename": "tools/assess_security_risk.py",
      "content": "async def assess_security_risk(**kwargs) -> dict:\\n    \\"\\"\\"Score security compliance\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_assess_security_risk'}",
      "installRequirements": []
    },
    {
      "filename": "tools/post_security_status.py",
      "content": "async def post_security_status(**kwargs) -> dict:\\n    \\"\\"\\"Update review status\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_post_security_status'}",
      "installRequirements": []
    },
    {
      "filename": "tools/review_contract_terms.py",
      "content": "async def review_contract_terms(**kwargs) -> dict:\\n    \\"\\"\\"Analyze legal agreements\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_review_contract_terms'}",
      "installRequirements": ["openai"]
    },
    {
      "filename": "tools/flag_compliance_gaps.py",
      "content": "async def flag_compliance_gaps(**kwargs) -> dict:\\n    \\"\\"\\"Identify legal issues\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_flag_compliance_gaps'}",
      "installRequirements": []
    },
    {
      "filename": "tools/post_legal_status.py",
      "content": "async def post_legal_status(**kwargs) -> dict:\\n    \\"\\"\\"Update review status\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_post_legal_status'}",
      "installRequirements": []
    },
    {
      "filename": "tools/monitor_spoke_progress.py",
      "content": "async def monitor_spoke_progress(**kwargs) -> dict:\\n    \\"\\"\\"Track review completion\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_monitor_spoke_progress'}",
      "installRequirements": []
    },
    {
      "filename": "tools/resolve_risk_conflicts.py",
      "content": "async def resolve_risk_conflicts(**kwargs) -> dict:\\n    \\"\\"\\"Mediate cross-spoke issues\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_resolve_risk_conflicts'}",
      "installRequirements": []
    },
    {
      "filename": "tools/trigger_account_provisioning.py",
      "content": "async def trigger_account_provisioning(**kwargs) -> dict:\\n    \\"\\"\\"Activate vendor account\\"\\"\\"\\n    # Mock implementation\\n    return {'status': 'success', 'data': 'mock_data_for_trigger_account_provisioning'}",
      "installRequirements": ["requests"]
    }
  ]
}""",
            9: """{
  "tools": [
    {
      "filename": "tools/check_subscription_tier.py",
      "content": "async def check_subscription_tier(user_id: str, **runtime) -> dict:\\n    \\"\\"\\"Check user subscription tier and feature access.\\"\\"\\"\\n    # Mock implementation\\n    return {\\n        'tier': 'pro',\\n        'features': ['dream_generation', 'video_export'],\\n        'credits_remaining': 50\\n    }",
      "installRequirements": []
    },
    {
      "filename": "tools/create_dream_tasks.py",
      "content": "async def create_dream_tasks(dream_description: str, **runtime) -> dict:\\n    \\"\\"\\"Decompose dream description into actionable tasks.\\"\\"\\"\\n    return {\\n        'tasks': [\\n            {'id': '1', 'title': 'Visualize scene', 'status': 'pending'},\\n            {'id': '2', 'title': 'Generate audio', 'status': 'pending'}\\n        ]\\n    }",
      "installRequirements": []
    },
    {
      "filename": "tools/generate_veo3_video.py",
      "content": "import os\\nimport time\\nimport asyncio\\nfrom google import genai\\nfrom google.genai import types\\n\\nasync def generate_veo3_video(prompt: str, aspect_ratio: str = '16:9', resolution: str = '1080p') -> dict:\\n    \\"\\"\\"Generate video using Google Veo 3 model via Gemini API.\\"\\"\\"\\n    api_key = os.environ.get('GEMINI_API_KEY')\\n    if not api_key:\\n        return {'status': 'error', 'message': 'GEMINI_API_KEY environment variable not set'}\\n\\n    client = genai.Client(api_key=api_key)\\n    model_id = 'veo-3.1-fast-generate-preview'\\n\\n    try:\\n        operation = client.models.generate_videos(\\n            model=model_id,\\n            prompt=prompt,\\n            config=types.GenerateVideosConfig(\\n                aspect_ratio=aspect_ratio,\\n                resolution=resolution,\\n            ),\\n        )\\n\\n        while not operation.done:\\n            await asyncio.sleep(5)\\n            operation = client.operations.get(operation)\\n\\n        if not operation.result.generated_videos:\\n             return {'status': 'failed', 'message': 'No videos generated'}\\n\\n        # In a real app, upload to storage and return URL.\\n        # Here we return metadata.\\n        return {\\n            'status': 'success',\\n            'data': {\\n                'message': 'Video generated successfully',\\n                'model': model_id\\n            }\\n        }\\n\\n    except Exception as e:\\n        return {'status': 'error', 'message': str(e)}",
      "installRequirements": ["google-genai"]
    },
    {
      "filename": "tools/analyze_dream_symbols.py",
      "content": "import os\\nfrom google import genai\\n\\nasync def analyze_dream_symbols(dream_text: str) -> dict:\\n    \\"\\"\\"Analyze psychological themes in a dream using Gemini.\\"\\"\\"\\n    api_key = os.environ.get('GEMINI_API_KEY')\\n    if not api_key:\\n        return {'status': 'error', 'message': 'GEMINI_API_KEY environment variable not set'}\\n\\n    client = genai.Client(api_key=api_key)\\n    \\n    prompt = f\\"Analyze the following dream and identify key psychological themes and symbols: {dream_text}\\"\\n    \\n    try:\\n        response = client.models.generate_content(\\n            model='gemini-2.0-flash',\\n            contents=prompt\\n        )\\n        return {'status': 'success', 'analysis': response.text}\\n    except Exception as e:\\n        return {'status': 'error', 'message': str(e)}",
      "installRequirements": ["google-genai"]
    }
  ]
}"""
        }
        
        example_json = agent_tool_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No agent tool example found for pattern_id {pattern_id}")
            return

        # Semantic Context Injection
        tool_planning = _get_upstream_context(agent, 'ToolPlanning')
        semantic_context = ""
        if tool_planning:
            tool_summary = ""
            for tool in tool_planning.get('agent_tools', []) or []:
                integration_hint = tool.get("integration")
                if integration_hint:
                    tool_summary += (
                        f"- Tool: {tool.get('name')} (Integration hint: {integration_hint})\n"
                        f"  Agent: {tool.get('agent')}\n"
                        f"  Purpose: {tool.get('purpose')}\n"
                    )
                else:
                    tool_summary += (
                        f"- Tool: {tool.get('name')}\n"
                        f"  Agent: {tool.get('agent')}\n"
                        f"  Purpose: {tool.get('purpose')}\n"
                    )
            
            if tool_summary:
                semantic_context = (
                    f"\n[UPSTREAM CONTEXT: TOOL PLANNING]\n"
                    f"ToolPlanningAgent has defined the following tools. You MUST generate the Python code for these EXACT tools:\n"
                    f"{tool_summary}\n\n"
                )

        planning_note = (
            "[PLANNING CONTRACT]\n"
            "- `integration` values in ToolPlanning.agent_tools are planning hints only for dependency-aware code generation.\n"
            "- Do NOT invent runtime manifest fields from them. Final `tools.yaml` is authored separately.\n\n"
        )

        guidance = (
            f"{planning_note}{semantic_context}"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete AgentToolsFileGeneratorOutput JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected agent tool guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_agent_tools_file_generator_guidance: {e}", exc_info=True)


def _to_snake_case(value: str) -> str:
    cleaned: List[str] = []
    last_was_sep = False
    for char in str(value or "").strip().lower():
        if char.isalnum():
            cleaned.append(char)
            last_was_sep = False
        elif not last_was_sep:
            cleaned.append("_")
            last_was_sep = True
    result = "".join(cleaned).strip("_")
    return result or "generated"


def _to_pascal_case(value: str) -> str:
    parts = [part for part in _to_snake_case(value).split("_") if part]
    return "".join(part.capitalize() for part in parts) or "Generated"


def _build_ui_file_generator_example(pattern_display_name: str) -> str:
    pattern_pascal = _to_pascal_case(pattern_display_name)
    workflow_name = f"{pattern_pascal}Workflow"
    component_name = f"{pattern_pascal}ReviewPanel"
    function_name = f"review_{_to_snake_case(pattern_display_name)}_panel"

    python_content = f"""from __future__ import annotations
from typing import Annotated, Any, Optional

from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool


async def {function_name}(
    context_variables: Annotated[Optional[Any], "AG2 context"] = None,
    chat_id: Annotated[Optional[str], "Current chat id"] = None,
    workflow_name: Annotated[Optional[str], "Active workflow name"] = None,
) -> dict[str, Any]:
    \"\"\"Render {component_name} and wait for the user's response.\"\"\"
    structured: Any = {{}}
    if context_variables is not None:
        getter = getattr(context_variables, "get", None)
        if callable(getter):
            structured = getter("structured_output") or {{}}
        else:
            data = getattr(context_variables, "data", None)
            if isinstance(data, dict):
                structured = data.get("structured_output") or {{}}

    if not isinstance(structured, dict):
        structured = {{}}

    payload = {{
        "title": structured.get("title") or "{pattern_display_name} Review",
        "summary": structured.get("summary") or "Review the generated output before continuing.",
        "status": structured.get("status") or "ready",
        "items": structured.get("items") or [],
        "agent_message": structured.get("agent_message") or "Review the generated output and choose the next step.",
    }}

    try:
        return await use_ui_tool(
            "{component_name}",
            payload,
            chat_id=chat_id,
            workflow_name=workflow_name or "{workflow_name}",
        )
    except UIToolError as exc:
        return {{"status": "error", "message": str(exc)}}


__all__ = ["{function_name}"]
"""

    react_content = f"""import {{ useState }} from 'react'
import {{ Alert, Button, Panel, StatusPill }} from '@mozaiks/chat-ui/ui'

export default function {component_name}({{
  payload = {{}},
  onResponse,
  onCancel,
  toolName,
  toolCallId,
  workflowName,
  componentId = '{component_name}',
}}) {{
  const [submitting, setSubmitting] = useState(false)
  const items = Array.isArray(payload.items) ? payload.items : []
  const title = payload.title || '{pattern_display_name} Review'
  const summary = payload.summary || 'Review the generated output before continuing.'
  const statusLabel = payload.status || 'ready'

  const submit = async (action) => {{
    setSubmitting(true)
    try {{
      await onResponse?.({{
        status: 'submitted',
        action,
        data: {{
          action,
          tool_name: toolName,
          tool_call_id: toolCallId,
          workflowName,
          componentId,
        }},
      }})
    }} finally {{
      setSubmitting(false)
    }}
  }}

  return (
    <Panel title={{title}} action={{<StatusPill label={{statusLabel}} tone="primary" />}}>
      <div className="space-y-4">
        <span className="text-xs text-muted-foreground">{{workflowName || 'GeneratedWorkflow'}}</span>
        {{payload.error ? <Alert message={{payload.error}} variant="warning" /> : null}}
        <p className="text-sm text-muted-foreground">{{summary}}</p>
        <div className="space-y-2">
          {{items.length ? items.map((item, index) => (
            <div
              key={{index}}
              className="rounded-lg border border-border bg-card/60 px-3 py-2 text-sm text-foreground"
            >
              {{typeof item === 'string' ? item : item?.label || item?.title || `Item ${{index + 1}}`}}
            </div>
          )) : (
            <Alert
              message="No line items were provided. Render the summary and still allow approval or revision."
              variant="info"
            />
          )}}
        </div>
        <div className="flex flex-wrap gap-3">
          <Button
            label="Approve"
            variant="primary"
            loading={{submitting}}
            onClick={{() => submit('approve')}}
          />
          <Button
            label="Needs Revision"
            variant="secondary"
            loading={{submitting}}
            onClick={{() => submit('revise')}}
          />
          {{onCancel ? (
            <Button
              label="Cancel"
              variant="ghost"
              onClick={{() => onCancel({{ status: 'cancelled' }})}}
            />
          ) : null}}
        </div>
      </div>
    </Panel>
  )
}}
"""

    example = {
        "tools": [
            {
                "filename": f"tools/{function_name}.py",
                "content": python_content,
                "installRequirements": [],
            },
            {
                "filename": f"ui/{workflow_name}/{component_name}.jsx",
                "content": react_content,
                "installRequirements": [],
            },
        ]
    }
    return json.dumps(example, indent=2)


def inject_ui_file_generator_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for UIFileGenerator.
    Injects pattern-specific UI tool generation guidance.

    UIFileGenerator OUTPUT FORMAT (UIFileGeneratorOutput JSON):
    {
      "tools": [
        {
          "filename": "tools/<snake_case>.py",
          "content": "<complete_python_async_function>",
          "installRequirements": []
        },
        {
          "filename": "ui/<WorkflowName>/<PascalCase>.jsx",
          "content": "<complete_react_component>",
          "installRequirements": []
        }
      ]
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        example_json = _build_ui_file_generator_example(pattern_display_name)

        blueprint = _get_upstream_context(agent, 'TechnicalBlueprint')
        semantic_context = ""
        if blueprint:
            components = blueprint.get('ui_components', [])
            if components:
                comp_summary = "\\n".join([
                    f"- Component: {c.get('component')} (Tool: {c.get('tool')})\\n"
                    f"  Display: {c.get('display')}, Interaction: {c.get('ui_pattern')}\\n"
                    f"  Summary: {c.get('summary')}"
                    for c in components
                ])
                semantic_context = (
                    f"\\n[UPSTREAM CONTEXT: TECHNICAL BLUEPRINT]\\n"
                    f"The WorkflowArchitectAgent has defined the following UI components. You MUST generate the React code and Python tool wrappers for these EXACT components:\\n"
                    f"{comp_summary}\\n\\n"
                )

        contract_rules = (
            "[UI FILE GENERATOR CONTRACT]\\n"
            "- Use `from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool` for generated Python UI tools.\\n"
            "- Do not import legacy `app.modules.ui_tools` or `app.plugins.ui_tools` helpers.\\n"
            "- Do not call low-level `send_tool_call_event` directly, or subscribe via `useAppEventBus` in generated React.\\n"
            "- `use_ui_tool(...)` emits a session-scoped `chat.tool_call`; generated React should assume the shell mounts it from that workflow UI lane, not from legacy `ui_tool_event` transport semantics.\\n"
            "- Generated React components are mounted by `WorkflowUIRouter` and receive props like `payload`, `onResponse`, `onCancel`, `toolName`, `toolCallId`, `workflowName`, and `componentId`.\\n"
            "- `ToolsManifest.ui.workflow_primitive` is the canonical interaction pattern, and `ToolsManifest.ui.realization` declares whether the component is shell-owned, direct shipped, a workflow wrapper, or a generated custom surface.\\n"
            "- If `ui.realization`=`shipped_component`, do not generate custom React for it; those shipped shared components already exist in `chat-ui`.\\n"
            "- If `ui.realization`=`workflow_wrapper`, generate only a thin wrapper or re-export around the shipped component.\\n"
            "- If `ui.realization`=`generated_component`, generate the full workflow-local React surface.\\n"
            "- The `use_ui_tool(...)` identifier must match the declared `ui.component` value so runtime lookup resolves correctly.\\n"
            "- Do not generate an inline text box just to collect ordinary chat feedback. Plain free-text user reply should use the shell composer/input-request lane unless the interaction is structured enough to require a dedicated component.\\n"
            "- Compose only the shipped `chat-ui` primitives declared in `[SHIPPED UI PRIMITIVES]`.\n"
            "\n"
            "[TRANSITION WRAPPER CONTRACT]\n"
            "- If the requested file lives under `extended_orchestration/ui/transitions/`, generate a React-only transition wrapper, not a Python + React `UI_Tool` pair.\n"
            "- For branded transition wrappers, compose the shell-owned pieces declared in `[TRANSITION UI PRIMITIVE CATALOG]`, especially `TransitionChoicePanel`, `TransitionChoiceCard`, and `useTransitionChoiceMotion`.\n"
            "- Do not import `TransitionOverlayFrame` into workflow-local transition files; the shell owns overlay behavior.\n"
            "- Export file-backed transition wrappers through `extended_orchestration/ui/index.js`; keep `extension_registry.json` declarative and semantic.\n"
            "- Do not invent workflow-local reusable transition primitive helpers; keep wrapper files focused on copy, imagery, option mapping, and `onResolve(optionId)` wiring.\n\n"
        )

        guidance = (
            f"{semantic_context}"
            f"{contract_rules}"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\\n"
            f"Here is a complete UIFileGeneratorOutput JSON example aligned with the {pattern_display_name} pattern.\\n\\n"
            f"```json\\n{example_json}\\n```\\n"
        )

        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected UI tool guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_ui_file_generator_guidance: {e}", exc_info=True)


def inject_handoffs_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for HandoffsAgent.
    Injects pattern-specific handoff rules into system message.

    HandoffsAgent OUTPUT FORMAT:
    Emit exactly one JSON object with the following structure:
    {
      "handoff_rules": [
        {
          "source_agent": "<AgentName>",
          "target_agent": "<AgentName>",
          "handoff_type": "condition|after_work",
          "condition_type": "expression|string_llm|null",
          "condition_scope": "pre|post|null",
          "condition": "<expression_string>|null",
          "transition_target": "AgentTarget|RevertToUserTarget|GroupManagerTarget",
          "metadata": {}
        }
      ],
      "agent_message": "<Summary>"
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)

        # Pattern-specific handoff examples (complete JSON payloads)
        handoff_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "handoff_rules": [
    {
      "source_agent": "RouterAgent",
      "target_agent": "BillingSpecialist",
      "handoff_type": "condition",
      "condition_type": "string_llm",
      "condition_scope": "post",
      "condition": "User is asking about an invoice, payment, or subscription.",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "RouterAgent",
      "target_agent": "TechSupportSpecialist",
      "handoff_type": "condition",
      "condition_type": "string_llm",
      "condition_scope": "post",
      "condition": "User is reporting a bug, error, or technical issue.",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "BillingSpecialist",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    },
    {
      "source_agent": "TechSupportSpecialist",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured routing rules based on content analysis."
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "handoff_rules": [
    {
      "source_agent": "TriageAgent",
      "target_agent": "FulfillmentAgent",
      "handoff_type": "condition",
      "condition_type": "string_llm",
      "condition_scope": "post",
      "condition": "Ticket draft is complete and ready for submission.",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "FulfillmentAgent",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Set up handoff from triage to fulfillment upon draft completion."
}""",
            2: """{
  "handoff_rules": [
    {
      "source_agent": "TriageAgent",
      "target_agent": "EscalationCoordinator",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${incident_severity} == 'high' or ${resolution_confidence} < 0.7",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "TriageAgent",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    },
    {
      "source_agent": "EscalationCoordinator",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured escalation triggers based on severity and confidence."
}""",
            3: """{
  "handoff_rules": [
    {
      "source_agent": "AuthoringAgent",
      "target_agent": "ReviewAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "ReviewAgent",
      "target_agent": "AuthoringAgent",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${review_status} == 'needs_revision'",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "ReviewAgent",
      "target_agent": "user",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${review_status} == 'approved'",
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured feedback loop with revision cycles."
}""",
            4: """{
  "handoff_rules": [
    {
      "source_agent": "StrategyLead",
      "target_agent": "DemandManager",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "DemandManager",
      "target_agent": "StrategyLead",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    }
  ],
  "agent_message": "Configured hierarchical delegation and reporting flow."
}""",
            5: """{
  "handoff_rules": [
    {
      "source_agent": "IdeationAgent",
      "target_agent": "CopyAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "CopyAgent",
      "target_agent": "IdeationAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    }
  ],
  "agent_message": "Configured organic flow for open collaboration."
}""",
            6: """{
  "handoff_rules": [
    {
      "source_agent": "IntakeAgent",
      "target_agent": "UnderwritingAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "UnderwritingAgent",
      "target_agent": "FulfillmentAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "FulfillmentAgent",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured sequential pipeline handoffs."
}""",
            7: """{
  "handoff_rules": [
    {
      "source_agent": "StatisticalAgent",
      "target_agent": "EvaluatorAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "CausalAgent",
      "target_agent": "EvaluatorAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "EvaluatorAgent",
      "target_agent": "user",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured redundant execution converging on evaluator."
}""",
            8: """{
  "handoff_rules": [
    {
      "source_agent": "CoordinatorAgent",
      "target_agent": "FinanceSpoke",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${finance_review_needed} == True",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "FinanceSpoke",
      "target_agent": "CoordinatorAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "CoordinatorAgent",
      "target_agent": "user",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${all_reviews_complete} == True",
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured hub-and-spoke handoffs."
}""",
            9: """{
  "handoff_rules": [
    {
      "source_agent": "DreamTriageAgent",
      "target_agent": "DreamRealizerAgent",
      "handoff_type": "after_work",
      "condition_type": null,
      "condition_scope": null,
      "condition": null,
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "DreamRealizerAgent",
      "target_agent": "DreamTriageAgent",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${tasks_remaining} > 0",
      "transition_target": "AgentTarget"
    },
    {
      "source_agent": "DreamRealizerAgent",
      "target_agent": "user",
      "handoff_type": "condition",
      "condition_type": "expression",
      "condition_scope": "post",
      "condition": "${tasks_remaining} == 0",
      "transition_target": "RevertToUserTarget"
    }
  ],
  "agent_message": "Configured Triage -> Realizer loop for dream processing."
}"""
        }

        example_json = handoff_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No handoff example found for pattern_id {pattern_id}")
            return

        guidance = (
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete HandoffsAgentOutput JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )

        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected handoff guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_handoffs_guidance: {e}", exc_info=True)




def inject_structured_outputs_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for StructuredOutputsAgent.
    Injects pattern-specific structured output schema generation guidance.
    
    StructuredOutputsAgent OUTPUT FORMAT (StructuredOutputsAgentOutput JSON):
    {
      "models": [
        {
          "model_name": "<PascalCase>",
          "fields": [
            {
              "name": "<snake_case>",
              "type": "str|int|bool|List[...]|Dict[...]",
              "description": "<field purpose and constraints>"
            }
          ]
        }
      ],
      "registry": [
        {
          "agent": "<PascalCaseAgentName>",
          "agent_definition": "<ModelName>" | null
        }
      ]
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific structured output examples (complete JSON payloads)
        structured_output_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "models": [
    {
      "model_name": "RoutingDecision",
      "fields": [
        {"name": "domain", "type": "str", "description": "The classified domain (billing, technical, account)"},
        {"name": "confidence", "type": "float", "description": "Confidence score between 0.0 and 1.0"},
        {"name": "reasoning", "type": "str", "description": "Explanation for the routing decision"}
      ]
    },
    {
      "model_name": "ResolutionSummary",
      "fields": [
        {"name": "issue_id", "type": "str", "description": "Unique identifier for the support issue"},
        {"name": "status", "type": "str", "description": "Final status (resolved, escalated, pending)"},
        {"name": "steps_taken", "type": "List[str]", "description": "List of remediation steps performed"}
      ]
    }
  ],
  "registry": [
    {"agent": "RouterAgent", "agent_definition": "RoutingDecision"},
    {"agent": "SpecialistAgent", "agent_definition": "ResolutionSummary"}
  ]
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "models": [
    {
      "model_name": "TicketDraft",
      "fields": [
        {"name": "summary", "type": "str", "description": "Concise summary of the issue"},
        {"name": "category", "type": "str", "description": "Hardware, Software, or Access"},
        {"name": "urgency", "type": "str", "description": "Low, Medium, High"}
      ]
    },
    {
      "model_name": "TicketSubmission",
      "fields": [
        {"name": "ticket_id", "type": "str", "description": "Generated ticket ID from ServiceNow"},
        {"name": "status", "type": "str", "description": "Current status (New, Assigned)"}
      ]
    }
  ],
  "registry": [
    {"agent": "TriageAgent", "agent_definition": "TicketDraft"},
    {"agent": "FulfillmentAgent", "agent_definition": "TicketSubmission"}
  ]
}""",
            2: """{
  "models": [
    {
      "model_name": "TriageReport",
      "fields": [
        {"name": "severity", "type": "str", "description": "Incident severity (P1, P2, P3)"},
        {"name": "affected_services", "type": "List[str]", "description": "List of impacted microservices"},
        {"name": "initial_diagnosis", "type": "str", "description": "Preliminary root cause analysis"}
      ]
    },
    {
      "model_name": "EscalationRequest",
      "fields": [
        {"name": "current_tier", "type": "int", "description": "Current support tier (1, 2, 3)"},
        {"name": "reason", "type": "str", "description": "Reason for escalation (complexity, timeout, unknown)"},
        {"name": "context_summary", "type": "str", "description": "Summary of findings so far"}
      ]
    }
  ],
  "registry": [
    {"agent": "TriageAgent", "agent_definition": "TriageReport"},
    {"agent": "EscalationCoordinator", "agent_definition": "EscalationRequest"}
  ]
}""",
            3: """{
  "models": [
    {
      "model_name": "CopyDraft",
      "fields": [
        {"name": "headline", "type": "str", "description": "Main campaign headline"},
        {"name": "body_copy", "type": "str", "description": "Primary body text"},
        {"name": "cta", "type": "str", "description": "Call to action text"},
        {"name": "rationale", "type": "str", "description": "Creative rationale for the draft"}
      ]
    },
    {
      "model_name": "ReviewFeedback",
      "fields": [
        {"name": "approved", "type": "bool", "description": "Whether the draft is approved"},
        {"name": "comments", "type": "List[str]", "description": "Specific feedback points"},
        {"name": "pillar_scores", "type": "Dict[str, int]", "description": "Scores for each messaging pillar (1-5)"}
      ]
    }
  ],
  "registry": [
    {"agent": "AuthoringAgent", "agent_definition": "CopyDraft"},
    {"agent": "ReviewAgent", "agent_definition": "ReviewFeedback"}
  ]
}""",
            4: """{
  "models": [
    {
      "model_name": "WorkstreamPlan",
      "fields": [
        {"name": "objective", "type": "str", "description": "Main strategic objective"},
        {"name": "streams", "type": "List[str]", "description": "List of active workstreams"},
        {"name": "timeline_weeks", "type": "int", "description": "Estimated duration in weeks"}
      ]
    },
    {
      "model_name": "ResearchFindings",
      "fields": [
        {"name": "stream", "type": "str", "description": "Workstream name (demand, competitor, regulatory)"},
        {"name": "key_insights", "type": "List[str]", "description": "Top 3-5 findings"},
        {"name": "risk_level", "type": "str", "description": "Risk assessment (low, medium, high)"}
      ]
    }
  ],
  "registry": [
    {"agent": "StrategyLead", "agent_definition": "WorkstreamPlan"},
    {"agent": "DemandManager", "agent_definition": "ResearchFindings"}
  ]
}""",
            5: """{
  "models": [
    {
      "model_name": "IdeaContribution",
      "fields": [
        {"name": "concept", "type": "str", "description": "The core idea or hook"},
        {"name": "tags", "type": "List[str]", "description": "Thematic tags"},
        {"name": "contributor_role", "type": "str", "description": "Role of the agent (copy, design, growth)"}
      ]
    },
    {
      "model_name": "ConsolidatedConcepts",
      "fields": [
        {"name": "top_themes", "type": "List[str]", "description": "Dominant themes from the session"},
        {"name": "selected_ideas", "type": "List[IdeaContribution]", "description": "Curated list of best ideas"},
        {"name": "next_steps", "type": "str", "description": "Action plan for asset creation"}
      ]
    }
  ],
  "registry": [
    {"agent": "IdeationAgent", "agent_definition": "ConsolidatedConcepts"},
    {"agent": "CopyAgent", "agent_definition": "IdeaContribution"}
  ]
}""",
            6: """{
  "models": [
    {
      "model_name": "ValidationResult",
      "fields": [
        {"name": "is_valid", "type": "bool", "description": "Whether the application is complete"},
        {"name": "missing_fields", "type": "List[str]", "description": "List of missing required fields"},
        {"name": "applicant_id", "type": "str", "description": "Unique applicant identifier"}
      ]
    },
    {
      "model_name": "UnderwritingDecision",
      "fields": [
        {"name": "approved", "type": "bool", "description": "Final approval status"},
        {"name": "amount", "type": "float", "description": "Approved loan amount"},
        {"name": "rate", "type": "float", "description": "Interest rate percentage"},
        {"name": "conditions", "type": "List[str]", "description": "Conditions for funding"}
      ]
    }
  ],
  "registry": [
    {"agent": "IntakeAgent", "agent_definition": "ValidationResult"},
    {"agent": "UnderwritingAgent", "agent_definition": "UnderwritingDecision"}
  ]
}""",
            7: """{
  "models": [
    {
      "model_name": "ForecastSubmission",
      "fields": [
        {"name": "model_type", "type": "str", "description": "Type of model (statistical, causal, heuristic)"},
        {"name": "projection", "type": "List[float]", "description": "Forecasted values"},
        {"name": "assumptions", "type": "List[str]", "description": "Key assumptions made"}
      ]
    },
    {
      "model_name": "EvaluationResult",
      "fields": [
        {"name": "best_model", "type": "str", "description": "Name of the winning model"},
        {"name": "scores", "type": "Dict[str, float]", "description": "Accuracy scores for each model"},
        {"name": "rationale", "type": "str", "description": "Reason for selection"}
      ]
    }
  ],
  "registry": [
    {"agent": "StatisticalAgent", "agent_definition": "ForecastSubmission"},
    {"agent": "EvaluatorAgent", "agent_definition": "EvaluationResult"}
  ]
}""",
            8: """{
  "models": [
    {
      "model_name": "SpokeReview",
      "fields": [
        {"name": "spoke_name", "type": "str", "description": "Name of the spoke (finance, security, legal)"},
        {"name": "status", "type": "str", "description": "Review status (pass, fail, conditional)"},
        {"name": "findings", "type": "List[str]", "description": "Key findings or issues"}
      ]
    },
    {
      "model_name": "HubSummary",
      "fields": [
        {"name": "overall_status", "type": "str", "description": "Consolidated status"},
        {"name": "blockers", "type": "List[str]", "description": "Outstanding blocking issues"},
        {"name": "ready_for_onboarding", "type": "bool", "description": "Whether vendor can be onboarded"}
      ]
    }
  ],
  "registry": [
    {"agent": "FinanceSpoke", "agent_definition": "SpokeReview"},
    {"agent": "CoordinatorAgent", "agent_definition": "HubSummary"}
  ]
}""",
            9: """{
  "models": [
    {
      "model_name": "DreamTaskQueue",
      "fields": [
        {"name": "tasks", "type": "List[Dict[str, Any]]", "description": "List of processing tasks"},
        {"name": "user_tier", "type": "str", "description": "User subscription tier (Standard/Premium)"},
        {"name": "session_id", "type": "str", "description": "Unique session identifier"}
      ]
    },
    {
      "model_name": "DreamBundle",
      "fields": [
        {"name": "video_url", "type": "str", "description": "URL to generated Veo3 video"},
        {"name": "analysis_report", "type": "str", "description": "Psychoanalytic interpretation (Premium only)"},
        {"name": "processing_metadata", "type": "Dict[str, str]", "description": "Generation stats and timestamps"}
      ]
    }
  ],
  "registry": [
    {"agent": "DreamTriageAgent", "agent_definition": "DreamTaskQueue"},
    {"agent": "DreamRealizerAgent", "agent_definition": "DreamBundle"}
  ]
}"""
        }
        
        example_json = structured_output_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No structured output example found for pattern_id {pattern_id}")
            return

        guidance = (
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete StructuredOutputsAgentOutput JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected structured outputs guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_structured_outputs_guidance: {e}", exc_info=True)


def inject_context_variables_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for ContextVariablesAgent.
    Injects pattern-specific context variable planning guidance.
    
    ContextVariablesAgent OUTPUT FORMAT (ContextVariablesAgentOutput JSON):
    {
      "ContextVariablesPlan": {
        "definitions": {
          "<variable_name>": {
            "type": "string|boolean|integer|object",
            "description": "<purpose>",
            "source": {
              "type": "database|environment|static|derived",
              "database_name": "<db_name>",
              "collection": "<collection>",
              "search_by": "<query_field>",
              "field": "<target_field>",
              "env_var": "<ENV_VAR_NAME>",
              "default": "<fallback_value>",
              "value": "<static_value>",
              "triggers": [
                {
                  "type": "agent_text|user_text|ui_response",
                  "agent": "<AgentName>",
                  "match": {"contains": "<substring>"},
                  "tool": "<tool_name>",
                  "response_key": "<json_key>"
                }
              ]
            }
          }
        },
        "agents": {
          "<PascalCaseAgentName>": {
            "variables": ["<variable_name1>", "<variable_name2>"]
          }
        }
      }
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific context variable examples (complete JSON payloads)
        context_variable_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "ContextVariablesPlan": {
    "definitions": {
      "routing_domain": {
        "type": "string",
        "description": "Detected support domain (Billing, Technical, Account)",
        "source": {
          "type": "derived",
          "default": "",
          "triggers": [
            {
              "type": "agent_text",
              "agent": "RouterAgent",
              "match": {
                "contains": "DOMAIN:"
              }
            }
          ]
        }
      },
      "routing_confidence": {
        "type": "float",
        "description": "Confidence score for routing decision (0.0-1.0)",
        "source": {
          "type": "derived",
          "default": 0.0,
          "triggers": [
            {
              "type": "ui_response",
              "tool": "confirm_routing_decision",
              "response_key": "confidence"
            }
          ]
        }
      }
    },
    "agents": {
      "RouterAgent": {
        "variables": ["routing_domain", "routing_confidence"]
      },
      "BillingSpecialist": {
        "variables": ["routing_domain"]
      },
      "TechSupportSpecialist": {
        "variables": ["routing_domain"]
      }
    }
  }
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "ContextVariablesPlan": {
    "definitions": {
      "ticket_draft_id": {
        "type": "string",
        "description": "Draft ticket ID from ServiceNow",
        "source": {
          "type": "derived",
          "default": "",
          "triggers": [
            {
              "type": "agent_text",
              "agent": "TriageAgent",
              "match": {
                "contains": "DRAFT:"
              }
            }
          ]
        }
      },
      "ticket_category": {
        "type": "string",
        "description": "Classified category (Hardware, Software, Access)",
        "source": {
          "type": "derived",
          "default": "",
          "triggers": [
            {
              "type": "ui_response",
              "tool": "review_ticket_draft",
              "response_key": "category"
            }
          ]
        }
      }
    },
    "agents": {
      "TriageAgent": {
        "variables": ["ticket_draft_id", "ticket_category"]
      },
      "FulfillmentAgent": {
        "variables": ["ticket_draft_id", "ticket_category"]
      }
    }
  }
}""",
            2: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            3: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            4: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            5: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            6: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            7: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            8: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}""",
            9: """{
  "ContextVariablesPlan": {
    "definitions": {},
    "agents": {}
  }
}"""
        }
        
        example_json = context_variable_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No context variable example found for pattern_id {pattern_id}")
            return

        guidance = (
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete ContextVariablesPlan JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected context variables guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_context_variables_guidance: {e}", exc_info=True)


def inject_agents_agent_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for AgentsAgent.
    Injects pattern-specific runtime agent configuration guidance.
    
    AgentsAgent OUTPUT FORMAT (RuntimeAgentsCall JSON):
    {
      "agents": [
        {
          "name": "<PascalCaseAgentName>",
          "display_name": "<Display Name>",
          "prompt_sections": [
            {"id": "<section_id>", "heading": "[SECTION HEADING]", "content": "<section content>"}
          ],
          "max_consecutive_auto_reply": <int>,
          "structured_outputs_required": true|false
        }
      ],
      "agent_message": "<Summary>"
    }

    NOTE: auto-tool execution is runtime-derived from `tools.yaml`; AgentsAgent
    MUST NOT emit any agent-level auto-tool field.
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific agent configuration examples (complete JSON payloads)
        agent_config_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "agents": [
    {
      "name": "RouterAgent",
      "display_name": "Router Agent (Intake)",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are the Intake Coordinator responsible for verifying user identity and classifying support intent."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "- Verify account details via UI card\\n- Classify support intent from user message\\n- Route to appropriate specialist queue"},
        {"id": "context", "heading": "[CONTEXT]", "content": "You execute in Phase 0. You have access to: customer_profile (CRM data). You produce: intent_classification."},
        {"id": "instructions", "heading": "[INSTRUCTIONS]", "content": "Step 1 - Read Context: Access customer_profile from context.\\nStep 2 - Verify Account: Call verify_account_details to show inline card.\\nStep 3 - Classify Intent: Call classify_intent with user message.\\nStep 4 - Emit Token: Output 'ROUTING_COMPLETE' to signal handoff."},
        {"id": "examples", "heading": "[EXAMPLES]", "content": "User: 'I have a billing issue' -> Call classify_intent -> Output 'ROUTING_COMPLETE'"},
        {"id": "output_format", "heading": "[OUTPUT FORMAT]", "content": "Output MUST be a valid JSON object with the following structure and NO additional text: {\\\"IntentClassification\\\": {...}}"}
      ],
      "max_consecutive_auto_reply": 1,
      "structured_outputs_required": true
    },
    {
      "name": "RoutingOrchestrator",
      "display_name": "Routing Orchestrator",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are the Internal Orchestrator responsible for assigning the best available specialist."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "- Check specialist queue availability\\n- Assign session to optimal specialist"},
        {"id": "context", "heading": "[CONTEXT]", "content": "You execute in Phase 1. You have access to: intent_classification. You produce: assigned_specialist_queue."},
        {"id": "instructions", "heading": "[INSTRUCTIONS]", "content": "Step 1 - Read Intent: Access intent_classification from context.\\nStep 2 - Check Availability: Call check_queue_availability.\\nStep 3 - Assign: Call assign_specialist.\\nStep 4 - Emit Token: Output 'HANDOFF_COMPLETE'."},
        {"id": "examples", "heading": "[EXAMPLES]", "content": "Intent: Billing -> Check Billing Queue -> Assign BillingSpecialist"},
        {"id": "output_format", "heading": "[OUTPUT FORMAT]", "content": "Output MUST be a valid JSON object: {\\\"SpecialistAssignment\\\": {...}}"}
      ],
      "max_consecutive_auto_reply": 30,
      "structured_outputs_required": false
    }
  ],
  "agent_message": "Configured RouterAgent for intake and RoutingOrchestrator for assignment."
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "agents": [
    {
      "name": "ConciergeAgent",
      "display_name": "Concierge Agent (Intake)",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are the IT Helpdesk Concierge responsible for capturing employee issues."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "- Greet employee and capture issue details\\n- Confirm details via inline summary card\\n- Initialize ticket draft"},
        {"id": "context", "heading": "[CONTEXT]", "content": "You execute in Phase 0. You have access to: employee_context. You produce: ticket_id."},
        {"id": "instructions", "heading": "[INSTRUCTIONS]", "content": "Step 1 - Greet: Welcome the employee using employee_context.name.\\nStep 2 - Capture: Ask for issue details.\\nStep 3 - Confirm: Call confirm_issue_details to show inline card.\\nStep 4 - Draft: Call create_draft_ticket.\\nStep 5 - Emit Token: Output 'INTAKE_COMPLETE'."},
        {"id": "examples", "heading": "[EXAMPLES]", "content": "User: 'Laptop broken' -> Confirm details -> Create ticket -> 'INTAKE_COMPLETE'"},
        {"id": "output_format", "heading": "[OUTPUT FORMAT]", "content": "Output MUST be a valid JSON object: {\\\"TicketDraft\\\": {...}}"}
      ],
      "max_consecutive_auto_reply": 5,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured ConciergeAgent for employee intake."
}""",
            2: """{
  "agents": [
    {
      "name": "TriageAgent",
      "display_name": "Incident Triage",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a triage officer responsible for initial incident assessment."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Assess severity and identify affected services. Request escalation for high-severity issues."}
      ],
      "max_consecutive_auto_reply": 3,
      "structured_outputs_required": true
    },
    {
      "name": "EscalationCoordinator",
      "display_name": "Escalation Manager",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are an escalation coordinator managing high-severity incidents."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Allocate senior resources and manage the incident response lifecycle."}
      ],
      "max_consecutive_auto_reply": 10,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured TriageAgent for assessment and EscalationCoordinator for complex incidents."
}""",
            3: """{
  "agents": [
    {
      "name": "AuthoringAgent",
      "display_name": "Content Creator",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a creative writer responsible for drafting content."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Draft compelling copy based on the campaign brief and feedback."}
      ],
      "max_consecutive_auto_reply": 2,
      "structured_outputs_required": true
    },
    {
      "name": "ReviewAgent",
      "display_name": "Senior Editor",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a senior editor responsible for quality control."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Critique drafts and provide specific feedback for improvement."}
      ],
      "max_consecutive_auto_reply": 2,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured AuthoringAgent for drafting and ReviewAgent for feedback loops."
}""",
            4: """{
  "agents": [
    {
      "name": "StrategyLead",
      "display_name": "Strategy Director",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are the strategy lead defining high-level objectives."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Decompose objectives into workstreams and oversee execution."}
      ],
      "max_consecutive_auto_reply": 5,
      "structured_outputs_required": true
    },
    {
      "name": "DemandManager",
      "display_name": "Research Manager",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a demand manager executing specific research tasks."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Conduct research and report findings back to the StrategyLead."}
      ],
      "max_consecutive_auto_reply": 5,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured StrategyLead for direction and DemandManager for execution."
}""",
            5: """{
  "agents": [
    {
      "name": "IdeationAgent",
      "display_name": "Facilitator",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a brainstorming facilitator."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Encourage idea generation and synthesize contributions into themes."}
      ],
      "max_consecutive_auto_reply": 10,
      "structured_outputs_required": true
    },
    {
      "name": "CopyAgent",
      "display_name": "Creative Copywriter",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a creative copywriter focused on volume and variety."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Generate wild, divergent ideas based on the prompt."}
      ],
      "max_consecutive_auto_reply": 10,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured IdeationAgent to facilitate and CopyAgent to generate ideas."
}""",
            6: """{
  "agents": [
    {
      "name": "IntakeAgent",
      "display_name": "Application Intake",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are an intake specialist validating applications."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Ensure application completeness before passing to underwriting."}
      ],
      "max_consecutive_auto_reply": 2,
      "structured_outputs_required": true
    },
    {
      "name": "UnderwritingAgent",
      "display_name": "Credit Underwriter",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a senior underwriter making credit decisions."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Evaluate risk and approve or deny the application."}
      ],
      "max_consecutive_auto_reply": 1,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured IntakeAgent for validation and UnderwritingAgent for decisioning."
}""",
            7: """{
  "agents": [
    {
      "name": "StatisticalAgent",
      "display_name": "Statistical Modeler",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a statistical modeler generating forecasts."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Generate forecasts using time-series analysis and state assumptions."}
      ],
      "max_consecutive_auto_reply": 1,
      "structured_outputs_required": true
    },
    {
      "name": "EvaluatorAgent",
      "display_name": "Model Evaluator",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a model evaluator comparing forecasts."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Select the most accurate model based on validation metrics."}
      ],
      "max_consecutive_auto_reply": 1,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured StatisticalAgent for forecasting and EvaluatorAgent for selection."
}""",
            8: """{
  "agents": [
    {
      "name": "CoordinatorAgent",
      "display_name": "Review Coordinator",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are the central coordinator for vendor reviews."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Distribute profiles to spokes and aggregate their findings."}
      ],
      "max_consecutive_auto_reply": 10,
      "structured_outputs_required": true
    },
    {
      "name": "FinanceSpoke",
      "display_name": "Finance Analyst",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a financial analyst reviewing vendor stability."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Analyze financial statements and flag solvency risks."}
      ],
      "max_consecutive_auto_reply": 1,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured CoordinatorAgent as the hub and FinanceSpoke as a reviewer."
}""",
            9: """{
  "agents": [
    {
      "name": "DreamTriageAgent",
      "display_name": "Dream Intake Specialist",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a dream intake specialist responsible for initial assessment."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Analyze the dream description, check subscription tier, and route to DreamRealizerAgent with appropriate instructions (Standard vs Premium)."}
      ],
      "max_consecutive_auto_reply": 3,
      "structured_outputs_required": true
    },
    {
      "name": "DreamRealizerAgent",
      "display_name": "Dream Visualizer",
      "prompt_sections": [
        {"id": "role", "heading": "[ROLE]", "content": "You are a creative visualizer turning dreams into video."},
        {"id": "objective", "heading": "[OBJECTIVE]", "content": "Generate video content from dream descriptions. For Premium users, also perform deep psychological analysis."}
      ],
      "max_consecutive_auto_reply": 10,
      "structured_outputs_required": true
    }
  ],
  "agent_message": "Configured DreamTriageAgent for intake and DreamRealizerAgent for visualization."
}"""
        }
        
        example_json = agent_config_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No agent config example found for pattern_id {pattern_id}")
            return

        contract_note = (
            "[RUNTIME AGENT CONTRACT]\n"
          "- Do NOT emit any agent-level auto-tool field in RuntimeAgentsCall.\n"
          "- The runtime derives auto-tool execution from `tools.yaml` after tool generation.\n"
            "- Emit only fields that belong in the final `agents.yaml` contract.\n\n"
        )

        guidance = (
            f"{contract_note}"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete RuntimeAgentsCall JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected agents configuration guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_agents_agent_guidance: {e}", exc_info=True)


def inject_hook_agent_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for HookAgent.
    Injects pattern-specific system hook generation guidance.
    
    HookAgent OUTPUT FORMAT (HookImplementationCall JSON):
    {
      "hook_files": [
        {
          "filename": "<hook_name>.py",
          "hook_type": "before_chat|after_chat|update_agent_state",
          "py_content": "<Python hook function code>"
        }
      ],
      "agent_message": "<Summary of hook generation>"
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific hook implementation examples (complete JSON payloads)
        hook_examples = {
            1: """{
  "hook_files": [
    {
      "filename": "route_request.py",
      "hook_type": "before_chat",
      "py_content": "def route_request(agent, messages):\\n    # Analyze message content\\n    last_msg = messages[-1].get('content', '')\\n    domain = classify_domain(last_msg)\\n    if domain == 'billing':\\n        return 'BillingAgent'\\n    elif domain == 'technical':\\n        return 'TechSupportAgent'\\n    return 'GeneralAgent'"
    }
  ],
  "agent_message": "Generated route_request hook for dynamic routing."
}""",
            2: """{
  "hook_files": [
    {
      "filename": "check_escalation.py",
      "hook_type": "update_agent_state",
      "py_content": "def check_escalation(agent, messages):\\n    # Check for keywords or sentiment\\n    last_msg = messages[-1].get('content', '').lower()\\n    if 'urgent' in last_msg or 'outage' in last_msg:\\n        agent.register_escalation_request()\\n    return None"
    }
  ],
  "agent_message": "Generated check_escalation hook for severity monitoring."
}""",
            3: """{
  "hook_files": [
    {
      "filename": "validate_feedback.py",
      "hook_type": "after_chat",
      "py_content": "def validate_feedback(agent, messages):\\n    # Ensure feedback is constructive\\n    last_msg = messages[-1].get('content', '')\\n    if len(last_msg.split()) < 5:\\n        return 'Please provide more detailed feedback.'\\n    return None"
    }
  ],
  "agent_message": "Generated validate_feedback hook for quality control."
}""",
            4: """{
  "hook_files": [
    {
      "filename": "aggregate_reports.py",
      "hook_type": "update_agent_state",
      "py_content": "def aggregate_reports(agent, messages):\\n    # Combine multiple reports into one summary\\n    reports = agent.get_reports()\\n    summary = ''\\n    for report in reports:\\n        summary += f'Stream: {report.stream}\\\\nFindings: {report.findings}\\\\n\\\\n'\\n    agent.set_summary(summary)"
    }
  ],
  "agent_message": "Generated aggregate_reports hook for consolidating workstream outputs."
}""",
            5: """{
  "hook_files": [
    {
      "filename": "filter_ideas.py",
      "hook_type": "after_chat",
      "py_content": "def filter_ideas(agent, messages):\\n    # Remove duplicates and low-quality ideas\\n    ideas = agent.get_ideas()\\n    unique_ideas = set(ideas)\\n    agent.set_ideas(list(unique_ideas))"
    }
  ],
  "agent_message": "Generated filter_ideas hook for deduplication."
}""",
            6: """{
  "hook_files": [
    {
      "filename": "validate_application.py",
      "hook_type": "before_chat",
      "py_content": "def validate_application(agent, messages):\\n    # Check for required fields\\n    app_data = agent.get_application_data()\\n    required = ['name', 'income', 'ssn']\\n    missing = [f for f in required if f not in app_data]\\n    if missing:\\n        return f'Missing fields: {missing}'\\n    return None"
    }
  ],
  "agent_message": "Generated validate_application hook for pre-processing checks."
}""",
            7: """{
  "hook_files": [
    {
      "filename": "compare_models.py",
      "hook_type": "update_agent_state",
      "py_content": "def compare_models(agent, messages):\\n    # Select model with lowest error\\n    results = agent.get_model_results()\\n    best_model = min(results, key=lambda x: x['error'])\\n    agent.set_best_model(best_model['name'])"
    }
  ],
  "agent_message": "Generated compare_models hook for model selection."
}""",
            8: """{
  "hook_files": [
    {
      "filename": "consolidate_reviews.py",
      "hook_type": "after_chat",
      "py_content": "def consolidate_reviews(agent, messages):\\n    # Check if any spoke rejected the vendor\\n    reviews = agent.get_spoke_reviews()\\n    for review in reviews:\\n        if review['status'] == 'reject':\\n            return 'Rejected'\\n    return 'Approved'"
    }
  ],
  "agent_message": "Generated consolidate_reviews hook for final decision logic."
}""",
            9: """{
  "hook_files": [
    {
      "filename": "enrich_dream_metadata.py",
      "hook_type": "before_chat",
      "py_content": "def enrich_dream_metadata(agent, messages):\\n    # Check context for subscription info\\n    user_tier = agent.context_variables.get('subscription_tier', 'Standard')\\n    \\n    # Add processing flags\\n    agent.context_variables['enable_deep_analysis'] = (user_tier == 'Premium')\\n    agent.context_variables['video_resolution'] = '4k' if user_tier == 'Premium' else '1080p'\\n    \\n    return None"
    }
  ],
  "agent_message": "Generated enrich_dream_metadata hook for tier-based configuration."
}"""
        }
        
        example_json = hook_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No hook example found for pattern_id {pattern_id}")
            return

        # Semantic Context Injection
        blueprint = _get_upstream_context(agent, 'TechnicalBlueprint')
        tool_planning = _get_upstream_context(agent, 'ToolPlanning')
        
        semantic_context = ""
        if blueprint or tool_planning:
            semantic_context = "\n[UPSTREAM CONTEXT: HOOK DEFINITIONS]\n"
            
            if blueprint:
                before = blueprint.get('before_chat_lifecycle')
                after = blueprint.get('after_chat_lifecycle')
                if before:
                    semantic_context += f"- Lifecycle Hook (before_chat): {before.get('name')} - {before.get('purpose')}\n"
                if after:
                    semantic_context += f"- Lifecycle Hook (after_chat): {after.get('name')} - {after.get('purpose')}\n"
            
            if tool_planning:
                for hook in tool_planning.get('system_hooks', []) or []:
                    semantic_context += (
                        f"- System Hook ({hook.get('trigger')}): {hook.get('name')} - {hook.get('purpose')} "
                        f"(Agent: {hook.get('agent')})\n"
                    )
            
            semantic_context += "\nYou MUST generate the Python code for these EXACT hooks.\n\n"

        guidance = (
            f"{semantic_context}"
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete HookImplementationCall JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected hook generation guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_hook_agent_guidance: {e}", exc_info=True)


def inject_orchestrator_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for OrchestratorAgent.
    Injects pattern-specific orchestration configuration guidance.
    
    OrchestratorAgent OUTPUT FORMAT (OrchestratorAgentOutput JSON):
    {
      "workflow_name": "<WorkflowName>",
      "max_turns": <int>,
      "human_in_the_loop": true,
      "startup_mode": "AgentDriven|UserDriven|BackendOnly",
      "orchestration_pattern": "<PatternName>",
      "initial_message_to_user": null,
      "initial_message": "<greeting string>|null",
      "recipient": "<FirstAgentName>",
      "visual_agents": ["<AgentName1>", "<AgentName2>"] | null,
      "agent_message": "<Summary of orchestration config>"
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific orchestration configuration examples (complete JSON payloads)
        orchestrator_examples = {
            1: """{
  "workflow_name": "ContextAwareRoutingWorkflow",
  "max_turns": 10,
  "human_in_the_loop": true,
  "startup_mode": "UserDriven",
  "orchestration_pattern": "Context-Aware Routing",
  "initial_message_to_user": null,
  "initial_message": "How can I help you today?",
  "recipient": "RouterAgent",
  "visual_agents": ["RouterAgent", "SpecialistAgent"],
  "agent_message": "Configured routing workflow starting with RouterAgent."
}""",
            2: """{
  "workflow_name": "EscalationWorkflow",
  "max_turns": 20,
  "human_in_the_loop": true,
  "startup_mode": "UserDriven",
  "orchestration_pattern": "Escalation",
  "initial_message_to_user": null,
  "initial_message": "Please describe the incident.",
  "recipient": "TriageAgent",
  "visual_agents": ["TriageAgent", "EscalationCoordinator"],
  "agent_message": "Configured escalation workflow starting with TriageAgent."
}""",
            3: """{
  "workflow_name": "FeedbackLoopWorkflow",
  "max_turns": 15,
  "human_in_the_loop": true,
  "startup_mode": "AgentDriven",
  "orchestration_pattern": "Feedback Loop",
  "initial_message_to_user": null,
  "initial_message": "Starting content creation cycle.",
  "recipient": "AuthoringAgent",
  "visual_agents": ["AuthoringAgent", "ReviewAgent"],
  "agent_message": "Configured feedback loop starting with AuthoringAgent."
}""",
            4: """{
  "workflow_name": "HierarchicalWorkflow",
  "max_turns": 20,
  "human_in_the_loop": true,
  "startup_mode": "AgentDriven",
  "orchestration_pattern": "Hierarchical",
  "initial_message_to_user": null,
  "initial_message": "Initiating strategic planning.",
  "recipient": "StrategyLead",
  "visual_agents": ["StrategyLead", "DemandManager"],
  "agent_message": "Configured hierarchical workflow starting with StrategyLead."
}""",
            5: """{
  "workflow_name": "OrganicBrainstormingWorkflow",
  "max_turns": 30,
  "human_in_the_loop": true,
  "startup_mode": "UserDriven",
  "orchestration_pattern": "Organic",
  "initial_message_to_user": null,
  "initial_message": "Let's brainstorm! What's the topic?",
  "recipient": "IdeationAgent",
  "visual_agents": ["IdeationAgent", "CopyAgent"],
  "agent_message": "Configured organic workflow starting with IdeationAgent."
}""",
            6: """{
  "workflow_name": "PipelineWorkflow",
  "max_turns": 10,
  "human_in_the_loop": true,
  "startup_mode": "UserDriven",
  "orchestration_pattern": "Pipeline",
  "initial_message_to_user": null,
  "initial_message": "Please submit your application.",
  "recipient": "IntakeAgent",
  "visual_agents": ["IntakeAgent", "UnderwritingAgent"],
  "agent_message": "Configured pipeline workflow starting with IntakeAgent."
}""",
            7: """{
  "workflow_name": "RedundantWorkflow",
  "max_turns": 15,
  "human_in_the_loop": true,
  "startup_mode": "AgentDriven",
  "orchestration_pattern": "Redundant",
  "initial_message_to_user": null,
  "initial_message": "Starting forecast generation.",
  "recipient": "StatisticalAgent",
  "visual_agents": ["StatisticalAgent", "EvaluatorAgent"],
  "agent_message": "Configured redundant workflow starting with StatisticalAgent."
}""",
            8: """{
  "workflow_name": "StarWorkflow",
  "max_turns": 25,
  "human_in_the_loop": true,
  "startup_mode": "AgentDriven",
  "orchestration_pattern": "Star",
  "initial_message_to_user": null,
  "initial_message": "Initiating vendor review process.",
  "recipient": "CoordinatorAgent",
  "visual_agents": ["CoordinatorAgent", "FinanceSpoke"],
  "agent_message": "Configured star workflow starting with CoordinatorAgent."
}""",
            9: """{
  "workflow_name": "DreamWeaverWorkflow",
  "max_turns": 50,
  "human_in_the_loop": true,
  "startup_mode": "UserDriven",
  "orchestration_pattern": "DreamWeaver Triage",
  "initial_message_to_user": null,
  "initial_message": "Tell me about your dream...",
  "recipient": "DreamTriageAgent",
  "visual_agents": ["DreamTriageAgent", "DreamRealizerAgent"],
  "agent_message": "Configured DreamWeaver workflow starting with DreamTriageAgent."
}"""
        }
        
        example_json = orchestrator_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No orchestrator example found for pattern_id {pattern_id}")
            return

        guidance = (
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete OrchestratorAgentOutput JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
            "Rules:\n"
            "- Use JSON null (not empty strings) for optional absent fields.\n"
            "- If startup_mode is BackendOnly and the workflow is headless, set visual_agents to null.\n"
        )
        
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected orchestrator guidance for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_orchestrator_guidance: {e}", exc_info=True)


def inject_project_overview_guidance(agent, messages: List[Dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for ProjectOverviewAgent.
    Injects pattern-specific Mermaid sequence diagram examples.
    
    ProjectOverviewAgent OUTPUT FORMAT (MermaidSequenceDiagram JSON):
    {
      "MermaidSequenceDiagram": {
        "workflow_name": "<string>",
        "mermaid_diagram": "<Mermaid sequence diagram string>",
        "legend": ["<string>"]
      },
      "agent_message": "<Approval-focused message requesting user confirmation>"
    }
    """
    try:
        pattern = _get_pattern_from_context(agent)
        if not pattern:
            logger.debug(f"No pattern available for {agent.name}, skipping guidance injection")
            return

        pattern_id = pattern.get('id')
        pattern_name = pattern.get('name')
        pattern_display_name = pattern.get('display_name', pattern_name)
        
        # Pattern-specific Mermaid diagram examples (complete JSON payloads)
        mermaid_examples = {
            1: """// EXAMPLE 1: SaaS Support Router
{
  "MermaidSequenceDiagram": {
    "workflow_name": "SaaS Support Domain Router",
    "mermaid_diagram": "sequenceDiagram\\n    participant User\\n    participant RouterAgent as Router Agent (Intake)\\n    participant RoutingOrchestrator as Routing Orchestrator\\n    participant SupportSpecialist as Support Specialist\\n\\n    Note over Agents: Phase 0: Automated Intake & Signal Capture\\n    User->>RouterAgent: I have a billing issue\\n    RouterAgent->>RouterAgent: verify_account_details, classify_intent\\n    Note over RouterAgent: Verify Account (inline)\\n    RouterAgent->>User: Verify Account\\n    alt Approved\\n        RouterAgent->>RoutingOrchestrator: Proceed\\n    else Rejected\\n        RouterAgent->>RouterAgent: Revise\\n    end\\n\\n    Note over Agents: Phase 1: Specialist Routing & Engagement\\n    RoutingOrchestrator->>RoutingOrchestrator: check_queue_availability, assign_specialist\\n    RoutingOrchestrator->>SupportSpecialist: Handoff session\\n    SupportSpecialist->>User: Hi, I can help with billing.\\n    SupportSpecialist->>SupportSpecialist: search_knowledge_base, run_diagnostic\\n\\n    Note over Agents: Phase 2: Resolution & Post-Chat Summary\\n    SupportSpecialist->>RouterAgent: Handoff for closure\\n    Note over RouterAgent: Rate Support Experience (artifact)\\n    RouterAgent->>User: Rate Support Experience\\n    RouterAgent->>RouterAgent: close_ticket",
    "legend": []
  },
  "agent_message": "Ready to build this routing workflow? Review the sequence diagram, then reply in chat with approval or requested changes."
}

// EXAMPLE 2: Internal IT Helpdesk Concierge
{
  "MermaidSequenceDiagram": {
    "workflow_name": "Internal IT Helpdesk Concierge",
    "mermaid_diagram": "sequenceDiagram\\n    participant User\\n    participant ConciergeAgent as Concierge Agent (Intake)\\n    participant IssueClassifier as Issue Classifier\\n    participant HardwareSpecialist as Hardware Specialist\\n\\n    Note over Agents: Phase 0: Employee Request Intake\\n    User->>ConciergeAgent: My laptop screen is flickering\\n    ConciergeAgent->>ConciergeAgent: create_draft_ticket\\n    Note over ConciergeAgent: Confirm Issue Details (inline)\\n    ConciergeAgent->>User: Confirm Issue Details\\n    alt Approved\\n        ConciergeAgent->>IssueClassifier: Proceed\\n    else Rejected\\n        ConciergeAgent->>ConciergeAgent: Revise\\n    end\\n\\n    Note over Agents: Phase 1: Issue Classification\\n    IssueClassifier->>IssueClassifier: classify_ticket_category, route_to_tier\\n    IssueClassifier->>HardwareSpecialist: Handoff to tier\\n\\n    Note over Agents: Phase 2: Support Execution\\n    Note over HardwareSpecialist: Grant Remote Access (inline)\\n    HardwareSpecialist->>User: Grant Remote Access\\n    alt Approved\\n        HardwareSpecialist->>HardwareSpecialist: diagnose_hardware\\n    else Rejected\\n        HardwareSpecialist->>HardwareSpecialist: Request alternative method\\n    end",
    "legend": []
  },
  "agent_message": "The IT Helpdesk workflow is mapped out with 3 phases coordinating 3 agents. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            2: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Cloud Incident Escalation Ladder",
    "mermaid_diagram": "sequenceDiagram\\n    participant System as Monitoring System\\n    participant TriageAgent as Triage Agent (Automated Diagnostics)\\n    participant EscalationCoordinator as Escalation Coordinator\\n    participant SRELead as SRE Lead (Expert Mitigation)\\n    participant User as Incident Commander\\n\\n    Note over System,TriageAgent: Phase 0: Alert Intake & Baseline Diagnostics\\n    System->>TriageAgent: P1 outage alert via webhook\\n    TriageAgent->>TriageAgent: Correlate recent deployments\\n    TriageAgent->>TriageAgent: Execute scripted remediation steps\\n    Note over TriageAgent: Incident intake confirmation (inline interaction)\\n    TriageAgent->>User: Present incident brief for confirmation\\n    User->>TriageAgent: Acknowledge incident details\\n\\n    Note over TriageAgent,EscalationCoordinator: Phase 1: Tier Promotion & Context Packaging\\n    TriageAgent->>EscalationCoordinator: Assess recovery confidence\\n    alt Confidence < 0.85\\n        EscalationCoordinator->>SRELead: Escalate with bundled findings\\n    else Confidence >= 0.85\\n        EscalationCoordinator->>TriageAgent: Continue automated recovery\\n    end\\n\\n    Note over SRELead,User: Phase 2: Expert Mitigation & Stakeholder Updates\\n    SRELead->>SRELead: Execute advanced playbooks\\n    SRELead->>User: Request approval for rollback decision\\n    alt User Approves Rollback\\n        User->>SRELead: Approve rollback\\n        SRELead->>SRELead: Execute rollback\\n    else User Rejects\\n        User->>SRELead: Continue mitigation\\n    end\\n    SRELead->>User: Share postmortem outline (artifact - delivered to tray)\\n    User->>SRELead: Acknowledge wrap-up",
    "legend": []
  },
  "agent_message": "The escalation workflow is mapped with 3 phases coordinating automated triage through expert mitigation. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            3: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Product Launch Copy Refinement",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Marketing Stakeholder\\n    participant FacilitatorAgent as Facilitator Agent\\n    participant AuthoringAgent as Authoring Agent\\n    participant ReviewAgent as Review Agent (PMM)\\n\\n    Note over User,FacilitatorAgent: Phase 0: Brief Capture & Acceptance Criteria\\n    User->>FacilitatorAgent: Request launch copy for campaign\\n    FacilitatorAgent->>User: Collect campaign goals, tone, audience data\\n    User->>FacilitatorAgent: Provide brief details\\n    FacilitatorAgent->>FacilitatorAgent: Define done criteria with stakeholders\\n\\n    Note over AuthoringAgent: Phase 1: Draft Creation\\n    FacilitatorAgent->>AuthoringAgent: Hand off campaign brief\\n    AuthoringAgent->>AuthoringAgent: Generate initial announcement copy\\n    AuthoringAgent->>User: Present draft with rationale\\n\\n    Note over ReviewAgent,User: Phase 2: Structured Review\\n    User->>ReviewAgent: Submit for stakeholder review\\n    Note over ReviewAgent: Feedback collection (artifact - multi-step interaction)\\n    ReviewAgent->>User: Open feedback form in tray (Step 1: Score pillars, Step 2: Add comments, Step 3: Submit)\\n    User->>ReviewAgent: Submit structured feedback\\n\\n    Note over AuthoringAgent,User: Phase 3: Revision & Approval\\n    loop Until Approved\\n        ReviewAgent->>AuthoringAgent: Forward feedback for revision\\n        AuthoringAgent->>AuthoringAgent: Apply accepted feedback\\n        AuthoringAgent->>User: Present revised copy\\n        Note over User: Approval gate (inline interaction)\\n        alt User Approves\\n            User->>AuthoringAgent: Approve for launch\\n        else User Requests Revisions\\n            User->>AuthoringAgent: Request final revisions\\n            AuthoringAgent->>AuthoringAgent: Apply additional feedback\\n        end\\n    end\\n    AuthoringAgent->>User: Deliver final approved copy",
    "legend": []
  },
  "agent_message": "The workflow is mapped as a 4-phase feedback loop with structured review and approval gates. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            4: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Market Entry Intelligence Stack",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Executive Team\\n    participant StrategyLead as Strategy Lead (Executive)\\n    participant DemandManager as Demand Analysis Manager\\n    participant CompetitorManager as Competitor Analysis Manager\\n    participant RegulatoryManager as Regulatory Analysis Manager\\n    participant DemandSpecialist as Demand Specialist\\n    participant CompetitorSpecialist as Competitor Specialist\\n    participant RegulatorySpecialist as Regulatory Specialist\\n\\n    Note over User,StrategyLead: Phase 0: Executive Briefing & Workstream Plan\\n    User->>StrategyLead: Explore new market entry\\n    StrategyLead->>StrategyLead: Clarify objectives, split into workstreams\\n    StrategyLead->>User: Share strategy overview (artifact - delivered to tray)\\n    StrategyLead->>DemandManager: Assign demand workstream\\n    StrategyLead->>CompetitorManager: Assign competitor workstream\\n    StrategyLead->>RegulatoryManager: Assign regulatory workstream\\n\\n    Note over DemandManager,RegulatorySpecialist: Phase 1: Manager Task Framing\\n    par Parallel Manager Planning\\n        DemandManager->>DemandManager: Design research backlog, define metrics\\n        CompetitorManager->>CompetitorManager: Design research backlog, define metrics\\n        RegulatoryManager->>RegulatoryManager: Design research backlog, define metrics\\n    end\\n\\n    Note over DemandSpecialist,RegulatorySpecialist: Phase 2: Specialist Deep Dives\\n    par Parallel Specialist Execution\\n        DemandManager->>DemandSpecialist: Assign analysis tasks\\n        DemandSpecialist->>DemandSpecialist: Execute demand analysis\\n        DemandSpecialist->>DemandManager: Share interim findings\\n        CompetitorManager->>CompetitorSpecialist: Assign analysis tasks\\n        CompetitorSpecialist->>CompetitorSpecialist: Execute competitor analysis\\n        CompetitorSpecialist->>CompetitorManager: Share interim findings\\n        RegulatoryManager->>RegulatorySpecialist: Assign analysis tasks\\n        RegulatorySpecialist->>RegulatorySpecialist: Execute regulatory analysis\\n        RegulatorySpecialist->>RegulatoryManager: Share interim findings\\n    end\\n\\n    Note over StrategyLead,User: Phase 3: Executive Synthesis & Go/No-Go\\n    DemandManager->>StrategyLead: Submit demand insights\\n    CompetitorManager->>StrategyLead: Submit competitor insights\\n    RegulatoryManager->>StrategyLead: Submit regulatory insights\\n    StrategyLead->>StrategyLead: Aggregate insights, prepare narrative deck\\n    StrategyLead->>User: Present market decision recommendation\\n    alt User Approves Go\\n        User->>StrategyLead: Approve market entry\\n    else User Rejects\\n        User->>StrategyLead: Decline market entry\\n    end",
    "legend": []
  },
  "agent_message": "The hierarchical workflow cascades research through 3 manager layers and specialist pods. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            5: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Omnichannel Campaign Content Studio",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Marketing Team\\n    participant FacilitatorAgent as Facilitator Agent\\n    participant IdeationAgent as Ideation Agent\\n    participant CopyAgent as Copy Contributor\\n    participant DesignAgent as Design Contributor\\n    participant GrowthAgent as Growth Contributor\\n    participant ContentAssembler as Content Assembler\\n    participant ReviewerAgent as Reviewer Agent\\n\\n    Note over User,FacilitatorAgent: Phase 0: Brief Alignment & Inspiration\\n    User->>FacilitatorAgent: Launch campaign sprint\\n    FacilitatorAgent->>User: Gather campaign goals, personas, messaging\\n    User->>FacilitatorAgent: Provide brief details\\n    FacilitatorAgent->>FacilitatorAgent: Seed room with high-performing assets\\n\\n    Note over IdeationAgent,GrowthAgent: Phase 1: Collaborative Concept Jam\\n    FacilitatorAgent->>IdeationAgent: Initiate brainstorm session\\n    par Open Brainstorming\\n        User->>IdeationAgent: Submit campaign idea (inline interaction)\\n        CopyAgent->>IdeationAgent: Share copy hook concepts\\n        DesignAgent->>IdeationAgent: Share visual themes\\n        GrowthAgent->>IdeationAgent: Share growth tactics\\n    end\\n    IdeationAgent->>IdeationAgent: Capture hooks, tag themes, surface gaps\\n    IdeationAgent->>User: Present consolidated idea pool\\n\\n    Note over ContentAssembler,User: Phase 2: Asset Assembly & Channel Packaging\\n    IdeationAgent->>ContentAssembler: Hand off strongest concepts\\n    ContentAssembler->>ContentAssembler: Compile email, social, landing page drafts\\n    ContentAssembler->>ReviewerAgent: Route for stakeholder preview\\n    Note over ReviewerAgent: Creative review (artifact - delivered to tray)\\n    ReviewerAgent->>User: Share creative board with variants for review\\n    User->>ReviewerAgent: Select variants to advance\\n    ReviewerAgent->>User: Deliver approved campaign package",
    "legend": []
  },
  "agent_message": "Ready to build this collaborative content workflow? Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            6: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Digital Loan Application Pipeline",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Borrower\\n    participant IntakeAgent as Intake Agent (Validation)\\n    participant RiskAgent as Risk Screening Agent\\n    participant ComplianceAgent as Compliance Agent\\n    participant UnderwritingAgent as Underwriting Agent\\n    participant FulfillmentAgent as Fulfillment Agent\\n\\n    Note over User,IntakeAgent: Phase 0: Intake Validation\\n    User->>IntakeAgent: Submit loan application form\\n    Note over IntakeAgent: Document checklist (inline multi-step interaction)\\n    IntakeAgent->>User: Request supporting documents (Step 1: ID, Step 2: Income, Step 3: Banking)\\n    User->>IntakeAgent: Upload financial documents\\n    IntakeAgent->>IntakeAgent: Verify required fields, normalize data\\n    alt Missing Documents\\n        IntakeAgent->>User: Halt - request missing documents\\n    else All Complete\\n        IntakeAgent->>RiskAgent: Proceed to risk screening\\n    end\\n\\n    Note over RiskAgent,ComplianceAgent: Phase 1: Risk & Compliance Screening\\n    RiskAgent->>RiskAgent: Run credit check\\n    RiskAgent->>ComplianceAgent: Hand off for KYC\\n    ComplianceAgent->>ComplianceAgent: Execute fraud and KYC checks\\n    ComplianceAgent->>UnderwritingAgent: Annotate application with risk scores\\n\\n    Note over UnderwritingAgent: Phase 2: Underwriting Decision\\n    UnderwritingAgent->>UnderwritingAgent: Evaluate policy rules, calculate terms\\n    alt Edge Case Detected\\n        UnderwritingAgent->>UnderwritingAgent: Flag for manual review\\n    else Standard Case\\n        UnderwritingAgent->>FulfillmentAgent: Approve with calculated terms\\n    end\\n\\n    Note over FulfillmentAgent,User: Phase 3: Offer & Fulfillment\\n    FulfillmentAgent->>FulfillmentAgent: Generate offer packet\\n    Note over FulfillmentAgent: Decision summary (artifact - delivered to tray)\\n    FulfillmentAgent->>User: Share underwriting decision package for review\\n    User->>FulfillmentAgent: Acknowledge decision\\n    FulfillmentAgent->>FulfillmentAgent: Sync status to servicing systems",
    "legend": []
  },
  "agent_message": "The pipeline workflow sequences 4 phases from intake validation through offer fulfillment. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            7: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Demand Forecast Comparison",
    "mermaid_diagram": "sequenceDiagram\\n    participant System as Planning Scheduler\\n    participant CoordinatorAgent as Coordinator Agent\\n    participant StatisticalAgent as Statistical Forecast Agent\\n    participant CausalAgent as Causal Forecast Agent\\n    participant HeuristicAgent as Heuristic Forecast Agent\\n    participant EvaluatorAgent as Evaluator Agent\\n    participant User as Planning Stakeholder\\n\\n    Note over System,CoordinatorAgent: Phase 0: Scenario Brief\\n    System->>CoordinatorAgent: Trigger weekly planning cycle\\n    CoordinatorAgent->>CoordinatorAgent: Summarize sales window, constraints, metrics\\n    CoordinatorAgent->>StatisticalAgent: Distribute scenario brief\\n    CoordinatorAgent->>CausalAgent: Distribute scenario brief\\n    CoordinatorAgent->>HeuristicAgent: Distribute scenario brief\\n\\n    Note over StatisticalAgent,HeuristicAgent: Phase 1: Parallel Forecast Generation\\n    par Independent Forecasting\\n        StatisticalAgent->>StatisticalAgent: Build statistical model with assumptions\\n        Note over StatisticalAgent: Forecast upload (inline multi-step interaction)\\n        StatisticalAgent->>CoordinatorAgent: Submit forecast bundle\\n        CausalAgent->>CausalAgent: Build causal model with assumptions\\n        CausalAgent->>CoordinatorAgent: Submit forecast bundle\\n        HeuristicAgent->>HeuristicAgent: Build heuristic model with assumptions\\n        HeuristicAgent->>CoordinatorAgent: Submit forecast bundle\\n    end\\n\\n    Note over EvaluatorAgent,User: Phase 2: Comparative Evaluation\\n    CoordinatorAgent->>EvaluatorAgent: Hand off all forecast submissions\\n    EvaluatorAgent->>EvaluatorAgent: Score accuracy, volatility, narrative fit\\n    Note over EvaluatorAgent: Forecast comparison (artifact - delivered to tray)\\n    EvaluatorAgent->>User: Share forecast comparison artifact\\n    alt Forecasts Diverge\\n        User->>EvaluatorAgent: Review divergence and select\\n    else Forecasts Align\\n        EvaluatorAgent->>CoordinatorAgent: Auto-select highest scoring\\n    end\\n\\n    Note over CoordinatorAgent,User: Phase 3: Recommendation Delivery\\n    EvaluatorAgent->>CoordinatorAgent: Report selected forecast\\n    CoordinatorAgent->>CoordinatorAgent: Document rationale\\n    CoordinatorAgent->>User: Distribute planning brief to stakeholders",
    "legend": []
  },
  "agent_message": "The workflow uses redundant forecasting with 3 parallel models and comparative evaluation. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            8: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "Vendor Onboarding Hub",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Vendor Requester\\n    participant CoordinatorAgent as Coordinator Agent (Hub)\\n    participant FinanceSpoke as Finance Spoke\\n    participant SecuritySpoke as Security Spoke\\n    participant LegalSpoke as Legal Spoke\\n\\n    Note over User,CoordinatorAgent: Phase 0: Hub Intake\\n    User->>CoordinatorAgent: Submit vendor onboarding form\\n    Note over CoordinatorAgent: Vendor profile capture (inline multi-step interaction)\\n    CoordinatorAgent->>User: Enter vendor profile details\\n    User->>CoordinatorAgent: Provide vendor information\\n    CoordinatorAgent->>CoordinatorAgent: Validate details, determine required spokes\\n    CoordinatorAgent->>CoordinatorAgent: Package briefing packets\\n\\n    Note over FinanceSpoke,LegalSpoke: Phase 1: Spoke Reviews\\n    CoordinatorAgent->>FinanceSpoke: Dispatch finance review\\n    CoordinatorAgent->>SecuritySpoke: Dispatch security review\\n    CoordinatorAgent->>LegalSpoke: Dispatch legal review\\n    par Independent Spoke Assessments\\n        FinanceSpoke->>FinanceSpoke: Perform financial assessment\\n        FinanceSpoke->>CoordinatorAgent: Post status update\\n        SecuritySpoke->>SecuritySpoke: Perform security assessment\\n        SecuritySpoke->>CoordinatorAgent: Post status update\\n        LegalSpoke->>LegalSpoke: Perform legal assessment\\n        LegalSpoke->>CoordinatorAgent: Post status update\\n    end\\n\\n    Note over CoordinatorAgent: Phase 2: Risk Alignment\\n    CoordinatorAgent->>CoordinatorAgent: Monitor spoke progress\\n    alt Conflicts Detected\\n        CoordinatorAgent->>CoordinatorAgent: Resolve conflicts\\n    else All Clear\\n        CoordinatorAgent->>CoordinatorAgent: Summarize findings\\n    end\\n\\n    Note over CoordinatorAgent,User: Phase 3: Hub Approval & Handoff\\n    CoordinatorAgent->>CoordinatorAgent: Compile approvals\\n    Note over CoordinatorAgent: Risk clearance (artifact - delivered to tray)\\n    CoordinatorAgent->>User: Share consolidated risk clearance artifact\\n    alt User Approves\\n        User->>CoordinatorAgent: Approve onboarding\\n        CoordinatorAgent->>CoordinatorAgent: Trigger account provisioning\\n    else User Rejects\\n        User->>CoordinatorAgent: Request additional review\\n    end\\n    CoordinatorAgent->>User: Deliver final onboarding summary",
    "legend": []
  },
  "agent_message": "The star workflow routes vendor checks to 3 independent spokes with hub coordination. Review the sequence diagram, then reply in chat with approval or requested changes."
}""",
            9: """{
  "MermaidSequenceDiagram": {
    "workflow_name": "DreamWeaver Production Pipeline",
    "mermaid_diagram": "sequenceDiagram\\n    participant User as Dreamer\\n    participant DreamTriageAgent as Dream Triage Agent\\n    participant DreamRealizerAgent as Dream Realizer Agent\\n\\n    Note over User,DreamTriageAgent: Phase 0: Dream Intake & Triage\\n    User->>DreamTriageAgent: Shares dream description\\n    DreamTriageAgent->>DreamTriageAgent: Analyze content & check subscription\\n    alt Premium User\\n        DreamTriageAgent->>DreamTriageAgent: Flag for Deep Analysis + 4K Video\\n    else Standard User\\n        DreamTriageAgent->>DreamTriageAgent: Flag for Standard Video only\\n    end\\n    DreamTriageAgent->>DreamRealizerAgent: Hand off dream bundle\\n\\n    Note over DreamRealizerAgent: Phase 1: Visualization & Analysis\\n    DreamRealizerAgent->>DreamRealizerAgent: Generate video prompts\\n    DreamRealizerAgent->>DreamRealizerAgent: Call Video Gen API\\n    alt Premium Bundle\\n        DreamRealizerAgent->>DreamRealizerAgent: Perform Jungian analysis\\n        DreamRealizerAgent->>DreamRealizerAgent: Generate interpretation report\\n    end\\n\\n    Note over DreamRealizerAgent,User: Phase 2: Delivery\\n    DreamRealizerAgent->>User: Deliver video link\\n    alt Premium Bundle\\n        DreamRealizerAgent->>User: Deliver analysis report\\n    end\\n    User->>DreamRealizerAgent: Feedback / Request refinement",
    "legend": []
  },
  "agent_message": "Ready to build the DreamWeaver pipeline? Review the sequence diagram, then reply in chat with approval or requested changes."
}"""
        }
        
        example_json = mermaid_examples.get(pattern_id)

        if not example_json:
            logger.warning(f"No Mermaid diagram example for pattern_id={pattern_id}")
            return

        guidance = (
            f"[PATTERN EXAMPLE - {pattern_display_name}]\n"
            f"Here is a complete MermaidSequenceDiagram JSON example aligned with the {pattern_display_name} pattern.\n\n"
            f"```json\n{example_json}\n```\n"
        )

        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"✓ Injected Mermaid diagram example for {pattern_display_name} into {agent.name}")
        else:
            logger.warning(f"Pattern guidance injection failed for {agent.name}")

    except Exception as e:
        logger.error(f"Error in inject_project_overview_guidance: {e}", exc_info=True)

