"""
Send email from the signed-in user's own Gmail address.
DO NOT EDIT: regenerated on framework upgrades. Put app-specific changes in handler.py.

Every message is sent as the requesting user with their own grant (see
mozaiksai.core.connected_accounts), and only on that user's own request: an AI
workflow may draft a message, the user sends it. Nothing is stored here, and no
token ever appears in a result, an error or a log line.
"""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from typing import Any

from mozaiksai.core.connected_accounts import account_request, google_api_base_url
from mozaiksai.core.runtime.composition.module_executor import ModuleInputValidationError

CONNECTION_ID = "gmail_send"
MAX_RECIPIENTS = 20
MAX_ADDRESS_LENGTH = 254
MAX_SUBJECT_LENGTH = 250
MAX_BODY_LENGTH = 50000
_SEND_PATH = "/gmail/v1/users/me/messages/send"
_ADDRESS_RE = re.compile(r"^[^@\s<>()\[\],;:\"\\]+@[^@\s<>()\[\],;:\"\\]+\.[^@\s<>()\[\],;:\"\\]+$")
# The user's own request in the app. Workflow dispatch (an AI agent acting in the
# user's session) is refused, so mail never leaves without the user's own click.
_SENDING_DISPATCH_KINDS = frozenset({"authenticated_user", "local_development"})


class GmailError(RuntimeError):
    """Gmail refused or failed the request."""


def _addresses(values: list[str] | None, field: str) -> list[str]:
    addresses: list[str] = []
    if len(values or []) > MAX_RECIPIENTS:
        raise ModuleInputValidationError(f"at most {MAX_RECIPIENTS} recipients per message")
    for value in values or []:
        address = str(value or "").strip()
        if len(address) > MAX_ADDRESS_LENGTH or not _ADDRESS_RE.fullmatch(address):
            raise ModuleInputValidationError(f"{field} must contain email addresses only")
        if address.lower() not in {existing.lower() for existing in addresses}:
            addresses.append(address)
    return addresses


class GmailService:
    async def send_email(
        self,
        ctx,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> dict[str, Any]:
        authority = getattr(ctx, "dispatch_authority", None)
        if getattr(authority, "kind", None) not in _SENDING_DISPATCH_KINDS:
            raise PermissionError("Sending email from the user's address takes the user's own request.")
        recipients = _addresses(to, "to")
        copies = [address for address in _addresses(cc, "cc") if address.lower() not in {r.lower() for r in recipients}]
        if not recipients:
            raise ModuleInputValidationError("to needs at least one address")
        if len(recipients) + len(copies) > MAX_RECIPIENTS:
            raise ModuleInputValidationError(f"at most {MAX_RECIPIENTS} recipients per message")
        subject = str(subject or "").strip()
        if not subject or "\r" in subject or "\n" in subject or len(subject) > MAX_SUBJECT_LENGTH:
            raise ModuleInputValidationError(f"subject must be one line of at most {MAX_SUBJECT_LENGTH} characters")
        body = str(body or "")
        if not body.strip() or len(body) > MAX_BODY_LENGTH:
            raise ModuleInputValidationError(f"body must be text of at most {MAX_BODY_LENGTH} characters")
        message = EmailMessage()
        message["To"] = ", ".join(recipients)
        if copies:
            message["Cc"] = ", ".join(copies)
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = await account_request(
            ctx, CONNECTION_ID, "POST", f"{google_api_base_url()}{_SEND_PATH}", json={"raw": raw},
        )
        if response.status_code >= 400:
            raise GmailError(f"Gmail refused the message (HTTP {response.status_code}).")
        try:
            data = response.json()
        except ValueError as exc:
            raise GmailError("Gmail returned an unreadable response.") from exc
        message_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise GmailError("Gmail did not confirm the message.")
        return {"success": True, "message_id": message_id}
