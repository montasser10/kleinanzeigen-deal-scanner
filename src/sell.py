"""Verkaufs-Assistent: aus einem gekauften Geraet ein fertiges Inserat machen.

Kleinanzeigen bietet keine API zum Inserieren, und Browser-Automation auf dem
eigenen Account kostet im Zweifel den Account. Der schnelle Weg ist deshalb
nicht "automatisch posten", sondern "beim Posten nichts mehr entscheiden
muessen": Preis, Titel und Text stehen fertig zum Einfuegen bereit.

    python -m src.sell 3501559366          # Anzeigen-ID aus dem Telegram-Link
    python -m src.sell --modell ps5_disc --gekauft 300
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys

from .normalize import MODEL_LABELS, label
from .store import OBSERVATIONS, PRICE_WINDOW_DAYS, Store

# Beim Verkauf handelt fast jeder. Wer auf Median-Niveau inseriert, landet
# nach der Verhandlung ungefaehr hier.
NEGOTIATION_MARGIN = 0.10

# Kleinanzeigen zeigt runde Preise prominenter, und krumme Betraege wirken
# wie eine Kalkulation, ueber die man diskutieren kann.
def _round_price(value: float) -> int:
    return int(round(value / 5.0) * 5)


def _lookup_ad(ad_id: str) -> tuple[str, int, str] | None:
    """Modell, Preis und Titel einer bereits gesehenen Anzeige holen."""
    if not OBSERVATIONS.exists():
        return None
    with OBSERVATIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("ad_id") == ad_id:
                return row["model_key"], int(row["price"]), row.get("title", "")
    return None


def _price_stats(store: Store, model_key: str) -> tuple[int | None, int, list[int]]:
    median, samples = store.median_price(model_key)
    return median, samples, store.prices_for(model_key)


# Anzeigentitel sind kein Datenmodell, sondern Suchtreffer. Kaeufer tippen
# "PlayStation 5", nicht "ps5_disc" - und Klammern helfen niemandem.
SELL_TITLES = {
    "ps5_pro": "PlayStation 5 Pro Konsole",
    "ps5_slim_digital": "PlayStation 5 Slim Digital Edition Konsole",
    "ps5_digital": "PlayStation 5 Digital Edition Konsole PS5",
    "ps5_slim": "PlayStation 5 Slim Konsole PS5 mit Laufwerk",
    "ps5_disc": "PlayStation 5 Konsole PS5 Disc Edition",
    "ps4_pro": "PlayStation 4 Pro Konsole PS4",
    "ps4_slim": "PlayStation 4 Slim Konsole PS4",
    "ps4": "PlayStation 4 Konsole PS4",
    "switch2": "Nintendo Switch 2 Konsole",
    "switch_oled": "Nintendo Switch OLED Konsole",
    "switch_lite": "Nintendo Switch Lite Konsole",
    "switch": "Nintendo Switch Konsole",
    "xbox_series_x": "Xbox Series X Konsole 1TB",
    "xbox_series_s": "Xbox Series S Konsole",
    "xbox_one_x": "Xbox One X Konsole",
    "xbox_one": "Xbox One Konsole",
    "steamdeck_oled": "Steam Deck OLED Handheld Konsole",
    "steamdeck": "Steam Deck Handheld Konsole",
}


def sell_title(model_key: str) -> str:
    return SELL_TITLES.get(model_key, label(model_key))


# Kleinanzeigen schneidet Anzeigentitel bei 65 Zeichen ab.
TITLE_LIMIT = 65


def round_price(value: float) -> int:
    return _round_price(value)


def draft_title(model_key: str, extras: int = 0, condition: str = "gebraucht") -> str:
    """Suchfreundlicher Anzeigentitel innerhalb des Zeichenlimits."""
    base = sell_title(model_key)
    suffixes = []
    if condition == "neu":
        suffixes.append("NEU OVP")
    if extras >= 2:
        suffixes.append("Bundle mit Spielen")
    elif extras == 1:
        suffixes.append("mit Zubehör")
    suffixes.append("top Zustand")

    title = base
    for suffix in suffixes:
        candidate = f"{title} - {suffix}" if " - " not in title else f"{title}, {suffix}"
        if len(candidate) <= TITLE_LIMIT:
            title = candidate
    return title[:TITLE_LIMIT]


def draft_body(model_key: str, price: int, extras: int = 0,
               condition: str = "gebraucht", has_shipping: bool = False) -> str:
    """Anzeigentext aus den Fakten, die wir tatsaechlich kennen.

    Bewusst keine Zustandsbehauptungen, die nicht belegt sind - was du nicht
    geprueft hast, steht als Platzhalter drin und muss von dir ersetzt werden.
    """
    lines = [f"Verkaufe meine {sell_title(model_key)}."]
    lines.append("")

    if condition == "neu":
        lines.append("Zustand: neu und originalverpackt, ungeöffnet.")
    else:
        lines.append("Zustand: <gepflegt / leichte Gebrauchsspuren - anpassen>")

    # Die Controller-Anzahl haengt an den erkannten Extras - sonst steht im
    # Text "1 Controller, 2 Controller".
    base = "Konsole, Netzteil, HDMI-Kabel"
    if extras >= 2:
        included = f"{base}, 2 Controller und mehrere Spiele"
    elif extras == 1:
        included = f"{base}, 2 Controller"
    else:
        included = f"{base}, 1 Controller"
    lines.append(f"Dabei: {included} <prüfen und anpassen>")

    lines.append("")
    lines.append("Alles getestet und voll funktionsfähig, keine Sperre auf dem Gerät.")
    lines.append(
        "Versand möglich, Abholung bevorzugt."
        if has_shipping
        else "Abholung bevorzugt, Versand nach Absprache."
    )
    lines.append("")
    lines.append(f"Preis: {price} EUR VB.")
    lines.append("Privatverkauf - keine Garantie, keine Rücknahme.")

    return "\n".join(lines)


def build_draft(model_key: str, median: int, extras: int = 0,
                condition: str = "gebraucht",
                has_shipping: bool = False) -> tuple[str, str, int]:
    """Titel, Text und Verkaufspreis in einem Rutsch."""
    price = round_price(median)
    return (
        draft_title(model_key, extras, condition),
        draft_body(model_key, price, extras, condition, has_shipping),
        price,
    )


def build_listing(model_key: str, price: int) -> str:
    return draft_body(model_key, price)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inserat-Vorschlag erzeugen")
    parser.add_argument("ad_id", nargs="?", help="Anzeigen-ID aus dem Telegram-Link")
    parser.add_argument("--modell", help=f"eines von: {', '.join(MODEL_LABELS)}")
    parser.add_argument("--gekauft", type=int, help="dein Einkaufspreis in EUR")
    args = parser.parse_args()

    model_key, paid, source_title = args.modell, args.gekauft, ""

    if args.ad_id:
        found = _lookup_ad(args.ad_id.strip().split("-")[0])
        if found is None:
            print(f"Anzeige {args.ad_id} nicht in der Historie gefunden.")
            print("Alternativ: --modell <key> --gekauft <preis>")
            return 1
        model_key, paid, source_title = found

    if not model_key or model_key not in MODEL_LABELS:
        print(f"Modell fehlt oder unbekannt. Moeglich: {', '.join(MODEL_LABELS)}")
        return 1

    store = Store()
    median, samples, prices = _price_stats(store, model_key)

    print()
    print(f"  {label(model_key)}")
    if source_title:
        print(f"  Quelle: {source_title}")
    print("  " + "-" * 62)

    if median is None:
        print(f"  Zu wenig Vergleichsdaten ({samples}). Preis nicht belastbar.")
        print("  Lass den Scanner ein paar Tage laufen.")
        return 1

    quick = _round_price(median * 0.92)
    fair = _round_price(median)
    patient = _round_price(median * 1.08)

    print(f"  Marktdaten:   Median {median} EUR aus {samples} Anzeigen "
          f"({PRICE_WINDOW_DAYS} Tage)")
    if len(prices) >= 4:
        low = statistics.quantiles(prices, n=4)[0]
        high = statistics.quantiles(prices, n=4)[2]
        print(f"                mittlere Haelfte: {int(low)} - {int(high)} EUR")
    print()
    print("  Preisstrategie:")
    print(f"    schnell (Tage)     {quick:>5} EUR")
    print(f"    marktueblich       {fair:>5} EUR   <- Standardempfehlung")
    print(f"    geduldig (Wochen)  {patient:>5} EUR")

    if paid:
        realised = int(fair * (1 - NEGOTIATION_MARGIN))
        print()
        print(f"  Eingekauft fuer {paid} EUR")
        print(f"    Erloes nach Verhandlung ca. {realised} EUR")
        print(f"    Rohgewinn ca. {realised - paid} EUR")
        if realised - paid < 30:
            print("    WARNUNG: Marge zu duenn - Fahrtkosten und Zeit fressen das auf.")

    print()
    print("  Titel zum Kopieren:")
    print(f"    {sell_title(model_key)} - top Zustand, voll funktionsfaehig")
    print()
    print("  Beschreibung zum Kopieren:")
    print("  " + "=" * 62)
    for line in build_listing(model_key, fair).splitlines():
        print(f"  {line}")
    print("  " + "=" * 62)
    print()
    print("  Vor dem Einstellen:")
    print("    [ ] Eigene Fotos gemacht (Fremdfotos = Urheberrechtsverletzung)")
    print("    [ ] Seriennummer und Displaybild auf einem Foto sichtbar")
    print("    [ ] Text mit eigenen Worten angepasst, Platzhalter <> ersetzt")
    print("    [ ] Geraet getestet: startet, liest Discs, Controller koppelt")
    print("    [ ] 'Privatverkauf, keine Ruecknahme' steht drin")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
