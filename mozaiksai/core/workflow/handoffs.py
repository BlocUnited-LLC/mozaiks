from __future__ import annotations

from typing import Dict, Any, List, Optional
from functools import wraps
from autogen.agentchat.group import (
    AgentTarget,
    RevertToUserTarget,
    OnCondition,
    StringLLMCondition,
    OnContextCondition,
    ExpressionContextCondition,
    ContextExpression,
    TerminateTarget,
)
from .workflow_manager import workflow_manager
from logs.logging_config import get_workflow_logger
from mozaiksai.core.events.handoff_events import emit_handoff_event, sanitize_identifier

log = get_workflow_logger("handoffs")

"""Production handoff integration for AG2 group orchestration.

Standardized JSON schema (handoffs.json):
  handoffs:
    handoff_rules:
      - source_agent: <name>
        target_agent: <name|user|terminate>
        handoff_type: after_work | condition
        condition: <natural language or context expression with ${...}> | null

Rules:
  - Multiple condition rules per source preserved in order.
  - condition containing '${' becomes a context expression condition.
  - Last after_work wins per source.
  - Unknown agents logged & skipped (won't raise).
"""


class HandoffManager:
    def __init__(self) -> None:
        # Canonical special target names
        self._special = {
            "user": lambda: RevertToUserTarget(),
            "terminate": lambda: TerminateTarget(),
        }
        # Accept common variants for robustness
        self._special_aliases = {
            "user": {"user", "User", "USER", "user_proxy", "userproxy", "UserProxy", "user-agent", "UserAgent"},
            "terminate": {"terminate", "Terminate", "TERMINATE", "end", "End", "END", "stop", "Stop", "STOP"},
        }

    def apply_handoffs_from_config(self, workflow_name: str, agents: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            "workflow": workflow_name,
            "rules_total": 0,
            "agents_with_rules": set(),
            "after_work_set": 0,
            "llm_conditions": 0,
            "context_conditions": 0,
            "conditional_after_works": 0,
            "missing_source_agents": [],
            "missing_target_agents": [],
            "errors": []
        }
        config = workflow_manager.get_config(workflow_name) or {}
        handoffs_block = config.get("handoffs", {})
        if "handoffs" in handoffs_block:  # tolerate nested key structure
            handoffs_block = handoffs_block["handoffs"]
        rules: List[Dict[str, Any]] = handoffs_block.get("handoff_rules", []) or []
        summary["rules_total"] = len(rules)
        if not rules:
            log.warning(f"⚠️ [HANDOFFS] No handoff_rules found for workflow {workflow_name}")
            summary["agents_with_rules"] = []
            return summary

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in rules:
            sa = r.get("source_agent")
            ta = r.get("target_agent")
            if not sa or not ta:
                summary["errors"].append(f"Rule missing source/target: {r}")
                continue
            grouped.setdefault(sa, []).append(r)

        for source, src_rules in grouped.items():
            agent_obj = agents.get(source)
            if not agent_obj:
                summary["missing_source_agents"].append(source)
                log.warning(f"⚠️ [HANDOFFS] Source agent '{source}' not present; skipping its rules")
                continue
            if not hasattr(agent_obj, "handoffs"):
                summary["errors"].append(f"Agent {source} lacks .handoffs attribute")
                log.error(f"❌ [HANDOFFS] Agent {source} lacks .handoffs attribute")
                continue

            llm_list: List[OnCondition] = []
            context_list: List[OnContextCondition] = []
            conditional_after_list: List[OnContextCondition] = []
            after_work_target = None

            for rule in src_rules:
                t_name = rule.get("target_agent")
                h_type = (rule.get("handoff_type") or "after_work").strip()
                cond_text = rule.get("condition")
                target = self._build_target(t_name, agents, summary)
                if not target:
                    continue
                if h_type == "after_work":
                    if after_work_target is not None:
                        log.info(f"🔁 [HANDOFFS] Overriding after_work for {source} -> {t_name}")
                    after_work_target = target
                elif h_type == "condition":
                    if cond_text is None or (isinstance(cond_text, str) and not cond_text.strip()):
                        log.warning(f"⚠️ [HANDOFFS] condition rule without condition text skipped: {rule}")
                        continue
                    cond_type_raw = rule.get("condition_type")
                    cond_type = ""
                    if isinstance(cond_type_raw, str):
                        cond_type = cond_type_raw.strip().lower()
                    elif cond_type_raw is not None:
                        cond_type = str(cond_type_raw).strip().lower()
                    if isinstance(cond_text, str):
                        cond_text = cond_text.strip()
                    use_expression = cond_type in {"expression", "context_expression", "context"}
                    use_llm = cond_type in {"llm", "string_llm"}
                    if not cond_type:
                        if isinstance(cond_text, str) and "${" in cond_text:
                            use_expression = True
                        else:
                            use_llm = True
                    if use_expression:
                        expr_text = cond_text if isinstance(cond_text, str) else str(cond_text)
                        scope_raw = rule.get("condition_scope") or rule.get("scope")
                        scope = str(scope_raw).strip().lower() if scope_raw is not None else ""
                        try:
                            condition_obj = ExpressionContextCondition(expression=ContextExpression(expr_text))
                            condition_obj = self._wrap_expression_condition_logging(condition_obj, source, target, expr_text)
                            context_condition = OnContextCondition(target=target, condition=condition_obj)
                            if scope in {"pre", "before_reply", "immediate", "context_pre"}:
                                context_list.append(context_condition)
                                self._log_expression_condition_debug(source, target, expr_text, "add_context_condition")
                                log.info(
                                    f"✅ [HANDOFFS] Added PRE-REPLY context handoff {source}->{t_name}: expr={expr_text!r} scope={scope or 'pre'} cond_type={cond_type or 'auto-expression'}"
                                )
                            else:
                                conditional_after_list.append(context_condition)
                                self._log_expression_condition_debug(source, target, expr_text, "add_after_work")
                                log.debug(
                                    f"[HANDOFFS] Added conditional after-work {source}->{t_name}: expr={expr_text!r} scope={scope or 'after'} cond_type={cond_type or 'auto-expression'}"
                                )
                        except Exception as e:
                            summary["errors"].append(f"Context condition build failed ({source}): {e}")
                            log.error(f"❌ [HANDOFFS] Context condition build failed for {source}: {e}")
                    elif use_llm:
                        llm_prompt = cond_text if isinstance(cond_text, str) else str(cond_text)
                        try:
                            llm_condition = StringLLMCondition(prompt=llm_prompt)
                            llm_list.append(
                                OnCondition(
                                    target=target,
                                    condition=llm_condition
                                )
                            )
                            log.info(f"[HANDOFFS] Added LLM condition {source}->{t_name}: prompt={llm_prompt!r} cond_type={cond_type or 'auto-llm'}")
                        except Exception as e:
                            summary["errors"].append(f"LLM condition build failed ({source}): {e}")
                            log.error(f"❌ [HANDOFFS] LLM condition build failed for {source}: {e}")
                    else:
                        log.warning(f"⚠️ [HANDOFFS] Unsupported condition_type '{cond_type_raw}' for {source}->{t_name}; defaulting to LLM evaluation")
                        llm_prompt = cond_text if isinstance(cond_text, str) else str(cond_text)
                        try:
                            llm_condition = StringLLMCondition(prompt=llm_prompt)
                            llm_list.append(
                                OnCondition(
                                    target=target,
                                    condition=llm_condition
                                )
                            )
                            log.info(f"[HANDOFFS] Added LLM condition {source}->{t_name}: prompt={llm_prompt!r} (fallback)")
                        except Exception as e:
                            summary["errors"].append(f"LLM condition build failed ({source}): {e}")
                            log.error(f"❌ [HANDOFFS] LLM condition build failed for {source}: {e}")

                else:
                    log.warning(f"⚠️ [HANDOFFS] Unknown handoff_type '{h_type}' skipped (rule={rule})")

            applied = False
            try:
                after_work_conditions: List[OnContextCondition] = []
                if llm_list:
                    agent_obj.handoffs.add_llm_conditions(llm_list)  # type: ignore[attr-defined]
                    summary["llm_conditions"] += len(llm_list)
                    applied = True
                if context_list:
                    agent_obj.handoffs.add_context_conditions(context_list)  # type: ignore[attr-defined]
                    summary["context_conditions"] += len(context_list)
                    applied = True
                if conditional_after_list:
                    after_work_conditions.extend(conditional_after_list)
                if after_work_target is not None:
                    after_work_conditions.append(OnContextCondition(target=after_work_target, condition=None))
                if after_work_conditions:
                    agent_obj.handoffs.add_after_works(after_work_conditions)  # type: ignore[attr-defined]
                    summary["conditional_after_works"] += len(conditional_after_list)
                    if after_work_target is not None:
                        summary["after_work_set"] += 1
                    applied = True
            except Exception as e:
                summary["errors"].append(f"Apply failed ({source}): {e}")
                log.error(f"❌ [HANDOFFS] Failed applying rules for {source}: {e}")

            if applied:
                summary["agents_with_rules"].add(source)
                log.info(
                    f"✅ [HANDOFFS] {source}: llm={len(llm_list)} ctx_pre={len(context_list)} conditional_after={len(conditional_after_list)} after_work={'yes' if after_work_target else 'no'}"
                )

        summary["agents_with_rules"] = list(summary["agents_with_rules"])
        if summary["errors"]:
            log.warning(f"⚠️ [HANDOFFS] Completed with {len(summary['errors'])} errors")
        return summary

    def verify(self, agents: Dict[str, Any]) -> Dict[str, Any]:  # optional health snapshot
        out = {"total": len(agents), "configured": 0, "details": {}}
        for name, a in agents.items():
            if not hasattr(a, "handoffs"):
                continue
            h = a.handoffs
            llm_rules = getattr(h, "llm_conditions", None) or getattr(h, "_llm_conditions", [])
            ctx_rules = getattr(h, "context_conditions", None) or getattr(h, "_context_conditions", [])
            after_work = getattr(h, "after_works", None) or getattr(h, "_after_work", None)
            out["details"][name] = {
                "llm": len(llm_rules) if hasattr(llm_rules, "__len__") else 0,
                "ctx": len(ctx_rules) if hasattr(ctx_rules, "__len__") else 0,
                "after_work": bool(after_work)
            }
            if any([out["details"][name]["llm"], out["details"][name]["ctx"], out["details"][name]["after_work"]]):
                out["configured"] += 1
        return out

    def _wrap_expression_condition_logging(self, condition_obj: ExpressionContextCondition, source: str, target: Any, expr_text: str) -> ExpressionContextCondition:
        """Wrap ExpressionContextCondition.evaluate to log runtime evaluation results."""
        original_evaluate = condition_obj.evaluate
        target_name = getattr(target, 'agent_name', None)
        if not target_name and hasattr(target, 'normalized_name'):
            try:
                target_name = target.normalized_name()
            except Exception:
                target_name = None
        if not target_name:
            target_name = target.__class__.__name__

        variable_names: list[str] = []
        expression = getattr(condition_obj, 'expression', None)
        candidate_vars = getattr(expression, '_variable_names', None)
        if isinstance(candidate_vars, list):
            variable_names = [str(name) for name in candidate_vars if isinstance(name, str)]

        def _logged_evaluate(context_variables):
            try:
                result = original_evaluate(context_variables)
            except Exception as exc:
                log.error(f'[HANDOFFS] Expression evaluate failed source={source} target={target_name}: {exc}')
                raise

            values = {}
            for name in variable_names:
                try:
                    if hasattr(context_variables, 'get'):
                        values[name] = context_variables.get(name, None)
                    elif hasattr(context_variables, 'data') and isinstance(getattr(context_variables, 'data'), dict):
                        values[name] = getattr(context_variables, 'data').get(name)
                    else:
                        values[name] = None
                except Exception:
                    values[name] = None

            log.info(f"[HANDOFFS][EVAL] source={source} target={target_name} expr={expr_text!r} vars={values} -> {result}")
            return result

        object.__setattr__(condition_obj, 'evaluate', _logged_evaluate)  # type: ignore[assignment]
        return condition_obj

    def _log_expression_condition_debug(self, source: str, target: Any, expr_text: str, method: str) -> None:
        """Emit a trace snippet showing how an expression handoff is wired."""
        try:
            target_name = getattr(target, 'agent_name', None)
            if not target_name and hasattr(target, 'normalized_name'):
                target_name = target.normalized_name()
            if not target_name:
                target_name = target.__class__.__name__
        except Exception:  # pragma: no cover - debug helper should never break wiring
            target_name = getattr(target, 'agent_name', None) or target.__class__.__name__
        expr_display = expr_text.replace('"', '\\"')
        display_method = 'add_context_condition'
        if method != 'add_context_condition':
            log.info(f'# Runtime uses {method} for after-work evaluation')
        snippet = (
            'from autogen.agentchat.group import OnContextCondition, ExpressionContextCondition, ContextExpression\n\n'
            f'# Set up context-based handoffs for the {source}\n'
            f'{source}.handoffs.{display_method}(\n'
            '    OnContextCondition(\n'
            f'        target=AgentTarget({target_name}),\n'
            '        condition=ExpressionContextCondition(\n'
            f'            expression=ContextExpression("{expr_display}")\n'
            '        )\n'
            '    )\n'
            ')\n'
        )
        log.info(''.join(snippet))

    def _build_target(self, target_name: Optional[str], agents: Dict[str, Any], summary: Dict[str, Any]):
        if not target_name:
            return None
        # Normalize special aliases first
        for canonical, aliases in self._special_aliases.items():
            if target_name in aliases:
                return self._special[canonical]()
        # Case-insensitive agent name match fallback
        if target_name in self._special:
            return self._special[target_name]()
        if target_name in agents:
            return AgentTarget(agents[target_name])
        # Try case-insensitive match for agent keys
        lower_map = {k.lower(): k for k in agents.keys()}
        tgt_key = lower_map.get(target_name.lower()) if isinstance(target_name, str) else None
        if tgt_key and tgt_key in agents:
            return AgentTarget(agents[tgt_key])
        summary["missing_target_agents"].append(target_name)
        log.warning(f"⚠️ [HANDOFFS] Target agent '{target_name}' not found; skipping")
        return None


