import json
import os
import sys
import time
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

# Telegram Bot API methods used by this publisher. No forwardMessage or
# copyMessage is used because the source message retrieval failed in the
# user's previous runs.


class PermanentPublishError(Exception):
    """The update cannot be published by this implementation."""


class TransientPublishError(Exception):
    """The update may work on a later attempt."""


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        die(f"Cannot read {path.name}: {exc}")


def save_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def get_offset() -> int:
    state = load_json(STATE_FILE, {"offset": 0})
    try:
        return int(state.get("offset", 0))
    except (TypeError, ValueError):
        die("state.json contains an invalid offset")


def telegram(method: str, http_method="GET", **params):
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty. Add the GitHub Actions repository secret BOT_TOKEN.")

    try:
        if http_method == "POST":
            response = requests.post(
                f"{API}/{method}",
                data=params,
                timeout=45,
            )
        else:
            response = requests.get(
                f"{API}/{method}",
                params=params,
                timeout=45,
            )
    except requests.RequestException as exc:
        raise TransientPublishError(
            f"Network error calling {method}: {exc}"
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        if response.status_code >= 500:
            raise TransientPublishError(
                f"Telegram HTTP {response.status_code} from {method}"
            )
        raise PermanentPublishError(
            f"Telegram returned non-JSON HTTP {response.status_code} from {method}"
        )

    if payload.get("ok"):
        return payload["result"]

    error_code = payload.get("error_code")
    description = payload.get("description", str(payload))
    message = f"Telegram {method}: {error_code} {description}"

    # Retryable Telegram conditions.
    if error_code == 429 or (isinstance(error_code, int) and error_code >= 500):
        raise TransientPublishError(message)

    raise PermanentPublishError(message)


def check_configuration():
    if not BOT_TOKEN:
        die("BOT_TOKEN is empty.")

    bot = telegram("getMe")
    webhook = telegram("getWebhookInfo")

    print(
        f"Bot authentication: PASS "
        f"@{bot.get('username', 'unknown')} (id={bot.get('id')})"
    )

    if webhook.get("url"):
        die(
            "Webhook is configured. Remove it before using getUpdates: "
            + webhook["url"]
        )

    print("Webhook: PASS (url is empty)")
    print(
        "Pending updates:",
        webhook.get("pending_update_count", 0),
    )

    destination = telegram(
        "getChat",
        chat_id=DESTINATION_CHANNEL,
    )
    print(
        f"Destination: PASS "
        f"{destination.get('title', DESTINATION_CHANNEL)} "
        f"(id={destination.get('id')})"
    )

    for channel in sorted(SOURCE_CHANNELS):
        chat = telegram("getChat", chat_id=channel)
        print(
            f"Source: PASS {channel} "
            f"(id={chat.get('id')}, "
            f"protected={bool(chat.get('has_protected_content', False))})"
        )


def get_updates(offset: int):
    return telegram(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def send(method: str, **params):
    return telegram(method, "POST", **params)


def caption_params(post: dict) -> dict:
    result = {}
    if "caption" in post:
        result["caption"] = post["caption"]
        entities = post.get("caption_entities")
        if entities:
            result["caption_entities"] = json.dumps(entities)
    return result


def publish_post(post: dict):
    """Publish the content already present in channel_post.

    The bot never asks Telegram to retrieve the source message. It uses the
    file_id/text/entities contained in the update received from getUpdates.
    """

    if post.get("has_protected_content") is True:
        raise PermanentPublishError(
            "Message has protected content and cannot be republished."
        )

    chat_id = DESTINATION_CHANNEL

    if "text" in post:
        params = {
            "chat_id": chat_id,
            "text": post["text"],
        }
        entities = post.get("entities")
        if entities:
            params["entities"] = json.dumps(entities)
        return send("sendMessage", **params)

    if "photo" in post:
        photo = post["photo"][-1]
        return send(
            "sendPhoto",
            chat_id=chat_id,
            photo=photo["file_id"],
            **caption_params(post),
        )

    if "video" in post:
        return send(
            "sendVideo",
            chat_id=chat_id,
            video=post["video"]["file_id"],
            **caption_params(post),
        )

    if "animation" in post:
        return send(
            "sendAnimation",
            chat_id=chat_id,
            animation=post["animation"]["file_id"],
            **caption_params(post),
        )

    if "document" in post:
        return send(
            "sendDocument",
            chat_id=chat_id,
            document=post["document"]["file_id"],
            **caption_params(post),
        )

    if "audio" in post:
        return send(
            "sendAudio",
            chat_id=chat_id,
            audio=post["audio"]["file_id"],
            **caption_params(post),
        )

    if "voice" in post:
        return send(
            "sendVoice",
            chat_id=chat_id,
            voice=post["voice"]["file_id"],
            **caption_params(post),
        )

    if "video_note" in post:
        return send(
            "sendVideoNote",
            chat_id=chat_id,
            video_note=post["video_note"]["file_id"],
        )

    if "sticker" in post:
        return send(
            "sendSticker",
            chat_id=chat_id,
            sticker=post["sticker"]["file_id"],
        )

    if "contact" in post:
        contact = post["contact"]
        params = {
            "chat_id": chat_id,
            "phone_number": contact["phone_number"],
            "first_name": contact["first_name"],
        }
        if contact.get("last_name"):
            params["last_name"] = contact["last_name"]
        if contact.get("vcard"):
            params["vcard"] = contact["vcard"]
        return send("sendContact", **params)

    if "location" in post:
        location = post["location"]
        params = {
            "chat_id": chat_id,
            "latitude": location["latitude"],
            "longitude": location["longitude"],
        }
        if location.get("horizontal_accuracy") is not None:
            params["horizontal_accuracy"] = location["horizontal_accuracy"]
        return send("sendLocation", **params)

    if "venue" in post:
        venue = post["venue"]
        location = venue["location"]
        return send(
            "sendVenue",
            chat_id=chat_id,
            latitude=location["latitude"],
            longitude=location["longitude"],
            title=venue["title"],
            address=venue["address"],
        )

    raise PermanentPublishError(
        "Unsupported channel_post type. "
        f"Available fields: {', '.join(sorted(post.keys()))}"
    )


def process():
    check_configuration()

    offset = get_offset()
    skipped = load_json(SKIPPED_FILE, {})
    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    delivered = 0
    skipped_count = 0

    # IMPORTANT:
    # We checkpoint after each handled update. A transient failure stops here
    # and leaves this update at the current offset so the next run retries it.
    # We never jump over an unprocessed update.
    for update in updates:
        update_id = update["update_id"]
        post = update.get("channel_post")

        if not post:
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        chat = post.get("chat", {})
        username = chat.get("username")
        source = f"@{username}" if username else None
        message_id = post.get("message_id")

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unexpected source {source}")
            save_json(STATE_FILE, {"offset": update_id + 1})
            continue

        print(
            f"Publishing {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION_CHANNEL}"
        )

        try:
            publish_post(post)
            print("  DIRECT PUBLISH PASS")
            delivered += 1

            # Only mark the update processed after successful delivery.
            save_json(STATE_FILE, {"offset": update_id + 1})

        except PermanentPublishError as exc:
            print(f"  PERMANENTLY SKIPPED: {exc}")

            skipped[str(update_id)] = {
                "source": source,
                "message_id": message_id,
                "reason": str(exc),
            }
            save_json(SKIPPED_FILE, skipped)

            # This update has been deliberately handled, so continue.
            save_json(STATE_FILE, {"offset": update_id + 1})
            skipped_count += 1

        except TransientPublishError as exc:
            # DO NOT advance the offset. This preserves the update for the
            # next workflow run. Exit non-zero so GitHub clearly shows failure.
            print(f"  TRANSIENT FAILURE: {exc}", file=sys.stderr)
            print(
                "  Offset was NOT advanced. The same update will be retried "
                "on the next run.",
                file=sys.stderr,
            )
            raise SystemExit(2)

        except Exception as exc:
            # Unknown errors are treated as transient. Never skip unknown
            # failures automatically because doing so could lose a post.
            print(
                f"  UNEXPECTED FAILURE: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            print(
                "  Offset was NOT advanced. The update will be retried.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    print(
        f"Finished. delivered={delivered}, "
        f"skipped={skipped_count}"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
    else:
        process()
