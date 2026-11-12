from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape

from app.email.config import EmailConfig, email_config_from_source
from app.email.messages import TransactionalEmailMessage
from app.utils.time import format_utc_timestamp


class AuthEmailTemplate(str, Enum):
    WELCOME = "welcome"
    WELCOME_VERIFY = "welcome_verify"
    RESEND_VERIFICATION = "resend_verification"
    PASSWORD_RESET = "password_reset"
    PASSWORD_CHANGED = "password_changed"
    NEW_DEVICE_LOGIN = "new_device_login"
    FAILED_LOGIN_ALERT = "failed_login_alert"


@dataclass(frozen=True)
class RenderedAuthEmail:
    subject: str
    preheader: str
    html_body: str
    text_body: str


@dataclass(frozen=True)
class AuthEmailContext:
    to_email: str
    user_name: str | None = None
    action_url: str | None = None
    action_label: str | None = None
    occurred_at: int | None = None
    user_agent_summary: str | None = None
    ip_context: str | None = None
    failure_count: int | None = None
    failure_window_minutes: int | None = None
    oauth_only_provider: str | None = None


def _display_name(context: AuthEmailContext) -> str:
    return (context.user_name or context.to_email).strip() or "there"


def _html_link(url: str, label: str) -> str:
    escaped_url = escape(url, quote=True)
    escaped_label = escape(label)
    return (
        f'<a href="{escaped_url}" '
        'style="display:inline-block;padding:12px 18px;background:#111827;color:#ffffff;'
        'text-decoration:none;border-radius:6px;font-weight:600">'
        f"{escaped_label}</a>"
    )


def _render_layout(
    *,
    title: str,
    preheader: str,
    greeting: str,
    body_lines: list[str],
    support_url: str,
    action_url: str | None = None,
    action_label: str | None = None,
) -> RenderedAuthEmail:
    html_lines = "\n".join(f"<p>{escape(line)}</p>" for line in body_lines)
    action_html = ""
    action_text = ""
    if action_url and action_label:
        action_html = (
            f'<p style="margin:24px 0">{_html_link(action_url, action_label)}</p>'
            f"<p>If the button does not work, copy and paste this URL into your browser:</p>"
            f'<p style="word-break:break-all"><a href="{escape(action_url, quote=True)}">{escape(action_url)}</a></p>'
        )
        action_text = f"\n\n{action_label}: {action_url}\n\nIf the button does not work, copy and paste the same URL into your browser."

    escaped_support_url = escape(support_url, quote=True)
    greeting_html = f"<p>{escape(greeting)}</p>" if greeting else ""
    greeting_text = f"{greeting}\n\n" if greeting else ""
    html_body = f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f7f9;color:#111827;font-family:Arial,sans-serif">
    <span style="display:none;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden">{escape(preheader)}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f7f9;padding:24px 0">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#ffffff;border:1px solid #e5e7eb">
            <tr>
              <td style="padding:28px">
                <h1 style="font-size:24px;line-height:32px;margin:0 0 16px">{escape(title)}</h1>
                {greeting_html}
                {html_lines}
                {action_html}
                <p style="margin-top:24px">Need help? Contact support: <a href="{escaped_support_url}">{escape(support_url)}</a></p>
                <p style="font-size:12px;color:#6b7280;margin-top:24px">HeyHanzi</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    text_body = (
        f"{title}\n\n"
        f"{greeting_text}"
        f"{chr(10).join(body_lines)}"
        f"{action_text}\n\n"
        f"Need help? Contact support: {support_url}\n\n"
        "HeyHanzi"
    )
    return RenderedAuthEmail(
        subject=title,
        preheader=preheader,
        html_body=html_body,
        text_body=text_body,
    )


