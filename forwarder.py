import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

TARGET = "@NewsroomHQ"

SOURCES = {
    "@BusinessNewsroom": "BusinessNewsroom",
    "@GamingNewsroom": "GamingNewsroom",
    "@TheTechNewsroom": "TheTechNewsroom",
    "@EntertainmentNewsroom": "EntertainmentNewsroom",
}

STATE_FILE = Path("telethon_state.json")
SESSION_FILE = "newsroom_forwarder"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("newsroom-hourly")


def fail(message):
    log.error(message)
    raise SystemExit(1)


def validate_env():
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if missing:
        fail("Missing GitHub secrets: " + ", ".join(missing))


def load_state():
    if not STATE_FILE.exists():
        return {"channels": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        data.setdefault("channels", {})
        return data
    except Exception as exc:
        fail(f"Cannot read {STATE_FILE}: {exc}")


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)


async def resolve_entity(client, username):
    return await client.get_entity(username)


async def forward_one(client, target, source_username, source_entity, message):
    for attempt in range(1, 4):
        try:
            result = await client.forward_messages(
                entity=target,
                messages=message,
                from_peer=source_entity,
            )

            destination_id = None
            if isinstance(result, list):
                if result:
                    destination_id = getattr(result[0], "id", None)
            else:
                destination_id = getattr(result, "id", None)

            log.info(
                "FORWARD PASS | source=%s | source_message_id=%s | destination_message_id=%s",
                source_username,
                message.id,
                destination_id,
            )
            return True

        except FloodWaitError as exc:
            log.warning(
                "FLOOD WAIT | source=%s | message_id=%s | seconds=%s",
                source_username,
                message.id,
                exc.seconds,
            )
            # In a one-hour job, sleeping for the requested Telegram wait is
            # safer than losing the message. The workflow timeout is expanded.
            await asyncio.sleep(exc.seconds + 1)

        except RPCError as exc:
            log.warning(
                "TELEGRAM ERROR | attempt=%s/3 | source=%s | message_id=%s | %s",
                attempt,
                source_username,
                message.id,
                exc,
            )
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
            else:
                log.error(
                    "FORWARD FAIL | source=%s | message_id=%s | %s",
                    source_username,
                    message.id,
                    exc,
                )
                return False

        except Exception as exc:
            log.warning(
                "UNEXPECTED ERROR | attempt=%s/3 | source=%s | message_id=%s | %s",
                attempt,
                source_username,
                message.id,
                exc,
            )
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
            else:
                log.exception(
                    "FORWARD FAIL | source=%s | message_id=%s",
                    source_username,
                    message.id,
                )
                return False

    return False


async def process():
    validate_env()
    state = load_state()

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)

    me = await client.get_me()
    log.info(
        "Authenticated | username=@%s | id=%s | bot=%s",
        getattr(me, "username", "unknown"),
        me.id,
        getattr(me, "bot", False),
    )

    target = await resolve_entity(client, TARGET)
    log.info("Target resolved: %s", TARGET)

    total_forwarded = 0
    total_failed = 0

    try:
        for source_username, entity_username in SOURCES.items():
            source = await resolve_entity(client, source_username)

            # Telegram message IDs are monotonically increasing per channel.
            last_id = int(
                state["channels"].get(source_username, {}).get("last_message_id", 0)
            )

            log.info(
                "CHECK | source=%s | last_message_id=%s",
                source_username,
                last_id,
            )

            # iter_messages(reverse=True) lets us process oldest first.
            # min_id excludes the already processed message.
            messages = []
            async for message in client.iter_messages(
                source,
                min_id=last_id,
                reverse=True,
            ):
                messages.append(message)

            log.info(
                "FOUND | source=%s | new_messages=%s",
                source_username,
                len(messages),
            )

            for message in messages:
                if message.id <= last_id:
                    continue

                log.info(
                    "FORWARD ATTEMPT | source=%s | message_id=%s",
                    source_username,
                    message.id,
                )

                ok = await forward_one(
                    client,
                    target,
                    source_username,
                    source,
                    message,
                )

                if not ok:
                    # Stop this source without advancing the failed message.
                    # The next hourly run will retry it.
                    total_failed += 1
                    log.error(
                        "STATE NOT ADVANCED | source=%s | failed_message_id=%s",
                        source_username,
                        message.id,
                    )
                    break

                last_id = message.id
                state["channels"][source_username] = {
                    "last_message_id": last_id
                }
                save_state(state)
                total_forwarded += 1

        log.info(
            "FINISHED | forwarded=%s | failed=%s",
            total_forwarded,
            total_failed,
        )

        if total_failed:
            raise SystemExit(2)

    finally:
        await client.disconnect()


def main():
    try:
        asyncio.run(process())
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
