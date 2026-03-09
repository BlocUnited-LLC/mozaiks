"""
AG2 Pattern Factory - Creates AG2 orchestration patterns with proper configuration.

Purpose:
- Factory for creating AG2 Pattern instances (AutoPattern, DefaultPattern, etc.)
- Handles pattern-specific configuration and constructor signatures
- Provides fallback logic for compatibility with different AG2 versions

Extracted from orchestration_patterns.py to improve separation of concerns.
"""

from typing import Any, Dict, List, Optional
import logging

from autogen import ConversableAgent, UserProxyAgent
from autogen.agentchat.group.patterns import (
    DefaultPattern as AG2DefaultPattern,
    AutoPattern as AG2AutoPattern,
    RoundRobinPattern as AG2RoundRobinPattern,
    RandomPattern as AG2RandomPattern,
)

logger = logging.getLogger(__name__)

__all__ = ['create_ag2_pattern']


def create_ag2_pattern(
    pattern_name: str,
    initial_agent: ConversableAgent,
    agents: List[ConversableAgent],
    user_agent: Optional[UserProxyAgent] = None,
    context_variables: Optional[Any] = None,
    group_manager_args: Optional[Dict[str, Any]] = None,
    **pattern_kwargs
) -> Any:
    """
    Create AG2 Pattern following proper constructor signature.
    
    AG2 Pattern constructor signature:
    - initial_agent: ConversableAgent
    - agents: List[ConversableAgent] 
    - user_agent: Optional[ConversableAgent]
    - context_variables: Optional[ContextVariables]
    - group_manager_args: Optional[Dict[str, Any]]
    
    Args:
        pattern_name: Pattern type (AutoPattern, DefaultPattern, RoundRobinPattern, RandomPattern)
        initial_agent: Agent that starts the conversation
        agents: List of agents participating in the group chat
        user_agent: Optional user proxy agent for human-in-the-loop
        context_variables: AG2 ContextVariables instance for shared state
        group_manager_args: Configuration for GroupChatManager (e.g., llm_config)
        **pattern_kwargs: Additional pattern-specific arguments
        
    Returns:
        Initialized AG2 Pattern instance
        
    Raises:
        ValueError: If pattern_name is not recognized
    """
    pattern_map = {
        "AutoPattern": AG2AutoPattern,
        "DefaultPattern": AG2DefaultPattern,
        "RoundRobinPattern": AG2RoundRobinPattern,
        "RandomPattern": AG2RandomPattern
    }

    if pattern_name not in pattern_map:
        # Fail fast so misconfiguration is visible instead of silently defaulting
        raise ValueError(f"Unknown orchestration pattern: {pattern_name}")

    pattern_class = pattern_map[pattern_name]

    logger.info(f" Creating {pattern_name} using AG2's native implementation")
    logger.info(f" Pattern setup - initial_agent: {initial_agent.name}")
    logger.info(f" Pattern setup - agents count: {len(agents)}")
    logger.info(f" Pattern setup - user_agent included: {user_agent is not None}")
    
    if context_variables is not None:
        try:
            # Best-effort context diagnostics
            cv_type = type(context_variables).__name__
            cv_keys = []
            if hasattr(context_variables, 'to_dict'):
                cv_keys = list(context_variables.to_dict().keys())
            elif hasattr(context_variables, 'data') and isinstance(getattr(context_variables, 'data', None), dict):
                cv_keys = list(context_variables.data.keys())
            elif isinstance(context_variables, dict):
                cv_keys = list(context_variables.keys())
            logger.info(f" Pattern setup - context_variables: True | type={cv_type} | keys={cv_keys}")
        except Exception as _log_err:
            logger.info(f" Pattern setup - context_variables: True (keys unavailable: {_log_err})")
    else:
        logger.info(" Pattern setup - context_variables: False")

    # Build AG2 Pattern constructor arguments following proper signature
    pattern_args = {
        "initial_agent": initial_agent,
        "agents": agents,
        "context_variables": context_variables,  # AG2 ContextVariables instance
    }

    # Add user_agent if provided (AG2 handles human-in-the-loop logic)
    if user_agent is not None:
        pattern_args["user_agent"] = user_agent
        logger.info(" User agent included in AG2 pattern")

    # Add group_manager_args for GroupChatManager configuration
    if group_manager_args is not None:
        pattern_args["group_manager_args"] = group_manager_args

    # Add any additional pattern-specific kwargs
    pattern_args.update(pattern_kwargs)

    try:
        pattern = pattern_class(**pattern_args)
        logger.info(f" {pattern_name} AG2 pattern created successfully")
        
        # Verify context presence on the created pattern/manager
        try:
            gm = getattr(pattern, 'group_manager', None)
            cv = getattr(gm, 'context_variables', None) if gm else None
            if cv is not None:
                try:
                    keys = list(cv.data.keys()) if hasattr(cv, 'data') else list(cv.to_dict().keys()) if hasattr(cv, 'to_dict') else []
                except Exception:
                    keys = []
                logger.info(f" Pattern created with ContextVariables attached to group_manager | keys={keys}")
            else:
                logger.debug("Pattern created; group_manager.context_variables not exposed at pattern level (will be set up in prepare_group_chat)")
        except Exception as _post_err:
            logger.debug(f"ContextVariables post-create check skipped: {_post_err}")
        
        return pattern
        
    except Exception as e:
        logger.warning(f" Failed to create {pattern_name} with all args, trying minimal: {e}")
        
        # Fallback to minimal constructor for compatibility
        minimal_args = {
            "initial_agent": initial_agent,
            "agents": agents,
        }
        if user_agent is not None:
            minimal_args["user_agent"] = user_agent

        # Include context_variables in minimal args if available
        if context_variables is not None:
            minimal_args["context_variables"] = context_variables
            
        minimal_pattern = pattern_class(**minimal_args)
        logger.info(f" {pattern_name} AG2 pattern created with minimal args")
        
        return minimal_pattern

