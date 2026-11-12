from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransactionalEmailMessage:
    to_email: str
    subject: str
    html_body: str
    text_body: str
    to_name: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
