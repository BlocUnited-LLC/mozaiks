# API Reference

**Document Type:** Reference  
**Last Updated:** October 2025  
**Intended Audience:** Frontend developers, API consumers, integration engineers

---

## Purpose

This document provides a complete reference for all HTTP and WebSocket endpoints exposed by the MozaiksAI FastAPI runtime (`shared_app.py`).

---

## Table of Contents

1. [Base URL & CORS](#base-url--cors)
2. [Health & Metrics](#health--metrics)
3. [Chat Session Management](#chat-session-management)
4. [WebSocket Communication](#websocket-communication)
5. [Workflow Discovery](#workflow-discovery)
6. [Token Management](#token-management)
7. [UI Tools & Components](#ui-tools--components)
8. [Theme Management](#theme-management)
9. [Error Responses](#error-responses)

---

## Base URL & CORS

**Development:** `http://localhost:8000`  
**Production:** Configure via reverse proxy (nginx/Caddy)

**CORS Configuration:**
```python
# Allows all origins in development
origins = ["*"]
```

---

## Health & Metrics

### GET /api/health

Simple health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2025-10-02T14:30:15.123Z"
}
```
**Status Codes:**
- `200`: Service healthy
- `500`: Service unhealthy

**Example:**
```bash
curl http://localhost:8000/api/health
```

---

### GET /health/active-runs
  "active_chats": 5,
  "total_agent_turns": 127,
  "total_tool_calls": 43,
  "total_tokens": 125000,
  "total_cost_usd": 1.875,
  "avg_turn_duration_sec": 2.3
}
```

**Example:**
```bash
curl http://localhost:8000/metrics/perf/aggregate
```

---

### GET /metrics/perf/chats

Get performance snapshots for all active chats.

**Response:**
```json
{
  "chats": [
    {
      "chat_id": "550e8400-e29b-41d4-a716-446655440000",
      "app_id": "507f1f77bcf86cd799439011",
      "workflow_name": "Generator",
      "agent_turns": 12,
      "tool_calls": 5,
      "tokens": 15000,
      "cost_usd": 0.225
    }
  ]
}
```

---

### GET /metrics/perf/chats/{chat_id}

Get performance metrics for a specific chat.

**Path Parameters:**
- `chat_id` (string): Chat session identifier

**Response:**
```json
{
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_turns": 12,
  "tool_calls": 5,
  "tokens": 15000,
  "cost_usd": 0.225,
  "started_at": "2025-10-02T14:00:00.000Z"
}
```

**Status Codes:**
- `200`: Metrics found
- `404`: Chat not found

---

### GET /metrics/prometheus

Prometheus-compatible metrics endpoint.

**Response (Plain Text):**
```
# HELP mozaiksai_active_chats Number of active chat sessions
# TYPE mozaiksai_active_chats gauge
mozaiksai_active_chats 5

# HELP mozaiksai_total_tokens Total tokens processed
# TYPE mozaiksai_total_tokens counter
mozaiksai_total_tokens 125000
```

**Example:**
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'mozaiksai'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics/prometheus'
```

---

## Chat Session Management

### POST /api/chats/{app_id}/{workflow_name}/start

Start a new chat session or reuse recent in-progress session (idempotent).

**Path Parameters:**
- `app_id` (string): App identifier (ObjectId or string)
- `workflow_name` (string): Workflow to execute (e.g., `"Generator"`)

**Request Body:**
```json
{
  "user_id": "user_12345",
  "required_min_tokens": 1000,
  "client_request_id": "req_abc123",
  "force_new": false
}
```

**Request Fields:**
- `user_id` (string, required): User identifier
- `required_min_tokens` (int, optional): Minimum tokens required to start (enforced if > 0)
- `client_request_id` (string, optional): Client-side request ID for deduplication
- `force_new` (boolean, optional): Force new session creation (skip reuse), default `false`

**Response:**
```json
{
  "success": true,
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_name": "Generator",
  "app_id": "507f1f77bcf86cd799439011",
  "user_id": "user_12345",
  "remaining_balance": 50000,
  "websocket_url": "/ws/Generator/507f1f77bcf86cd799439011/550e8400-e29b-41d4-a716-446655440000/user_12345",
  "message": "Chat session initialized; connect to websocket to start.",
  "reused": false,
  "cache_seed": 1234567890
}
```

**Idempotency Behavior:**
- If in-progress session for `(app_id, user_id, workflow_name)` created within last 15 seconds (configurable via `CHAT_START_IDEMPOTENCY_SEC`), returns existing `chat_id`
- Set `force_new=true` to always create new session

**Status Codes:**
- `200`: Session started or reused
- `400`: Missing `user_id`
- `402`: Insufficient tokens (if `required_min_tokens` check fails)
- `500`: Server error

**Example:**
```bash
curl -X POST http://localhost:8000/api/chats/507f1f77bcf86cd799439011/Generator/start \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_12345"}'
```

---

### GET /api/chats/{app_id}/{workflow_name}

List recent chat sessions for app and workflow.

**Path Parameters:**
- `app_id` (string): App identifier
- `workflow_name` (string): Workflow name

**Response:**
```json
{
  "chat_ids": [
    "550e8400-e29b-41d4-a716-446655440000",
    "660e8400-e29b-41d4-a716-446655440001",
    "770e8400-e29b-41d4-a716-446655440002"
  ]
}
```

**Returns:** Up to 20 most recent chat IDs (sorted by `created_at` descending)

---

### GET /api/chats/exists/{app_id}/{workflow_name}/{chat_id}

Check if chat session exists (lightweight existence check).

**Path Parameters:**
- `app_id` (string): App identifier
- `workflow_name` (string): Workflow name
- `chat_id` (string): Chat session identifier

**Response:**
```json
{
  "exists": true
}
```

**Use Case:** Frontend uses this to decide whether to clear cached artifact UI state before restoration.

---

### GET /api/chats/meta/{app_id}/{workflow_name}/{chat_id}

Get chat metadata including cache_seed and last_artifact (no transcript).

**Path Parameters:**
- `app_id` (string): App identifier
- `workflow_name` (string): Workflow name
- `chat_id` (string): Chat session identifier

**Response:**
```json
{
  "_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_name": "Generator",
  "cache_seed": 1234567890,
  "last_artifact": {
    "ui_tool_id": "generate_and_download",
    "event_id": "evt_987654",
    "display": "artifact",
    "payload": {
      "filename": "app.zip",
      "url": "https://cdn.example.com/files/app.zip"
    },
    "updated_at": "2025-10-02T14:30:00.000Z"
  }
}
```

**Use Case:** Second user/browser can restore artifact UI state even if localStorage is empty.

**Status Codes:**
- `200`: Metadata found
- `404`: Chat not found

---

## WebSocket Communication

### WS /ws/{workflow_name}/{app_id}/{chat_id}/{user_id}

Bidirectional WebSocket for chat communication.

**Path Parameters:**
- `workflow_name` (string): Workflow name (e.g., `"Generator"`)
- `app_id` (string): App identifier
- `chat_id` (string): Chat session identifier
- `user_id` (string): User identifier

**Connection:**
```javascript
const ws = new WebSocket(
  'ws://localhost:8000/ws/Generator/507f.../550e.../user_12345'
);
```

**Client → Server Messages:**

**1. Initial Message (Start Workflow):**
```json
{
  "type": "user_message",
  "content": "Create a React todo app with authentication"
}
```

**2. User Input Response:**
```json
{
  "type": "user_input_response",
  "input_request_id": "input_req_123",
  "user_input": "Yes, proceed with deployment"
}
```

**3. UI Tool Response:**
```json
{
  "type": "ui_tool_response",
  "event_id": "evt_987654",
  "response_data": {
    "status": "completed",
    "data": {
      "ui_tool_id": "generate_and_download",
      "result": "downloaded"
    }
  }
}
```

**Server → Client Events:**

**1. Agent Message:**
```json
{
  "type": "chat.message",
  "event_id": "msg_123",
  "chat_id": "550e...",
  "sender": "ArchitectAgent",
  "content": "I'll design a React app with JWT authentication.",
  "timestamp": "2025-10-02T14:30:15.123Z"
}
```

**2. Tool Call:**
```json
{
  "type": "chat.tool_call",
  "event_id": "tool_456",
  "chat_id": "550e...",
  "tool_name": "create_file",
  "arguments": {
    "path": "src/App.js",
    "content": "import React from 'react'..."
  }
}
```

**3. UI Tool Invocation:**
```json
{
  "type": "chat.ui_tool_invoked",
  "event_id": "evt_789",
  "chat_id": "550e...",
  "ui_tool_id": "generate_and_download",
  "display": "artifact",
  "workflow_name": "Generator",
  "payload": {
    "filename": "app.zip",
    "url": "https://cdn.example.com/files/app.zip"
  }
}
```

**4. User Input Request:**
```json
{
  "type": "chat.user_input_request",
  "event_id": "input_req_123",
  "chat_id": "550e...",
  "prompt": "Do you want to deploy to production?",
  "timeout_sec": 300
}
```

**5. Workflow Completed:**
```json
{
  "type": "chat.workflow_completed",
  "event_id": "complete_999",
  "chat_id": "550e...",
  "status": "success",
  "summary": "Generated React app with authentication"
}
```

**6. Error:**
```json
{
  "type": "chat.error",
  "event_id": "err_500",
  "chat_id": "550e...",
  "error": "Tool execution failed",
  "details": "File write permission denied"
}
```

**Connection Lifecycle:**
1. Client connects to WebSocket with chat session parameters
2. Server authenticates and registers connection
3. Client sends initial user message
4. Server orchestrates workflow, streams events to client
5. Client responds to user input/UI tool requests as needed
6. Workflow completes, server sends `chat.workflow_completed`
7. Connection remains open for multi-turn conversations

**Reconnection:**
- Frontend reconnects with same `chat_id` and `last_sequence` to resume
- Server sends message diff (`messages` with `sequence > last_sequence`)

---

## Workflow Discovery

### GET /api/workflows

Get all workflow configurations (alias for `/api/workflows/config`).

**Response:**
```json
{
  "Generator": {
    "name": "Generator",
    "description": "AI-driven startup foundry",
    "agents": ["ArchitectAgent", "CodeGeneratorAgent"],
    "tools": ["create_file", "generate_and_download"],
    "ui_tools": ["generate_and_download"]
  },
  "CustomerSupport": {
    "name": "CustomerSupport",
    "description": "Automated customer support",
    "agents": ["SupportAgent"],
    "tools": ["search_kb", "create_ticket"],
    "ui_tools": []
  }
}
```

---

### GET /api/workflows/config

Get all workflow configurations (same as `/api/workflows`).

---

### GET /api/workflows/{workflow_name}/transport

Get transport information for specific workflow.

**Path Parameters:**
- `workflow_name` (string): Workflow name

**Response:**
```json
{
  "workflow_name": "Generator",
  "transport": "websocket",
  "endpoints": {
    "websocket": "/ws/Generator/{app_id}/{chat_id}/{user_id}",
    "input": "/chat/{app_id}/{chat_id}/{user_id}/input"
  }
}
```

---

### GET /api/workflows/{workflow_name}/tools

Get tools manifest for specific workflow.

**Response:**
```json
{
  "workflow_name": "Generator",
  "tools": [
    {
      "name": "create_file",
      "description": "Create a file in the project",
      "parameters": {
        "path": "string",
        "content": "string"
      }
    },
    {
      "name": "generate_and_download",
      "description": "Generate project and create download link",
      "parameters": {
        "project_spec": "object"
      }
    }
  ]
}
```

---

### GET /api/workflows/{workflow_name}/ui-tools

Get UI tools manifest with component schemas.

**Response:**
```json
{
  "workflow_name": "Generator",
  "ui_tools_count": 1,
  "ui_tools": [
    {
      "ui_tool_id": "generate_and_download",
      "component": "FileDownloadCenter",
      "mode": "artifact",
      "agent": "CodeGeneratorAgent",
      "workflow": "Generator"
    }
  ]
}
```

---

## Token Management

### GET /api/tokens/{user_id}/balance

Get user token balance.

**Path Parameters:**
- `user_id` (string): User identifier

**Query Parameters:**
- `app_id` (string, required): App identifier

**Response:**
```json
{
  "balance": 50000,
  "remaining": 50000,
  "user_id": "user_12345",
  "app_id": "507f1f77bcf86cd799439011"
}
```

**Example:**
```bash
curl "http://localhost:8000/api/tokens/user_12345/balance?app_id=507f1f77bcf86cd799439011"
```

---

### POST /api/tokens/{user_id}/consume

Consume (debit) user tokens.

**Path Parameters:**
- `user_id` (string): User identifier

**Request Body:**
```json
{
  "amount": 2100,
  "app_id": "507f1f77bcf86cd799439011",
  "reason": "manual_consume"
}
```

**Request Fields:**
- `amount` (int, required): Tokens to debit
- `app_id` (string, required): App identifier
- `reason` (string, optional): Debit reason (default: `"manual_consume"`)

**Response:**
```json
{
  "success": true,
  "remaining": 47900
}
```

**Status Codes:**
- `200`: Tokens consumed successfully
- `400`: Missing `app_id`
- `402`: Insufficient tokens
- `500`: Server error

**Example:**
```bash
curl -X POST http://localhost:8000/api/tokens/user_12345/consume \
  -H "Content-Type: application/json" \
  -d '{"amount": 2100, "app_id": "507f1f77bcf86cd799439011", "reason": "api_usage"}'
```

---

## UI Tools & Components

### POST /api/ui-tool/submit

Submit UI tool response from frontend component.

**Request Body:**
```json
{
  "event_id": "evt_987654",
  "response_data": {
    "status": "completed",
    "data": {
      "ui_tool_id": "generate_and_download",
      "result": "downloaded"
    }
  }
}
```

**Request Fields:**
- `event_id` (string, required): Event ID from `chat.ui_tool_invoked`
- `response_data` (object, required): Response data from UI component

**Response:**
```json
{
  "status": "success",
  "message": "UI tool response submitted successfully"
}
```

**Status Codes:**
- `200`: Response submitted
- `400`: Missing required fields or invalid JSON
- `404`: Event not found or already completed
- `503`: Transport not available

---

### POST /api/user-input/submit

Submit user input response (for AG2 human_input_mode).

**Request Body:**
```json
{
  "input_request_id": "input_req_123",
  "user_input": "Yes, proceed with deployment"
}
```

**Request Fields:**
- `input_request_id` (string, required): Request ID from `chat.user_input_request`
- `user_input` (string, required): User's text input

**Response:**
```json
{
  "status": "success",
  "message": "User input submitted successfully"
}
```

**Status Codes:**
- `200`: Input submitted
- `400`: Missing required fields
- `404`: Request not found or already completed

---

### POST /chat/{app_id}/{chat_id}/component_action

Handle component actions (WebSocket-compatible context variables).

**Path Parameters:**
- `app_id` (string): App identifier
- `chat_id` (string): Chat session identifier

**Request Body:**
```json
{
  "component_id": "db_selector",
  "action_type": "value_changed",
  "action_data": {
    "selected_database": "production"
  }
}
```

**Request Fields:**
- `component_id` (string, required): Component identifier
- `action_type` (string, required): Action type (e.g., `"value_changed"`)
- `action_data` (object, optional): Action-specific data

**Response:**
```json
{
  "status": "success",
  "message": "Component action applied",
  "applied": {
    "selected_database": "production"
  },
  "timestamp": "2025-10-02T14:30:15.123Z"
}
```

---

## Theme Management

### GET /api/themes/{app_id}

Get theme configuration for app.

**Path Parameters:**
- `app_id` (string): App identifier

**Response:**
```json
{
  "app_id": "507f1f77bcf86cd799439011",
  "theme": {
    "primary_color": "#1976d2",
    "secondary_color": "#dc004e",
    "logo_url": "https://cdn.example.com/logo.png"
  }
}
```

---

### PUT /api/themes/{app_id}

Update theme configuration for app.

**Path Parameters:**
- `app_id` (string): App identifier

**Request Body:**
```json
{
  "theme": {
    "primary_color": "#1976d2",
    "secondary_color": "#dc004e",
    "logo_url": "https://cdn.example.com/logo.png"
  }
}
```

**Response:**
```json
{
  "app_id": "507f1f77bcf86cd799439011",
  "theme": {
    "primary_color": "#1976d2",
    "secondary_color": "#dc004e",
    "logo_url": "https://cdn.example.com/logo.png"
  }
}
```

---

## Error Responses

All endpoints return consistent error format:

**Error Response:**
```json
{
  "detail": "Error message describing the issue"
}
```

**Common Status Codes:**
- `400`: Bad Request (missing required fields, invalid JSON)
- `402`: Payment Required (insufficient tokens)
- `404`: Not Found (chat/workflow/resource not found)
- `500`: Internal Server Error
- `503`: Service Unavailable (transport not initialized)

**Example Error:**
```json
{
  "detail": "user_id is required"
}
```

---

## Related Documentation

- **[Transport and Streaming](../runtime/transport_and_streaming.md):** WebSocket event flow and message patterns
- **[Event Reference](./event_reference.md):** Complete catalog of `chat.*` events
- **[Persistence and Resume](../runtime/persistence_and_resume.md):** Chat session persistence and resumption
- **[Environment Variables](./environment_variables.md):** Configuration for CORS, ports, and endpoints
- **[Frontend Integration](../frontend/workflow_integration.md):** React ChatUI API consumption patterns

---

**End of API Reference**

For questions or additional endpoints, review `shared_app.py` source code.
