# Mozaiks SDK API Contract

This document defines the HTTP/WebSocket API that mozaiksai exposes for SDK consumption.

## Base URL

```
Production: https://ai.mozaiks.io
Development: http://localhost:8000
```

## Authentication

All requests require a Bearer token in the Authorization header:
```
Authorization: Bearer <jwt_token>
```

For service-to-service calls, use an internal service token.

---

## Core APIs

### 1. Health Check

```http
GET /api/health
```

**Response:**
```json
{
  "status": "ok",
  "version": "5.0.0",
  "workflows_loaded": 4
}
```

---

### 2. List Workflows

```http
GET /api/workflows
```

**Response:**
```json
{
  "workflows": [
    {
      "name": "ValueEngine",
      "display_name": "Value Engine",
      "initial_agent": "ValueInterviewAgent",
      "visual_agents": ["ValueInterviewAgent", "GapAnalysisAgent"],
      "status": "ready"
    }
  ]
}
```

---

### 3. Start Chat Session

```http
POST /api/chats/{app_id}/{workflow_name}/start
Content-Type: application/json
```

**Request:**
```json
{
  "user_id": "user_123",
  "context_variables": {
    "concept_overview": "A task management app for developers",
    "is_child_workflow": false
  }
}
```

**Response:**
```json
{
  "success": true,
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_name": "ValueEngine",
  "app_id": "app_456",
  "websocket_url": "/ws/ValueEngine/app_456/550e8400.../user_123"
}
```

---

### 4. Get Chat Metadata

```http
GET /api/chats/meta/{app_id}/{workflow_name}/{chat_id}
```

**Response:**
```json
{
  "chat_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_name": "ValueEngine",
  "app_id": "app_456",
  "user_id": "user_123",
  "status": 1,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:35:00Z",
  "current_agent": "GapAnalysisAgent",
  "context_variables": {
    "concept_overview": "..."
  }
}
```

---

### 5. List User Sessions

```http
GET /api/sessions/list/{app_id}/{user_id}?workflow_name=ValueEngine&limit=10&offset=0
```

**Response:**
```json
{
  "sessions": [
    {
      "chat_id": "...",
      "workflow_name": "ValueEngine",
      "status": 1,
      "created_at": "...",
      "last_message_preview": "Tell me about your app idea..."
    }
  ],
  "total": 5
}
```

---

### 6. Submit UI Tool Response (WebSocket)

Response-required interactions are delivered on the chat stream as
`chat.tool_call` envelopes with `awaiting_response=true`. Clients answer them by
posting a `tool_call_response` message on the active websocket connection.

**Request:**
```json
{
  "type": "tool_call_response",
  "event_id": "evt_123",
  "component_id": "comp_456",
  "response_data": {
    "value": "I want to build a task management app"
  }
}
```

---

### 7. Upload File

```http
POST /api/chat/upload/{app_id}/{user_id}
Content-Type: multipart/form-data
```

**Form Fields:**
- `file`: The file to upload
- `chat_id`: (optional) Associate with existing chat

**Response:**
```json
{
  "success": true,
  "attachment_id": "att_789",
  "filename": "requirements.pdf",
  "content_type": "application/pdf",
  "size_bytes": 102400
}
```

---

### 8. Trigger Workflow (Backend-to-Backend)

```http
POST /api/workflows/{workflow_name}/trigger
Content-Type: application/json
Authorization: Bearer <service_token>
```

**Request:**
```json
{
  "user_id": "user_123",
  "app_id": "app_456",
  "context": {
    "concept_overview": "...",
    "is_child_workflow": true
  },
  "webhook_url": "https://your-service.com/webhook/workflow-complete"
}
```

**Response:**
```json
{
  "success": true,
  "chat_id": "...",
  "run_id": "run_abc123"
}
```

---

## WebSocket API

### Connect to Chat

```
WS /ws/{workflow_name}/{app_id}/{chat_id}/{user_id}
```

