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
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_offset() -> int:
    if not STATE_FILE.exists():
        return 0

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("offset", 0))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        fail(f"Invalid state.json: {exc}")


def save_offset(offset: int) -> None:
    temp_file = STATE_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f, indent=2)
        f.write("\n")
    temp_file.replace(STATE_FILE)


def telegram_get(method: str, **params):
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty. Add a GitHub Actions secret named BOT_TOKEN.")

    try:
        response = requests.get(
            f"{API}/{method}",
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        fail(f"Network error calling Telegram {method}: {exc}")

    if response.status_code != 200:
        detail = response.text[:500]
        fail(
            f"Telegram returned HTTP {response.status_code} for {method}: {detail}"
        )

    try:
        data = response.json()
    except ValueError:
        fail(f"Telegram returned non-JSON data for {method}: {response.text[:500]}")

    if not data.get("ok"):
        fail(f"Telegram API error in {method}: {data}")

    return data["result"]


def telegram_post(method: str, **data):
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty. Add a GitHub Actions secret named BOT_TOKEN.")

    try:
        response = requests.post(
            f"{API}/{method}",
            data=data,
            timeout=30,
        )
    except requests.RequestException as exc:
        fail(f"Network error calling Telegram {method}: {exc}")

    if response.status_code != 200:
        detail = response.text[:500]
        fail(
            f"Telegram returned HTTP {response.status_code} for {method}: {detail}"
        )

    try:
        result = response.json()
    except ValueError:
        fail(f"Telegram returned non-JSON data for {method}: {response.text[:500]}")

    if not result.get("ok"):
        fail(f"Telegram API error in {method}: {result}")

    return result["result"]


def check_configuration() -> None:
    if not BOT_TOKEN:
        fail("BOT_TOKEN is empty. In GitHub, create Settings -> Secrets and variables -> Actions -> Repository secret named BOT_TOKEN.")

    bot = telegram_get("getMe")
    username = bot.get("username", "unknown")
    bot_id = bot.get("id", "unknown")

    webhook = telegram_get("getWebhookInfo")
    webhook_url = webhook.get("url", "")

    print(f"Telegram bot authenticated: @{username} (id={bot_id})")

    if webhook_url:
        fail(
            "A Telegram webhook is configured. "
            f"Webhook URL: {webhook_url}. "
            "Remove it with deleteWebhook before using getUpdates."
        )

    print("Webhook check: PASS (url is empty)")
    print(
        f"Telegram pending updates: "
        f"{webhook.get('pending_update_count', 0)}"
    )

    # Verify the destination is resolvable. The bot also needs permission to
    # post there, which can only be confirmed by a real forwarding/send call.
    destination = telegram_get("getChat", chat_id=DESTINATION_CHANNEL)
    print(
        f"Destination check: PASS "
        f"({destination.get('title', DESTINATION_CHANNEL)})"
    )


def get_updates(offset: int):
    return telegram_get(
        "getUpdates",
        offset=offset,
        limit=100,
        timeout=0,
        allowed_updates=json.dumps(["channel_post"]),
    )


def forward_message(source_chat_id: int, message_id: int):
    return telegram_post(
        "forwardMessage",
        chat_id=DESTINATION_CHANNEL,
        from_chat_id=source_chat_id,
        message_id=message_id,
    )


def main():
    check_only = len(sys.argv) > 1 and sys.argv[1] == "--check"

    check_configuration()

    if check_only:
        print("Configuration test: PASS")
        return

    offset = load_offset()
    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    highest_update_id = offset - 1
    forwarded = 0
    ignored = 0

    for update in updates:
        update_id = update["update_id"]
        highest_update_id = max(highest_update_id, update_id)

        post = update.get("channel_post")
        if not post:
            ignored += 1
            continue

        chat = post.get("chat", {})
        username = chat.get("username")
        source = f"@{username}" if username else None

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unconfigured channel update {update_id}: {source}")
            ignored += 1
            continue

        message_id = post["message_id"]

        print(
            f"Forwarding {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION_CHANNEL}"
        )

        forward_message(
            source_chat_id=chat["id"],
            message_id=message_id,
        )

        forwarded += 1

    # Only advance the offset after all updates in this batch have been
    # handled successfully. This avoids silently losing an update when a
    # forwarding call fails.
    save_offset(highest_update_id + 1)

    print(
        f"Finished successfully. Forwarded={forwarded}, ignored={ignored}, "
        f"new_offset={highest_update_id + 1}"
    )


if __name__ == "__main__":
    main()
