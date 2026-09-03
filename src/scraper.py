"""Abruf und Parsing der Kleinanzeigen-Suchergebnisse.

Die Seite nutzt generierte Utility-Klassen, die sich bei jedem Frontend-Deploy
aendern koennen. Deshalb haengt hier nichts an Klassennamen, sondern an drei
stabilen Ankern:

  1. article[data-adid]                  -> die Anzeige selbst
  2. <script type="application/ld+json"> -> Titel, Beschreibung, Bild
  3. Inhaltsmuster                       -> PLZ, Datum, Preis per Regex

Wenn Kleinanzeigen das Layout erneut umbaut, ueberlebt das hier deutlich
laenger als eine Selektorliste. Zum Nachpruefen: --dump schreibt das rohe
HTML nach debug/.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from selectolax.parser import HTMLParser

from .config import DEBUG_DIR
from .models import Listing

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) "
    "Gecko/20100101 Firefox/132.0",
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Referer": "https://www.kleinanzeigen.de/",
    "Connection": "keep-alive",
}

RETRIES = 3
RETRY_PAUSE = 6.0

BLOCK_MARKERS = ("captcha", "zugriff verweigert", "access denied", "unusual traffic")

ZIP_RE = re.compile(r"^\d{5}\s+\S")
DATE_RE = re.compile(r"(heute|gestern|\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
PRICE_RE = re.compile(r"(\d[\d.]*)\s*€|\bvb\b|zu\s+verschenken", re.IGNORECASE)


class BlockedError(RuntimeError):
    """Kleinanzeigen hat den Request abgewiesen oder unerwartetes HTML geliefert."""


def build_url(url: str, private_only: bool, shipping_only: bool = False) -> str:
    """Suchprofil-URL um Sortierung und Filter-Facets ergaenzen.

    Kleinanzeigen filtert ueber Pfad-Facets direkt hinter dem Kategorie-
    segment: /s-konsolen/versand:ja/anbieter:privat/ps5/k0c279. Das ist der
    Filterung im Code vorzuziehen - so enthaelt schon Seite 1 ausschliesslich
    passende Anzeigen, statt dass zwei Drittel davon wieder wegfallen.
    """
    parts = urlparse(url)

    facets = []
    if shipping_only and "versand:" not in parts.path:
        facets.append("versand:ja")
    if private_only and "anbieter:" not in parts.path:
        facets.append("anbieter:privat")

    if facets:
        segments = [segment for segment in parts.path.split("/") if segment]
        if segments:
            parts = parts._replace(path="/" + "/".join(segments[:1] + facets + segments[1:]))

    query = dict(parse_qsl(parts.query))
    query["sortingField"] = "SORTING_DATE"
    return urlunparse(parts._replace(query=urlencode(query)))


def parse_price(raw: str) -> tuple[int | None, bool]:
    """Preistext in (Betrag, ist_verhandelbar) zerlegen."""
    if not raw:
        return None, False
    lowered = raw.lower()
    is_vb = "vb" in lowered or "verhandlung" in lowered
    if "verschenk" in lowered:
        return 0, False
    match = re.search(r"(\d[\d.]*)\s*(?:€|eur)", lowered)
    if not match:
        return None, is_vb
    return int(match.group(1).replace(".", "")), is_vb


def parse_age_minutes(raw: str, now: datetime | None = None) -> int | None:
    """Relatives Datum der Anzeige in Minuten seit Veroeffentlichung umrechnen."""
    if not raw:
        return None
    now = now or datetime.now()
    lowered = raw.strip().lower()
    time_match = re.search(r"(\d{1,2}):(\d{2})", lowered)

    if "heute" in lowered and time_match:
        posted = now.replace(
            hour=int(time_match.group(1)),
            minute=int(time_match.group(2)),
            second=0,
            microsecond=0,
        )
        # Eine Uhrzeit in der Zukunft bedeutet Zeitzonen-Drift, nicht morgen.
        if posted > now:
            posted = now
    elif "gestern" in lowered and time_match:
        posted = (now - timedelta(days=1)).replace(
            hour=int(time_match.group(1)),
            minute=int(time_match.group(2)),
            second=0,
            microsecond=0,
        )
    else:
        date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", lowered)
        if not date_match:
            return None
        posted = datetime(
            int(date_match.group(3)),
            int(date_match.group(2)),
            int(date_match.group(1)),
        )

    return max(0, int((now - posted).total_seconds() // 60))


def _structured_data(article) -> dict:
    """Das ld+json-Objekt der Anzeige lesen - der stabilste Anker der Seite."""
    for script in article.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and ("title" in data or "contentUrl" in data):
            return data
    return {}


def _parse_article(article) -> Listing | None:
    ad_id = article.attributes.get("data-adid")
    href = article.attributes.get("data-href") or ""
    if not ad_id:
        return None

    data = _structured_data(article)

    title = (data.get("title") or "").strip()
    if not title:
        heading = article.css_first("h3 a") or article.css_first("h3")
        title = heading.text(strip=True) if heading else ""
    if not title:
        return None

    description = (data.get("description") or "").strip()
    if not description:
        paragraphs = [p.text(strip=True) for p in article.css("p")]
        description = next((p for p in paragraphs if len(p) > 40), "")

    image_url = data.get("contentUrl")
    if not image_url:
        img = article.css_first("img")
        image_url = img.attributes.get("src") if img else None

    spans = [span.text(strip=True) for span in article.css("span") if span.text(strip=True)]
    location = next((text for text in spans if ZIP_RE.match(text)), "")
    date_raw = next((text for text in spans if DATE_RE.search(text)), "")
    has_shipping = any("versand" in text.lower() for text in spans)

    price, is_vb = None, False
    for paragraph in article.css("p"):
        text = paragraph.text(strip=True)
        if text and PRICE_RE.search(text) and len(text) < 40:
            price, is_vb = parse_price(text)
            if price is not None:
                break

    return Listing(
        ad_id=ad_id,
        title=title,
        url="https://www.kleinanzeigen.de" + href if href.startswith("/") else href,
        price=price,
        is_vb=is_vb,
        description=description,
        location=location,
        posted_minutes_ago=parse_age_minutes(date_raw),
        image_url=image_url,
        # Gesponserte Platzierungen und Shop-Anzeigen tragen kein Datum.
        # Das ist das einzige verlaessliche Signal in der Ergebnisliste.
        is_pro_seller=not date_raw,
        has_shipping=has_shipping,
    )


def parse_listings(html: str) -> list[Listing]:
    tree = HTMLParser(html)
    listings = []
    for article in tree.css("article[data-adid]") or tree.css("[data-adid]"):
        listing = _parse_article(article)
        if listing is not None:
            listings.append(listing)
    return listings


def fetch(
    url: str,
    private_only: bool = True,
    shipping_only: bool = False,
    dump_name: str | None = None,
    timeout: float = 25.0,
) -> list[Listing]:
    """Seite 1 eines Suchprofils holen und parsen.

    Seite 1 reicht: nach Datum sortiert stehen dort genau die neuen Anzeigen,
    und alles Aeltere hat der Bot beim vorherigen Lauf schon gesehen.
    """
    target = build_url(url, private_only, shipping_only)
    response = None

    # Ein 403 ist meist voruebergehend, kein dauerhafter Bann. Mit Pause und
    # anderem User-Agent kommt der naechste Versuch oft durch - das ist
    # deutlich billiger als ein abgebrochener Lauf.
    for attempt in range(RETRIES):
        headers = {**HEADERS, "User-Agent": random.choice(USER_AGENTS)}
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            response = client.get(target)
        if response.status_code not in (403, 429, 503):
            break
        if attempt < RETRIES - 1:
            time.sleep(RETRY_PAUSE * (attempt + 1) + random.uniform(0, 2))

    if response is None or response.status_code in (403, 429, 503):
        raise BlockedError(f"HTTP {response.status_code} nach {RETRIES} Versuchen")

    body = response.text
    if any(marker in body[:6000].lower() for marker in BLOCK_MARKERS):
        raise BlockedError("Bot-Schutz-Seite statt Suchergebnissen erhalten")

    response.raise_for_status()

    if dump_name:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{dump_name}.html").write_text(body, encoding="utf-8")

    listings = parse_listings(body)
    if not listings:
        raise BlockedError(
            "Keine Anzeigen im HTML gefunden - Markup geaendert oder Seite geblockt. "
            "Mit --dump erneut laufen lassen und debug/*.html pruefen."
        )

    # Hoefliche Pause zwischen Profilen, damit wir keine Last erzeugen.
    time.sleep(random.uniform(1.5, 3.0))
    return listings
