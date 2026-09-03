/**
 * Telegram -> GitHub Actions Bruecke.
 *
 * Telegram schickt bei jeder Nachricht einen Webhook hierher. Der Worker
 * prueft Herkunft und Befehl und loest per repository_dispatch den Workflow
 * aus. Die eigentliche Arbeit macht Python im Actions-Lauf - hier liegt
 * bewusst keine Fachlogik, damit nichts doppelt gepflegt werden muss.
 *
 * Benoetigte Variablen (Cloudflare -> Worker -> Settings -> Variables):
 *   TELEGRAM_BOT_TOKEN   Secret   Token von @BotFather
 *   WEBHOOK_SECRET       Secret   frei gewaehltes Passwort, s.u.
 *   GITHUB_TOKEN         Secret   Fine-grained PAT, nur dieses Repo, Contents: write
 *   GITHUB_REPO          Text     montasser10/kleinanzeigen-deal-scanner
 *   ALLOWED_CHAT_ID      Text     deine Telegram-Chat-ID
 */

// Muss zu HANDLERS in src/bot.py passen.
const COMMANDS = {
  scan: "Suche laeuft, Ergebnis kommt gleich ...",
  suche: "Suche laeuft, Ergebnis kommt gleich ...",
  status: "Hole Status ...",
  preise: "Hole Referenzpreise ...",
  hilfe: "Hole Befehlsuebersicht ...",
  help: "Hole Befehlsuebersicht ...",
  start: "Hole Befehlsuebersicht ...",
};

async function reply(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}

async function dispatch(env, command) {
  const response = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        // GitHub weist Anfragen ohne User-Agent ab.
        "user-agent": "kleinanzeigen-deal-scanner-worker",
      },
      body: JSON.stringify({
        event_type: "telegram-scan",
        client_payload: { command },
      }),
    }
  );
  // 204 = angenommen. Alles andere ist ein Fehler, den der Nutzer sehen soll.
  return response.status === 204
    ? null
    : `GitHub antwortete ${response.status}: ${(await response.text()).slice(0, 200)}`;
}

export default {
  async fetch(request, env) {
    // Diagnose: zeigt, welche Variablen der Worker sieht. Nur Namen und
    // Laengen, niemals Werte - damit laesst sich eine fehlende oder falsch
    // benannte Variable finden, ohne ein Geheimnis preiszugeben.
    if (request.method === "GET") {
      const names = [
        "TELEGRAM_BOT_TOKEN",
        "GITHUB_TOKEN",
        "WEBHOOK_SECRET",
        "GITHUB_REPO",
        "ALLOWED_CHAT_ID",
      ];
      const status = {};
      for (const name of names) {
        const value = env[name];
        status[name] = value ? `gesetzt (${String(value).length} Zeichen)` : "FEHLT";
      }
      return new Response(JSON.stringify(status, null, 2), {
        headers: { "content-type": "application/json" },
      });
    }

    if (request.method !== "POST") {
      return new Response("ok", { status: 200 });
    }

    // Ohne dieses Geheimnis koennte jeder, der die Worker-URL kennt,
    // Scans ausloesen und dein Anthropic-Guthaben verbrauchen.
    if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok", { status: 200 });
    }

    const message = update.message || update.channel_post || {};
    const chatId = String(message.chat?.id ?? "");
    const text = (message.text || "").trim();

    // Telegram wiederholt jeden Webhook, der nicht mit 200 quittiert wird.
    // Deshalb ab hier immer 200 - auch wenn wir nichts tun.
    if (!text.startsWith("/")) return new Response("ok");

    if (chatId !== String(env.ALLOWED_CHAT_ID)) {
      console.log(`Befehl aus fremdem Chat ${chatId} verworfen`);
      return new Response("ok");
    }

    const command = text.slice(1).split(/\s+/)[0].split("@")[0].toLowerCase();
    const ack = COMMANDS[command];

    if (!ack) {
      await reply(env, chatId, `Unbekannter Befehl: /${command}\nVersuch /hilfe`);
      return new Response("ok");
    }

    const error = await dispatch(env, command);
    await reply(env, chatId, error ? `Fehler: ${error}` : ack);
    return new Response("ok");
  },
};