def _template_content(template: AuthEmailTemplate, context: AuthEmailContext) -> tuple[str, str, list[str], str | None, str | None]:
    name = _display_name(context)
    timestamp = format_utc_timestamp(context.occurred_at) if context.occurred_at else None
    device_parts = [
        part
        for part in (
            f"Time: {timestamp}" if timestamp else None,
            f"Device: {context.user_agent_summary}" if context.user_agent_summary else None,
            f"Approximate location: {context.ip_context}" if context.ip_context else None,
        )
        if part
    ]
    oauth_only_recovery = context.oauth_only_provider == "google"
    security_recovery_copy = (
        "If this was not you, secure your Google account and contact HeyHanzi support."
        if oauth_only_recovery
        else "If this was not you, reset your password."
    )

    if template == AuthEmailTemplate.WELCOME:
        return ("Welcome to HeyHanzi", "Your HeyHanzi account is ready.",
                [f"Hi {name},", "Welcome to HeyHanzi. Your account is ready to start learning Chinese."], None, None)

    if template == AuthEmailTemplate.WELCOME_VERIFY:
        return (
            "Verify your HeyHanzi email",
            "Verify your email address to finish setting up your HeyHanzi account.",
            [
                f"Hi {name},",
                "Welcome to HeyHanzi. Verify your email address to keep your account secure.",
            ],
            context.action_label or "Verify email",
            context.action_url,
        )
    if template == AuthEmailTemplate.RESEND_VERIFICATION:
        return (
            "Verify your HeyHanzi email",
            "Use this new verification link to verify your HeyHanzi account.",
            [
                f"Hi {name},",
                "Use this new verification link to verify your HeyHanzi account.",
            ],
            context.action_label or "Verify email",
            context.action_url,
        )
    if template == AuthEmailTemplate.PASSWORD_RESET:
        return (
            "Reset your HeyHanzi password",
            "Use this link to reset your HeyHanzi password.",
            [
                f"Hi {name},",
                "We received a request to reset your HeyHanzi password.",
                "If you did not request this, you can ignore this email.",
            ],
            context.action_label or "Reset password",
            context.action_url,
        )
    if template == AuthEmailTemplate.PASSWORD_CHANGED:
        return (
            "Your HeyHanzi password was changed",
            "Your HeyHanzi password was changed.",
            [
                f"Hi {name},",
                "Your HeyHanzi password was changed.",
                "If you did not make this change, reset your password and contact support.",
                *device_parts,
            ],
            None,
            None,
        )
    if template == AuthEmailTemplate.NEW_DEVICE_LOGIN:
        return (
            "New sign-in to your HeyHanzi account",
            "A new device signed in to your HeyHanzi account.",
            [
                f"Hi {name},",
                "A new device signed in to your HeyHanzi account.",
                "If this was you, no action is needed.",
                security_recovery_copy,
                *device_parts,
            ],
            context.action_label or "Review account security",
            context.action_url,
        )
    if template == AuthEmailTemplate.FAILED_LOGIN_ALERT:
        failure_count = context.failure_count or 1
        window_copy = (
            f" in the last {context.failure_window_minutes} minutes"
            if context.failure_window_minutes
            else ""
        )
        return (
            "Unsuccessful sign-in attempts on your HeyHanzi account",
            "We noticed unsuccessful sign-in attempts on your HeyHanzi account.",
            [
                f"Hi {name},",
                f"We noticed {failure_count} unsuccessful sign-in attempts{window_copy} on your HeyHanzi account.",
                security_recovery_copy,
                *device_parts,
            ],
            context.action_label or "Reset password",
            context.action_url,
        )
    raise ValueError(f"Unsupported auth email template: {template}")


def render_auth_email(
    template: AuthEmailTemplate,
    context: AuthEmailContext,
    *,
    config: dict | None = None,
) -> RenderedAuthEmail:
    email_config = email_config_from_source(config)
    title, preheader, body_lines, action_label, action_url = _template_content(template, context)
    return _render_layout(
        title=title,
        preheader=preheader,
        greeting="",
        body_lines=body_lines,
        action_url=action_url,
        action_label=action_label,
        support_url=email_config.support_url,
    )


def build_auth_email_message(
    template: AuthEmailTemplate,
    context: AuthEmailContext,
    *,
    config: dict | None = None,
) -> TransactionalEmailMessage:
    email_config: EmailConfig = email_config_from_source(config)
    rendered = render_auth_email(template, context, config=config)
    return TransactionalEmailMessage(
        to_email=context.to_email,
        to_name=context.user_name,
        subject=rendered.subject,
        html_body=rendered.html_body,
        text_body=rendered.text_body,
        from_email=email_config.from_email,
        from_name=email_config.from_name,
        reply_to=email_config.reply_to_email,
        tags={"template": template.value},
    )
