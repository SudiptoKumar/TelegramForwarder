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


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        fail(f"Invalid {path.name}: {exc}")


def load_offset() -> int:
    data = load_json(STATE_FILE, {"offset": 0})
    try:
        return int(data.get("offset", 0))
    except (ValueError, TypeError):
        fail("state.json contains an invalid offset")


def save_state(offset: int) -> None:
    STATE_FILE.write_text(
        json.dumps({"offset": offset}, indent=2) + "\n",
        encoding="utf-8",
    )


def save_skipped(data) -> None:
    SKIPPED_FILE.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def tg(method: str, http_method: str = "GET", **params):
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty.")

    try:
        if http_method == "POST":
            r = requests.post(f"{API}/{method}", data=params, timeout=30)
        else:
            r = requests.get(f"{API}/{method}", params=params, timeout=30)
    except requests.RequestException as exc:
        fail(f"Network error calling {method}: {exc}")

    try:
        data = r.json()
    except ValueError:
        fail(f"Telegram returned non-JSON for {method}: HTTP {r.status_code}")

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]


def check_configuration() -> None:
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty. Add repository secret BOT_TOKEN.")

    bot = tg("getMe")
    webhook = tg("getWebhookInfo")

    print(
        f"Telegram bot authenticated: @{bot.get('username', 'unknown')} "
        f"(id={bot.get('id', 'unknown')})"
    )

    if webhook.get("url"):
        fail(f"Webhook is configured: {webhook['url']}")

    print("Webhook check: PASS (url is empty)")
    print(f"Telegram pending updates: {webhook.get('pending_update_count', 0)}")

    dest = tg("getChat", chat_id=DESTINATION_CHANNEL)
    print(f"Destination check: PASS ({dest.get('title', DESTINATION_CHANNEL)})")

    print("Source channel checks:")
    for channel in sorted(SOURCE_CHANNELS):
        try:
            chat = tg("getChat", chat_id=channel)
            print(
                f"  {channel}: FOUND, id={chat.get('id')}, "
                f"protected_content={bool(chat.get('has_protected_content', False))}"
            )
        except RuntimeError as exc:
            fail(f"Cannot access source channel {channel}: {exc}")


def get_updates(offset: int):
    return tg(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def forward_message(source_chat_id: int, message_id: int):
    return tg(
        "forwardMessage",
        "POST",
        chat_id=DESTINATION_CHANNEL,
        from_chat_id=source_chat_id,
        message_id=message_id,
    )


def copy_message(source_chat_id: int, message_id: int):
    return tg(
        "copyMessage",
        "POST",
        chat_id=DESTINATION_CHANNEL,
        from_chat_id=source_chat_id,
        message_id=message_id,
    )


def should_try_copy(error_text: str) -> bool:
    text = error_text.lower()
    return any(
        phrase in text
        for phrase in (
            "message to forward not found",
            "message_id_invalid",
            "message id invalid",
            "message to copy not found",
        )
    )


def process():
    check_configuration()

    offset = load_offset()
    skipped = load_json(SKIPPED_FILE, {})
    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    highest_update_id = offset - 1
    forwarded = 0
    copied = 0
    skipped_count = 0

    for update in updates:
        update_id = update["update_id"]
        highest_update_id = max(highest_update_id, update_id)

        post = update.get("channel_post")
        if not post:
            continue

        chat = post.get("chat", {})
        username = chat.get("username")
        source = f"@{username}" if username else None
        message_id = post.get("message_id")

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unconfigured source {source}")
            continue

        if post.get("has_protected_content") is True:
            print(
                f"SKIP {source} message {message_id}: "
                "protected content cannot be forwarded/copy-delivered."
            )
            skipped[str(update_id)] = {
                "source": source,
                "message_id": message_id,
                "reason": "protected content",
            }
            skipped_count += 1
            continue

        print(
            f"Processing {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION_CHANNEL}"
        )

        try:
            forward_message(chat["id"], message_id)
            print("  FORWARD PASS")
            forwarded += 1
            continue
        except RuntimeError as first_error:
            print(f"  FORWARD FAILED: {first_error}")

            # The real log showed "message to forward not found" for every
            # queued update. Try Telegram copyMessage so a valid source post
            # can still be delivered even when Telegram rejects forwarding.
            if not should_try_copy(str(first_error)):
                raise

        try:
            copy_message(chat["id"], message_id)
            print(
                "  COPY FALLBACK PASS "
                "(message delivered without the Telegram forwarded-from header)"
            )
            copied += 1
        except RuntimeError as copy_error:
            print(f"  COPY FALLBACK FAILED: {copy_error}")
            skipped[str(update_id)] = {
                "source": source,
                "message_id": message_id,
                "reason": "forward and copy failed",
                "telegram_error": str(copy_error),
            }
            skipped_count += 1

    save_state(highest_update_id + 1)
    save_skipped(skipped)

    print(
        "Finished successfully. "
        f"forwarded={forwarded}, copied={copied}, "
        f"skipped={skipped_count}, offset={highest_update_id + 1}"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
    else:
        process()
