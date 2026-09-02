"""Datenstrukturen fuer Anzeigen und bewertete Deals."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    ad_id: str
    title: str
    url: str
    price: int | None            # None = "VB ohne Preis" oder unlesbar
    is_vb: bool
    description: str
    location: str
    posted_minutes_ago: int | None
    image_url: str | None
    is_pro_seller: bool
    has_shipping: bool

    # Wird von normalize.py / llm.py befuellt
    model_key: str | None = None
    condition: str = "unklar"    # neu | gebraucht | defekt | unklar
    extras: int = 0              # zusaetzliche Controller / Spiele im Bundle
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class Deal:
    listing: Listing
    median: int
    sample_size: int
    discount: float              # 0..1, Anteil unter dem Median
    expected_profit: int         # EUR nach angenommenem Verhandlungsabschlag
    score: float                 # 0..1
    reasons: list[str] = field(default_factory=list)
