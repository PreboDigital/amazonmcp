"""
Email service using Resend for invite and password reset notifications.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def send_invite_email(to_email: str, invite_link: str, inviter_name: Optional[str] = None) -> bool:
    """Send invitation email via Resend. Returns True if sent, False if skipped (no API key)."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.resend_api_key or not settings.from_email:
        logger.info("Resend not configured; skipping invite email")
        return False

    try:
        import resend
        resend.api_key = settings.resend_api_key

        inviter = inviter_name or "Your administrator"
        subject = "You're invited to Amazon Ads Optimizer"
        html = f"""
        <p>Hello,</p>
        <p>{inviter} has invited you to join Amazon Ads Optimizer.</p>
        <p>Click the link below to create your account and set your password:</p>
        <p><a href="{invite_link}" style="color: #6366f1; font-weight: 600;">Accept invitation</a></p>
        <p>Or copy this link: {invite_link}</p>
        <p>This invitation expires in 7 days.</p>
        <p>If you didn't expect this invite, you can safely ignore this email.</p>
        <p>— Amazon Ads Optimizer</p>
        """

        params = {
            "from": settings.from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        logger.info(f"Invite email sent to {to_email}")
        return True
    except Exception as e:
        logger.exception(f"Failed to send invite email to {to_email}: {e}")
        return False


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Send password reset email via Resend. Returns True if sent, False if skipped."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.resend_api_key or not settings.from_email:
        logger.info("Resend not configured; skipping password reset email")
        return False

    try:
        import resend
        resend.api_key = settings.resend_api_key

        subject = "Reset your Amazon Ads Optimizer password"
        html = f"""
        <p>Hello,</p>
        <p>You requested a password reset for your Amazon Ads Optimizer account.</p>
        <p>Click the link below to set a new password:</p>
        <p><a href="{reset_link}" style="color: #6366f1; font-weight: 600;">Reset password</a></p>
        <p>Or copy this link: {reset_link}</p>
        <p>This link expires in 1 hour.</p>
        <p>If you didn't request this, you can safely ignore this email.</p>
        <p>— Amazon Ads Optimizer</p>
        """

        params = {
            "from": settings.from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        logger.info(f"Password reset email sent to {to_email}")
        return True
    except Exception as e:
        logger.exception(f"Failed to send password reset email to {to_email}: {e}")
        return False
