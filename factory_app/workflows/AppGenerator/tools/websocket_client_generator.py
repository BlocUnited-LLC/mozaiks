"""Tool for generating WebSocket client code based on AgentGenerator's websocket_config.

This tool reads the websocket_config (from context_variables) and generates
client-side code to connect to the generated agent's WebSocket endpoints.
"""

from __future__ import annotations

from typing import Any

from logs.logging_config import get_workflow_logger


def _generate_python_ws_client(config: dict[str, Any]) -> str:
    """Generate Python WebSocket client code."""
    workflow_id = config.get("workflow_id", "workflow")
    endpoints = config.get("endpoints", {})
    settings = config.get("connection_settings", {})
    
    primary = endpoints.get("primary", {})
    chat = endpoints.get("chat", {})
    
    code = f'''"""WebSocket client for {workflow_id} workflow.

Auto-generated from websocket_config.yaml
"""

import asyncio
import json
from typing import Any, Callable, Optional

import websockets


class {workflow_id.title().replace("-", "")}WebSocketClient:
    """Client for connecting to the {workflow_id} agent workflow."""
    
    def __init__(
        self,
        base_url: str,
        auth_token: str,
        on_message: Optional[Callable[[dict], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.on_message = on_message or self._default_message_handler
        self.on_error = on_error or self._default_error_handler
        self._ws = None
        self._heartbeat_interval = {settings.get("heartbeat_interval_ms", 30000)} / 1000
        self._reconnect_attempts = {settings.get("reconnect_attempts", 3)}
        
    @property
    def primary_endpoint(self) -> str:
        return f"{{self.base_url}}{primary.get('path', '/ws/' + workflow_id)}"
    
    @property
    def chat_endpoint(self) -> str:
        return f"{{self.base_url}}{chat.get('path', '/ws/' + workflow_id + '/chat')}"
    
    async def connect(self, endpoint: str = "chat") -> None:
        """Connect to the specified WebSocket endpoint."""
        url = self.chat_endpoint if endpoint == "chat" else self.primary_endpoint
        headers = {{"Authorization": f"Bearer {{self.auth_token}}"}}
        
        self._ws = await websockets.connect(url, extra_headers=headers)
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._receive_loop())
    
    async def send_message(self, content: str, message_type: str = "user_message") -> None:
        """Send a message to the agent."""
        if not self._ws:
            raise RuntimeError("Not connected. Call connect() first.")
        
        payload = {{
            "type": message_type,
            "content": content,
        }}
        await self._ws.send(json.dumps(payload))
    
    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep connection alive."""
        while self._ws:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._ws:
                    await self._ws.ping()
            except Exception:
                break
    
    async def _receive_loop(self) -> None:
        """Receive and process messages from the server."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    self.on_message(data)
                except json.JSONDecodeError:
                    self.on_message({{"raw": message}})
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            self.on_error(e)
    
    @staticmethod
    def _default_message_handler(data: dict) -> None:
        print(f"[WS] Received: {{data}}")
    
    @staticmethod
    def _default_error_handler(error: Exception) -> None:
        print(f"[WS] Error: {{error}}")


# Usage example:
# client = {workflow_id.title().replace("-", "")}WebSocketClient(
#     base_url="wss://your-agent-host.com",
#     auth_token="your-jwt-token",
# )
# await client.connect()
# await client.send_message("Hello, agent!")
'''
    return code


