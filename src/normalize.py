"""Modell-Erkennung und Vorfilter fuer Konsolen-Anzeigen.

Die Regex-Stufe erledigt den Grossteil kostenlos. Was sie nicht sicher
zuordnen kann, geht spaeter an das LLM - aber nur fuer Anzeigen, die den
Preisfilter ohnehin schon passiert haben.
"""
from __future__ import annotations

import re

from .models import Listing

# Reihenfolge ist entscheidend: spezifischste Variante zuerst.
MODEL_PATTERNS: list[tuple[str, str]] = [
    ("ps5_pro",          r"\bps\s?5\s*pro\b|\bplaystation\s*5\s*pro\b"),
    ("ps5_slim_digital", r"\bps\s?5\b.*\bslim\b.*\bdigital\b|\bslim\b.*\bps\s?5\b.*\bdigital\b"),
    ("ps5_digital",      r"\bps\s?5\b.*\bdigital\b|\bplaystation\s*5\b.*\bdigital\b"),
    ("ps5_slim",         r"\bps\s?5\b.*\bslim\b|\bplaystation\s*5\b.*\bslim\b"),
    ("ps5_disc",         r"\bps\s?5\b|\bplaystation\s*5\b"),
    ("ps4_pro",          r"\bps\s?4\s*pro\b|\bplaystation\s*4\s*pro\b"),
    ("ps4_slim",         r"\bps\s?4\b.*\bslim\b"),
    ("ps4",              r"\bps\s?4\b|\bplaystation\s*4\b"),
    # "Switch 2 Controller" heisst fast immer "Switch mit 2 Controllern", nicht
    # "Switch 2". Folgt dem Zweier ein Zubehoerwort, ist es eine Stueckzahl.
    ("switch2",          r"\bswitch\s*2\b(?!\s*(?:controller|joy-?cons?|"
                         r"spiel(?:e|en)?|games?|dualsense))"),
    ("switch_oled",      r"\bswitch\b.*\boled\b|\boled\b.*\bswitch\b"),
    ("switch_lite",      r"\bswitch\b.*\blite\b|\blite\b.*\bswitch\b"),
    ("switch",           r"\bswitch\b"),
    # "Serie X" ohne s ist ein haeufiger Tippfehler in echten Anzeigen.
    ("xbox_series_x",    r"\bseries?\s*x\b|\bxbox\s*x\b"),
    ("xbox_series_s",    r"\bseries?\s*s\b|\bxbox\s*s\b"),
    ("xbox_one_x",       r"\bxbox\s*one\s*x\b"),
    ("xbox_one",         r"\bxbox\s*one\b"),
    ("steamdeck_oled",   r"\bsteam\s*deck\b.*\boled\b|\boled\b.*\bsteam\s*deck\b"),
    ("steamdeck",        r"\bsteam\s*deck\b"),
]

MODEL_LABELS = {
    "ps5_pro": "PlayStation 5 Pro",
    "ps5_slim_digital": "PS5 Slim Digital",
    "ps5_digital": "PS5 Digital",
    "ps5_slim": "PS5 Slim",
    "ps5_disc": "PS5 (Disc)",
    "ps4_pro": "PS4 Pro",
    "ps4_slim": "PS4 Slim",
    "ps4": "PS4",
    "switch2": "Nintendo Switch 2",
    "switch_oled": "Switch OLED",
    "switch_lite": "Switch Lite",
    "switch": "Nintendo Switch",
    "xbox_series_x": "Xbox Series X",
    "xbox_series_s": "Xbox Series S",
    "xbox_one_x": "Xbox One X",
    "xbox_one": "Xbox One",
    "steamdeck_oled": "Steam Deck OLED",
    "steamdeck": "Steam Deck",
}

