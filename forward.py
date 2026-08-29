import json
import os
import sys
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SOURCE_CHANNELS = {
    "@BusinessNewsroom",
    "@GamingNewsroom",
    "@TheTechNewsroom",
    "@EntertainmentNewsroom",
}
DESTINATION_CHANNEL = "@NewsroomHQ"
STATE_FILE = Path("state.json")
SKIPPED_FILE = Path("skipped.json")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


class TelegramError(Exception):
    def __init__(self, method, code, description):
        self.method = method
        self.code = code
        self.description = description
        super().__init__(f"{method}: {code}: {description}")


class TransientTelegramError(TelegramError):
    pass


class PermanentTelegramError(TelegramError):
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
        die(f"Cannot read {path.name}: {exc}")


def save_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def telegram(method, http_method="GET", **params):
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty. Add repository secret BOT_TOKEN.")
    try:
        if http_method == "POST":
            response = requests.post(f"{API}/{method}", data=params, timeout=45)
        else:
            response = requests.get(f"{API}/{method}", params=params, timeout=45)
    except requests.RequestException as exc:
        raise TransientTelegramError(method, "NETWORK", str(exc)) from exc

    try:
        payload = response.json()
    except ValueError:
        if response.status_code >= 500:
            raise TransientTelegramError(method, response.status_code, "Non-JSON server response")
        raise PermanentTelegramError(method, response.status_code, "Non-JSON response")

    if payload.get("ok"):
        return payload["result"]

    code = payload.get("error_code", response.status_code)
    description = payload.get("description", str(payload))
    if code == 429 or (isinstance(code, int) and code >= 500):
        raise TransientTelegramError(method, code, description)
    raise PermanentTelegramError(method, code, description)


def check_configuration():
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty. Add repository secret BOT_TOKEN.")

    bot = telegram("getMe")
    webhook = telegram("getWebhookInfo")

    print(f"Bot authentication: PASS @{bot.get('username', 'unknown')} id={bot.get('id')}")

    if webhook.get("url"):
        die(f"Webhook is configured: {webhook['url']}")
    print(f"Webhook: PASS (url is empty); pending_updates={webhook.get('pending_update_count', 0)}")

    dest = telegram("getChat", chat_id=DESTINATION_CHANNEL)
    print(f"Destination: PASS {dest.get('title', DESTINATION_CHANNEL)} id={dest.get('id')}")

    for source in sorted(SOURCE_CHANNELS):
        chat = telegram("getChat", chat_id=source)
        print(
            f"Source: PASS {source} id={chat.get('id')} "
            f"protected={bool(chat.get('has_protected_content', False))}"
        )


def get_updates(offset):
    return telegram(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def forward_message(source_chat_id, message_id):
    return telegram(
        "forwardMessage",
        "POST",
        chat_id=DESTINATION_CHANNEL,
        from_chat_id=source_chat_id,
        message_id=message_id,
    )


def copy_message(source_chat_id, message_id):
    return telegram(
        "copyMessage",
        "POST",
        chat_id=DESTINATION_CHANNEL,
        from_chat_id=source_chat_id,
        message_id=message_id,
    )


def process():
    check_configuration()

    state = load_json(STATE_FILE, {"offset": 0})
    skipped = load_json(SKIPPED_FILE, {})

    try:
        offset = int(state.get("offset", 0))
    except (TypeError, ValueError):
        die("Invalid state.json offset.")

    updates = get_updates(offset)
    if not updates:
        print("No new channel posts.")
        return

    forwarded = copied = skipped_count = 0

    for update in updates:
        update_id = update["update_id"]
        post = update.get("channel_post")

        if not post:
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        chat = post.get("chat", {})
        source = f"@{chat.get('username')}" if chat.get("username") else None
        source_chat_id = chat.get("id")
        message_id = post.get("message_id")

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unexpected source={source} chat_id={source_chat_id} message_id={message_id}")
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        content_fields = sorted(
            k for k in post.keys()
            if k not in {"chat", "message_id", "date", "edit_date"}
        )
        print(
            f"SOURCE: {source} CHAT_ID: {source_chat_id} "
            f"MESSAGE_ID: {message_id} UPDATE_ID: {update_id}"
        )
        print(f"CONTENT_FIELDS: {','.join(content_fields) or 'none'}")

        if not source_chat_id or not message_id:
            reason = "missing source chat id or message id"
            print(f"  SKIP: {reason}")
            skipped[str(update_id)] = {
                "source": source,
                "source_chat_id": source_chat_id,
                "message_id": message_id,
                "reason": reason,
            }
            save_json(SKIPPED_FILE, skipped)
            save_json(STATE_FILE, {"offset": update_id + 1})
            skipped_count += 1
            continue

        if post.get("has_protected_content") is True:
            reason = "protected content"
            print(f"  SKIP: {reason}")
            skipped[str(update_id)] = {
                "source": source,
                "source_chat_id": source_chat_id,
                "message_id": message_id,
                "reason": reason,
            }
            save_json(SKIPPED_FILE, skipped)
            save_json(STATE_FILE, {"offset": update_id + 1})
            skipped_count += 1
            continue

        print(
            f"FORWARD ATTEMPT: {source_chat_id}/{message_id} "
            f"-> {DESTINATION_CHANNEL}"
        )

        try:
            result = forward_message(source_chat_id, message_id)
            print(f"FORWARD PASS: destination_message_id={result.get('message_id')}")
            save_json(STATE_FILE, {"offset": update_id + 1})
            forwarded += 1
            continue
        except TransientTelegramError as exc:
            print(
                f"FORWARD TRANSIENT FAIL: code={exc.code} description={exc.description}",
                file=sys.stderr,
            )
            print("Offset NOT advanced. This update will be retried.", file=sys.stderr)
            raise SystemExit(2)
        except PermanentTelegramError as exc:
            forward_error = exc
            print(f"FORWARD FAIL: code={exc.code} description={exc.description}")

        try:
            result = copy_message(source_chat_id, message_id)
            print(
                "COPY FALLBACK PASS: "
                f"destination_message_id={result.get('message_id')}"
            )
            save_json(STATE_FILE, {"offset": update_id + 1})
            copied += 1
            continue
        except TransientTelegramError as exc:
            print(
                f"COPY TRANSIENT FAIL: code={exc.code} description={exc.description}",
                file=sys.stderr,
            )
            print("Offset NOT advanced. This update will be retried.", file=sys.stderr)
            raise SystemExit(2)
        except PermanentTelegramError as exc:
            print(f"COPY FAIL: code={exc.code} description={exc.description}")
            skipped[str(update_id)] = {
                "source": source,
                "source_chat_id": source_chat_id,
                "message_id": message_id,
                "reason": "forwardMessage and copyMessage both failed",
                "forward_error": {
                    "code": forward_error.code,
                    "description": forward_error.description,
                },
                "copy_error": {
                    "code": exc.code,
                    "description": exc.description,
                },
            }
            save_json(SKIPPED_FILE, skipped)
            save_json(STATE_FILE, {"offset": update_id + 1})
            skipped_count += 1
            continue
        except Exception as exc:
            print(f"COPY UNEXPECTED FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("Offset NOT advanced. This update will be retried.", file=sys.stderr)
            raise SystemExit(2)

    print(f"Finished. forwarded={forwarded}, copied={copied}, skipped={skipped_count}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
    else:
        process()
