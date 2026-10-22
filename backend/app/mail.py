"""Sending email from the server (dashboard digests).

Sign-in and invite emails go through Supabase. Other email needs an SMTP account, set in .env:

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@gmail.com
    SMTP_PASSWORD=<an app password, never your account password>
    SMTP_FROM=datawazi <you@gmail.com>

Without them, digests are delivered as in-app notifications only.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    @classmethod
    def from_env(cls) -> "Mailer | None":
        host, user, password = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
        if not (host and user and password):
            return None
        return cls(host, int(os.getenv("SMTP_PORT") or 587), user, password, os.getenv("SMTP_FROM") or user)

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, to, subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.user, self.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"Couldn't send email to {to}: {exc}") from exc
