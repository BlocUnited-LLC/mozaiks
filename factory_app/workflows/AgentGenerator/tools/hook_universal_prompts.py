"""
Universal prompt injections for Generator workflow agents.

This single hook consolidates all universal and shared prompt sections:
1. UNIVERSAL (all agents): Compliance, agentic best practices, runtime context
2. CONDITIONAL (specific agents): JSON compliance, semantic references, validation

Separation of Concerns (with other hooks):
- hook_universal_prompts.py: ALL universal behavior and compliance (this file)
- hook_file_generation.py: Code generation best practices (UIFileGenerator, AgentToolsFileGenerator)
- update_agent_state_pattern.py: Dynamic pattern-specific guidance
"""

import logging
from typing import Any, List, Dict

logger = logging.getLogger(__name__)


def _apply_system_message(agent: Any, message: str) -> None:
    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = message
    else:
        setattr(agent, "_system_message", message)
    setattr(agent, "_mozaiks_base_system_message", message)

# =============================================================================
# SECTION 1: UNIVERSAL - Apply to ALL agents
# =============================================================================

COMPLIANCE_REQUIREMENTS = """
[COMPLIANCE REQUIREMENTS]
You MUST follow these guidelines strictly for legal reasons. NEVER stray from them.

**Output Compliance**:
- You MUST adhere to the specified '[OUTPUT FORMAT]' section below and its instructions EXACTLY.
- NEVER include any additional commentary, explanations, or text outside your structured output.
- Your outputs are used in an automation process where schema mismatches halt the workflow.
"""

AGENTIC_BEST_PRACTICES = """
[AGENTIC BEST PRACTICES]
You are an AI agent in a multi-agent workflow. Follow these universal behaviors:

**Execution Discipline**:
- Focus ONLY on your designated role and objective - NEVER attempt tasks outside your scope.
- Read upstream context fully before generating output - NEVER assume or fabricate data.
- When in doubt about requirements, prefer conservative/safe choices over speculative ones.
- Complete your task fully in a single turn when possible - avoid partial outputs.

**Context Awareness**:
- Reference upstream outputs by their semantic wrapper keys (e.g., WorkflowStrategy, TechnicalBlueprint).
- NEVER reference agent names, filenames, or internal implementation details.
- Copy names, identifiers, and values EXACTLY from upstream outputs - NEVER paraphrase or abbreviate.
- Validate cross-references before emitting output.

**Quality Standards**:
- No placeholders, TODOs, or "implement later" markers in your output, anything you output MUST be production ready.
- All identifiers MUST follow naming conventions: PascalCase for agents/components, snake_case for tools/variables.
- All outputs MUST be syntactically valid and immediately usable by downstream agents.
- Verify your output against the '[OUTPUT FORMAT]' schema before emitting.

**Error Handling**:
- If required upstream data is missing, state clearly what is missing rather than inventing data.
- If instructions conflict, follow the most specific instruction and note the conflict.
- If a validation check fails, correct the error and re-validate before emitting.
"""

RUNTIME_CONTEXT = """
[MOZAIKS RUNTIME CONTEXT]
The MozaiksCore platform provides these capabilities automatically. NEVER design workflows that recreate them.

**Chat Transport & UI (ChatUI + WebSocket)**
- Real-time bidirectional messaging via WebSocket
- React chat interface with message streaming, typing indicators, reconnection handling
- Message history display and scroll management
- NEVER CREATE: UserProxy agents, chat interfaces, or message transport

**Persistence (AG2PersistenceManager)**
- Automatic conversation history persistence to MongoDB
- Session state management across reconnections
- Chat resume with full message history
- Context variable storage and retrieval
- NEVER CREATE: Database schemas, persistence logic, or history tracking

**Token Management (MozaiksPay)**
- Real-time token usage tracking per app_id and user_id
- Wallet balance management and low-balance warnings
- Cost attribution per workflow and chat session
- NEVER CREATE: Token trackers, cost analytics, or usage monitors

**Multi-Tenant Isolation**
- App and user boundaries enforced at runtime
- Data isolation between tenants, session scoping
- NEVER CREATE: Tenant validation or scoped query logic

**Observability & Events**
- Structured logging with correlation IDs
- Performance metrics, agent lifecycle tracking
- Unified event dispatching for agent actions
- NEVER CREATE: Logging infrastructure, metrics collection, or event systems

**UI Component Delivery (ChatUI artifacts)**
- Inline component rendering within chat flow
- Artifact panel for rich content display
- Form submission and response handling
- NEVER CREATE: React components, UI layouts, or form state handlers

**What Workflows SHOULD Define**:
1. Agent Roles & Prompts - What each agent does and how it thinks
2. Tools & Integrations - External APIs and business logic (Stripe, SendGrid, OpenAI, etc.)
3. Orchestration Flow - How agents coordinate and hand off
4. Context Variables - Domain-specific state the workflow tracks
5. Human Interaction Points - Where users provide input or approval
6. Lifecycle Hooks - Custom initialization/cleanup beyond runtime defaults

**Anti-Patterns to Avoid**:
❌ "ChatAgent" or "UserProxyAgent" - Runtime handles user communication
❌ "PersistenceAgent" or "DatabaseAgent" - Use context variables; runtime persists
❌ "TokenTracker" or "UsageMonitor" - MozaiksPay handles automatically
❌ "WebSocketHandler" or "MessageRouter" - Transport layer is provided
❌ "SessionManager" or "LoggingAgent" - Runtime manages these
"""


