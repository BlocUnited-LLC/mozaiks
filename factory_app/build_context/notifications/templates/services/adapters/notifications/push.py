"""Push notification delivery adapter stub.

Replace the stub body with your chosen push provider SDK.
Credentials must be loaded from environment variables — never hardcoded.

Supported providers (choose one):
  - Firebase FCM:  pip install firebase-admin  / env var: FCM_SERVER_KEY or GOOGLE_APPLICATION_CREDENTIALS
  - OneSignal:     HTTP REST API               / env vars: ONESIGNAL_APP_ID, ONESIGNAL_API_KEY
  - APNs:          pip install aioapns         / env vars: APNS_KEY_ID, APNS_TEAM_ID, APNS_CERT_PATH
  - Expo:          pip install exponent-server-sdk  / env var: EXPO_ACCESS_TOKEN (optional)
"""
from __future__ import annotations

import os


async def send_push(
    *,
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> bool:
    """Send a push notification through the configured provider.

    Args:
        token: Device push registration token (FCM token, APNs token, etc.).
        title: Notification title displayed to the user.
        body: Notification body text.
        data: Optional key-value payload for in-app handling.

    Returns:
        True if the push was accepted by the provider, False otherwise.
    """
    fcm_key = os.environ.get("FCM_SERVER_KEY", "")
    if not fcm_key:
        raise EnvironmentError(
            "FCM_SERVER_KEY is not set. Configure it in your .env file "
            "with your push provider credentials."
        )

    # TODO: Replace this stub with your provider SDK call.
    # Example (Firebase Admin SDK):
    #
    # import firebase_admin
    # from firebase_admin import messaging
    # message = messaging.Message(
    #     notification=messaging.Notification(title=title, body=body),
    #     data={str(k): str(v) for k, v in (data or {}).items()},
    #     token=token,
    # )
    # messaging.send(message)
    # return True

    raise NotImplementedError(
        "Push adapter stub — implement send_push() with your provider SDK."
    )
