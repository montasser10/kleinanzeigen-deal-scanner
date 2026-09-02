"""Konfiguration: Profile aus profiles.yml + Secrets aus der Umgebung."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
DEBUG_DIR = ROOT / "debug"

# Haiku 4.5 -- bewusst gewaehlt: die Extraktion ist eine simple
# Klassifikationsaufgabe, bei der das guenstigste Modell reicht.
LLM_MODEL = "claude-haiku-4-5"
LLM_BATCH_SIZE = 25


@dataclass
class Profile:
    name: str
    url: str
    min_discount: float = 0.22
    min_score: float = 0.45
    min_profit_eur: int = 40
    max_price: int = 900
    private_only: bool = True
    shipping_only: bool = False


@dataclass
class Settings:
    telegram_token: str = ""
    telegram_channel: str = ""
    anthropic_key: str = ""
    loop_minutes: int = 12
    scan_interval: int = 90
    dry_run: bool = False
    profiles: list[Profile] = field(default_factory=list)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_channel) and not self.dry_run

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_key)


def _load_dotenv() -> None:
    """Minimaler .env-Loader, damit lokal keine Extra-Abhaengigkeit noetig ist."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_settings() -> Settings:
    _load_dotenv()

    raw = yaml.safe_load((ROOT / "profiles.yml").read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}

    profiles: list[Profile] = []
    for entry in raw.get("profiles") or []:
        merged = {**defaults, **entry}
        profiles.append(
            Profile(
                name=merged["name"],
                url=merged["url"],
                min_discount=float(merged.get("min_discount", 0.22)),
                min_score=float(merged.get("min_score", 0.45)),
                min_profit_eur=int(merged.get("min_profit_eur", 40)),
                max_price=int(merged.get("max_price", 900)),
                private_only=bool(merged.get("private_only", True)),
                shipping_only=bool(merged.get("shipping_only", False)),
            )
        )

    return Settings(
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_channel=os.environ.get("TELEGRAM_CHANNEL_ID", ""),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        loop_minutes=int(os.environ.get("LOOP_MINUTES", "12")),
        scan_interval=int(os.environ.get("SCAN_INTERVAL_SECONDS", "90")),
        dry_run=os.environ.get("DRY_RUN", "0") == "1",
        profiles=profiles,
    )