# Zubehoer darf nie gegen Konsolenpreise bewertet werden - sonst sieht jeder
# Controller wie ein Jahrhundert-Deal aus und verzerrt ausserdem den Median.
# "disc" fehlt hier bewusst: "PS5 Disc Edition" ist eine Konsolenvariante.
# Plural- und Beugungsformen muessen mit: "Joycons", "Docking Station" und
# "Spielen" sind genauso Zubehoer wie ihre Grundformen.
ACCESSORY_ONLY = re.compile(
    r"\b(controllers?|dualsense|dualshock|joy-?cons?|"
    r"headsets?|kopfhoerer|ladestation(?:en)?|dock(?:ing)?|halterung(?:en)?|"
    r"huelle(?:n)?|tasche(?:n)?|schutzfolie(?:n)?|skins?|kabel|netzteil(?:e)?|"
    r"fernbedienung(?:en)?|spiel(?:e|en)?|games?|cartridges?|speicherkarte(?:n)?|"
    r"adapter|adaptor|covers?|faceplates?|seitenteil(?:e)?|seitenplatte(?:n)?|"
    r"blende(?:n)?|vr\s*brille|psvr2?|portal|kameras?|lenkrad|standfuss)\b",
    re.IGNORECASE,
)

# Modellnamen enthalten Ziffern ("PlayStation 5"), die sonst als Stueckzahl
# durchgehen - dann waere "PlayStation 5 Fernbedienung" ein Konsolen-Bundle.
# Vor der Bundle-Pruefung fliegen diese Tokens deshalb raus.
# Die Generationsziffer muss mit raus: bleibt sie stehen, liest die
# Bundle-Pruefung die "2" aus "Switch 2 Joycons" als Stueckzahl.
CONSOLE_TOKEN = re.compile(
    r"\b(?:playstation|ps)\s*\d\b|\bxbox(?:\s*(?:one|series))?\b|"
    r"\bseries?\s*[xs]\b|\bswitch\s*(?:2|1|v2|oled|lite)?\b|\bsteam\s*deck\b",
    re.IGNORECASE,
)

# In echten Bundles haengt das Zubehoer hinten dran - erkennbar an einem
# Verbindungswort irgendwo davor ("PS5 mit 1. Controller") oder an einer
# Stueckzahl direkt davor ("... 1x Controller").
# "&" und "," fehlen bewusst: in "Loetspitze fuer Xbox Series X & PS5
# Controller" wuerde das "&" ein Bundle vortaeuschen.
BUNDLE_CONNECTOR = re.compile(
    r"\b(?:mit|inkl\.?|inklusive|und|sowie|dazu|plus|bundle)\b|\+", re.IGNORECASE
)
BUNDLE_QUANTITY = re.compile(r"\b\d+\s*x?\s*$")

CONSOLE_WORDS = re.compile(
    r"\b(konsole|console|ps\s?[45]|playstation|switch|xbox|steam\s*deck)\b",
    re.IGNORECASE,
)

DEFECT_WORDS = re.compile(
    r"\b(defekt|teildefekt|kaputt|bastler|ersatzteil|ersatzteile|"
    r"nicht\s*funktion|funktioniert\s*nicht|gesperrt|banned|gebannt|"
    r"wasserschaden|hdmi\s*(port\s*)?defekt|liest\s*keine)\b",
    re.IGNORECASE,
)

# Ausdruecke, die bei auffaellig guenstigen Anzeigen auf Betrug hindeuten.
SCAM_WORDS = re.compile(
    r"\b(nur\s*versand|vorkasse|paypal\s*freunde|freunde\s*und\s*familie|"
    r"whatsapp\s*nummer|dringend\s*verkaufen|umzug\s*schnell|"
    r"kein\s*abholung|nachnahme)\b",
    re.IGNORECASE,
)

MIN_PLAUSIBLE_PRICE = 30

# Untergrenze je Modell, unterhalb derer es sich nicht um ein funktionierendes
# Geraet handeln kann. Diese Zahlen kommen aus dem Markt, nicht aus unseren
# eigenen Daten - deshalb koennen sie sich nicht mit dem Median gegenseitig
# verziehen. Sie fangen ab, was Wortlisten prinzipiell nicht koennen:
# Tippfehler ("PS5 Contoller"), unbekannte Zubehoernamen, Einzelteile.
MODEL_PRICE_FLOOR = {
    "ps5_pro": 250,
    "ps5_slim_digital": 150,
    "ps5_digital": 150,
    "ps5_slim": 150,
    "ps5_disc": 150,
    "ps4_pro": 50,
    "ps4_slim": 50,
    "ps4": 50,
    "switch2": 250,
    "switch_oled": 100,
    "switch_lite": 50,
    "switch": 60,
    "xbox_series_x": 150,
    "xbox_series_s": 100,
    "xbox_one_x": 40,
    "xbox_one": 40,
    "steamdeck_oled": 250,
    "steamdeck": 150,
}

