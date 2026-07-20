from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Integration catalog
# Canonical source of truth for the runtime. Keep in sync with
# factory_app/build_context/integrations/catalog.yaml (which the factory agent reads).
# ---------------------------------------------------------------------------

INTEGRATIONS_CATALOG: list[dict[str, Any]] = [
    {
        "id": "openai",
        "name": "OpenAI",
        "category": "ai",
        "description": "GPT models, embeddings, and assistants API.",
        "required_secrets": ["OPENAI_API_KEY"],
        "optional_secrets": ["OPENAI_ORG_ID"],
        "setup_steps": [
            "Sign in to platform.openai.com → API Keys",
            "Create a new secret key and copy it immediately (shown once)",
        ],
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "category": "ai",
        "description": "Claude models for reasoning, analysis, and generation.",
        "required_secrets": ["ANTHROPIC_API_KEY"],
        "optional_secrets": [],
        "setup_steps": [
            "Sign in to console.anthropic.com → API Keys",
            "Create a new key and copy it immediately (shown once)",
        ],
    },
    {
        "id": "mozaikspay",
        "name": "Mozaiks Pay",
        "category": "payments",
        "description": "Managed subscription billing, token billing, billing portal, and usage display for Mozaiks apps.",
        "required_secrets": ["MOZAIKSPAY_API_BASE", "MOZAIKSPAY_API_KEY"],
        "optional_secrets": ["MOZAIKS_APP_URL", "MOZAIKSPAY_CLIENT_ID", "MOZAIKSPAY_CLIENT_SECRET"],
        "default_for": ["subscription_or_usage_app"],
        "removable_default": True,
        "capabilities": ["subscriptions", "usage_billing", "billing_portal"],
        "setup_steps": [
            "Open the workspace Integrations page and select Mozaiks Pay",
            "Enter the Mozaiks Pay API base URL and app-scoped API key",
            "Save the connector before launching paid plans or billing portal pages",
        ],
    },
    {
        "id": "resend",
        "name": "Resend",
        "category": "email",
        "description": "Transactional email delivery with high deliverability.",
        "required_secrets": ["RESEND_API_KEY"],
        "optional_secrets": ["RESEND_FROM_EMAIL"],
        "setup_steps": [
            "Sign in to resend.com → API Keys",
            "Create an API key with Send access",
            "Verify your sending domain under Domains",
        ],
    },
    {
        "id": "twilio",
        "name": "Twilio",
        "category": "sms",
        "description": "SMS, voice, and WhatsApp messaging.",
        "required_secrets": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"],
        "optional_secrets": [],
        "setup_steps": [
            "Sign in to Twilio Console → Account → API keys & tokens",
            "Copy the Account SID and Auth Token",
            "Purchase or verify a phone number to use as the sender",
        ],
    },
    {
        "id": "slack",
        "name": "Slack",
        "category": "notifications",
        "description": "Team notifications, alerts, and workflow triggers via Slack.",
        "required_secrets": ["SLACK_BOT_TOKEN"],
        "optional_secrets": ["SLACK_WEBHOOK_URL", "SLACK_DEFAULT_CHANNEL"],
        "setup_steps": [
            "Go to api.slack.com/apps → Create New App",
            "Add OAuth scopes: chat:write, channels:read",
            "Install the app to your workspace and copy the Bot User OAuth Token",
        ],
    },
    {
        "id": "github",
        "name": "GitHub",
        "category": "source_control",
        "description": "Repository access, webhooks, and CI/CD triggers.",
        "required_secrets": ["GITHUB_TOKEN"],
        "optional_secrets": ["GITHUB_ORG", "GITHUB_WEBHOOK_SECRET"],
        "setup_steps": [
            "Go to GitHub → Settings → Developer settings → Personal access tokens",
            "Generate a token with repo and workflow scopes",
            "For org-level access, use a fine-grained token scoped to the org",
        ],
    },
    {
        "id": "s3",
        "name": "AWS S3",
        "category": "storage",
        "description": "Object storage for files, images, and generated artifacts.",
        "required_secrets": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_BUCKET"],
        "optional_secrets": ["AWS_REGION", "AWS_S3_ENDPOINT_URL"],
        "setup_steps": [
            "In AWS IAM, create a user with a scoped S3 policy",
            "Generate Access Key ID and Secret Access Key under Security Credentials",
            "Create or identify the S3 bucket this app will use",
        ],
    },
    {
        "id": "postgres",
        "name": "PostgreSQL",
        "category": "database",
        "description": "Relational database for structured app data.",
        "required_secrets": ["POSTGRES_URL"],
        "optional_secrets": ["POSTGRES_POOL_SIZE"],
        "setup_steps": [
            "Provision a PostgreSQL instance (Supabase, Neon, RDS, or self-hosted)",
            "Copy the connection string: postgresql://user:pass@host:port/dbname",
        ],
    },
    {
        "id": "redis",
        "name": "Redis",
        "category": "cache",
        "description": "In-memory cache, session store, and pub/sub broker.",
        "required_secrets": ["REDIS_URL"],
        "optional_secrets": [],
        "setup_steps": [
            "Provision a Redis instance (Upstash, Redis Cloud, or self-hosted)",
            "Copy the connection URL: redis://user:pass@host:port",
        ],
    },
    {
        "id": "google_oauth",
        "name": "Google OAuth",
        "category": "auth",
        "description": "Sign in with Google for user authentication.",
        "required_secrets": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "optional_secrets": ["GOOGLE_REDIRECT_URI"],
        "setup_steps": [
            "Go to Google Cloud Console → APIs & Services → Credentials",
            "Create an OAuth 2.0 Client ID (Web application type)",
            "Add your app's redirect URI to the Authorized redirect URIs list",
        ],
    },
    {
        "id": "segment",
        "name": "Segment",
        "category": "analytics",
        "description": "Customer data platform for event tracking and analytics routing.",
        "required_secrets": ["SEGMENT_WRITE_KEY"],
        "optional_secrets": [],
        "setup_steps": [
            "Sign in to Segment → Sources → Add Source → Node.js",
            "Copy the Write Key from the source settings",
        ],
    },
]

