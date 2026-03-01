"""
Firebase Cloud Messaging (FCM) integration for Sarna Broker.
Handles push notification sending and in-app notification storage.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# ──── Firebase Admin SDK Initialization ────
_firebase_initialized = False

def _init_firebase():
    """Initialize Firebase Admin SDK from service account credentials."""
    global _firebase_initialized
    if _firebase_initialized:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred_path = os.environ.get("FIREBASE_CREDENTIALS", "")

        if not cred_path:
            # Try inline JSON from env var
            cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
            if cred_json:
                cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
            else:
                logger.warning("Firebase credentials not configured. Push notifications disabled.")
                return False
        else:
            cred = credentials.Certificate(cred_path)

        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("Firebase Admin SDK initialized successfully.")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return False


def send_push_notification(token, title, body):
    """
    Send a push notification via FCM.
    Returns True on success, False on failure.
    Never raises — safe to call without try/except.
    """
    if not token:
        return False

    if not _init_firebase():
        return False

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            token=token,
            webpush=messaging.WebpushConfig(
                notification=messaging.WebpushNotification(
                    icon="/static/image/Sarna broker.png",
                    badge="/static/image/Sarna broker.png",
                ),
                fcm_options=messaging.WebpushFCMOptions(
                    link="/miller"
                )
            )
        )

        response = messaging.send(message)
        logger.info(f"FCM push sent successfully: {response}")
        return True

    except Exception as e:
        logger.error(f"FCM push failed (non-fatal): {e}")
        return False


def save_notification(get_db_func, user_id, title, message):
    """
    Save an in-app notification to the database.
    Never raises — safe to call without try/except.
    """
    try:
        con = get_db_func()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO notifications (user_id, title, message)
            VALUES (%s, %s, %s)
        """, (user_id, title, message))
        con.commit()
        con.close()
        return True
    except Exception as e:
        logger.error(f"Failed to save notification: {e}")
        return False
