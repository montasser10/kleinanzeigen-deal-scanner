"""Bewertung: Preis-Delta gegen den Median plus gewichtete Zusatzsignale."""
from __future__ import annotations

from .config import Profile
from .models import Deal, Listing
from .store import Store

# Beim Weiterverkauf handelt fast jeder Kaeufer. Der Median ist der
# Angebotspreis - realisieren wirst du eher 90 % davon.
RESALE_HAIRCUT = 0.90

# Unterhalb dieser Marke ist ein Angebot nicht guenstig, sondern falsch:
# Zubehoer, das als Konsole durchgerutscht ist, ein Ersatzteil, ein defektes
# Geraet oder ein Fake-Inserat. Ein echter Privatverkauf liegt nie 65 % unter
# Markt. Das ist die letzte Verteidigungslinie hinter den Textfiltern - sie
# faengt ab, was die Wortlisten nicht kennen.
SCAM_DISCOUNT_THRESHOLD = 0.65


def _freshness_bonus(minutes: int | None) -> tuple[float, str | None]:
    if minutes is None:
        return 0.0, None
    if minutes <= 30:
        return 0.12, "brandneu (< 30 min)"
    if minutes <= 120:
        return 0.06, "frisch (< 2 h)"
    if minutes >= 60 * 24 * 14:
        return -0.05, "liegt seit > 2 Wochen"
    return 0.0, None


def evaluate(listing: Listing, profile: Profile, store: Store) -> tuple[Deal | None, str]:
    """Eine Anzeige bewerten. Gibt (Deal oder None, Ablehnungsgrund) zurueck."""
    if listing.price is None or listing.price <= 0:
        return None, "kein Preis"
    if listing.price > profile.max_price:
        return None, f"ueber Preisobergrenze ({profile.max_price} EUR)"
    if profile.private_only and listing.is_pro_seller:
        return None, "gewerblicher Anbieter"
    if profile.shipping_only and not listing.has_shipping:
        # Sicherheitsnetz hinter dem URL-Facet, falls die Suche doch etwas
        # ohne Versand durchlaesst.
        return None, "kein Versand angeboten"

    median, samples, problem = store.price_reference(listing.model_key or "")
    if median is None:
        return None, f"kein Referenzpreis: {problem}"

    discount = (median - listing.price) / median
    if discount < profile.min_discount:
        return None, f"nur {discount:.0%} unter Median (noetig: {profile.min_discount:.0%})"
    if discount > SCAM_DISCOUNT_THRESHOLD:
        return None, f"unrealistisch guenstig ({discount:.0%} unter Median) - kein echter Deal"

    expected_profit = int(median * RESALE_HAIRCUT) - listing.price
    if expected_profit < profile.min_profit_eur:
        return None, f"Rohgewinn nur {expected_profit} EUR"

    reasons: list[str] = [f"{discount:.0%} unter Median von {median} EUR ({samples} Vergleiche)"]

    # Der Rabatt ist das Fundament des Scores, gedeckelt bei 50 % Ersparnis.
    score = min(discount / 0.50, 1.0) * 0.65

    bonus, note = _freshness_bonus(listing.posted_minutes_ago)
    score += bonus
    if note:
        reasons.append(note)

    if listing.condition == "neu":
        score += 0.08
        reasons.append("als neu/OVP beschrieben")
    if listing.extras > 0:
        score += min(listing.extras, 3) * 0.03
        reasons.append(f"Bundle mit Extras (+{listing.extras})")
    if listing.has_shipping:
        score += 0.04
        reasons.append("Versand moeglich")
    if listing.is_vb:
        score += 0.03
        reasons.append("VB - Verhandlungsspielraum")

    flags = list(listing.risk_flags)
    if listing.condition == "unklar":
        score -= 0.05

    score -= 0.10 * len(listing.risk_flags)
    score = max(0.0, min(score, 1.0))

    if score < profile.min_score:
        return None, f"Score {score:.2f} unter Schwelle {profile.min_score:.2f}"

    reasons.extend(f"WARNUNG: {flag}" for flag in flags)

    return (
        Deal(
            listing=listing,
            median=median,
            sample_size=samples,
            discount=discount,
            expected_profit=expected_profit,
            score=score,
            reasons=reasons,
        ),
        "",
    )