CATALOG_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in INTEGRATIONS_CATALOG}


# ---------------------------------------------------------------------------
# Typed document shapes
# ---------------------------------------------------------------------------

def build_integration_response(
    spec: dict[str, Any],
    status: str,
    missing_secrets: list[str],
    note: str | None = None,
) -> dict[str, Any]:
    """Build the public-facing integration dict. Never exposes secret values."""
    response: dict[str, Any] = {
        "id": spec["id"],
        "name": spec["name"],
        "category": spec["category"],
        "description": spec["description"],
        "status": status,
        "secrets": [
            {"name": s, "present": s not in missing_secrets}
            for s in spec["required_secrets"]
        ],
        "optional_secrets": spec.get("optional_secrets", []),
        "setup_steps": spec.get("setup_steps", []),
        "note": note,
    }
    for metadata_key in ("default_for", "removable_default", "capabilities"):
        if metadata_key in spec:
            response[metadata_key] = spec[metadata_key]
    if status == "missing":
        response["setup_url"] = f"/integrations/{spec['id']}"
    return response


def build_note_document(
    *,
    integration_id: str,
    note: str,
    updated_by: str,
) -> dict[str, Any]:
    return {
        "integration_id": integration_id,
        "note": note,
        "updated_by": updated_by,
    }


# ---------------------------------------------------------------------------
# Integration declaration shapes
# Persisted by the factory after IntegrationReadinessAgent resolves needs.
# ---------------------------------------------------------------------------

DECLARATIONS_ENTITY = "integration_declarations"


def build_declaration_document(
    *,
    app_id: str,
    service: str,
    catalog_id: str | None = None,
    display_name: str | None = None,
    kind: str = "api_key",
    purpose: str | None = None,
    required_at: str = "runtime",
    optional: bool = False,
    workspace_status: str | None = None,
    connector_status: str = "not_configured",
    defaulted: bool = False,
    removable: bool = False,
    source: str = "build",
    required_fields: list[dict[str, Any]] | None = None,
    removed: bool = False,
    declared_at: str,
) -> dict[str, Any]:
    """Build a declaration document for a single integration need from a build.

    Workspace status is the catalog-derived env-var presence check.
    Connector status reflects the per-app vault inventory at build time.
    Neither field stores secret values.
    """
    return {
        "app_id": app_id,
        "service": service,
        "catalog_id": catalog_id,
        "display_name": display_name or service,
        "kind": kind,
        "purpose": purpose,
        "required_at": required_at,
        "optional": optional,
        "workspace_status": workspace_status,
        "connector_status": connector_status,
        "defaulted": defaulted,
        "removable": removable,
        "source": source,
        "required_fields": list(required_fields or []),
        "removed": removed,
        "declared_at": declared_at,
    }


def build_declaration_response(doc: dict[str, Any]) -> dict[str, Any]:
    """Public-facing shape for a declaration — never includes secret values."""
    return {
        "app_id": doc.get("app_id"),
        "service": doc.get("service"),
        "catalog_id": doc.get("catalog_id"),
        "display_name": doc.get("display_name"),
        "kind": doc.get("kind"),
        "purpose": doc.get("purpose"),
        "required_at": doc.get("required_at"),
        "optional": bool(doc.get("optional")),
        "workspace_status": doc.get("workspace_status"),
        "connector_status": doc.get("connector_status"),
        "defaulted": bool(doc.get("defaulted")),
        "removable": bool(doc.get("removable")),
        "source": doc.get("source"),
        "required_fields": doc.get("required_fields") if isinstance(doc.get("required_fields"), list) else [],
        "declared_at": doc.get("declared_at"),
        "setup_url": f"/integrations/{doc['catalog_id']}" if doc.get("catalog_id") and doc.get("workspace_status") == "missing" else None,
    }
