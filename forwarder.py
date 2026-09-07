import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.patched import MessageService

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "").strip()
TELETHON_SESSION = os.environ.get("TELETHON_SESSION", "").strip()

TARGET = "@NewsroomHQ"

SOURCES = {
    "@BusinessNewsroom": "BusinessNewsroom",
    "@GamingNewsroom": "GamingNewsroom",
    "@TheTechNewsroom": "TheTechNewsroom",
    "@EntertainmentNewsroom": "EntertainmentNewsroom",
    "@ScienceNewsroom": "ScienceNewsroom",
    "@CareerNewsroom": "CareerNewsroom",
    "@ComicsNewsroom": "ComicsNewsroom",
    "@TheSportsNewsroom": "TheSportsNewsroom",
}

STATE_FILE = Path("telethon_state.json")
POSTED_URLS_FILE = Path("posted_urls.txt")
SESSION_NAME = "newsroom_forwarder"

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
    if not TELETHON_SESSION:
        missing.append("TELETHON_SESSION")
    if missing:
        fail("Missing GitHub secrets: " + ", ".join(missing))


def default_state():
    return {
        "initialized": False,
        "channels": {},
    }


def normalize_channel_state(value):
    if not isinstance(value, dict):
        return None
    initialized = bool(value.get("initialized", False))
    raw_last_id = value.get("last_message_id")
    try:
        last_id = int(raw_last_id)
    except (TypeError, ValueError):
        last_id = None
    if initialized and last_id is not None and last_id >= 0:
        return {
            "initialized": True,
            "last_message_id": last_id,
        }
    return {
        "initialized": False,
    }


