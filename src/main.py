"""Einstiegspunkt.

Ein Lauf scannt alle Profile mehrfach ueber ein Zeitfenster hinweg. Das
gleicht aus, dass GitHub-Cron nicht puenktlich feuert: statt 96 Momentaufnahmen
pro Tag bekommst du eine nahezu durchgehende Abdeckung.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import llm, notify, pricing, scraper, sell
from .config import Settings, load_settings
from .models import Deal
from .normalize import classify, is_relevant
from .store import Store


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def build_drafts(deals: list[Deal], settings: Settings) -> dict[str, tuple[str, str, int]]:
    """Fuer jeden Deal einen fertigen Inserat-Entwurf erzeugen.

    Mit API-Key formuliert Haiku die Quellanzeige neu, sonst greift die
    Vorlage. Der Rueckfall ist wichtig: ein fehlender Entwurf waere aergerlicher
    als ein generischer.
    """
    fallback = {
        deal.listing.ad_id: sell.build_draft(
            deal.listing.model_key or "",
            deal.median,
            extras=deal.listing.extras,
            condition=deal.listing.condition,
            has_shipping=deal.listing.has_shipping,
        )
        for deal in deals
    }

    if not deals or not settings.llm_enabled:
        return fallback

    jobs = [
        {
            "quelle_titel": deal.listing.title[:140],
            "quelle_text": deal.listing.description[:400],
            "modell": sell.sell_title(deal.listing.model_key or ""),
            "preis": fallback[deal.listing.ad_id][2],
        }
        for deal in deals
    ]

    written = llm.rewrite_listings(jobs, settings.anthropic_key)
    for index, deal in enumerate(deals):
        if index in written:
            title, body = written[index]
            fallback[deal.listing.ad_id] = (title, body, fallback[deal.listing.ad_id][2])

    return fallback


def scan_once(settings: Settings, store: Store, dump: bool) -> tuple[int, int]:
    """Alle Profile einmal durchgehen. Gibt (neue Beobachtungen, Posts) zurueck."""
    new_observations = 0
    posted = 0

    for profile in settings.profiles:
        try:
            listings = scraper.fetch(
                profile.url,
                private_only=profile.private_only,
                shipping_only=profile.shipping_only,
                dump_name=profile.name.replace(" ", "_") if dump else None,
            )
        except scraper.BlockedError as error:
            log(f"  {profile.name}: GEBLOCKT - {error}")
            if settings.telegram_enabled:
                notify.send_alert(
                    f"Scraper geblockt bei '{profile.name}': {error}",
                    settings.telegram_token,
                    settings.telegram_channel,
                )
            continue
        except Exception as error:  # noqa: BLE001 - ein Profil darf den Lauf nicht killen
            log(f"  {profile.name}: Fehler - {type(error).__name__}: {error}")
            continue

        for listing in listings:
            classify(listing)

        # Nur intakte Konsolen werden zu Preisbeobachtungen. Defekte Geraete
        # und Zubehoer wuerden den Median nach unten ziehen und damit jede
        # spaetere Bewertung verfaelschen.
        relevant_listings = [item for item in listings if is_relevant(item)[0]]
        new_observations += store.record_observations(relevant_listings)

        candidates = []
        for listing in relevant_listings:
            if store.already_posted(listing.ad_id):
                continue
            deal, _ = pricing.evaluate(listing, profile, store)
            if deal is not None:
                candidates.append(listing)

        if not candidates:
            log(f"  {profile.name}: {len(listings)} Anzeigen, kein Treffer")
            continue

        # Erst jetzt das LLM - auf einer Handvoll Kandidaten statt auf allem.
        if settings.llm_enabled:
            llm.enrich(candidates, settings.anthropic_key)

        deals: list[Deal] = []
        for listing in candidates:
            relevant, reason = is_relevant(listing)
            if not relevant:
                log(f"  verworfen nach LLM-Pruefung ({reason}): {listing.title[:60]}")
                continue
            deal, reject = pricing.evaluate(listing, profile, store)
            if deal is None:
                log(f"  verworfen nach LLM-Pruefung ({reject}): {listing.title[:60]}")
                continue
            deals.append(deal)

        deals.sort(key=lambda item: item.score, reverse=True)

        drafts = build_drafts(deals, settings)

        for deal in deals:
            if not settings.telegram_enabled:
                # Ein Probelauf darf einen Deal nicht "verbrauchen" - sonst
                # fehlt er beim ersten echten Lauf, ohne je zugestellt worden
                # zu sein. Deshalb hier kein record_posted.
                title, body, price = drafts[deal.listing.ad_id]
                print(notify.render(deal, profile.name))
                print(f"  -> {deal.listing.url}")
                print(f"\n  Inserat-Entwurf ({price} EUR):\n  {title}")
                for line in body.splitlines():
                    print(f"    {line}")
                print()
                posted += 1
                continue

            message_id = notify.send(
                deal, profile.name, settings.telegram_token, settings.telegram_channel
            )
            if message_id is None:
                continue

            title, body, price = drafts[deal.listing.ad_id]
            notify.send_draft(
                title, body, price,
                settings.telegram_token, settings.telegram_channel, reply_to=message_id,
            )

            store.record_posted(deal)
            posted += 1

        log(f"  {profile.name}: {len(listings)} Anzeigen, {len(deals)} Deals")

    return new_observations, posted


def main() -> int:
    parser = argparse.ArgumentParser(description="Kleinanzeigen Deal-Scanner")
    parser.add_argument("--once", action="store_true", help="nur einen Durchlauf statt Schleife")
    parser.add_argument("--dump", action="store_true", help="rohes HTML nach debug/ schreiben")
    args = parser.parse_args()

    settings = load_settings()
    store = Store()

    log(
        f"Start - {len(settings.profiles)} Profile, "
        f"Telegram={'an' if settings.telegram_enabled else 'aus'}, "
        f"LLM={'an' if settings.llm_enabled else 'aus'}"
    )

    deadline = time.monotonic() + settings.loop_minutes * 60
    total_observations = 0
    total_posts = 0
    round_number = 0

    while True:
        round_number += 1
        log(f"Durchlauf {round_number}")
        observations, posts = scan_once(settings, store, args.dump)
        total_observations += observations
        total_posts += posts

        if args.once or time.monotonic() + settings.scan_interval >= deadline:
            break
        time.sleep(settings.scan_interval)

    removed = store.prune()
    log(
        f"Fertig - {total_observations} neue Beobachtungen, {total_posts} Posts, "
        f"{removed} alte Eintraege entfernt"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
