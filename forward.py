import json
import os
import sys
from pathlib import Path

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]

SOURCE_CHANNELS = {
    "@BusinessNewsroom",
    "@GamingNewsroom",
    "@TheTechNewsroom",
    "@EntertainmentNewsroom",
}

DESTINATION_CHANNEL = "@NewsroomHQ"

STATE_FILE = Path("state.json")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_offset() -> int:
    if not STATE_FILE.exists():
        return 0

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("offset", 0))
    except (ValueError, json.JSONDecodeError):
        print("Invalid state.json. Starting from offset 0.")
        return 0


def save_offset(offset: int) -> None:
    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f, indent=2)
        f.write("\n")

    temp_file.replace(STATE_FILE)


def telegram_get(method: str, **params):
    response = requests.get(
        f"{API}/{method}",
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")

    return data["result"]


def telegram_post(method: str, **data):
    response = requests.post(
        f"{API}/{method}",
        data=data,
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result["result"]


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

        if not username:
            print(
                f"Skipping channel without username: "
                f"chat_id={chat.get('id')}"
            )
            ignored += 1
            continue

        source = f"@{username}"

        if source not in SOURCE_CHANNELS:
            print(f"Skipping unconfigured channel: {source}")
            ignored += 1
            continue

        message_id = post["message_id"]

        print(f"Forwarding {source} message {message_id} -> {DESTINATION_CHANNEL}")

        # State is saved only after every forwarding operation succeeds.
        # If a forwarding operation fails, the update remains pending for
        # the next workflow run rather than being silently lost.
        forward_message(
            source_chat_id=chat["id"],
            message_id=message_id,
        )

        forwarded += 1

    save_offset(highest_update_id + 1)

    print(f"Finished. Forwarded: {forwarded}, ignored: {ignored}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
