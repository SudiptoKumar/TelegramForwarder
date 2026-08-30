import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    RPCError,
)

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
        return {"initialized": False, "channels": {}}

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state is not an object")
        state.setdefault("initialized", False)
        state.setdefault("channels", {})
        return state
    except Exception as exc:
        fail(f"Cannot read {STATE_FILE}: {exc}")


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)


async def get_latest_message_id(client, entity):
    """Return the current highest message ID without scanning history."""
    async for message in client.iter_messages(entity, limit=1):
        return int(message.id)
    return 0


async def initialize_state(client):
    """Initialize to the current channel heads.

    This intentionally does not forward historical posts on first deployment.
    """
    state = {"initialized": True, "channels": {}}

    for username in SOURCES:
        try:
            entity = await client.get_entity(username)
            latest_id = await get_latest_message_id(client, entity)

            if latest_id:
                state["channels"][username] = {
                    "last_message_id": latest_id
                }
                log.info(
                    "INITIALIZE | source=%s | latest_message_id=%s",
                    username,
                    latest_id,
                )
            else:
                state["channels"][username] = {
                    "last_message_id": 0
                }
                log.warning(
                    "INITIALIZE | source=%s | channel has no messages",
                    username,
                )

        except ChannelPrivateError as exc:
            fail(
                f"Cannot access source {username}. "
                f"Make sure the bot/account has access. Telegram: {exc}"
            )
        except FloodWaitError as exc:
            fail(
                f"Telegram requested a flood wait of {exc.seconds}s "
                f"while initializing {username}."
            )
        except RPCError as exc:
            fail(f"Telegram RPC error while initializing {username}: {exc}")
        except Exception as exc:
            fail(
                f"Unexpected error while initializing {username}: "
                f"{type(exc).__name__}: {exc}"
            )

    save_state(state)
    log.info(
        "INITIALIZATION COMPLETE | saved current channel heads; "
        "no historical posts were forwarded."
    )


async def forward_one(client, target, source_username, source_entity, message):
    for attempt in range(1, 4):
        try:
            result = await client.forward_messages(
                entity=target,
                messages=message,
                from_peer=source_entity,
            )

            if isinstance(result, list):
                destination_id = (
                    getattr(result[0], "id", None) if result else None
                )
            else:
                destination_id = getattr(result, "id", None)

            log.info(
                "FORWARD PASS | source=%s | source_message_id=%s | "
                "destination_message_id=%s",
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
            await asyncio.sleep(exc.seconds + 1)

        except RPCError as exc:
            log.warning(
                "FORWARD TELEGRAM ERROR | attempt=%s/3 | source=%s | "
                "message_id=%s | %s",
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
                "FORWARD UNEXPECTED ERROR | attempt=%s/3 | source=%s | "
                "message_id=%s | %s",
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
        "AUTHENTICATED | username=@%s | id=%s | bot=%s",
        getattr(me, "username", "unknown"),
        me.id,
        getattr(me, "bot", False),
    )

    target = await client.get_entity(TARGET)
    log.info("TARGET RESOLVED | %s", TARGET)

    try:
        # First deployment: capture the current heads only.
        if not state.get("initialized"):
            await initialize_state(client)
            return

        total_forwarded = 0
        total_failed = 0

        for source_username in SOURCES:
            try:
                source = await client.get_entity(source_username)
                channel_state = state["channels"].setdefault(
                    source_username,
                    {"last_message_id": 0},
                )
                last_id = int(channel_state.get("last_message_id", 0))

                log.info(
                    "CHECK | source=%s | last_message_id=%s",
                    source_username,
                    last_id,
                )

                # Only request messages newer than the saved ID.
                # No reverse=True full-history scan.
                messages = []
                async for message in client.iter_messages(
                    source,
                    min_id=last_id,
                    limit=100,
                ):
                    if message.id > last_id:
                        messages.append(message)

                messages.reverse()

                log.info(
                    "FOUND | source=%s | new_messages=%s",
                    source_username,
                    len(messages),
                )

                for message in messages:
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
                        total_failed += 1
                        log.error(
                            "STATE NOT ADVANCED | source=%s | "
                            "failed_message_id=%s",
                            source_username,
                            message.id,
                        )
                        break

                    channel_state["last_message_id"] = message.id
                    save_state(state)
                    total_forwarded += 1

            except ChannelPrivateError as exc:
                total_failed += 1
                log.error(
                    "SOURCE ACCESS FAIL | source=%s | %s",
                    source_username,
                    exc,
                )
                break

            except FloodWaitError as exc:
                total_failed += 1
                log.error(
                    "SOURCE FLOOD WAIT | source=%s | seconds=%s",
                    source_username,
                    exc.seconds,
                )
                break

            except RPCError as exc:
                total_failed += 1
                log.error(
                    "SOURCE TELEGRAM ERROR | source=%s | %s",
                    source_username,
                    exc,
                )
                break

            except Exception as exc:
                total_failed += 1
                log.exception(
                    "SOURCE UNEXPECTED ERROR | source=%s",
                    source_username,
                )
                break

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
