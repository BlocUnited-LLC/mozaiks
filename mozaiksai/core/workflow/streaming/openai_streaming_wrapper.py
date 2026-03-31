# ==============================================================================
# FILE: mozaiksai/core/workflow/streaming/openai_streaming_wrapper.py
# DESCRIPTION: OpenAI wrapper that emits streaming chunks to WebSocket
# ==============================================================================

"""
OpenAI Streaming Wrapper

Wraps the OpenAI client to intercept streaming responses and emit
individual tokens to the WebSocket transport in real-time.

This enables typewriter effect in the frontend while preserving
AG2's aggregated response handling.

Usage:
    from mozaiksai.core.workflow.streaming import install_streaming_wrapper

    # Install before workflow runs
    install_streaming_wrapper(transport, chat_id)

    # AG2 workflow runs normally, but tokens stream to frontend
"""

import asyncio
import functools
import logging
import threading
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

logger = logging.getLogger(__name__)

# Thread-local storage for streaming context
_streaming_context = threading.local()


def set_streaming_context(
    transport: "SimpleTransport",
    chat_id: str,
    agent_name: str = "Agent",
) -> None:
    """Set the streaming context for the current thread."""
    _streaming_context.transport = transport
    _streaming_context.chat_id = chat_id
    _streaming_context.agent_name = agent_name
    _streaming_context.sequence = 0
    _streaming_context.stream_id = None
    _streaming_context.loop = None

    # Try to capture the event loop
    try:
        _streaming_context.loop = asyncio.get_running_loop()
    except RuntimeError:
        pass


def clear_streaming_context() -> None:
    """Clear the streaming context."""
    _streaming_context.transport = None
    _streaming_context.chat_id = None
    _streaming_context.agent_name = "Agent"
    _streaming_context.sequence = 0
    _streaming_context.stream_id = None
    _streaming_context.loop = None


def update_streaming_agent(agent_name: str) -> None:
    """Update the current agent name for streaming."""
    _streaming_context.agent_name = agent_name
    _streaming_context.stream_id = None  # Reset stream ID


def _emit_chunk(content: str) -> None:
    """Emit a streaming chunk to the WebSocket transport."""
    transport = getattr(_streaming_context, 'transport', None)
    chat_id = getattr(_streaming_context, 'chat_id', None)

    if not transport or not chat_id or not content:
        return

    _streaming_context.sequence = getattr(_streaming_context, 'sequence', 0) + 1
    seq = _streaming_context.sequence
    agent = getattr(_streaming_context, 'agent_name', 'Agent')

    if not getattr(_streaming_context, 'stream_id', None):
        _streaming_context.stream_id = f"{chat_id}:{agent}:{seq}"

    chunk_event = {
        "kind": "stream_chunk",
        "agent": agent,
        "content": content,
        "chunk_seq": seq,
        "stream_id": _streaming_context.stream_id,
    }

    try:
        loop = getattr(_streaming_context, 'loop', None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                transport.send_event_to_ui(chunk_event, chat_id),
                loop,
            )
        else:
            # Try to create task (may fail)
            try:
                asyncio.create_task(
                    transport.send_event_to_ui(chunk_event, chat_id)
                )
            except RuntimeError:
                logger.debug("[STREAMING] No event loop for chunk emission")
    except Exception as e:
        logger.debug(f"[STREAMING] Failed to emit chunk: {e}")


def _emit_stream_end(full_content: str = "") -> None:
    """Emit stream_end event."""
    transport = getattr(_streaming_context, 'transport', None)
    chat_id = getattr(_streaming_context, 'chat_id', None)

    if not transport or not chat_id:
        return

    agent = getattr(_streaming_context, 'agent_name', 'Agent')
    stream_id = getattr(_streaming_context, 'stream_id', None)

    end_event = {
        "kind": "stream_end",
        "agent": agent,
        "stream_id": stream_id,
        "full_content": full_content,
    }

    try:
        loop = getattr(_streaming_context, 'loop', None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                transport.send_event_to_ui(end_event, chat_id),
                loop,
            )
    except Exception as e:
        logger.debug(f"[STREAMING] Failed to emit stream_end: {e}")

    # Reset for next stream
    _streaming_context.stream_id = None
    _streaming_context.sequence = 0