# Ungefaehre Neupreise. Sie begrenzen den Referenzpreis nach oben: gebrauchte
# Ware kann nicht mehr wert sein als neue. Ohne diese Grenze setzen
# Wunschpreis-Inserate den Massstab - bei der PS5 Pro standen 1199 EUR
# Median gegen 799 EUR Neupreis, und jedes 850-EUR-Angebot sah nach Gewinn aus.
MODEL_NEW_PRICE = {
    "ps5_pro": 799,
    "ps5_slim_digital": 449,
    "ps5_digital": 449,
    "ps5_slim": 549,
    "ps5_disc": 549,
    "ps4_pro": 300,
    "ps4_slim": 250,
    "ps4": 250,
    "switch2": 470,
    "switch_oled": 350,
    "switch_lite": 200,
    "switch": 300,
    "xbox_series_x": 500,
    "xbox_series_s": 300,
    "xbox_one_x": 250,
    "xbox_one": 200,
    "steamdeck_oled": 679,
    "steamdeck": 419,
}

# Selbst neuwertige Gebrauchtware bleibt unter dem Neupreis.
USED_CEILING_FACTOR = 0.85

SEARCH_WORDS = re.compile(r"\b(suche|tausche?|tausch\b|ankauf|kaufe)\b", re.IGNORECASE)


UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                         "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def fold(text: str) -> str:
    """Umlaute vereinheitlichen, damit "Huellen" und "Hüllen" gleich matchen.

    Ohne das greift jedes Wortmuster mit Umlaut nur auf einer der beiden
    Schreibweisen - und Verkaeufer nutzen beide.
    """
    return text.translate(UMLAUTS).lower()


def _haystack(listing: Listing) -> str:
    return fold(f"{listing.title} {listing.description}")


def detect_model(text: str) -> str | None:
    for key, pattern in MODEL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return key
    return None


def count_extras(text: str) -> int:
    """Grober Bundle-Bonus: zusaetzliche Controller und Spiele."""
    extras = 0
    if re.search(r"\b(2|zwei|2x|zwei\s*x)\s*(controller|dualsense|joy-?con)", text, re.I):
        extras += 1
    if re.search(r"\b(3|drei|3x)\s*(controller|dualsense|joy-?con)", text, re.I):
        extras += 2
    # "3 Spiele", "3 Spielen", "3 Games" - die Beugung muss mitgehen.
    games = re.search(r"\b(\d{1,2})\s*(?:spiel(?:e|en)?|games?)\b", text, re.I)
    if games:
        extras += min(int(games.group(1)), 10) // 3
    return extras


def classify(listing: Listing) -> Listing:
    """Regex-Stufe: Modell, Zustand, Extras und Risiko-Flags setzen."""
    text = _haystack(listing)

    # Ausschliesslich der Titel. Der Beschreibungstext enthaelt Nebensaetze
    # ("tausche gegen Switch", "habe noch eine Switch 2"), die reihenweise das
    # falsche Modell liefern - eine als Switch gezaehlte 1-Euro-Xbox reicht,
    # um einen Median zu verziehen. Lieber eine Anzeige verpassen.
    listing.model_key = detect_model(fold(listing.title))

    if DEFECT_WORDS.search(text):
        listing.condition = "defekt"
    elif re.search(r"\b(neu|ovp|originalverpackt|ungeoeffnet|versiegelt)\b", text, re.I):
        listing.condition = "neu"
    else:
        listing.condition = "gebraucht"

    listing.extras = count_extras(text)

    flags: list[str] = []
    if SCAM_WORDS.search(text):
        flags.append("Betrugsmuster im Text")
    if listing.is_pro_seller:
        flags.append("gewerblicher Anbieter")
    listing.risk_flags = flags

    return listing


