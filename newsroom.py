import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

DESTINATION = "@NewsroomHQ"

SOURCES = {
    "@BusinessNewsroom": "BUSINESS",
    "@GamingNewsroom": "GAMING",
    "@TheTechNewsroom": "TECHNOLOGY",
    "@EntertainmentNewsroom": "ENTERTAINMENT",
}

STATE_FILE = Path("state.json")
SKIPPED_FILE = Path("skipped.json")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


class PermanentError(Exception):
    pass


class TransientError(Exception):
    pass


def die(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"Cannot read {path}: {exc}")


def save_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def tg(method, http_method="GET", **params):
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty.")

    try:
        if http_method == "POST":
            response = requests.post(
                f"{API}/{method}", data=params, timeout=45
            )
        else:
            response = requests.get(
                f"{API}/{method}", params=params, timeout=45
            )
    except requests.RequestException as exc:
        raise TransientError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError:
        if response.status_code >= 500:
            raise TransientError(
                f"Telegram HTTP {response.status_code} from {method}"
            )
        raise PermanentError(
            f"Telegram returned invalid response from {method}: "
            f"HTTP {response.status_code}"
        )

    if data.get("ok"):
        return data["result"]

    code = data.get("error_code")
    desc = data.get("description", str(data))
    message = f"Telegram {method}: {code} {desc}"

    if code == 429 or (isinstance(code, int) and code >= 500):
        raise TransientError(message)

    raise PermanentError(message)


def check_configuration():
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty.")

    bot = tg("getMe")
    webhook = tg("getWebhookInfo")

    print(
        f"Bot authentication: PASS "
        f"@{bot.get('username', 'unknown')} (id={bot.get('id')})"
    )

    if webhook.get("url"):
        die(f"Webhook configured: {webhook['url']}")

    print("Webhook: PASS (url is empty)")

    dest = tg("getChat", chat_id=DESTINATION)
    print(
        f"Destination: PASS "
        f"{dest.get('title', DESTINATION)} (id={dest.get('id')})"
    )

    for source in SOURCES:
        chat = tg("getChat", chat_id=source)
        print(
            f"Source: PASS {source} "
            f"(id={chat.get('id')}, "
            f"protected={bool(chat.get('has_protected_content', False))})"
        )


