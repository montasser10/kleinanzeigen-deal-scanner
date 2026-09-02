# Kleinanzeigen Deal-Scanner

Durchsucht Kleinanzeigen nach unterbewerteten Spielkonsolen, bewertet sie
gegen selbst erhobene Marktpreise und postet die Treffer in einen
Telegram-Channel. Läuft auf GitHub Actions.

Der Bot entscheidet nichts — er filtert. Kaufen, prüfen und weiterverkaufen
bleibt deine Aufgabe.

---

## Rechtlicher Rahmen

Zwei Punkte, die den Unterschied zwischen einem Nebenverdienst und einer
Abmahnung ausmachen:

- **Erst kaufen, dann inserieren.** Ein fremdes Inserat zu kopieren, bevor du
  die Ware besitzt, verstößt gegen die Kleinanzeigen-AGB, und die Fotos des
  Verkäufers sind urheberrechtlich geschützt. Kaufen, selbst fotografieren,
  selbst beschreiben.
- **Ab ca. 20–30 Verkäufen pro Jahr giltst du als gewerblich.** Dann brauchst
  du Gewerbeanmeldung, Impressum, Widerrufsbelehrung und musst Umsatzsteuer
  abführen. Plane das ein, bevor du dort ankommst.

Kleinanzeigen bietet keine öffentliche API. Der Scraper liest ausschließlich
öffentlich sichtbare Suchergebnisseiten, holt pro Profil nur Seite 1 und
pausiert 1,5–3 s zwischen Abrufen. Halte es dabei — höheres Tempo bringt
nichts außer einer Sperre.

---

## Funktionsweise

```
Scheduler (GitHub Actions, alle 15 min)
   ↓
Scraper ── Seite 1 pro Suchprofil, nach Datum sortiert
   ↓
Vorfilter ── Zubehör, Defektes, Suchanzeigen und Shop-Anzeigen raus
   ↓
Preis-Historie (state/observations.csv)
   ↓
Bewertung ── Rabatt gegen Median + Frische + Zustand + Bundle
   ↓
LLM (Haiku 4.5) ── nur für die wenigen Kandidaten, die alles überstanden haben
   ↓
Telegram-Channel
```

### Woher der Vergleichspreis kommt

Es gibt keine externe Preisquelle. Der Bot legt **jede saubere Konsolenanzeige,
die er sieht, als Preisbeobachtung ab** — auch die uninteressanten. Aus diesem
wachsenden Datensatz entsteht pro Modell ein Median.

Daraus folgen drei Dinge:

- **Vor dem 5. Vergleichswert pro Modell postet der Bot nichts.** Am ersten Tag
  ist er fast still. Das ist gewollt.
- **Belastbar wird der Median nach etwa 1–2 Wochen.** Bis dahin die Treffer eher
  als Vorschläge lesen und die Schwellen noch nicht nachjustieren.
- **Was in die Historie kommt, entscheidet über alles.** Deshalb sind Zubehör,
  Defektware und gesponserte Shop-Anzeigen konsequent ausgeschlossen — ein
  einziger 20-Euro-Controller im PS5-Median verschiebt jede spätere Bewertung.

Angebotspreise sind nicht Verkaufspreise. Für Arbitrage reicht das: du kaufst
deutlich unter dem Median und verkaufst auf Median-Niveau.

### Wie ein Angebot bewertet wird

Basis ist der Rabatt gegen den Median (65 % des Scores). Dazu kommen:

| Signal | Wirkung |
|---|---|
| Anzeige < 30 min alt | +0.12 |
| Anzeige < 2 h alt | +0.06 |
| liegt seit > 2 Wochen | −0.05 |
| neu / OVP | +0.08 |
| Bundle (Extra-Controller, Spiele) | +0.03 je Extra, max. +0.09 |
| Versand möglich | +0.04 |
| VB im Preis | +0.03 |
| Zustand unklar | −0.05 |
| Risiko-Flag (Betrugsmuster etc.) | −0.10 je Flag |

Ein Angebot wird **hart abgelehnt**, wenn es mehr als 65 % unter dem Median
liegt. Das ist kein Schnäppchen, sondern ein Signal: durchgerutschtes Zubehör,
Ersatzteil, defektes Gerät oder Betrug. Diese Regel fängt ab, was die
Wortfilter nicht kennen — sie ist die letzte Verteidigungslinie.