# =============================================================================
# SECTION 2: CONDITIONAL - Apply to specific agent types
# =============================================================================

JSON_OUTPUT_COMPLIANCE = """
[JSON OUTPUT COMPLIANCE]
(CRITICAL - REQUIRED FOR ALL STRUCTURED OUTPUTS)
You MUST output valid, parseable JSON. Follow these rules EXACTLY:

**1. Output Format**:
- Output ONLY raw JSON object - no markdown code fences (```json), no explanatory text
- JSON MUST be valid and parseable by json.loads() without any cleaning

**2. String Escaping (CRITICAL)**:
When JSON strings contain special characters, escape them correctly:
- Double quotes: Use `\\"` (single backslash + quote)
  * CORRECT: `"description": "This is a \\"quoted\\" word"`
  * WRONG: `"description": "This is a \\\\\\"quoted\\\\\\" word"` (double-escaped)

- Python docstrings (triple quotes): Use `\\"\\"\\"` (escape each quote separately)
  * CORRECT: `"code": "def func():\\n    \\"\\"\\"This is a docstring\\"\\"\\"\\n    pass"`

- Single quotes in strings: Use `'` (NO escaping needed in JSON)
  * CORRECT: `"text": "It's a test"`
  * WRONG: `"text": "It\\'s a test"` (invalid escape sequence)

- Backslashes: Use `\\\\` (double backslash)
  * CORRECT: `"path": "C:\\\\Users\\\\file.txt"`

- Newlines: Use `\\n`, tabs: Use `\\t`

**3. No Trailing Commas**:
- CORRECT: `{"a": 1, "b": 2}`
- WRONG: `{"a": 1, "b": 2,}` (trailing comma)

**4. No Trailing Garbage**:
- JSON MUST end with final closing brace `}`
- NO additional text, notes, or comments after JSON

**5. Test Your Output**:
Before emitting, mentally verify your JSON would pass `json.loads(your_output)`

**Summary**: Single-escape quotes (`\\"`), no markdown fences, valid JSON only.
"""

SEMANTIC_REFERENCE_RULES = """
[SEMANTIC REFERENCE RULES]
(CRITICAL - PREVENTS CROSS-REFERENCE ERRORS)

When referencing names from upstream outputs, you MUST copy them EXACTLY:

**Agent Names**: Copy character-for-character (case-sensitive, PascalCase)
  ✅ CORRECT: StageAgents has "RouterAgent" → your output uses "RouterAgent"
  ❌ WRONG: StageAgents has "RouterAgent" → your output uses "Router" or "routerAgent"

**Tool Names**: Copy character-for-character (snake_case preserved)
  ✅ CORRECT: agent_tools has "classify_request" → your output uses "classify_request"
  ❌ WRONG: agent_tools has "classify_request" → your output uses "ClassifyRequest"

**Variable Names**: Copy character-for-character (snake_case preserved)
  ✅ CORRECT: context_variables has "current_domain" → your output uses "current_domain"
  ❌ WRONG: context_variables has "current_domain" → your output uses "currentDomain"

**Workflow Stage Names**: Copy character-for-character (including "Module N:" prefix)
  ✅ CORRECT: WorkflowStrategy has "Stage 1: Intake" → your output uses "Stage 1: Intake"
  ❌ WRONG: WorkflowStrategy has "Stage 1: Intake" → your output uses "Intake Module"

**NEVER**: Paraphrase, abbreviate, change casing, or invent names not in upstream data.

**WHY**: The runtime performs exact string matching. Any deviation causes runtime failures.
"""

VALIDATION_CHECKLIST = """
[CROSS-REFERENCE VALIDATION]
(REQUIRED BEFORE EMITTING JSON OUTPUT)

Before you emit your final JSON, verify these checks:

□ Every `agent` or `agent_name` field matches an agent from StageAgents EXACTLY
□ Every `tool`, `function`, or tool name matches a tool from upstream EXACTLY  
□ Every `source_agent` and `target_agent` in handoffs exists in the agents list
□ Every context variable reference matches a variable from ContextVariablesPlan
□ Every `stage_name` reference matches WorkflowStrategy.workflow_stages[].stage_name EXACTLY
□ No fabricated names that weren't in upstream outputs

If any check fails, correct the error before emitting.
"""


