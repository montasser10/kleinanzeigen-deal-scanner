"""Selbsttest ohne Netzzugriff: Parser, Klassifikation und Bewertung.

Das Fixture ist auf das reale Kleinanzeigen-Markup gekuerzt (Stand: 2026-09).
Laeuft dieser Test durch, funktioniert die Logik. Ob die Seite ihr HTML
inzwischen geaendert hat, zeigt dagegen nur ein echter Abruf:

    python -m src.main --once --dump
"""
from __future__ import annotations

import sys
from datetime import datetime

from src import scraper
from src.config import Profile
from src.models import Listing
from src.normalize import classify, detect_model, is_accessory_listing, is_relevant
from src.pricing import evaluate

# Gekuerzte, aber strukturgetreue Kopie echter Suchergebnisse.
FIXTURE = """
<div>
<article data-adid="3001" data-href="/s-anzeige/ps5-slim/3001-279-7044">
  <div>
    <script type="application/ld+json">{"title":"PS5 Slim mit Laufwerk, 2 Controller und 3 Spielen",
    "description":"Sehr guter Zustand, laeuft einwandfrei. Alles funktioniert.",
    "contentUrl":"https://img.kleinanzeigen.de/api/v1/prod-ads/images/2f/abc?rule=$_59.AUTO",
    "@type":"ImageObject"}</script>
  </div>
  <div>
    <div><span>86462 Langweid am Lech</span></div>
    <div><span>Heute, 09:00</span></div>
    <h3><a href="#">PS5 Slim mit Laufwerk, 2 Controller und 3 Spielen</a></h3>
    <p>Sehr guter Zustand, laeuft einwandfrei.</p>
    <div><p>280 &euro; VB</p></div>
    <p><span data-dhl-promotion="">Versand m&ouml;glich</span></p>
  </div>
</article>
<article data-adid="3002" data-href="/s-anzeige/defekt/3002-279-1000">
  <script type="application/ld+json">{"title":"PS5 defekt Bastler HDMI Port kaputt",
  "description":"Nur fuer Bastler, liest keine Discs.","@type":"ImageObject"}</script>
  <div><span>10115 Berlin</span></div>
  <div><span>Gestern, 18:30</span></div>
  <div><p>120 &euro;</p></div>
</article>
<article data-adid="3003" data-href="/s-anzeige/controller/3003-279-1000">
  <script type="application/ld+json">{"title":"DualSense Controller weiss",
  "description":"Kaum benutzt.","@type":"ImageObject"}</script>
  <div><span>10115 Berlin</span></div>
  <div><span>Heute, 11:00</span></div>
  <div><p>35 &euro;</p></div>
</article>
<article data-adid="3004" data-href="/s-anzeige/suche/3004-279-1000">
  <script type="application/ld+json">{"title":"Suche PS5 guenstig",
  "description":"Zahle bar.","@type":"ImageObject"}</script>
  <div><span>10115 Berlin</span></div>
  <div><span>Heute, 12:00</span></div>
  <div><p>200 &euro;</p></div>
</article>
<article data-adid="3005" data-href="/s-anzeige/shop/3005-279-1000">
  <script type="application/ld+json">{"title":"PS5 Slim neu OVP Haendlerware",
  "description":"Sofort lieferbar.","@type":"ImageObject"}</script>
  <div><span>34314 Espenau</span></div>
  <div><span>INFINITE GAMES</span></div>
  <div><p>499 &euro;</p></div>
</article>
</div>
"""


class FakeStore:
    """Store-Ersatz mit fest verdrahtetem Median."""

    def __init__(self, median: int, samples: int = 20):
        self._median = median
        self._samples = samples

    def price_reference(self, model_key: str) -> tuple[int | None, int, str]:
        if not model_key or not model_key.startswith("ps5"):
            return None, 0, "unbekanntes Modell"
        if self._samples < 5:  # spiegelt store.MIN_SAMPLES
            return None, self._samples, f"nur {self._samples} Vergleichswerte"
        return self._median, self._samples, ""

    def median_price(self, model_key: str) -> tuple[int | None, int]:
        median, samples, _ = self.price_reference(model_key)
        return median, samples

    def already_posted(self, ad_id: str) -> bool:
        return False