---

## Einrichtung

### 1. Telegram

1. Bei [@BotFather](https://t.me/BotFather) `/newbot` → du bekommst den Token.
2. Channel anlegen, den Bot als **Administrator** hinzufügen (nur „Nachrichten
   posten" nötig).
3. Channel-ID holen: eine Nachricht in den Channel posten, dann
   `https://api.telegram.org/bot<TOKEN>/getUpdates` aufrufen und die
   `chat.id` ablesen (beginnt mit `-100`).

### 2. Lokal testen

```bash
pip install -r requirements.txt
```

```bash
python selftest.py
```

```bash
DRY_RUN=1 python -m src.main --once
```

Der Dry-Run postet nichts, sondern schreibt die Deal-Karten ins Terminal.
So siehst du sofort, ob Filter und Schwellen zu dir passen.

### 3. Auf GitHub deployen

Dieser Ordner ist die Repository-Wurzel — `.github/workflows/` muss ganz oben
liegen, sonst startet Actions den Workflow nicht.

```bash
git init && git add . && git commit -m "Kleinanzeigen Deal-Scanner"
```

**Repository auf `public` stellen.** Actions-Minuten sind dann unbegrenzt; bei
einem privaten Repo verbraucht dieser Workflow das Freikontingent in wenigen
Tagen. Im Code stehen keine Secrets, und die Preisdaten sind unkritisch.

Unter *Settings → Secrets and variables → Actions* anlegen:

| Secret | Pflicht |
|---|---|
| `TELEGRAM_BOT_TOKEN` | ja |
| `TELEGRAM_CHANNEL_ID` | ja |
| `ANTHROPIC_API_KEY` | nein — ohne läuft nur die Regex-Stufe |

Unter *Settings → Actions → General → Workflow permissions* muss
**Read and write permissions** aktiv sein, sonst kann der Bot die Preisdaten
nicht zurückschreiben.

---

## Betrieb

### Zeitverhalten

GitHub-Cron feuert unpünktlich, oft 5–20 Minuten zu spät. Deshalb scannt jeder
Job intern 12 Minuten lang alle 90 Sekunden (`LOOP_MINUTES`,
`SCAN_INTERVAL_SECONDS`). Ergebnis: nahezu durchgehende Abdeckung statt
isolierter Schnappschüsse.

Trotzdem ehrlich: die wirklich heißen Anzeigen sind nach 2–5 Minuten weg. Du
fängst zuverlässig die Angebote ab, die eine halbe Stunde und länger liegen —
das sind immer noch genug.

### Wenn GitHub geblockt wird

Actions läuft auf Rechenzentrums-IPs, die Kleinanzeigen abweisen kann. Der
Scraper erkennt das und meldet `BlockedError` in den Channel statt still zu
scheitern.

Gegenmittel ohne Code-Änderung: einen
[self-hosted Runner](https://docs.github.com/en/actions/hosting-your-own-runners)
auf deinem PC installieren und in `.github/workflows/scan.yml`
`runs-on: ubuntu-latest` durch `runs-on: self-hosted` ersetzen. GitHub steuert
weiterhin alles, gescrapt wird über deine Heim-IP.

### Wenn Kleinanzeigen das Markup ändert

Dann kommt `BlockedError: Keine Anzeigen im HTML gefunden`. So findest du die
neue Struktur:

```bash
python -m src.main --once --dump
```

Das rohe HTML landet in `debug/`. Der Parser hängt bewusst an drei stabilen
Ankern statt an CSS-Klassen: `article[data-adid]`, dem `ld+json`-Block je
Anzeige (Titel, Beschreibung, Bild) und Inhaltsmustern für PLZ, Datum und
Preis. Ein Redesign überlebt das meistens.

### Schwellen justieren

Alles in `profiles.yml`, pro Profil überschreibbar:

| Wert | Bedeutung |
|---|---|
| `min_discount` | Mindestrabatt gegen den Median |
| `min_score` | Score-Schwelle 0..1 |
| `min_profit_eur` | Mindest-Rohgewinn nach 10 % Verhandlungsabschlag |
| `max_price` | Obergrenze, begrenzt die Kapitalbindung pro Stück |
| `private_only` | setzt automatisch den Filter `anbieter:privat` in der URL |

Zu viele Posts → `min_discount` und `min_score` anheben. Zu wenige → erst
prüfen, ob überhaupt genug Vergleichsdaten da sind (`state/observations.csv`),
bevor du die Schwellen senkst.

Eigene Suchprofile: Suche im Browser einstellen, URL aus der Adresszeile
kopieren, in `profiles.yml` eintragen. Sortierung und Privat-Filter setzt der
Bot selbst. Für Abholware unbedingt eine Umkreissuche verwenden — die
mitgelieferten Profile sind deutschlandweit.

---

## Verkaufen: `src/sell.py`

Kleinanzeigen hat keine API zum Inserieren. Browser-Automation auf dem eigenen
Account verstößt gegen die AGB und kostet im Zweifel den Account samt
Bewertungen. Der Assistent automatisiert deshalb nicht das Posten, sondern die
Entscheidungen davor — einfügen musst du selbst.

Jede Deal-Nachricht in Telegram bekommt automatisch eine zweite Nachricht mit
dem fertigen Inserat-Entwurf angehängt: Titel und Beschreibung als Codeblock.
Ein Tipp darauf kopiert den Text in die Zwischenablage — Telegram blendet bei
Codeblöcken einen Kopier-Button ein.

Mit gesetztem `ANTHROPIC_API_KEY` formuliert Haiku 4.5 den Text aus der
Originalanzeige **neu** (nicht kopiert). Ohne Key greift eine Vorlage, die aus
Modell, erkannten Extras und Zustand gebaut wird.

Für ein Gerät, das du schon gekauft hast, geht es auch direkt:

```bash
python -m src.sell 3501559366
```

Die Anzeigen-ID steht am Ende des Links, den dir der Bot schickt. Er zieht sich
Modell und deinen Einkaufspreis aus der Historie. Alternativ von Hand:

```bash
python -m src.sell --modell ps5_disc --gekauft 300
```

Ausgegeben werden drei Preisstrategien (schnell / marktüblich / geduldig), die
erwartete Marge nach Verhandlung, ein suchfreundlicher Titel, eine
Beschreibungsvorlage mit Platzhaltern und eine Checkliste.

Bei zu dünner Datenlage nennt der Assistent keinen Preis, sondern sagt das —
ein aus vier Anzeigen geschätzter Median ist keine Kalkulationsgrundlage.

**Die Platzhalter in `<spitzen Klammern>` musst du ersetzen.** Ein wörtlich
kopierter Vorlagentext fällt Käufern auf und wirkt gewerblich.

---

## Aufbau

| Datei | Zweck |
|---|---|
| `src/scraper.py` | Abruf und Parsing, URL-Aufbau, Block-Erkennung |
| `src/normalize.py` | Modellerkennung, Zubehör-/Defekt-/Suchanzeigen-Filter |
| `src/store.py` | Preishistorie als append-only CSV, Median-Berechnung |
| `src/pricing.py` | Scoring und Deal-Entscheidung |
| `src/llm.py` | Haiku-4.5-Batch für unsaubere Titel |
| `src/notify.py` | Telegram-Ausgabe |
| `src/sell.py` | Verkaufs-Assistent: Preisstrategie und Inserat-Vorlage |
| `src/main.py` | Ablaufsteuerung und Scan-Schleife |
| `selftest.py` | 40+ Prüfungen ohne Netzzugriff |
| `state/*.csv` | Preisbeobachtungen und bereits gepostete Anzeigen |

Der State liegt als CSV im Repo, nicht als SQLite: der Workflow committet ihn
nach jedem Lauf zurück, und Textzeilen erzeugen winzige Diffs, während eine
Binärdatei jedes Mal komplett neu geschrieben würde.

### Kosten

Bei vier Profilen und 15-Minuten-Takt gehen pro Lauf typischerweise 0–10
Kandidaten an Haiku 4.5, gebündelt in einem einzigen Request. Das landet im
Bereich von 1–3 € im Monat. Ohne `ANTHROPIC_API_KEY` läuft der Bot vollständig
auf der Regex-Stufe weiter — nur unsaubere Titel wie
„Playstaton 5 mit 2 controllern" rutschen dann durch.
