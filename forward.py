import json
import os
import sys
import time
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DESTINATION_CHANNEL = "@NewsroomHQ"

SOURCE_CHANNELS = {
    "@BusinessNewsroom",
    "@GamingNewsroom",
    "@TheTechNewsroom",
    "@EntertainmentNewsroom",
}

STATE_FILE = Path("state.json")
SKIPPED_FILE = Path("skipped.json")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Cannot read {path}: {exc}")


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def telegram(method, http_method="GET", **params):
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty.")

    try:
        if http_method == "POST":
            r = requests.post(f"{API}/{method}", data=params, timeout=45)
        else:
            r = requests.get(f"{API}/{method}", params=params, timeout=45)
    except requests.RequestException as exc:
        fail(f"Network error calling {method}: {exc}")

    try:
        data = r.json()
    except ValueError:
        fail(f"Telegram returned non-JSON for {method}: HTTP {r.status_code}")

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]


def check_configuration():
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty. Add repository secret BOT_TOKEN.")

    bot = telegram("getMe")
    webhook = telegram("getWebhookInfo")

    print(
        f"Bot: @{bot.get('username', 'unknown')} "
        f"(id={bot.get('id', 'unknown')})"
    )

    if webhook.get("url"):
        fail(f"Webhook is configured: {webhook['url']}")

    print("Webhook: PASS (no webhook configured)")
    print(
        "Pending Telegram updates:",
        webhook.get("pending_update_count", 0),
    )

    destination = telegram("getChat", chat_id=DESTINATION_CHANNEL)
    print(
        f"Destination: PASS ({destination.get('title', DESTINATION_CHANNEL)})"
    )

    for channel in sorted(SOURCE_CHANNELS):
        chat = telegram("getChat", chat_id=channel)
        print(
            f"Source: PASS {channel} "
            f"(id={chat.get('id')}, "
            f"protected={bool(chat.get('has_protected_content', False))})"
        )


def get_updates(offset):
    return telegram(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def send(method, **params):
    return telegram(method, "POST", **params)


def send_post(post, source):
    """
    Re-publish the content from the channel_post update directly.

    This intentionally does NOT call forwardMessage/copyMessage. The message
    data already exists inside the update, so we publish that data directly.
    """

    chat_id = DESTINATION_CHANNEL

    # Protected content is not useful for this publisher if Telegram marks the
    # individual message as protected.
    if post.get("has_protected_content") is True:
        raise RuntimeError(
            {"description": "Message has protected content"}
        )

    if "text" in post:
        result = send(
            "sendMessage",
            chat_id=chat_id,
            text=post["text"],
            entities=json.dumps(post.get("entities", [])),
        )
        return result

    if "photo" in post:
        photo = post["photo"][-1]
        params = {
            "chat_id": chat_id,
            "photo": photo["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendPhoto", **params)

    if "video" in post:
        video = post["video"]
        params = {
            "chat_id": chat_id,
            "video": video["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendVideo", **params)

    if "animation" in post:
        animation = post["animation"]
        params = {
            "chat_id": chat_id,
            "animation": animation["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendAnimation", **params)

    if "document" in post:
        document = post["document"]
        params = {
            "chat_id": chat_id,
            "document": document["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendDocument", **params)

    if "audio" in post:
        audio = post["audio"]
        params = {
            "chat_id": chat_id,
            "audio": audio["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendAudio", **params)

    if "voice" in post:
        voice = post["voice"]
        params = {
            "chat_id": chat_id,
            "voice": voice["file_id"],
        }
        if "caption" in post:
            params["caption"] = post["caption"]
            params["caption_entities"] = json.dumps(
                post.get("caption_entities", [])
            )
        return send("sendVoice", **params)

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
        c = post["contact"]
        return send(
            "sendContact",
            chat_id=chat_id,
            phone_number=c["phone_number"],
            first_name=c["first_name"],
            last_name=c.get("last_name"),
            vcard=c.get("vcard"),
        )

    if "location" in post:
        loc = post["location"]
        return send(
            "sendLocation",
            chat_id=chat_id,
            latitude=loc["latitude"],
            longitude=loc["longitude"],
            horizontal_accuracy=loc.get("horizontal_accuracy"),
        )

    if "venue" in post:
        venue = post["venue"]
        loc = venue["location"]
        return send(
            "sendVenue",
            chat_id=chat_id,
            latitude=loc["latitude"],
            longitude=loc["longitude"],
            title=venue["title"],
            address=venue["address"],
        )

    if "poll" in post:
        poll = post["poll"]
        # Telegram Bot API does not allow recreating every poll state from an
        # incoming post reliably. Record unsupported types rather than losing
        # the whole update.
        raise RuntimeError(
            {"description": "Poll posts are not supported by this publisher"}
        )

    if "dice" in post:
        raise RuntimeError(
            {"description": "Dice posts are not supported by this publisher"}
        )

    raise RuntimeError(
        {"description": "Unsupported channel post type"}
    )


def process():
    check_configuration()

    state = load_json(STATE_FILE, {"offset": 0})
    skipped = load_json(SKIPPED_FILE, {})

    try:
        offset = int(state.get("offset", 0))
    except Exception:
        fail("state.json contains an invalid offset.")

    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    highest = offset - 1
    delivered = 0
    skipped_count = 0

    for update in updates:
        update_id = update["update_id"]
        highest = max(highest, update_id)

        post = update.get("channel_post")
        if not post:
            continue

        chat = post.get("chat", {})
        username = chat.get("username")
        source = f"@{username}" if username else None
        message_id = post.get("message_id")

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unknown source: {source}")
            continue

        print(
            f"Publishing {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION_CHANNEL}"
        )

        try:
            send_post(post, source)
            print("  DIRECT PUBLISH PASS")
            delivered += 1

        except RuntimeError as exc:
            print(f"  DIRECT PUBLISH FAILED: {exc}")

            # One retry for transient Telegram/API issues.
            time.sleep(2)
            try:
                send_post(post, source)
                print("  DIRECT PUBLISH RETRY PASS")
                delivered += 1
            except RuntimeError as retry_exc:
                print(f"  SKIPPED: {retry_exc}")
                skipped[str(update_id)] = {
                    "source": source,
                    "message_id": message_id,
                    "reason": str(retry_exc),
                }
                skipped_count += 1

    save_json(STATE_FILE, {"offset": highest + 1})
    save_json(SKIPPED_FILE, skipped)

    print(
        f"Finished. delivered={delivered}, "
        f"skipped={skipped_count}, next_offset={highest + 1}"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
    else:
        process()
