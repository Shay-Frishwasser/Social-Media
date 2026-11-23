from __future__ import annotations

import smtplib
import ssl
from contextlib import suppress
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from pydantic import TypeAdapter

from brandai.adapters.publishing import DeliveryReceipt

from .models import EmailAddress, ProviderFailure
from .package import DeliveryBundle


class EmailDeliveryAdapter(Protocol):
    def deliver(self, recipient: str, bundle: DeliveryBundle, *, idempotency_key: str) -> DeliveryReceipt: ...


class MockEmailDelivery:
    def __init__(self):
        self.deliveries: list[tuple[str, DeliveryBundle, str]] = []

    def deliver(self, recipient: str, bundle: DeliveryBundle, *, idempotency_key: str) -> DeliveryReceipt:
        self.deliveries.append((recipient, bundle, idempotency_key))
        return DeliveryReceipt("mock-email", idempotency_key, "DELIVERED")


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    host: str
    sender: str
    port: int = 465
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    implicit_tls: bool = True
    timeout: float = 25
    attachment_limit: int = 18 * 1024**2

    def __post_init__(self) -> None:
        TypeAdapter(EmailAddress).validate_python(self.sender)
        if not 1 <= self.port <= 65535 or min(self.timeout, self.attachment_limit) <= 0:
            raise ValueError("invalid SMTP port, timeout or attachment limit")


class SMTPEmailDelivery:
    def __init__(self, config: SMTPConfig):
        self.config = config

    def deliver(self, recipient: str, bundle: DeliveryBundle, *, idempotency_key: str) -> DeliveryReceipt:
        config = self.config
        if len(bundle.data) > config.attachment_limit:
            raise ProviderFailure("email_attachment_too_large")
        message = EmailMessage()
        message["From"], message["To"] = config.sender, recipient
        message["Subject"] = f"BrandAI approved {bundle.manifest['platform']} package"
        domain = config.sender.rsplit("@", 1)[-1]
        message_id = f"<brandai-{idempotency_key}@{domain}>"
        message["Message-ID"] = message_id
        message.set_content("Your approved content, media, and publishing details are attached.")
        message.add_attachment(bundle.data, maintype="application", subtype="zip", filename="brandai-package.zip")
        started = False
        smtp = None
        try:
            context = ssl.create_default_context()
            if config.implicit_tls:
                smtp = smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=config.timeout)
            else:
                smtp = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
                smtp.starttls(context=context)
            if config.username:
                smtp.login(config.username, config.password or "")
            started = True
            smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [detail[0] for detail in exc.recipients.values()]
            raise ProviderFailure("smtp_recipient_rejected", retry_after=30 if codes and all(code < 500 for code in codes) else None) from None
        except smtplib.SMTPResponseException as exc:
            raise ProviderFailure("smtp_rejected", retry_after=30 if 400 <= exc.smtp_code < 500 else None) from None
        except (OSError, smtplib.SMTPException):
            raise ProviderFailure("smtp_connection_failure", retry_after=None if started else 30, uncertain=started) from None
        finally:
            if smtp is not None:
                with suppress(OSError):
                    smtp.close()
        return DeliveryReceipt("smtp", message_id, "DELIVERED")
