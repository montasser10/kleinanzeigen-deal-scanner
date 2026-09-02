"""Telegram-Ausgabe: eine Deal-Karte pro Fund in den Channel."""
from __future__ import annotations

import html

import httpx

from .models import Deal
from .normalize import label

API = "https://api.telegram.org/bot{token}/{method}"


def _score_bar(score: float) -> str:
    filled = round(score * 5)
    return "#" * filled + "." * (5 - filled)


def render(deal: Deal, profile_name: str) -> str:
    listing = deal.listing
    escape = html.escape

    lines = [
        f"<b>{escape(listing.title[:110])}</b>",
        "",
        f"Preis: <b>{listing.price} EUR</b>{' VB' if listing.is_vb else ''}",
        f"Median {label(listing.model_key)}: {deal.median} EUR",
        f"Ersparnis: <b>{deal.discount:.0%}</b>  |  "
        f"Rohgewinn ca. <b>{deal.expected_profit} EUR</b>",
        f"Score: {_score_bar(deal.score)} {deal.score:.2f}",
        "",
        f"Ort: {escape(listing.location or 'unbekannt')}",
    ]

    if listing.posted_minutes_ago is not None:
        minutes = listing.posted_minutes_ago
        age = f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"
        lines.append(f"Online seit: {age}")

    lines.append("")
    lines.extend(f"- {escape(reason)}" for reason in deal.reasons)
    lines.append("")
    lines.append(f"<i>Profil: {escape(profile_name)}</i>")

    return "\n".join(lines)


def send(deal: Deal, profile_name: str, token: str, channel: str) -> int | None:
    """Deal-Karte senden. Gibt die message_id zurueck, damit der Entwurf als
    Antwort daran haengen kann - sonst None."""
    caption = render(deal, profile_name)
    keyboard = {
        "inline_keyboard": [[{"text": "Anzeige oeffnen", "url": deal.listing.url}]]
    }

    if deal.listing.image_url:
        method = "sendPhoto"
        payload = {
            "chat_id": channel,
            "photo": deal.listing.image_url,
            "caption": caption[:1024],
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        }
    else:
        method = "sendMessage"
        payload = {
            "chat_id": channel,
            "text": caption[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": keyboard,
        }

    try:
        response = httpx.post(API.format(token=token, method=method), json=payload, timeout=20.0)
        if response.status_code != 200:
            print(f"  [telegram] {response.status_code}: {response.text[:200]}")
            return None
        return response.json().get("result", {}).get("message_id")
    except httpx.HTTPError as error:
        print(f"  [telegram] Netzwerkfehler: {error}")
        return None


def send_draft(
    title: str, body: str, price: int, token: str, channel: str, reply_to: int | None = None
) -> bool:
    """Fertigen Inserat-Entwurf als antippbare Codebloecke nachschicken.

    <pre> ist hier kein Styling: Telegram blendet bei Codebloecken einen
    Kopier-Button ein. Ein Tipp und der Text liegt in der Zwischenablage -
    das ist der ganze Punkt an "copy paste".
    """
    escape = html.escape
    text = (
        f"<b>Inserat-Entwurf</b>  |  Verkaufspreis {price} EUR\n\n"
        f"Titel:\n<pre>{escape(title)}</pre>\n"
        f"Beschreibung:\n<pre>{escape(body)}</pre>\n"
        "<i>Platzhalter in &lt;spitzen Klammern&gt; ersetzen. Eigene Fotos machen.</i>"
    )

    payload = {
        "chat_id": channel,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to is not None:
        payload["reply_parameters"] = {"message_id": reply_to}

    try:
        response = httpx.post(
            API.format(token=token, method="sendMessage"), json=payload, timeout=20.0
        )
        if response.status_code != 200:
            print(f"  [telegram] Entwurf {response.status_code}: {response.text[:200]}")
            return False
        return True
    except httpx.HTTPError as error:
        print(f"  [telegram] Entwurf Netzwerkfehler: {error}")
        return False


def send_alert(text: str, token: str, channel: str) -> None:
    """Betriebsmeldung (z. B. Scraper geblockt) in denselben Channel."""
    try:
        httpx.post(
            API.format(token=token, method="sendMessage"),
            json={"chat_id": channel, "text": text[:4096]},
            timeout=15.0,
        )
    except httpx.HTTPError:
        pass