def load_posted_urls():
    if not POSTED_URLS_FILE.exists():
        return set()
    try:
        return {
            line.strip()
            for line in POSTED_URLS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError as exc:
        fail(f"Cannot read {POSTED_URLS_FILE}: {exc}")


def save_posted_url(url):
    POSTED_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with POSTED_URLS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(url + "\n")


def message_url(source_username, message_id):
    return f"https://t.me/{source_username.lstrip('@')}/{int(message_id)}"


def is_forwardable_message(message):
    if isinstance(message, MessageService):
        return False
    if getattr(message, "empty", False):
        return False
    return True


def load_state():
    if not STATE_FILE.exists():
        return default_state()

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state is not an object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        fail(f"Cannot read {STATE_FILE}: {exc}")

    channels = state.get("channels", {})
    if not isinstance(channels, dict):
        channels = {}

    normalized = default_state()
    normalized["channels"] = channels
    for username in SOURCES:
        if username in channels:
            channel_state = normalize_channel_state(channels[username])
            if channel_state is not None:
                normalized["channels"][username] = channel_state

    normalized["initialized"] = all(
        normalized["channels"].get(username, {}).get("initialized") is True
        for username in SOURCES
    )
    return normalized


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=STATE_FILE.parent,
        prefix=f".{STATE_FILE.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(STATE_FILE)


def mark_channel_initialized(state, username, latest_id):
    state["channels"][username] = {
        "initialized": True,
        "last_message_id": int(latest_id),
    }
    state["initialized"] = all(
        state["channels"].get(source, {}).get("initialized") is True
        for source in SOURCES
    )


async def get_latest_message_id(client, entity):
    """Return the current highest message ID without scanning history."""
    async for message in client.iter_messages(entity, limit=1):
        return int(message.id)
    return 0


async def initialize_channel(client, state, username, entity):
    """Safely establish a baseline for one source without forwarding history."""
    try:
        latest_id = await get_latest_message_id(client, entity)
        mark_channel_initialized(state, username, latest_id)
        save_state(state)
        log.info(
            "INITIALIZE PASS | source=%s | baseline_message_id=%s",
            username,
            latest_id,
        )
        return True
    except ChannelPrivateError as exc:
        log.error(
            "INITIALIZE ACCESS FAIL | source=%s | ChannelPrivateError: %s",
            username,
            exc,
        )
    except FloodWaitError as exc:
        log.error(
            "INITIALIZE FLOOD WAIT | source=%s | seconds=%s",
            username,
            exc.seconds,
        )
    except RPCError as exc:
        log.error(
            "INITIALIZE TELEGRAM ERROR | source=%s | %s: %s",
            username,
            type(exc).__name__,
            exc,
        )
    except Exception as exc:
        log.exception(
            "INITIALIZE UNEXPECTED ERROR | source=%s | %s",
            username,
            type(exc).__name__,
        )
    return False


async def resolve_sources(client):
    """Resolve all sources independently and return only successful entities."""
    resolved = {}
    for username in SOURCES:
        try:
            entity = await client.get_entity(username)
            resolved[username] = entity
            log.info("SOURCE ACCESS PASS | %s accessible", username)
        except ChannelPrivateError as exc:
            log.error(
                "SOURCE ACCESS FAIL | source=%s | ChannelPrivateError: %s",
                username,
                exc,
            )
        except FloodWaitError as exc:
            log.error(
                "SOURCE ACCESS FLOOD WAIT | source=%s | seconds=%s",
                username,
                exc.seconds,
            )
        except RPCError as exc:
            log.error(
                "SOURCE ACCESS TELEGRAM ERROR | source=%s | %s: %s",
                username,
                type(exc).__name__,
                exc,
            )
        except Exception as exc:
            log.exception(
                "SOURCE ACCESS UNEXPECTED ERROR | source=%s | %s",
                username,
                type(exc).__name__,
            )
    return resolved


async def forward_one(client, target, source_username, source_entity, message):
    for attempt in range(1, 4):
        try:
            result = await client.forward_messages(
                entity=target,
                messages=message,
                from_peer=source_entity,
            )

            if isinstance(result, list):
                destination_id = getattr(result[0], "id", None) if result else None
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
                "FLOOD WAIT | source=%s | message_id=%s | seconds=%s | attempt=%s/3",
                source_username,
                message.id,
                exc.seconds,
                attempt,
            )
            if attempt < 3:
                await asyncio.sleep(exc.seconds + 1)
            else:
                log.error(
                    "FORWARD FAIL | source=%s | message_id=%s | FloodWaitError",
                    source_username,
                    message.id,
                )
                return False

        except RPCError as exc:
            log.warning(
                "FORWARD TELEGRAM ERROR | attempt=%s/3 | source=%s | message_id=%s | %s: %s",
                attempt,
                source_username,
                message.id,
                type(exc).__name__,
                exc,
            )
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
            else:
                log.error(
                    "FORWARD FAIL | source=%s | message_id=%s | %s",
                    source_username,
                    message.id,
                    type(exc).__name__,
                )
                return False

        except Exception as exc:
            log.warning(
                "FORWARD UNEXPECTED ERROR | attempt=%s/3 | source=%s | message_id=%s | %s: %s",
                attempt,
                source_username,
                message.id,
                type(exc).__name__,
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


async def process_source(client, state, target, source_username, source_entity):
    channel_state = state["channels"].get(source_username)
    if not isinstance(channel_state, dict) or not channel_state.get("initialized"):
        log.warning(
            "SOURCE NOT INITIALIZED | source=%s | establishing safe baseline; no history will be forwarded",
            source_username,
        )
        await initialize_channel(client, state, source_username, source_entity)
        return 0, 0

    try:
        last_id = int(channel_state.get("last_message_id", 0))
        if last_id < 0:
            raise ValueError("last_message_id is negative")

        log.info(
            "CHECK | source=%s | last_message_id=%s",
            source_username,
            last_id,
        )

        posted_urls = load_posted_urls()
        messages = []
        async for message in client.iter_messages(
            source_entity,
            min_id=last_id,
            limit=100,
        ):
            if message.id > last_id:
                messages.append(message)
        messages.reverse()

        log.info(
            "FOUND | source=%s | batch_messages=%s | after_id=%s",
            source_username,
            len(messages),
            last_id,
        )

        total_forwarded = 0
        total_failed = 0

        for message in messages:
            url = message_url(source_username, message.id)

            if not is_forwardable_message(message):
                channel_state["last_message_id"] = int(message.id)
                save_state(state)
                log.info(
                    "SKIP NON-NEWS | source=%s | message_id=%s | type=%s",
                    source_username,
                    message.id,
                    type(message).__name__,
                )
                continue

            if url in posted_urls:
                channel_state["last_message_id"] = int(message.id)
                save_state(state)
                log.info(
                    "DUPLICATE SKIP | source=%s | message_id=%s | url=%s",
                    source_username,
                    message.id,
                    url,
                )
                continue

            log.info(
                "FORWARD ATTEMPT | source=%s | message_id=%s | url=%s",
                source_username,
                message.id,
                url,
            )
            ok = await forward_one(
                client,
                target,
                source_username,
                source_entity,
                message,
            )
            if not ok:
                total_failed += 1
                log.error(
                    "STATE NOT ADVANCED | source=%s | failed_message_id=%s",
                    source_username,
                    message.id,
                )
                break

            save_posted_url(url)
            posted_urls.add(url)
            channel_state["last_message_id"] = int(message.id)
            save_state(state)
            total_forwarded += 1

        return total_forwarded, total_failed

    except ChannelPrivateError as exc:
        log.error(
            "SOURCE ACCESS FAIL | source=%s | ChannelPrivateError: %s",
            source_username,
            exc,
        )
    except FloodWaitError as exc:
        log.error(
            "SOURCE FLOOD WAIT | source=%s | seconds=%s",
            source_username,
            exc.seconds,
        )
    except RPCError as exc:
        log.error(
            "SOURCE TELEGRAM ERROR | source=%s | %s: %s",
            source_username,
            type(exc).__name__,
            exc,
        )
    except Exception as exc:
        log.exception(
            "SOURCE UNEXPECTED ERROR | source=%s | %s",
            source_username,
            type(exc).__name__,
        )
    return 0, 1


async def process():
    validate_env()
    state = load_state()

    client = TelegramClient(
        StringSession(TELETHON_SESSION),
        API_ID,
        API_HASH,
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            fail(
                "TELETHON_SESSION is not authorized. Generate a user StringSession "
                "with the same API_ID/API_HASH and store it as the TELETHON_SESSION secret."
            )

        me = await client.get_me()
        log.info(
            "AUTHENTICATED | username=@%s | id=%s | bot=%s",
            getattr(me, "username", "unknown"),
            me.id,
            getattr(me, "bot", False),
        )
        if getattr(me, "bot", False):
            fail(
                "TELETHON_SESSION belongs to a bot account. A user account session is "
                "required for this history-polling implementation."
            )

        try:
            target = await client.get_entity(TARGET)
            log.info("TARGET RESOLVED | %s", TARGET)
        except Exception as exc:
            fail(f"Cannot resolve target {TARGET}: {type(exc).__name__}: {exc}")

        sources = await resolve_sources(client)
        if not sources:
            fail("No source channels are accessible; nothing can be processed safely.")

        total_forwarded = 0
        total_failed = 0

        # Each channel is independent. A missing baseline is initialized safely and skipped.
        for source_username in SOURCES:
            source_entity = sources.get(source_username)
            if source_entity is None:
                total_failed += 1
                continue

            forwarded, failed = await process_source(
                client,
                state,
                target,
                source_username,
                source_entity,
            )
            total_forwarded += forwarded
            total_failed += failed

        state["initialized"] = all(
            state["channels"].get(username, {}).get("initialized") is True
            for username in SOURCES
        )
        save_state(state)

        log.info(
            "FINISHED | initialized=%s | forwarded=%s | failed=%s | posted_urls=%s",
            state["initialized"],
            total_forwarded,
            total_failed,
            len(load_posted_urls()),
        )

        # The cycle stays fault-tolerant. Fail visibly at the end if any channel failed,
        # allowing GitHub Actions to mark the run red while still persisting valid state.
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
