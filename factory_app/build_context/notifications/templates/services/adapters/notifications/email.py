"""Email delivery adapter stub.

Replace the stub body with your chosen email provider SDK.
Credentials must be loaded from environment variables — never hardcoded.

Supported providers (choose one):
  - SendGrid:  pip install sendgrid      / env var: EMAIL_API_KEY
  - AWS SES:   pip install boto3         / env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
  - Postmark:  pip install postmarker    / env var: EMAIL_API_KEY
  - Mailgun:   pip install mailgun2      / env var: EMAIL_API_KEY, MAILGUN_DOMAIN
  - SMTP:      stdlib smtplib           / env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
"""
from __future__ import annotations

import os


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    template_id: str | None = None,
    template_data: dict | None = None,
) -> bool:
    """Send an email through the configured provider.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text body fallback.
        template_id: Optional provider template/transactional ID.
        template_data: Optional key-value data for template rendering.

    Returns:
        True if delivery was accepted by the provider, False otherwise.
    """
    api_key = os.environ.get("EMAIL_API_KEY", "")
    if not api_key:
        raise OSError(
            "EMAIL_API_KEY is not set. Configure it in your .env file "
            "with your email provider API key."
        )

    # TODO: Replace this stub with your provider SDK call.
    # Example (SendGrid):
    #
    # from sendgrid import SendGridAPIClient
    # from sendgrid.helpers.mail import Mail
    # message = Mail(from_email="no-reply@yourapp.com", to_emails=to,
    #                subject=subject, plain_text_content=body)
    # response = SendGridAPIClient(api_key).send(message)
    # return response.status_code < 300

    raise NotImplementedError(
        "Email adapter stub — implement send_email() with your provider SDK."
    )