handoff_manager = HandoffManager()

###################################################################
# HANDOFF EVENT #
###################################################################
def _describe_target(obj: Any) -> str:
    if obj is None:
        return "None"
    if isinstance(obj, str):
        return obj
    name = getattr(obj, "name", None)
    if isinstance(name, str) and name:
        return name
    label = getattr(obj, "agent_name", None)
    if isinstance(label, str) and label:
        return label
    if hasattr(obj, "__class__"):
        return obj.__class__.__name__
    return str(obj)


def _extract_context_metadata(agent: Any) -> Dict[str, Any]:
    """Pull workflow/chat identifiers from the agent's context variables if present."""
    metadata: Dict[str, Any] = {}
    try:
        context_variables = getattr(agent, "context_variables", None)
        if context_variables and hasattr(context_variables, "get"):
            workflow_name = context_variables.get("workflow_name")  # type: ignore[call-arg]
            chat_id = context_variables.get("chat_id")  # type: ignore[call-arg]
            if workflow_name:
                metadata["workflow_name"] = workflow_name
            if chat_id:
                metadata["chat_id"] = chat_id
    except Exception:  # pragma: no cover - defensive guard
        return metadata
    return metadata


def _patch_autogen_handoff_logging() -> None:
    """Enable lightweight runtime logging for AG2 handoff evaluation."""
    if getattr(_patch_autogen_handoff_logging, "_applied", False):
        return
    try:
        from autogen.agentchat.group import group_utils as ag_group_utils  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive guard
        log.warning(f"⚠️ [HANDOFFS] Unable to import AG2 group_utils for logging patch: {exc}")
        return

    try:
        original_ctx = ag_group_utils._run_oncontextconditions
        if not getattr(original_ctx, "__mozaiks_instrumented__", False):

            @wraps(original_ctx)
            def _mozaiks_run_oncontextconditions(agent, messages=None, sender=None, config=None):  # type: ignore[override]
                agent_name = sanitize_identifier(agent) or agent.__class__.__name__
                handoffs_obj = getattr(agent, "handoffs", None)
                context_conditions = getattr(handoffs_obj, "context_conditions", []) or []
                log.info(f"[HANDOFFS][CTX] Evaluating context handoffs | agent={agent_name} count={len(context_conditions)}")

                try:
                    for idx, on_condition in enumerate(context_conditions):
                        available = (
                            on_condition.available.is_available(agent, messages if messages else [])
                            if getattr(on_condition, "available", None)
                            else True
                        )
                        condition_expr = None
                        condition_obj = getattr(on_condition, "condition", None)
                        if getattr(condition_obj, "expression", None) is not None:
                            condition_expr = getattr(condition_obj.expression, "expression", None)

                        if not available:
                            continue

                        condition_result = True
                        if condition_obj is not None:
                            condition_result = condition_obj.evaluate(agent.context_variables)

                        if condition_result:
                            target = on_condition.target
                            try:
                                target.activate_target(agent._group_manager.groupchat)  # type: ignore[attr-defined]
                            except Exception as exc:  # pragma: no cover - safety fallback
                                log.error(f"❌ [HANDOFFS][CTX] Failed to activate target for {agent_name}: {exc}")
                                raise

                            transfer_name = _describe_target(target)
                            payload = {
                                "source_agent": agent_name,
                                "target": transfer_name,
                                "condition_index": idx,
                                "condition_expression": condition_expr,
                                "available": available,
                                "trigger": "context",
                            }
                            payload.update(_extract_context_metadata(agent))
                            emit_handoff_event("context", payload)
                            log.info(f"[HANDOFFS][CTX] Triggered handoff {agent_name}->{transfer_name}")
                            return True, "[Handing off to " + transfer_name + "]"

                    log.info(f"[HANDOFFS][CTX] No context handoff matched for agent={agent_name}")
                    return False, None

                except Exception as exc:  # pragma: no cover - revert to original on failure
                    log.warning(f"⚠️ [HANDOFFS][CTX] Instrumentation fallback due to error: {exc}")
                    return original_ctx(agent, messages=messages, sender=sender, config=config)

            setattr(_mozaiks_run_oncontextconditions, "__mozaiks_instrumented__", True)
            ag_group_utils._run_oncontextconditions = _mozaiks_run_oncontextconditions

        original_after = ag_group_utils._evaluate_after_works_conditions
        if not getattr(original_after, "__mozaiks_instrumented__", False):

            @wraps(original_after)
            def _mozaiks_evaluate_after_works(agent, groupchat, user_agent=None):  # type: ignore[override]
                agent_name = sanitize_identifier(agent) or agent.__class__.__name__
                handoffs_obj = getattr(agent, "handoffs", None)
                after_work_conditions = getattr(handoffs_obj, "after_works", []) or []
                log.info(f"[HANDOFFS][AFTER] Evaluating after-work handoffs | agent={agent_name} count={len(after_work_conditions)}")

                if not after_work_conditions:
                    return None

                try:
                    for idx, after_work_condition in enumerate(after_work_conditions):
                        available = (
                            after_work_condition.available.is_available(agent, groupchat.messages)
                            if getattr(after_work_condition, "available", None)
                            else True
                        )
                        condition_expr = None
                        condition_obj = getattr(after_work_condition, "condition", None)
                        if getattr(condition_obj, "expression", None) is not None:
                            condition_expr = getattr(condition_obj.expression, "expression", None)

                        if not available:
                            continue

                        condition_result = True
                        if condition_obj is not None:
                            condition_result = condition_obj.evaluate(agent.context_variables)

                        if condition_result:
                            resolved = after_work_condition.target.resolve(
                                groupchat,
                                agent,
                                user_agent,
                            ).get_speaker_selection_result(groupchat)

                            payload = {
                                "source_agent": agent_name,
                                "target": _describe_target(after_work_condition.target),
                                "resolved_target": _describe_target(resolved),
                                "condition_index": idx,
                                "condition_expression": condition_expr,
                                "available": available,
                                "trigger": "after_work",
                            }
                            payload.update(_extract_context_metadata(agent))
                            emit_handoff_event("after_work", payload)
                            log.info(
                                f"[HANDOFFS][AFTER] Resolved after-work target for {agent_name}: {_describe_target(resolved)}"
                            )
                            return resolved

                    log.info(f"[HANDOFFS][AFTER] No after-work condition matched for agent={agent_name}")
                    return None

                except Exception as exc:  # pragma: no cover - fallback to original
                    log.warning(f"⚠️ [HANDOFFS][AFTER] Instrumentation fallback due to error: {exc}")
                    return original_after(agent, groupchat, user_agent)

            setattr(_mozaiks_evaluate_after_works, "__mozaiks_instrumented__", True)
            ag_group_utils._evaluate_after_works_conditions = _mozaiks_evaluate_after_works

        original_create_funcs = ag_group_utils.create_on_condition_handoff_functions
        if not getattr(original_create_funcs, "__mozaiks_instrumented__", False):
            
            @wraps(original_create_funcs)
            def _mozaiks_create_on_condition_handoff_functions(agent):
                # Re-implementation with instrumentation
                agent.handoffs.set_llm_function_names()
                for on_condition in agent.handoffs.llm_conditions:
                    # Create the base transfer function
                    base_func = ag_group_utils._create_on_condition_handoff_function(on_condition.target)
                    
                    # Wrap it to emit event
                    @wraps(base_func)
                    def _instrumented_transfer():
                        try:
                            agent_name = sanitize_identifier(agent) or "Unknown"
                            target_name = _describe_target(on_condition.target)
                            payload = {
                                "source_agent": agent_name,
                                "target": target_name,
                                "trigger": "llm_condition",
                                "available": True
                            }
                            payload.update(_extract_context_metadata(agent))
                            emit_handoff_event("llm_condition", payload)
                            log.info(f"[HANDOFFS][LLM] Triggered LLM handoff {agent_name}->{target_name}")
                        except Exception as e:
                            log.warning(f"⚠️ [HANDOFFS][LLM] Event emission failed: {e}")
                        return base_func()

                    agent._add_single_function(
                        _instrumented_transfer,
                        on_condition.llm_function_name,
                        on_condition.condition.get_prompt(agent, []),
                    )
                
                log.info(f"[HANDOFFS][LLM] Registered {len(agent.handoffs.llm_conditions)} LLM handoff functions for {sanitize_identifier(agent)}")

            setattr(_mozaiks_create_on_condition_handoff_functions, "__mozaiks_instrumented__", True)
            ag_group_utils.create_on_condition_handoff_functions = _mozaiks_create_on_condition_handoff_functions

        setattr(_patch_autogen_handoff_logging, "_applied", True)
        log.info("[HANDOFFS] Runtime handoff instrumentation enabled")
    except Exception as exc:  # pragma: no cover - defensive guard
        log.warning(f"⚠️ [HANDOFFS] Failed to patch AG2 handoff logging: {exc}")


_patch_autogen_handoff_logging()
###################################################################
###################################################################

def wire_handoffs(workflow_name: str, agents: Dict[str, Any]) -> None:
    try:
        summary = handoff_manager.apply_handoffs_from_config(workflow_name, agents)
        log.info(
            f"HANDOFFS_APPLIED rules={summary['rules_total']} agents={len(summary['agents_with_rules'])} "
            f"after_work={summary['after_work_set']} llm={summary['llm_conditions']} ctx={summary['context_conditions']} conditional_after={summary['conditional_after_works']}"
        )
    except Exception as e:
        log.error(f"❌ [HANDOFFS] Wiring failed: {e}", exc_info=True)


def wire_handoffs_with_debugging(workflow_name: str, agents: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return handoff_manager.apply_handoffs_from_config(workflow_name, agents)
    except Exception as e:
        return {"success": False, "error": str(e)}


__all__ = ["wire_handoffs", "wire_handoffs_with_debugging", "handoff_manager", "HandoffManager"]