def _generate_typescript_ws_client(config: dict[str, Any]) -> str:
    """Generate TypeScript/JavaScript WebSocket client code."""
    workflow_id = config.get("workflow_id", "workflow")
    endpoints = config.get("endpoints", {})
    settings = config.get("connection_settings", {})
    
    primary = endpoints.get("primary", {})
    chat = endpoints.get("chat", {})
    events = endpoints.get("events", {})
    
    class_name = "".join(word.title() for word in workflow_id.replace("-", "_").split("_"))
    
    code = f'''/**
 * WebSocket client for {workflow_id} workflow.
 * Auto-generated from websocket_config.yaml
 */

export interface WebSocketMessage {{
  type: string;
  content?: string;
  data?: unknown;
  error?: string;
}}

export type MessageHandler = (message: WebSocketMessage) => void;
export type ErrorHandler = (error: Event | Error) => void;

export class {class_name}WebSocketClient {{
  private baseUrl: string;
  private authToken: string;
  private ws: WebSocket | null = null;
  private heartbeatInterval: number = {settings.get("heartbeat_interval_ms", 30000)};
  private reconnectAttempts: number = {settings.get("reconnect_attempts", 3)};
  private heartbeatTimer: number | null = null;
  
  public onMessage: MessageHandler = (msg) => console.log('[WS] Received:', msg);
  public onError: ErrorHandler = (err) => console.error('[WS] Error:', err);
  public onOpen: () => void = () => console.log('[WS] Connected');
  public onClose: () => void = () => console.log('[WS] Disconnected');

  constructor(baseUrl: string, authToken: string) {{
    this.baseUrl = baseUrl.replace(/\\/$/, '');
    this.authToken = authToken;
  }}

  get primaryEndpoint(): string {{
    return `${{this.baseUrl}}{primary.get('path', '/ws/' + workflow_id)}`;
  }}

  get chatEndpoint(): string {{
    return `${{this.baseUrl}}{chat.get('path', '/ws/' + workflow_id + '/chat')}`;
  }}

  get eventsEndpoint(): string {{
    return `${{this.baseUrl}}{events.get('path', '/ws/' + workflow_id + '/events')}`;
  }}

  connect(endpoint: 'primary' | 'chat' | 'events' = 'chat'): void {{
    const url = endpoint === 'chat' 
      ? this.chatEndpoint 
      : endpoint === 'events'
        ? this.eventsEndpoint
        : this.primaryEndpoint;
    
    // Note: Auth token typically passed via query param for browser WebSocket
    const wsUrl = `${{url}}?token=${{encodeURIComponent(this.authToken)}}`;
    
    this.ws = new WebSocket(wsUrl);
    
    this.ws.onopen = () => {{
      this.onOpen();
      this.startHeartbeat();
    }};
    
    this.ws.onmessage = (event) => {{
      try {{
        const data = JSON.parse(event.data) as WebSocketMessage;
        this.onMessage(data);
      }} catch {{
        this.onMessage({{ type: 'raw', content: event.data }});
      }}
    }};
    
    this.ws.onerror = (event) => {{
      this.onError(event);
    }};
    
    this.ws.onclose = () => {{
      this.stopHeartbeat();
      this.onClose();
    }};
  }}

  sendMessage(content: string, type: string = 'user_message'): void {{
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {{
      throw new Error('Not connected. Call connect() first.');
    }}
    
    const payload: WebSocketMessage = {{
      type,
      content,
    }};
    
    this.ws.send(JSON.stringify(payload));
  }}

  close(): void {{
    this.stopHeartbeat();
    if (this.ws) {{
      this.ws.close();
      this.ws = null;
    }}
  }}

  private startHeartbeat(): void {{
    this.heartbeatTimer = window.setInterval(() => {{
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {{
        this.ws.send(JSON.stringify({{ type: 'ping' }}));
      }}
    }}, this.heartbeatInterval);
  }}

  private stopHeartbeat(): void {{
    if (this.heartbeatTimer) {{
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }}
  }}
}}

// Usage example:
// const client = new {class_name}WebSocketClient(
//   'wss://your-agent-host.com',
//   'your-jwt-token'
// );
// client.onMessage = (msg) => console.log('Agent says:', msg);
// client.connect();
// client.sendMessage('Hello, agent!');
'''
    return code


async def generate_websocket_client(
    *,
    app_id: str,
    language: str = "python",
    websocket_config: dict[str, Any] | None = None,
    context_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate WebSocket client code from websocket_config.
    
    Args:
        app_id: The application ID
        language: Target language ("python" or "typescript")
        websocket_config: WebSocket config from context (preferred)
        context_variables: Full context (fallback to extract websocket_config)
    
    Returns:
        Dict with success status and generated code
    """
    wf_logger = get_workflow_logger(workflow_name="AppGenerator", app_id=app_id)
    
    # Get websocket_config from context if not provided directly
    config = websocket_config
    if not config and context_variables:
        config = context_variables.get("websocket_config")
    
    if not config:
        wf_logger.warning("[WS_CLIENT] No websocket_config available - cannot generate client")
        return {
            "success": False,
            "error": "No websocket_config found. Run AgentGenerator export first.",
            "code": None,
            "language": language,
        }
    
    try:
        if language.lower() in ("python", "py"):
            code = _generate_python_ws_client(config)
            filename = "ws_client.py"
        elif language.lower() in ("typescript", "ts", "javascript", "js"):
            code = _generate_typescript_ws_client(config)
            filename = "ws_client.ts"
        else:
            return {
                "success": False,
                "error": f"Unsupported language: {language}. Use 'python' or 'typescript'.",
                "code": None,
                "language": language,
            }
        
        wf_logger.info("[WS_CLIENT] Generated %s WebSocket client (%d chars)", language, len(code))
        
        return {
            "success": True,
            "code": code,
            "filename": filename,
            "language": language,
            "workflow_id": config.get("workflow_id"),
            "endpoints": list(config.get("endpoints", {}).keys()),
        }
        
    except Exception as e:
        wf_logger.error("[WS_CLIENT] Failed to generate client: %s", e)
        return {
            "success": False,
            "error": str(e),
            "code": None,
            "language": language,
        }


__all__ = ["generate_websocket_client"]
