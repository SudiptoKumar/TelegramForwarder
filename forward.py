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


def load_skipped():
    return load_json(SKIPPED_FILE, {})


def save_skipped(data) -> None:
    SKIPPED_FILE.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def telegram_request(method: str, http_method: str = "GET", **params):
    if not BOT_TOKEN:
        fail(
            "BOT_TOKEN is empty. Add a GitHub Actions repository secret "
            "named BOT_TOKEN."
        )

    try:
        if http_method == "POST":
            response = requests.post(
                f"{API}/{method}",
                data=params,
                timeout=30,
            )
        else:
            response = requests.get(
                f"{API}/{method}",
                params=params,
                timeout=30,
            )
    except requests.RequestException as exc:
        fail(f"Network error calling Telegram {method}: {exc}")

    try:
        payload = response.json()
    except ValueError:
        fail(
            f"Telegram returned non-JSON data for {method}: "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )

    if not payload.get("ok"):
        raise RuntimeError(payload)

    return payload["result"]


def telegram_get(method: str, **params):
    return telegram_request(method, "GET", **params)


def telegram_post(method: str, **params):
    return telegram_request(method, "POST", **params)


def check_configuration() -> None:
    if not BOT_TOKEN:
        fail(
            "BOT_TOKEN is empty. Add Settings -> Secrets and variables -> "
            "Actions -> Repository secret named BOT_TOKEN."
        )

    bot = telegram_get("getMe")
    webhook = telegram_get("getWebhookInfo")

    print(
        f"Telegram bot authenticated: "
        f"@{bot.get('username', 'unknown')} (id={bot.get('id', 'unknown')})"
    )

    if webhook.get("url"):
        fail(
            "A webhook is configured. URL: "
            f"{webhook['url']}. Remove it before using getUpdates."
        )

    print("Webhook check: PASS (url is empty)")
    print(
        "Telegram pending updates: "
        f"{webhook.get('pending_update_count', 0)}"
    )

    destination = telegram_get(
        "getChat",
        chat_id=DESTINATION_CHANNEL,
    )
    print(
        "Destination check: PASS "
        f"({destination.get('title', DESTINATION_CHANNEL)})"
    )

    print("Source channel checks:")
    for channel in sorted(SOURCE_CHANNELS):
        try:
            chat = telegram_get("getChat", chat_id=channel)
            protected = bool(chat.get("has_protected_content", False))
            print(
                f"  {channel}: FOUND, "
                f"protected_content={protected}"
            )
        except RuntimeError as exc:
            fail(f"Cannot access source channel {channel}: {exc}")


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


def process():
    check_configuration()

    offset = load_offset()
    skipped = load_skipped()
    updates = get_updates(offset)

    if not updates:
        print("No new channel posts.")
        return

    highest_update_id = offset - 1
    forwarded = 0
    skipped_count = 0
    unexpected_failures = 0

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
            print(
                f"Skipping update {update_id}: unconfigured source {source}"
            )
            continue

        # Telegram may expose protected-content status on the message.
        # Chat-wide protection is checked during configuration and also here
        # for defense in depth.
        if post.get("has_protected_content") is True:
            reason = "protected message"
            print(
                f"SKIP {source} message {message_id}: {reason}. "
                "Telegram does not permit forwarding protected content."
            )
            skipped[str(update_id)] = {
                "source": source,
                "message_id": message_id,
                "reason": reason,
            }
            skipped_count += 1
            continue

        if str(update_id) in skipped:
            print(
                f"SKIP already recorded update {update_id} "
                f"({source} message {message_id})"
            )
            skipped_count += 1
            continue

        print(
            f"Forwarding {source} message {message_id} "
            f"(update_id={update_id}) -> {DESTINATION_CHANNEL}"
        )

        try:
            forward_message(
                source_chat_id=chat["id"],
                message_id=message_id,
            )
            forwarded += 1
            print("  PASS")
        except RuntimeError as exc:
            details = str(exc)

            # This exact error is commonly produced when Telegram can no
            # longer forward the source message (for example, the message
            # was deleted or the source chat protects its content).
            # Do not let one bad update block every later update.
            if (
                "message to forward not found" in details.lower()
                or "protected content" in details.lower()
                or "chat_forwards_restricted" in details.lower()
            ):
                reason = "Telegram would not allow forwarding this message"
                print(f"  SKIP: {reason}. API response: {details}")
                skipped[str(update_id)] = {
                    "source": source,
                    "message_id": message_id,
                    "reason": reason,
                    "telegram_error": details,
                }
                skipped_count += 1
                continue

            # Retry transient errors once.
            print("  Retrying once after transient Telegram error...")
            time.sleep(2)

            try:
                forward_message(
                    source_chat_id=chat["id"],
                    message_id=message_id,
                )
                forwarded += 1
                print("  PASS on retry")
            except RuntimeError as retry_exc:
                unexpected_failures += 1
                fail(
                    "Forwarding failed for "
                    f"{source} message {message_id}. "
                    f"Telegram error: {retry_exc}"
                )

    save_state(highest_update_id + 1)
    save_skipped(skipped)

    print(
        "Finished successfully. "
        f"Forwarded={forwarded}, skipped={skipped_count}, "
        f"offset={highest_update_id + 1}"
    )

    if unexpected_failures:
        fail(f"Unexpected forwarding failures: {unexpected_failures}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_configuration()
        print("Configuration test: PASS")
        return

    process()


if __name__ == "__main__":
    main()