# "... fuer <Konsole>" ist die Standardformulierung fuer Zubehoer. Diese Regel
# braucht den Produktnamen nicht zu kennen und faengt deshalb auch Marken ab,
# die in keiner Wortliste stehen ("Nexigo Gripcon fuer Nintendo Switch").
# Achtung: laeuft auf gefaltetem Text, "fuer" statt "für".
COMPATIBLE_WITH = re.compile(
    r"\b(?:passend\s+)?(?:fuer|fur|kompatibel\s+mit|geeignet\s+fuer)\b", re.IGNORECASE
)


def is_accessory_listing(title: str) -> bool:
    """Verkauft die Anzeige Zubehoer statt einer Konsole?

    Der Modellname allein reicht nicht als Gegenbeweis - "VR Brille fuer die
    PS5" und "PlayStation 5 HD Kamera" enthalten ihn ebenfalls. Entscheidend
    ist, ob das Zubehoerwort als Hauptsache oder als Beigabe auftaucht.
    """
    title = fold(title)

    # "X fuer <Konsole>": wenn die Konsole erst nach dem "fuer" auftaucht, ist
    # sie die Kompatibilitaetsangabe und X die verkaufte Sache.
    compat = COMPATIBLE_WITH.search(title)
    if compat and not CONSOLE_WORDS.search(title[: compat.start()]):
        if CONSOLE_WORDS.search(title[compat.end():]):
            return True

    match = ACCESSORY_ONLY.search(title)
    if match is None:
        return False

    prefix = title[: match.start()]

    # Steht die Konsole erst hinter dem Zubehoer, ist sie nur die Angabe,
    # wofuer das Teil passt: "4 Joycons fuer Switch 1", "VR Brille fuer die
    # PS5". Nur was VOR dem Zubehoerwort steht, macht daraus ein Bundle.
    if not CONSOLE_WORDS.search(prefix):
        return True

    # Modellname raus, damit seine Ziffer nicht als Stueckzahl gilt.
    stripped = CONSOLE_TOKEN.sub(" ", prefix)
    is_bundle = bool(BUNDLE_CONNECTOR.search(stripped) or BUNDLE_QUANTITY.search(stripped))
    return not is_bundle


def is_relevant(listing: Listing) -> tuple[bool, str]:
    """Harte Ausschlusskriterien vor jeder Preisbewertung.

    Gibt (relevant, Grund) zurueck - der Grund landet nur im Log.
    """
    text = _haystack(listing)

    if SEARCH_WORDS.search(listing.title):
        return False, "Suchanzeige oder Tauschgesuch"
    if listing.is_pro_seller:
        # Gesponserte Platzierungen sind Haendlerware zu Haendlerpreisen.
        # Als Vergleichswert wuerden sie den Median nach oben ziehen und
        # damit reihenweise falsche Deals erzeugen.
        return False, "gesponserte / gewerbliche Anzeige"
    if listing.price is None:
        return False, "kein Preis angegeben"
    if listing.price <= 0:
        return False, "Preis 0 / zu verschenken"
    if listing.price < MIN_PLAUSIBLE_PRICE:
        # Keine funktionierende Konsole wechselt fuer unter 30 Euro den
        # Besitzer. Was hier landet, ist Zubehoer, Schrott oder ein Platzhalter.
        return False, f"unter {MIN_PLAUSIBLE_PRICE} EUR - keine Konsole"

    floor = MODEL_PRICE_FLOOR.get(listing.model_key or "")
    if floor and listing.price < floor:
        return False, f"unter {floor} EUR unmoeglich fuer {label(listing.model_key)}"
    if listing.model_key is None:
        return False, "kein Konsolenmodell erkannt"
    if listing.condition == "defekt":
        return False, "als defekt beschrieben"
    if is_accessory_listing(listing.title):
        return False, "Zubehoer statt Konsole"

    return True, ""


def label(model_key: str | None) -> str:
    return MODEL_LABELS.get(model_key or "", model_key or "unbekannt")