def get_updates(offset):
    return tg(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def find_urls(post):
    candidates = []

    text = post.get("text", "")
    caption = post.get("caption", "")

    for value in (text, caption):
        candidates.extend(URL_RE.findall(value or ""))

    # Telegram entities can contain URLs that aren't visible in text.
    for entity in post.get("entities", []) + post.get("caption_entities", []):
        if entity.get("type") == "text_link" and entity.get("url"):
            candidates.append(entity["url"])

    cleaned = []
    for url in candidates:
        url = url.rstrip(".,;:!?)]}")
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            if url not in cleaned:
                cleaned.append(url)

    return cleaned


def strip_urls(text):
    if not text:
        return ""
    return URL_RE.sub("", text).strip()


def telegram_html_escape(text):
    return html.escape(text or "", quote=False)


def rich_html(post, category):
    """Build the NewsroomHQ rich text from the incoming post itself.

    This intentionally does not call an LLM or article scraper. The source
    channels already contain the published news item. We preserve the source
    content and format it for the main channel.
    """
    text = post.get("text") or post.get("caption") or ""
    text = text.strip()

    urls = find_urls(post)

    # Preserve the source post's content. Remove trailing raw URLs so the
    # source button does not appear twice.
    body = strip_urls(text)

    lines = [x.strip() for x in body.splitlines() if x.strip()]

    if not lines:
        body = "New update"
    else:
        body = "\n".join(lines)

    # First line is treated as the headline only when the post has multiple
    # lines. Otherwise the complete post becomes the body.
    if len(lines) >= 2:
        headline = lines[0]
        remainder = "\n".join(lines[1:])
    else:
        headline = ""
        remainder = body

    category_html = telegram_html_escape(category)

    chunks = [
        f"<b>{category_html}</b>",
    ]

    if headline:
        chunks.append(f"<b>{telegram_html_escape(headline)}</b>")

    if remainder:
        # Keep the source's line structure in a readable paragraph.
        paragraphs = [
            telegram_html_escape(p)
            for p in remainder.split("\n")
            if p.strip()
        ]
        chunks.append("<br>".join(paragraphs))

    if urls:
        chunks.append(f'<a href="{html.escape(urls[0], quote=True)}">Source</a>')

    return "\n\n".join(chunks)


def publish_post(post, category):
    # Direct publishing. No forwardMessage/copyMessage.
    text = post.get("text")
    caption = post.get("caption")

    # For media, preserve the media and use the generated rich HTML as
    # caption. Standard Bot API HTML is used for maximum compatibility.
    rich = rich_html(post, category)

    if post.get("photo"):
        photo = post["photo"][-1]["file_id"]
        return tg(
            "sendPhoto",
            "POST",
            chat_id=DESTINATION,
            photo=photo,
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("video"):
        return tg(
            "sendVideo",
            "POST",
            chat_id=DESTINATION,
            video=post["video"]["file_id"],
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("animation"):
        return tg(
            "sendAnimation",
            "POST",
            chat_id=DESTINATION,
            animation=post["animation"]["file_id"],
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("document"):
        return tg(
            "sendDocument",
            "POST",
            chat_id=DESTINATION,
            document=post["document"]["file_id"],
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("audio"):
        return tg(
            "sendAudio",
            "POST",
            chat_id=DESTINATION,
            audio=post["audio"]["file_id"],
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("voice"):
        return tg(
            "sendVoice",
            "POST",
            chat_id=DESTINATION,
            voice=post["voice"]["file_id"],
            caption=rich,
            parse_mode="HTML",
        )

    if post.get("video_note"):
        raise PermanentError("Video-note posts are not suitable for rich HTML captions.")

    if post.get("sticker"):
        raise PermanentError("Sticker posts are not suitable for rich HTML aggregation.")

    if post.get("has_protected_content") is True:
        raise PermanentError("Protected content cannot be republished.")

    return tg(
        "sendMessage",
        "POST",
        chat_id=DESTINATION,
        text=rich,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def process():
    check_configuration()

    state = load_json(STATE_FILE, {"offset": 0})
    skipped = load_json(SKIPPED_FILE, {})

    try:
        offset = int(state.get("offset", 0))
    except Exception:
        die("Invalid state.json offset.")

    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    delivered = 0
    skipped_count = 0

    for update in updates:
        update_id = update["update_id"]
        post = update.get("channel_post")

        if not post:
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        chat = post.get("chat", {})
        source = f"@{chat.get('username')}" if chat.get("username") else None

        if source not in SOURCES:
            print(f"Skipping unexpected source: {source}")
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        category = SOURCES[source]
        message_id = post.get("message_id")

        print(
            f"Publishing {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION}"
        )

        try:
            publish_post(post, category)
            print("  PUBLISH PASS")
            delivered += 1

            # Advance only after successful delivery.
            save_json(STATE_FILE, {"offset": update_id + 1})

        except PermanentError as exc:
            print(f"  SKIPPED: {exc}")

            skipped[str(update_id)] = {
                "source": source,
                "message_id": message_id,
                "reason": str(exc),
            }
            save_json(SKIPPED_FILE, skipped)

            # Deliberately handled and logged. Continue to newer updates.
            save_json(STATE_FILE, {"offset": update_id + 1})
            skipped_count += 1

        except TransientError as exc:
            print(f"  TRANSIENT ERROR: {exc}", file=sys.stderr)
            print(
                "  Offset NOT advanced. This update will be retried.",
                file=sys.stderr,
            )
            raise SystemExit(2)

        except Exception as exc:
            print(
                f"  UNEXPECTED ERROR: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            print(
                "  Offset NOT advanced. This update will be retried.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    print(
        f"Finished. delivered={delivered}, skipped={skipped_count}"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
    else:
        process()
