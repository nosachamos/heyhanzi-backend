from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config_loader import env
from app.email.config import EmailConfig, email_config_from_source
from app.email.messages import TransactionalEmailMessage

logger = logging.getLogger(__name__)


def create_ses_client(config: dict | None = None, *, region_name: str, client_config: Config | None = None):
    """Use the explicitly configured AWS application principal.

    Fly receives credentials inside APP_CONFIGS, not AWS_* environment variables.
    Never fall back to an unrelated ambient/default AWS identity.
    """
    source = env.config if config is None else config
    aws = source.get("AWS", {})
    if not isinstance(aws, dict) or any(
        not isinstance(aws.get(key), str) or not aws[key].strip()
        for key in ("AccessKeyId", "SecretAccessKey")
    ):
        raise ValueError("email_aws_credentials_required")
    options = {"region_name": region_name,
        "aws_access_key_id": aws["AccessKeyId"], "aws_secret_access_key": aws["SecretAccessKey"]}
    if client_config is not None:
        options["config"] = client_config
    return boto3.client("sesv2", **options)


@dataclass(frozen=True)
class SendEmailResult:
    provider: str
    message_id: str | None = None


class TransactionalEmailProvider(Protocol):
    async def send(self, message: TransactionalEmailMessage) -> SendEmailResult:
        ...


def _display_address(email: str, name: str | None = None) -> str:
    return f"{name} <{email}>" if name else email


def _message_sender(message: TransactionalEmailMessage, config: EmailConfig) -> str:
    return _display_address(
        message.from_email or config.from_email,
        message.from_name or config.from_name,
    )


def _message_reply_to(message: TransactionalEmailMessage, config: EmailConfig) -> str | None:
    return message.reply_to or config.reply_to_email


class FakeEmailProvider:
    def __init__(self):
        self.messages: list[TransactionalEmailMessage] = []

    async def send(self, message: TransactionalEmailMessage) -> SendEmailResult:
        self.messages.append(message)
        return SendEmailResult(provider="fake", message_id=f"fake-{len(self.messages)}")


class ConsoleEmailProvider:
    def __init__(self, config: EmailConfig):
        self.config = config

    async def send(self, message: TransactionalEmailMessage) -> SendEmailResult:
        logger.info(
            "Transactional email prepared",
            extra={
                "provider": "console",
                "to_email": message.to_email,
                "template": message.tags.get("template"),
            },
        )
        return SendEmailResult(provider="console")


class SmtpCaptureEmailProvider:
    def __init__(self, config: EmailConfig, smtp_factory=None):
        self.config = config
        self.smtp_factory = smtp_factory

    async def send(self, message: TransactionalEmailMessage) -> SendEmailResult:
        await asyncio.to_thread(self._send_sync, message)
        return SendEmailResult(provider="smtp_capture")

    def _send_sync(self, message: TransactionalEmailMessage) -> None:
        email_message = EmailMessage()
        email_message["From"] = _message_sender(message, self.config)
        email_message["To"] = _display_address(message.to_email, message.to_name)
        email_message["Subject"] = message.subject
        reply_to = _message_reply_to(message, self.config)
        if reply_to:
            email_message["Reply-To"] = reply_to

        email_message.set_content(message.text_body)
        email_message.add_alternative(message.html_body, subtype="html")

        smtp_factory = self.smtp_factory or (
            smtplib.SMTP_SSL if self.config.smtp_use_tls else smtplib.SMTP
        )
        with smtp_factory(self.config.smtp_host, self.config.smtp_port, timeout=10) as smtp:
            if self.config.smtp_start_tls:
                smtp.starttls()
            if self.config.smtp_username:
                smtp.login(self.config.smtp_username, self.config.smtp_password or "")
            smtp.send_message(email_message)


class SesEmailProvider:
    def __init__(self, config: EmailConfig, client=None):
        self.config = config
        self.client = client if client is not None else create_ses_client(region_name=config.ses_region)

    async def send(self, message: TransactionalEmailMessage) -> SendEmailResult:
        try:
            response = await asyncio.to_thread(self._send_sync, message)
        except (BotoCoreError, ClientError) as exc:
            provider_error_code = None
            if isinstance(exc, ClientError):
                provider_error_code = exc.response.get("Error", {}).get("Code")
            logger.warning(
                "SES email send failed",
                extra={
                    "provider": "ses",
                    "template": message.tags.get("template"),
                    "provider_error_code": provider_error_code,
                },
            )
            raise

        return SendEmailResult(
            provider="ses",
            message_id=response.get("MessageId") if isinstance(response, dict) else None,
        )

    def _send_sync(self, message: TransactionalEmailMessage):
        payload = {
            "FromEmailAddress": _message_sender(message, self.config),
            "Destination": {"ToAddresses": [message.to_email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": message.html_body, "Charset": "UTF-8"},
                        "Text": {"Data": message.text_body, "Charset": "UTF-8"},
                    },
                }
            },
        }
        reply_to = _message_reply_to(message, self.config)
        if reply_to:
            payload["ReplyToAddresses"] = [reply_to]
        if self.config.ses_configuration_set:
            payload["ConfigurationSetName"] = self.config.ses_configuration_set
        return self.client.send_email(**payload)


def create_transactional_email_provider(config: dict | None = None) -> TransactionalEmailProvider:
    email_config = email_config_from_source(config)
    if email_config.provider == "fake":
        return FakeEmailProvider()
    if email_config.provider == "console":
        return ConsoleEmailProvider(email_config)
    if email_config.provider == "smtp_capture":
        return SmtpCaptureEmailProvider(email_config)
    if email_config.provider == "ses":
        return SesEmailProvider(email_config, client=create_ses_client(
            config, region_name=email_config.ses_region,
            client_config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
        ))
    raise ValueError(f"Unsupported email provider: {email_config.provider}")
