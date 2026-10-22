"""The language a person works in: English or Bahasa Malaysia.

The browser sends it with every request (X-Language: en or ms); it is kept for the request (and for the
background jobs it starts), and every AI call is asked to write for people in that language. Numbers stay
as the data gives them, so the checks on insights and answers still hold.
"""

from contextvars import ContextVar

SUPPORTED = ("en", "ms")
LANGUAGE: ContextVar[str] = ContextVar("language", default="en")

MALAY = (
    "\n\nLanguage: write everything meant for people (replies, titles, questions, summaries, insights, reasons) in "
    "Bahasa Malaysia. Keep measure, table and column names exactly as they are. Write numbers the way the data "
    "gives them, with K, M and B for thousands, millions and billions and % for percentages (RM 1.2M, 34%), "
    "never in words, so every number can be checked."
)


def instruction() -> str:
    """Added to every AI system prompt (llm.ask_json)."""
    return MALAY if LANGUAGE.get() == "ms" else ""


class LanguageMiddleware:
    """Reads X-Language for the request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        wanted = dict(scope.get("headers") or []).get(b"x-language", b"en").decode(errors="ignore").lower()
        token = LANGUAGE.set(wanted if wanted in SUPPORTED else "en")
        try:
            await self.app(scope, receive, send)
        finally:
            LANGUAGE.reset(token)