def _wrap_streaming_response(response: Any) -> tuple:
    """
    Wrap a streaming response to emit chunks while iterating.

    Returns (aggregated_content, original_response_with_content)
    """
    # Check if this is a streaming response (has iterator)
    if not hasattr(response, '__iter__') or isinstance(response, (str, dict)):
        return response

    # Check if it's an OpenAI streaming response
    try:
        # OpenAI streaming responses have 'choices' in each chunk
        chunks = []
        full_content = ""

        for chunk in response:
            chunks.append(chunk)

            # Extract delta content from OpenAI chunk
            delta_content = ""
            try:
                if hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content:
                        delta_content = delta.content
            except (IndexError, AttributeError):
                pass

            if delta_content:
                full_content += delta_content
                _emit_chunk(delta_content)

        # Emit stream_end
        if full_content:
            _emit_stream_end(full_content)

        # Return a mock response object that has the aggregated content
        return _create_aggregated_response(chunks, full_content)

    except Exception as e:
        logger.debug(f"[STREAMING] Failed to wrap streaming response: {e}")
        return response


def _create_aggregated_response(chunks: list, full_content: str) -> Any:
    """Create an aggregated response from streaming chunks."""
    # Try to reconstruct a response that AG2 can process
    if not chunks:
        return None

    # Use the last chunk as a template and fill in the content
    last_chunk = chunks[-1]

    try:
        # For OpenAI responses, we need to create a non-streaming response format
        from types import SimpleNamespace

        # Create a mock response that looks like a non-streaming response
        mock_choice = SimpleNamespace()
        mock_choice.message = SimpleNamespace()
        mock_choice.message.content = full_content
        mock_choice.message.role = "assistant"
        mock_choice.message.function_call = None
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_choice.index = 0

        mock_response = SimpleNamespace()
        mock_response.choices = [mock_choice]
        mock_response.id = getattr(last_chunk, 'id', 'stream-response')
        mock_response.model = getattr(last_chunk, 'model', 'unknown')
        mock_response.object = "chat.completion"
        mock_response.created = getattr(last_chunk, 'created', 0)
        mock_response.usage = None  # Streaming doesn't provide usage

        return mock_response

    except Exception as e:
        logger.debug(f"[STREAMING] Failed to create aggregated response: {e}")
        # Return a simple dict as fallback
        return {"content": full_content, "role": "assistant"}


# Store original create method
_original_create = None


def _patched_create(self, *args, **kwargs):
    """Patched create method that handles streaming."""
    global _original_create

    # Check if streaming context is set
    transport = getattr(_streaming_context, 'transport', None)

    if not transport:
        # No streaming context, use original
        return _original_create(self, *args, **kwargs)

    # Check if streaming is enabled in config
    config_list = kwargs.get('config_list') or getattr(self, '_config_list', [])
    stream_enabled = any(
        c.get('stream', False) for c in config_list if isinstance(c, dict)
    )

    # Also check llm_config
    if not stream_enabled:
        stream_enabled = kwargs.get('stream', False)

    if not stream_enabled:
        return _original_create(self, *args, **kwargs)

    # Call original with streaming
    response = _original_create(self, *args, **kwargs)

    # Wrap the response to emit chunks
    return _wrap_streaming_response(response)


def install_streaming_wrapper(
    transport: "SimpleTransport",
    chat_id: str,
    agent_name: str = "Agent",
) -> None:
    """
    Install the streaming wrapper on OpenAIWrapper.

    This patches OpenAIWrapper.create to intercept streaming responses
    and emit chunks to the WebSocket transport.

    Args:
        transport: SimpleTransport instance
        chat_id: Chat session ID
        agent_name: Initial agent name
    """
    global _original_create

    try:
        from autogen.oai.client import OpenAIWrapper

        # Only patch once
        if _original_create is None:
            _original_create = OpenAIWrapper.create
            OpenAIWrapper.create = _patched_create
            logger.info("[STREAMING] Installed OpenAI streaming wrapper")

        # Set context for this chat
        set_streaming_context(transport, chat_id, agent_name)
        logger.info(f"[STREAMING] Set streaming context for chat {chat_id}")

    except Exception as e:
        logger.warning(f"[STREAMING] Failed to install streaming wrapper: {e}")


def uninstall_streaming_wrapper() -> None:
    """Uninstall the streaming wrapper and restore original behavior."""
    global _original_create

    try:
        if _original_create is not None:
            from autogen.oai.client import OpenAIWrapper
            OpenAIWrapper.create = _original_create
            _original_create = None
            logger.info("[STREAMING] Uninstalled OpenAI streaming wrapper")

        clear_streaming_context()

    except Exception as e:
        logger.warning(f"[STREAMING] Failed to uninstall streaming wrapper: {e}")