**Authentication:**
- Token in query param: `?token=<jwt>`
- Or via initial message

### Client → Server Messages

```json
// User message
{
  "type": "user_message",
  "content": "I want to build a task management app"
}

// Component action (e.g., button click)
{
  "type": "component_action",
  "component": "ConceptBlueprint",
  "action": "approve",
  "payload": { "approved": true }
}

// Ping (keepalive)
{
  "type": "ping"
}
```

### Server → Client Messages

```json
// Agent text (streaming)
{
  "type": "agent_text",
  "agent": "ValueInterviewAgent",
  "content": "Great! Let me ask you a few questions...",
  "is_final": false
}

// Agent text complete
{
  "type": "agent_text",
  "agent": "ValueInterviewAgent",
  "content": "Great! Let me ask you a few questions about your app idea.",
  "is_final": true
}

// Tool call started
{
  "type": "tool_call",
  "tool": "save_value_manifest",
  "agent": "GapAnalysisAgent",
  "status": "started"
}

// Tool result (UI artifact)
{
  "type": "tool_result",
  "tool": "save_value_manifest",
  "component": "ConceptBlueprint",
  "display_type": "artifact",
  "payload": {
    "title": "Concept Blueprint: TaskFlow",
    "blueprint": { ... }
  }
}

// Workflow status change
{
  "type": "workflow_status",
  "status": "completed",
  "current_agent": null
}

// Error
{
  "type": "error",
  "code": "WORKFLOW_ERROR",
  "message": "Agent failed to respond"
}

// Pong (keepalive response)
{
  "type": "pong"
}
```

---

## Event Subscription (SSE)

For backend services that want to react to workflow events:

```http
GET /api/events/subscribe?app_id=app_456&events=artifact_generated,workflow_complete
Accept: text/event-stream
```

**Event Stream:**
```
event: artifact_generated
data: {"chat_id": "...", "artifact_type": "ConceptBlueprint", "payload": {...}}

event: workflow_complete
data: {"chat_id": "...", "workflow_name": "ValueEngine", "final_context": {...}}
```

---

## Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "WORKFLOW_NOT_FOUND",
    "message": "Workflow 'InvalidWorkflow' does not exist",
    "details": null
  }
}
```

**Common Error Codes:**
- `UNAUTHORIZED` - Invalid or missing token
- `FORBIDDEN` - Token valid but insufficient permissions
- `WORKFLOW_NOT_FOUND` - Workflow doesn't exist
- `CHAT_NOT_FOUND` - Chat session doesn't exist
- `INVALID_REQUEST` - Malformed request body
- `WORKFLOW_ERROR` - Internal workflow execution error

---

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| Chat start | 10/min per user |
| User input | 60/min per chat |
| File upload | 5/min per user |
| Trigger | 100/min per service |

---

## SDK Method Mapping

| SDK Method | HTTP Endpoint |
|------------|---------------|
| `client.HealthAsync()` | `GET /api/health` |
| `client.ListWorkflowsAsync()` | `GET /api/workflows` |
| `client.StartChatAsync()` | `POST /api/chats/{app_id}/{workflow}/start` |
| `client.GetChatAsync()` | `GET /api/chats/meta/{app_id}/{workflow}/{chat_id}` |
| `client.ListSessionsAsync()` | `GET /api/sessions/list/{app_id}/{user_id}` |
| `client.SendMessageAsync()` | `WS tool_call_response` |
| `client.UploadFileAsync()` | `POST /api/chat/upload/{app_id}/{user_id}` |
| `client.TriggerWorkflowAsync()` | `POST /api/workflows/{workflow}/trigger` |
| `client.ConnectChatAsync()` | `WS /ws/{workflow}/{app_id}/{chat_id}/{user_id}` |
| `client.SubscribeEventsAsync()` | `GET /api/events/subscribe` (SSE) |