# =============================================================================
# HOOK FUNCTION - Single entry point for all injections
# =============================================================================

def inject_universal_prompts(agent, messages: List[Dict[str, Any]], groupchat: Any = None) -> str:
    """
    Injects universal and conditional prompt sections into agent system message.
    
    UNIVERSAL (all agents):
    - COMPLIANCE_REQUIREMENTS: Output format adherence
    - AGENTIC_BEST_PRACTICES: Execution discipline, context awareness
    
    CONDITIONAL (by agent type):
    - RUNTIME_CONTEXT: workflow_design_agents - might invent wrong agent types
    - JSON_OUTPUT_COMPLIANCE: Agents with structured output requirements
    - SEMANTIC_REFERENCE_RULES: cross_referencing_agents - must copy names exactly
    - VALIDATION_CHECKLIST: artifact_producing_agents - outputs become final artifacts
    
    This keeps prompts lean - agents only get what they need.
    """
    try:
        agent_name = getattr(agent, 'name', 'unknown')
        system_message = getattr(agent, 'system_message', '') or ""
        
        # Skip user agent
        if agent_name == "user":
            logger.debug(f"Skipping prompt injection for {agent_name} (user agent)")
            return system_message
        
        sections_added = []
        
        # =================================================================
        # UNIVERSAL SECTIONS (all agents)
        # =================================================================
        
        if "[COMPLIANCE REQUIREMENTS]" not in system_message:
            system_message += f"\n\n{COMPLIANCE_REQUIREMENTS}"
            sections_added.append("COMPLIANCE_REQUIREMENTS")
        
        if "[AGENTIC BEST PRACTICES]" not in system_message:
            system_message += f"\n\n{AGENTIC_BEST_PRACTICES}"
            sections_added.append("AGENTIC_BEST_PRACTICES")
        
        # =================================================================
        # CONDITIONAL SECTIONS (specific agent types)
        # =================================================================
        
        # Skip purely conversational agents for all conditional sections
        conversational_agents = {"InterviewAgent"}
        if agent_name in conversational_agents:
            _apply_system_message(agent, system_message)
            if sections_added:
                logger.info(f"✓ Injected universal sections into {agent_name}: {', '.join(sections_added)}")
            return system_message
        
        # Agents that design workflow structure - might accidentally create
        # UserProxy, persistence, or token tracking agents
        workflow_design_agents = {
            "WorkflowStrategyAgent",
        }
        
        if agent_name in workflow_design_agents and "[MOZAIKS RUNTIME CONTEXT]" not in system_message:
            system_message += f"\n\n{RUNTIME_CONTEXT}"
            sections_added.append("RUNTIME_CONTEXT")
        
        # JSON compliance for structured output agents
        needs_structured = (
            "structured_output" in system_message.lower() or
            "json" in system_message.lower() or
            "[OUTPUT FORMAT]" in system_message
        )
        
        if needs_structured and "[JSON OUTPUT COMPLIANCE]" not in system_message:
            system_message += f"\n\n{JSON_OUTPUT_COMPLIANCE}"
            sections_added.append("JSON_OUTPUT_COMPLIANCE")
        
        # Agents that read names from upstream and must copy them exactly
        cross_referencing_agents = {
            "ToolsManagerAgent", "ContextVariablesAgent", "AgentsAgent", 
            "HandoffsAgent", "OrchestratorAgent", "StructuredOutputsAgent",
            "UIFileGenerator", "AgentToolsFileGenerator", "HookAgent"
        }
        
        if agent_name in cross_referencing_agents and "[SEMANTIC REFERENCE RULES]" not in system_message:
            system_message += f"\n\n{SEMANTIC_REFERENCE_RULES}"
            sections_added.append("SEMANTIC_REFERENCE_RULES")
        
        # Agents whose outputs become final workflow artifacts (JSON files or code)
        # Need cross-reference validation before emitting
        artifact_producing_agents = {
            "ToolsManagerAgent", "AgentsAgent", "HandoffsAgent", 
            "OrchestratorAgent", "StructuredOutputsAgent",
            "UIFileGenerator", "AgentToolsFileGenerator", "HookAgent"
        }
        
        if agent_name in artifact_producing_agents and "[CROSS-REFERENCE VALIDATION]" not in system_message:
            system_message += f"\n\n{VALIDATION_CHECKLIST}"
            sections_added.append("VALIDATION_CHECKLIST")
        
        # Update agent and log
        _apply_system_message(agent, system_message)
        
        if sections_added:
            logger.info(f"✓ Injected prompt sections into {agent_name}: {', '.join(sections_added)}")
        else:
            logger.debug(f"No changes needed for {agent_name} (all sections already present)")
        
        return system_message

    except Exception as e:
        logger.error(f"Error injecting prompts for {getattr(agent, 'name', 'unknown')}: {e}", exc_info=True)
        return getattr(agent, 'system_message', '') or ""

