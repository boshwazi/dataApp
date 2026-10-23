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
from pathlib import Path

# The logo travels inside each email (an inline image), so it shows without loading anything from the site.
MARK = Path(__file__).parent / "brand" / "email-mark.png"  # written by frontend/brand/render.py
MARK_CID = "datawazi-mark"


class MailError(Exception):
    pass


def branded(content: str) -> str:
    """An email's HTML body under the datawazi header: the mark and the wordmark."""
    return (
        "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:600px;color:#16202e'>"
        "<table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:collapse;margin:0 0 18px'><tr>"
        f"<td style='padding:0 8px 0 0;vertical-align:middle'><img src='cid:{MARK_CID}' width='28' height='28' alt='' "
        "style='display:block;border:0'></td>"
        "<td style='vertical-align:middle;font-size:20px;font-weight:700;letter-spacing:-0.04em'>datawazi</td>"
        "</tr></table>"
        f"<div style='border-top:1px solid #e3e7ee;padding-top:18px'>{content}</div></div>"
    )


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
            if f"cid:{MARK_CID}" in html and MARK.exists():
                message.get_payload()[-1].add_related(MARK.read_bytes(), "image", "png", cid=f"<{MARK_CID}>")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.user, self.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"Couldn't send email to {to}: {exc}") from exc