def check(condition: bool, message: str, failures: list[str]) -> None:
    print(f"  {'OK  ' if condition else 'FAIL'}  {message}")
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    print("Parser")
    listings = scraper.parse_listings(FIXTURE)
    check(len(listings) == 5, f"5 Anzeigen geparst (erhalten: {len(listings)})", failures)

    by_id = {listing.ad_id: listing for listing in listings}
    first = by_id["3001"]
    check(first.title.startswith("PS5 Slim"), "Titel aus ld+json", failures)
    check("Zustand" in first.description, "Beschreibung aus ld+json", failures)
    check(first.price == 280, f"Preis 280 gelesen (erhalten: {first.price})", failures)
    check(first.is_vb, "VB erkannt", failures)
    check(first.has_shipping, "Versand erkannt", failures)
    check(first.location.startswith("86462"), f"Ort erkannt ({first.location})", failures)
    check(first.image_url is not None, "Bild-URL gefunden", failures)
    check(not first.is_pro_seller, "private Anzeige nicht als gewerblich markiert", failures)
    check(
        first.url == "https://www.kleinanzeigen.de/s-anzeige/ps5-slim/3001-279-7044",
        "absolute URL gebaut",
        failures,
    )
    check(by_id["3005"].is_pro_seller, "Shop-Anzeige ohne Datum als gewerblich erkannt", failures)

    print("\nURL-Aufbau")
    both = scraper.build_url("https://www.kleinanzeigen.de/s-konsolen/ps5/k0c279", True, True)
    check(
        "/s-konsolen/versand:ja/anbieter:privat/ps5/" in both,
        f"beide Facets in korrekter Reihenfolge: {both}",
        failures,
    )
    check(
        scraper.build_url(both, True, True).count("versand:ja") == 1,
        "Versand-Facet wird nicht doppelt gesetzt",
        failures,
    )
    check(
        "versand" not in scraper.build_url(
            "https://www.kleinanzeigen.de/s-konsolen/ps5/k0c279", True, False
        ),
        "shipping_only=false laesst URL ohne Versand-Facet",
        failures,
    )

    built = scraper.build_url("https://www.kleinanzeigen.de/s-konsolen/berlin/ps5/k0c279", True)
    check("/s-konsolen/anbieter:privat/berlin/ps5/" in built, f"Privat-Facet gesetzt: {built}", failures)
    check("sortingField=SORTING_DATE" in built, "Datumssortierung gesetzt", failures)
    twice = scraper.build_url(built, True)
    check(twice.count("anbieter:privat") == 1, "Facet wird nicht doppelt gesetzt", failures)
    check(
        "anbieter" not in scraper.build_url("https://www.kleinanzeigen.de/s-konsolen/ps5/k0c279", False),
        "private_only=false laesst URL unveraendert",
        failures,
    )

    print("\nPreis- und Datumsparser")
    check(scraper.parse_price("1.250 € VB") == (1250, True), "Tausenderpunkt", failures)
    check(scraper.parse_price("Zu verschenken") == (0, False), "Zu verschenken", failures)
    check(scraper.parse_price("VB") == (None, True), "VB ohne Betrag", failures)
    now = datetime(2026, 9, 2, 12, 0)
    check(scraper.parse_age_minutes("Heute, 11:00", now) == 60, "Heute-Datum", failures)
    check(scraper.parse_age_minutes("Gestern, 12:00", now) == 1440, "Gestern-Datum", failures)

    print("\nModellerkennung")
    check(detect_model("ps5 pro 2tb") == "ps5_pro", "PS5 Pro vor PS5", failures)
    check(detect_model("nintendo switch oled") == "switch_oled", "Switch OLED", failures)
    check(detect_model("xbox series x") == "xbox_series_x", "Xbox Series X", failures)
    check(detect_model("waschmaschine") is None, "Fremdprodukt abgelehnt", failures)
    check(detect_model("xbox serie x") == "xbox_series_x", "Schreibvariante 'Serie X'", failures)

    # "Switch 2 Controller" ist eine Stueckzahl, kein Modellname.
    check(
        detect_model("nintendo switch 2 controller 128 gb") == "switch",
        "'Switch 2 Controller' ist keine Switch 2",
        failures,
    )
    check(
        detect_model("nintendo switch 2 konsole mit ovp") == "switch2",
        "echte Switch 2 weiterhin erkannt",
        failures,
    )

    # Der Titel gewinnt: eine "Switch 2" im Beschreibungstext darf aus einer
    # Switch OLED kein Switch-2-Angebot machen.
    oled = Listing(
        ad_id="7", title="Nintendo Switch oled", url="", price=210, is_vb=False,
        description="Verkaufe meine Switch, da ich mir eine Switch 2 geholt habe.",
        location="", posted_minutes_ago=5, image_url=None, is_pro_seller=False,
        has_shipping=False,
    )
    classify(oled)
    check(
        oled.model_key == "switch_oled",
        f"Titel schlaegt Beschreibung (erhalten: {oled.model_key})",
        failures,
    )

    # Beschreibungstexte duerfen gar kein Modell mehr liefern - sonst wird
    # eine 1-Euro-Xbox mit "tausche gegen Switch" als Switch gezaehlt.
    trade = Listing(
        ad_id="8", title="Xbox Serie X", url="", price=1, is_vb=False,
        description="Tausche gegen Nintendo Switch oder PS5.",
        location="", posted_minutes_ago=5, image_url=None, is_pro_seller=False,
        has_shipping=False,
    )
    classify(trade)
    check(
        trade.model_key == "xbox_series_x",
        f"Modell nur aus dem Titel (erhalten: {trade.model_key})",
        failures,
    )
    check(
        not is_relevant(trade)[0],
        f"1-Euro-Anzeige verworfen ({is_relevant(trade)[1]})",
        failures,
    )

    print("\nZubehoer-Abgrenzung (echte Titel aus der Live-Suche)")
    accessories = [
        "VR Brille für die Ps5",
        "PlayStation 5 HD Kamera NEU",
        "DualSense Controller weiss",
        "PS5 Spiele Sammlung",
        "Ladestation für PS5 Controller",
        "PlayStation 5 Fernbedienung NEU",
        "Sony HD Kamera für PlayStation 5",
        "Original PS5 Covers / Seitenplatte",
        "SCUF DUELLSENS EDGE CONTROLLER",
        "Lötaufsatz / Lötspitze für Xbox Series X & PS5 Controller T900",
        "4 noname Joycons für Switch 1",
        "Nintendo Switch Docking Station NEU mit Verp. (keine Konsole)",
        # Unbekannte Produktnamen - nur ueber das "fuer <Konsole>"-Muster fassbar
        "Nexigo Gripcon für Nintendo Switch 1/v2/OLED - Hall-Effect",
        "Silikonhülle passend für Steam Deck OLED",
        # Echte Umlaute statt ae/oe/ue - Verkaeufer schreiben beides
        "Nintendo Switch 1/v2/OLED Hüllen-Konvolut",
        "Kopfhörer für Xbox Series X",
        # Die "2" gehoert zum Modellnamen, nicht zur Stueckzahl
        "Original Nintendo Switch 2 Joycons",
        # Remote-Play-Handheld, keine Konsole
        "Ps5 Portal",
        "PlayStation Portal Remote Play",
    ]
    consoles = [
        "Sony PlayStation 5 PS5 Disc Edition 825GB",
        "PS5 Digital Edition + 2 Controller",
        "PlayStation 5 825 GB 1x Controller",
        "PS5 Slim mit Laufwerk, 2 Controller und 3 Spielen",
        "PS5 Konsole mit Zubehör",
        "Ps5 Slim mit Laufwerk",
        "PS5 mit 1. Controller",
        "PlayStation 5 mit Controller und Spielen",
        "Nintendo Switch Konsole Set mit Spielen und Zubehör",
        "Nintendo Switch Animal Crossing Edition + Tasche",
    ]
    for title in accessories:
        check(is_accessory_listing(title), f"Zubehoer erkannt: {title}", failures)
    for title in consoles:
        check(not is_accessory_listing(title), f"Konsole erkannt: {title}", failures)

    print("\nVorfilter")
    for listing in listings:
        classify(listing)
    check(is_relevant(by_id["3001"])[0], "gute Anzeige durchgelassen", failures)
    check(not is_relevant(by_id["3002"])[0], "defekte Anzeige blockiert", failures)
    check(not is_relevant(by_id["3003"])[0], "reines Zubehoer blockiert", failures)
    check(not is_relevant(by_id["3004"])[0], "Suchanzeige blockiert", failures)
    check(by_id["3001"].extras >= 2, f"Bundle-Extras erkannt ({by_id['3001'].extras})", failures)

    print("\nBewertung")
    profile = Profile(name="test", url="x")

    deal, reason = evaluate(by_id["3001"], profile, FakeStore(median=430))
    check(deal is not None, f"Deal bei Median 430 erkannt ({reason})", failures)
    if deal:
        check(
            deal.expected_profit == 107,
            f"Rohgewinn 107 (erhalten: {deal.expected_profit})",
            failures,
        )
        check(0.0 <= deal.score <= 1.0, f"Score im Bereich 0..1 ({deal.score:.2f})", failures)

    no_deal, _ = evaluate(by_id["3001"], profile, FakeStore(median=300))
    check(no_deal is None, "zu geringer Rabatt abgelehnt", failures)

    pro_deal, reason = evaluate(by_id["3005"], profile, FakeStore(median=900))
    check(pro_deal is None and "gewerblich" in reason, "gewerbliche Anzeige abgelehnt", failures)

    scam = Listing(
        ad_id="9", title="PS5 Slim", url="", price=90, is_vb=False, description="",
        location="", posted_minutes_ago=5, image_url=None, is_pro_seller=False,
        has_shipping=True,
    )
    classify(scam)
    scam_deal, reason = evaluate(scam, profile, FakeStore(median=430))
    check(
        scam_deal is None and "unrealistisch" in reason,
        f"unrealistisch guenstige Anzeige hart abgelehnt ({reason})",
        failures,
    )

    no_reference, reason = evaluate(by_id["3001"], profile, FakeStore(median=430, samples=2))
    check(
        no_reference is None and "Vergleichswerte" in reason,
        f"kein Post ohne genug Vergleichsdaten ({reason})",
        failures,
    )

    # Zwei Preiscluster ohne Mitte duerfen keinen Marktwert ergeben.
    print("\nStreuungspruefung")
    from src.store import MAX_DISPERSION, Store as RealStore

    class SpreadStore(RealStore):
        def __init__(self, prices):
            self._prices = sorted(prices)
            self._posted_ids = set()

        def prices_for(self, model_key):
            return self._prices

    bimodal = SpreadStore([480, 500, 1199, 1199, 1200, 1200, 1250])
    median, samples, problem = bimodal.price_reference("ps5_pro")
    check(
        median is None and "uneinheitlich" in problem,
        f"bimodale Verteilung abgelehnt ({problem})",
        failures,
    )

    tight = SpreadStore([399, 450, 460, 500, 500, 515, 599])
    median, samples, problem = tight.price_reference("xbox_series_x")
    check(
        median is not None,
        f"einheitliche Verteilung akzeptiert (Median {median}, {problem})",
        failures,
    )

    # Ein Markt, der geschlossen ueber Neupreis inseriert, misst Wunschdenken.
    from src.normalize import MODEL_NEW_PRICE, USED_CEILING_FACTOR

    inflated = SpreadStore([1199, 1199, 1200, 1200, 1220, 1250, 1250])
    median, _, _ = inflated.price_reference("ps5_pro")
    ceiling = int(MODEL_NEW_PRICE["ps5_pro"] * USED_CEILING_FACTOR)
    check(
        median == ceiling,
        f"Referenz auf Neupreis gedeckelt ({median} statt ~1200, Grenze {ceiling})",
        failures,
    )

    print()
    if failures:
        print(f"{len(failures)} Test(s) fehlgeschlagen:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Alle Tests bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
