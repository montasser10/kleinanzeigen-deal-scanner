"""Telegram-Listener: Befehle entgegennehmen und Suchen ausloesen.

    python -m src.bot

Laeuft solange dein Rechner an ist und horcht per Long-Polling auf Befehle.
Der Zeitplan auf GitHub laeuft davon unabhaengig weiter - beide teilen sich
den State ueber das Repository, damit dir kein Angebot doppelt zugestellt
wird (siehe _sync_state).

Sicherheit: Es werden ausschliesslich Nachrichten aus dem konfigurierten
Chat ausgefuehrt. Alles andere wird protokolliert und verworfen - ein
fremder Chat darf diesen Bot nicht steuern.
"""
from __future__ import annotations

import html
import subprocess
import sys
import time
from datetime import datetime

import httpx

from .config import ROOT, STATE_DIR, Settings, load_settings
from .main import scan_once
from .normalize import MODEL_LABELS, label
from .store import PRICE_WINDOW_DAYS, Store

API = "https://api.telegram.org/bot{token}/{method}"
OFFSET_FILE = STATE_DIR / "telegram_offset.txt"
POLL_TIMEOUT = 25

COMMANDS = [
    ("scan", "Jetzt eine Suche starten"),
    ("status", "Filter, Datenbasis und letzte Treffer"),
    ("preise", "Referenzpreise aller Modelle"),
    ("hilfe", "Befehle anzeigen"),
]


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def call(token: str, method: str, **payload):
    try:
        response = httpx.post(
            API.format(token=token, method=method), json=payload, timeout=POLL_TIMEOUT + 10
        )
        return response.json()
    except httpx.HTTPError as error:
        log(f"Telegram-Fehler bei {method}: {error}")
        return {}


def say(settings: Settings, text: str) -> None:
    call(
        settings.telegram_token,
        "sendMessage",
        chat_id=settings.telegram_channel,
        text=text[:4096],
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_offset(value: int) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    OFFSET_FILE.write_text(str(value), encoding="utf-8")


def _git(*args: str) -> bool:
    """Git-Aufruf, der niemals den Bot stoppt."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _sync_state(push: bool) -> None:
    """State mit dem Repository abgleichen.

    Ohne das wuerden lokaler Lauf und GitHub-Zeitplan getrennte Listen
    gepposteter Anzeigen fuehren - und du bekaemst dasselbe Angebot zweimal.
    Schlaegt Git fehl (kein Remote, offline), laeuft der Scan trotzdem.
    """
    if not (ROOT / ".git").exists():
        return
    if push:
        _git("add", "state")
        if _git("diff", "--staged", "--quiet"):
            return  # nichts geaendert
        _git("commit", "-m", "chore(state): lokaler Scan [skip ci]")
        if not _git("push"):
            log("Push fehlgeschlagen - State bleibt lokal.")
    else:
        if not _git("pull", "--rebase", "--autostash", "--quiet"):
            log("Pull fehlgeschlagen - arbeite mit lokalem State weiter.")


# -- Befehle ---------------------------------------------------------------

def cmd_scan(settings: Settings) -> None:
    say(settings, "Suche laeuft ...")
    _sync_state(push=False)

    store = Store()
    started = time.monotonic()
    observations, posts = scan_once(settings, store, dump=False)
    store.prune()
    _sync_state(push=True)

    seconds = int(time.monotonic() - started)
    if posts:
        say(settings, f"Fertig in {seconds}s - {posts} Treffer, siehe oben.")
    else:
        say(
            settings,
            f"Fertig in {seconds}s - kein Treffer.\n"
            f"{observations} neue Anzeigen gesehen, keine ueber deinen Schwellen.",
        )


def cmd_status(settings: Settings) -> None:
    store = Store()
    profile = settings.profiles[0] if settings.profiles else None

    lines = ["<b>Status</b>", ""]
    if profile:
        lines += [
            f"Mindestgewinn: {profile.min_profit_eur} EUR",
            f"Mindestrabatt: {profile.min_discount:.0%}",
            f"Nur mit Versand: {'ja' if profile.shipping_only else 'nein'}",
            f"Nur privat: {'ja' if profile.private_only else 'nein'}",
            "",
        ]
    lines.append(f"Profile: {', '.join(p.name for p in settings.profiles)}")
    lines.append(f"Beobachtungen: {store.observation_count}")
    lines.append(f"Bereits gemeldet: {store.posted_count}")
    say(settings, "\n".join(lines))


def cmd_preise(settings: Settings) -> None:
    store = Store()
    rows = []
    for key in MODEL_LABELS:
        median, samples, problem = store.price_reference(key)
        if samples == 0:
            continue
        value = f"{median} EUR" if median else f"- ({problem})"
        rows.append((samples, f"{label(key):<18} n={samples:<3} {value}"))

    rows.sort(reverse=True)
    body = "\n".join(row for _, row in rows) or "Noch keine Daten."
    say(
        settings,
        f"<b>Referenzpreise</b> (letzte {PRICE_WINDOW_DAYS} Tage)\n"
        f"<pre>{html.escape(body)}</pre>",
    )


def cmd_hilfe(settings: Settings) -> None:
    lines = ["<b>Befehle</b>", ""]
    lines += [f"/{name} - {description}" for name, description in COMMANDS]
    lines += ["", "Der Zeitplan auf GitHub laeuft unabhaengig weiter."]
    say(settings, "\n".join(lines))


HANDLERS = {
    "scan": cmd_scan,
    "suche": cmd_scan,
    "status": cmd_status,
    "preise": cmd_preise,
    "hilfe": cmd_hilfe,
    "help": cmd_hilfe,
    "start": cmd_hilfe,
}


def handle(settings: Settings, update: dict) -> None:
    message = update.get("message") or update.get("channel_post") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = (message.get("text") or "").strip()

    if not text.startswith("/"):
        return

    # Nachrichten sind Fremdeingabe. Nur der konfigurierte Chat darf steuern.
    if chat_id != str(settings.telegram_channel):
        log(f"Befehl aus fremdem Chat {chat_id} ignoriert: {text[:40]}")
        return

    command = text[1:].split()[0].split("@")[0].lower()
    handler = HANDLERS.get(command)
    if handler is None:
        say(settings, f"Unbekannter Befehl: /{html.escape(command)}\nVersuch /hilfe")
        return

    log(f"Befehl /{command}")
    try:
        handler(settings)
    except Exception as error:  # noqa: BLE001 - ein Befehl darf den Bot nicht killen
        log(f"Fehler bei /{command}: {type(error).__name__}: {error}")
        say(settings, f"Fehler bei /{command}: {type(error).__name__}")


def main() -> int:
    settings = load_settings()
    if not settings.telegram_token or not settings.telegram_channel:
        print("TELEGRAM_BOT_TOKEN und TELEGRAM_CHANNEL_ID fehlen in .env")
        return 1

    call(
        settings.telegram_token,
        "setMyCommands",
        commands=[{"command": name, "description": text} for name, text in COMMANDS],
    )

    log(f"Listener aktiv - {len(settings.profiles)} Profile, LLM="
        f"{'an' if settings.llm_enabled else 'aus'}")
    log("Schreib /scan in Telegram. Beenden mit Strg+C.")

    offset = _read_offset()
    while True:
        result = call(
            settings.telegram_token, "getUpdates", offset=offset, timeout=POLL_TIMEOUT
        )
        for update in result.get("result") or []:
            offset = update["update_id"] + 1
            _write_offset(offset)
            handle(settings, update)

        if not result.get("ok", True):
            time.sleep(5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nListener beendet.")
