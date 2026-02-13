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


def send_sync_complete_email(
    to_email: str,
    success: bool,
    stats: Optional[dict] = None,
    error_message: Optional[str] = None,
    account_name: Optional[str] = None,
) -> bool:
    """Send campaign sync completion email. Returns True if sent, False if skipped."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.resend_api_key or not settings.from_email:
        logger.info("Resend not configured; skipping sync complete email")
        return False

    try:
        import resend
        resend.api_key = settings.resend_api_key

        if success:
            subject = "Campaign sync completed — Amazon Ads Optimizer"
            stats_str = ""
            if stats:
                stats_str = (
                    f"<p><strong>Synced:</strong> {stats.get('campaigns', 0)} campaigns, "
                    f"{stats.get('ad_groups', 0)} ad groups, "
                    f"{stats.get('targets', 0)} targets, "
                    f"{stats.get('ads', 0)} ads</p>"
                )
            account_str = f" for {account_name}" if account_name else ""
            html = f"""
            <p>Hello,</p>
            <p>Your campaign sync{account_str} has completed successfully.</p>
            {stats_str}
            <p>You can now view your updated campaigns in the Campaign Manager.</p>
            <p>— Amazon Ads Optimizer</p>
            """
        else:
            subject = "Campaign sync failed — Amazon Ads Optimizer"
            err = error_message or "An unknown error occurred."
            account_str = f" for {account_name}" if account_name else ""
            html = f"""
            <p>Hello,</p>
            <p>Your campaign sync{account_str} failed.</p>
            <p><strong>Error:</strong> {err}</p>
            <p>Please check your credentials and try again, or contact support if the issue persists.</p>
            <p>— Amazon Ads Optimizer</p>
            """

        params = {
            "from": settings.from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        logger.info(f"Sync complete email sent to {to_email} (success={success})")
        return True
    except Exception as e:
        logger.exception(f"Failed to send sync complete email to {to_email}: {e}")
        return False
