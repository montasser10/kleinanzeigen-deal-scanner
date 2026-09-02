"""Persistenz als append-only CSV.

Bewusst kein SQLite: der State wird von GitHub Actions nach jedem Lauf ins
Repo zurueckcommittet, und Textzeilen erzeugen winzige Diffs, waehrend eine
Binaerdatei bei jedem Commit komplett neu geschrieben wuerde.
"""
from __future__ import annotations

import csv
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import STATE_DIR
from .models import Listing
from .normalize import MODEL_NEW_PRICE, USED_CEILING_FACTOR

OBSERVATIONS = STATE_DIR / "observations.csv"
POSTED = STATE_DIR / "posted.csv"

# Der Titel wird nur zur Fehlersuche mitgeschrieben: wenn ein Median unplausibel
# aussieht, muss man sehen koennen, welche Anzeigen ihn erzeugt haben.
OBSERVATION_FIELDS = [
    "ts", "ad_id", "model_key", "price", "condition", "extras", "location", "title",
]
POSTED_FIELDS = ["ts", "ad_id", "model_key", "price", "median", "score", "profit"]

# Aeltere Beobachtungen verzerren den Median, weil Konsolenpreise fallen.
RETENTION_DAYS = 45
PRICE_WINDOW_DAYS = 30
MIN_SAMPLES = 5

# Verhaeltnis von oberem zu unterem Quartil, ab dem die Preise eines Modells
# keinen einheitlichen Markt mehr beschreiben.
MAX_DISPERSION = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure(path: Path, fields: list[str]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()


def _read(path: Path, fields: list[str]) -> list[dict]:
    _ensure(path, fields)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    _ensure(path, fields)
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writerows(rows)


class Store:
    def __init__(self) -> None:
        self._observations = _read(OBSERVATIONS, OBSERVATION_FIELDS)
        self._posted_ids = {row["ad_id"] for row in _read(POSTED, POSTED_FIELDS)}
        self._seen_ids = {row["ad_id"] for row in self._observations}

    # -- Lesen -------------------------------------------------------------

    def already_posted(self, ad_id: str) -> bool:
        return ad_id in self._posted_ids

    def is_new_ad(self, ad_id: str) -> bool:
        return ad_id not in self._seen_ids

    def prices_for(self, model_key: str) -> list[int]:
        """Beobachtete Preise eines Modells im Zeitfenster, aufsteigend."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=PRICE_WINDOW_DAYS)
        prices = []
        for row in self._observations:
            if row["model_key"] != model_key or row.get("condition") == "defekt":
                continue
            try:
                if datetime.fromisoformat(row["ts"]) < cutoff:
                    continue
                prices.append(int(row["price"]))
            except (ValueError, KeyError):
                continue
        return sorted(prices)

    def price_reference(self, model_key: str) -> tuple[int | None, int, str]:
        """Belastbarer Vergleichspreis, oder None mit Begruendung.

        Zwei Gruende, keinen Preis zu nennen: zu wenige Beobachtungen, oder
        eine zu breit gestreute Verteilung. Letzteres tritt auf, wenn ein
        Modell in zwei Preisclustern inseriert wird - z.B. gebrauchte PS5 Pro
        um 500 EUR neben Wunschpreis-Anzeigen um 1200 EUR, die nie verkauft
        werden. Ein Median dazwischen beschreibt keinen realen Markt und
        erzeugt Deals, die es nicht gibt.
        """
        prices = self.prices_for(model_key)
        if len(prices) < MIN_SAMPLES:
            return None, len(prices), f"nur {len(prices)} Vergleichswerte"

        q1, _, q3 = statistics.quantiles(prices, n=4)
        if q1 > 0 and q3 / q1 >= MAX_DISPERSION:
            return None, len(prices), (
                f"Preise zu uneinheitlich ({int(q1)}-{int(q3)} EUR) - "
                "kein belastbarer Marktwert"
            )

        # Extreme in beide Richtungen kappen: Schnaeppchen und Traumpreise
        # sollen den Referenzwert nicht verschieben.
        trim = len(prices) // 10
        core = prices[trim: len(prices) - trim] or prices
        median = int(statistics.median(core))

        # Obergrenze aus dem Neupreis. Wenn ein ganzer Markt ueber Neupreis
        # inseriert, misst der Median Wunschdenken, nicht Nachfrage.
        new_price = MODEL_NEW_PRICE.get(model_key)
        if new_price:
            median = min(median, int(new_price * USED_CEILING_FACTOR))

        return median, len(prices), ""

    def median_price(self, model_key: str) -> tuple[int | None, int]:
        """Median-Angebotspreis eines Modells im Beobachtungsfenster.

        Angebotspreise sind nicht Verkaufspreise - fuer Arbitrage reicht das
        aber: du kaufst deutlich unter dem Median und verkaufst auf Median.
        """
        median, samples, _ = self.price_reference(model_key)
        return median, samples

    # -- Schreiben ---------------------------------------------------------

    def record_observations(self, listings: list[Listing]) -> int:
        """Jede noch unbekannte Anzeige als Preisbeobachtung festhalten."""
        rows = []
        for listing in listings:
            if not listing.model_key or not listing.price or listing.price <= 0:
                continue
            if listing.ad_id in self._seen_ids:
                continue
            self._seen_ids.add(listing.ad_id)
            row = {
                "ts": _now(),
                "ad_id": listing.ad_id,
                "model_key": listing.model_key,
                "price": listing.price,
                "condition": listing.condition,
                "extras": listing.extras,
                "location": listing.location,
                "title": listing.title[:120],
            }
            rows.append(row)
            self._observations.append(row)

        _append(OBSERVATIONS, OBSERVATION_FIELDS, rows)
        return len(rows)

    def record_posted(self, deal) -> None:
        listing = deal.listing
        self._posted_ids.add(listing.ad_id)
        _append(
            POSTED,
            POSTED_FIELDS,
            [
                {
                    "ts": _now(),
                    "ad_id": listing.ad_id,
                    "model_key": listing.model_key,
                    "price": listing.price,
                    "median": deal.median,
                    "score": round(deal.score, 3),
                    "profit": deal.expected_profit,
                }
            ],
        )

    def prune(self) -> int:
        """Beobachtungen ausserhalb der Aufbewahrungsfrist entfernen."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        kept = []
        for row in self._observations:
            try:
                if datetime.fromisoformat(row["ts"]) >= cutoff:
                    kept.append(row)
            except ValueError:
                continue

        removed = len(self._observations) - len(kept)
        if removed <= 0:
            return 0

        with OBSERVATIONS.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OBSERVATION_FIELDS)
            writer.writeheader()
            writer.writerows(kept)

        self._observations = kept
        self._seen_ids = {row["ad_id"] for row in kept}
        return removed
