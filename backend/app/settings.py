"""AI provider settings, stored in a local JSON file.

Environment variables (or a .env file) take precedence over the saved file, so a key
can be supplied without ever being written to disk. The API key is never returned to
the browser; the public view only says whether one is set and shows its last 4 chars.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.ilmu.ai/v1"
DEFAULT_MODEL = "ilmu-glm-5.1"

ENV_VARS = {
    "base_url": "LLM_BASE_URL",
    "model": "LLM_MODEL",
    "api_key": "LLM_API_KEY",
}


@dataclass
class ProviderSettings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    # Filled in by the connection test, so later stages know which protocol to use.
    capabilities: dict = field(default_factory=dict)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> ProviderSettings:
        saved = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text())
        settings = ProviderSettings(**{k: v for k, v in saved.items() if k in ProviderSettings.__dataclass_fields__})
        for attr, var in ENV_VARS.items():
            if os.environ.get(var):
                setattr(settings, attr, os.environ[var])
        return settings

    def save(self, settings: ProviderSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(settings)
        # A key supplied through the environment stays out of the file.
        if os.environ.get(ENV_VARS["api_key"]) == settings.api_key:
            data["api_key"] = self._saved_key()
        self.path.write_text(json.dumps(data, indent=2))
        os.chmod(self.path, 0o600)

    def update(self, base_url: str | None, model: str | None, api_key: str | None) -> ProviderSettings:
        settings = self.load()
        changed = False
        for attr, value in (("base_url", base_url), ("model", model), ("api_key", api_key)):
            if value is not None and value.strip() and value.strip() != getattr(settings, attr):
                setattr(settings, attr, value.strip())
                changed = True
        if changed:
            settings.capabilities = {}  # stale once the provider changes
        self.save(settings)
        return settings

    def _saved_key(self) -> str:
        if not self.path.exists():
            return ""
        return json.loads(self.path.read_text()).get("api_key", "")


def public_view(settings: ProviderSettings) -> dict:
    key = settings.api_key
    return {
        "base_url": settings.base_url,
        "model": settings.model,
        "has_api_key": bool(key),
        "api_key_hint": f"…{key[-4:]}" if len(key) >= 8 else ("set" if key else ""),
        "api_key_from_env": bool(os.environ.get(ENV_VARS["api_key"])),
        "capabilities": settings.capabilities,
    }
