import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FloodWaitError, RPCError
from telethon.sessions import StringSession

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
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
    return {"initialized": False, "channels": {}, "posted_urls": []}


def normalize_channel_state(value):
    if not isinstance(value, dict):
        return {"initialized": False, "last_message_id": 0}
    try:
        last_id = int(value.get("last_message_id", 0))
    except (TypeError, ValueError):
        last_id = 0
    return {
        "initialized": bool(value.get("initialized", False)),
        "last_message_id": max(0, last_id),
    }


def normalize_urls(value):
    if not isinstance(value, list):
        return []
    seen = set()
    result = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in seen:
            url = item.strip()
            seen.add(url)
            result.append(url)
    return result


def load_posted_urls_file():
    if not POSTED_URLS_FILE.exists():
        return []
    try:
        lines = POSTED_URLS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"Cannot read {POSTED_URLS_FILE}: {exc}")
    return normalize_urls(lines)


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def save_posted_urls(urls):
    atomic_write_text(POSTED_URLS_FILE, "".join(f"{u}\n" for u in normalize_urls(urls)))


def load_state():
    if not STATE_FILE.exists():
        state = default_state()
    else:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"Cannot read {STATE_FILE}: {exc}")
        if not isinstance(state, dict):
            fail(f"Invalid state format in {STATE_FILE}: expected object")

    normalized = default_state()
    raw_channels = state.get("channels", {})
    if isinstance(raw_channels, dict):
        for username in SOURCES:
            if username in raw_channels:
                normalized["channels"][username] = normalize_channel_state(raw_channels[username])

    urls = normalize_urls(state.get("posted_urls", []))
    # Merge legacy/dedup history file if present.
    urls = normalize_urls(urls + load_posted_urls_file())
    normalized["posted_urls"] = urls
    normalized["initialized"] = all(
        normalized["channels"].get(username, {}).get("initialized") is True
        for username in SOURCES
    )
    return normalized


def save_state(state):
    state["posted_urls"] = normalize_urls(state.get("posted_urls", []))
    state["initialized"] = all(
        state["channels"].get(username, {}).get("initialized") is True
        for username in SOURCES
    )
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(STATE_FILE, payload)
    save_posted_urls(state["posted_urls"])


def build_message_url(username, message_id):
    return f"https://t.me/{username.lstrip('@')}/{int(message_id)}"


async def resolve_sources(client):
    resolved = {}
    for username in SOURCES:
        try:
            entity = await client.get_entity(username)
            resolved[username] = entity
            log.info("SOURCE ACCESS PASS | %s accessible", username)
        except FloodWaitError as exc:
            log.error("SOURCE ACCESS FLOOD WAIT | source=%s | seconds=%s", username, exc.seconds)
        except ChannelPrivateError as exc:
            log.error("SOURCE ACCESS FAIL | source=%s | ChannelPrivateError: %s", username, exc)
        except RPCError as exc:
            log.error("SOURCE ACCESS TELEGRAM ERROR | source=%s | %s: %s", username, type(exc).__name__, exc)
        except Exception as exc:
            log.exception("SOURCE ACCESS UNEXPECTED ERROR | source=%s | %s", username, type(exc).__name__)
    return resolved


async def forward_one(client, target, source_username, source_entity, message):
    for attempt in range(1, 4):
        try:
            result = await client.forward_messages(entity=target, messages=message, from_peer=source_entity)
            destination_id = None
            if isinstance(result, list):
                destination_id = getattr(result[0], "id", None) if result else None
            else:
                destination_id = getattr(result, "id", None)
            log.info(
                "FORWARD PASS | source=%s | source_message_id=%s | destination_message_id=%s",
                source_username, message.id, destination_id,
            )
            return True
        except FloodWaitError as exc:
            log.warning("FLOOD WAIT | source=%s | message_id=%s | seconds=%s | attempt=%s/3", source_username, message.id, exc.seconds, attempt)
            if attempt < 3:
                await asyncio.sleep(exc.seconds + 1)
            else:
                return False
        except RPCError as exc:
            log.warning("FORWARD TELEGRAM ERROR | attempt=%s/3 | source=%s | message_id=%s | %s: %s", attempt, source_username, message.id, type(exc).__name__, exc)
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
            else:
                return False
        except Exception as exc:
            log.warning("FORWARD UNEXPECTED ERROR | attempt=%s/3 | source=%s | message_id=%s | %s: %s", attempt, source_username, message.id, type(exc).__name__, exc)
            if attempt < 3:
                await asyncio.sleep(3 * attempt)
            else:
                return False
    return False


async def process_source(client, state, target, source_username, source_entity):
    channel_state = state["channels"].setdefault(source_username, {"initialized": False, "last_message_id": 0})
    last_id = max(0, int(channel_state.get("last_message_id", 0)))

    if not channel_state.get("initialized"):
        # Backfill mode: initialize at zero, then immediately process all existing history.
        channel_state["initialized"] = True
        channel_state["last_message_id"] = last_id
        save_state(state)
        log.info("BACKFILL START | source=%s | starting_message_id=%s", source_username, last_id)

    total_forwarded = 0
    total_failed = 0

    while True:
        batch = []
        try:
            async for message in client.iter_messages(
                source_entity,
                min_id=last_id,
                limit=100,
                reverse=True,
            ):
                if message.id > last_id:
                    batch.append(message)
        except FloodWaitError as exc:
            log.error("SOURCE FLOOD WAIT | source=%s | seconds=%s", source_username, exc.seconds)
            return total_forwarded, total_failed + 1
        except ChannelPrivateError as exc:
            log.error("SOURCE ACCESS FAIL | source=%s | ChannelPrivateError: %s", source_username, exc)
            return total_forwarded, total_failed + 1
        except RPCError as exc:
            log.error("SOURCE TELEGRAM ERROR | source=%s | %s: %s", source_username, type(exc).__name__, exc)
            return total_forwarded, total_failed + 1
        except Exception as exc:
            log.exception("SOURCE UNEXPECTED ERROR | source=%s | %s", source_username, type(exc).__name__)
            return total_forwarded, total_failed + 1

        if not batch:
            log.info("FOUND | source=%s | new_messages=0 | last_message_id=%s", source_username, last_id)
            return total_forwarded, total_failed

        log.info("FOUND | source=%s | batch_messages=%s | after_id=%s", source_username, len(batch), last_id)

        for message in batch:
            message_id = int(message.id)
            url = build_message_url(source_username, message_id)
            if url in state["posted_urls"]:
                last_id = max(last_id, message_id)
                channel_state["last_message_id"] = last_id
                save_state(state)
                log.info("DUPLICATE SKIP | source=%s | message_id=%s | url=%s", source_username, message_id, url)
                continue

            log.info("FORWARD ATTEMPT | source=%s | message_id=%s | url=%s", source_username, message_id, url)
            ok = await forward_one(client, target, source_username, source_entity, message)
            if not ok:
                total_failed += 1
                log.error("STATE NOT ADVANCED | source=%s | failed_message_id=%s", source_username, message_id)
                return total_forwarded, total_failed

            state["posted_urls"].append(url)
            state["posted_urls"] = normalize_urls(state["posted_urls"])
            last_id = message_id
            channel_state["last_message_id"] = last_id
            save_state(state)
            total_forwarded += 1

        if len(batch) < 100:
            log.info("SOURCE CAUGHT UP | source=%s | last_message_id=%s", source_username, last_id)
            return total_forwarded, total_failed


async def process():
    validate_env()
    state = load_state()

    client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            fail("TELETHON_SESSION is not authorized.")
        me = await client.get_me()
        log.info("AUTHENTICATED | username=@%s | id=%s | bot=%s", getattr(me, "username", "unknown"), me.id, getattr(me, "bot", False))
        if getattr(me, "bot", False):
            fail("TELETHON_SESSION belongs to a bot account; a user account is required.")

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
        for source_username in SOURCES:
            source_entity = sources.get(source_username)
            if source_entity is None:
                total_failed += 1
                continue
            forwarded, failed = await process_source(client, state, target, source_username, source_entity)
            total_forwarded += forwarded
            total_failed += failed

        save_state(state)
        log.info("FINISHED | initialized=%s | forwarded=%s | failed=%s | posted_urls=%s", state["initialized"], total_forwarded, total_failed, len(state["posted_urls"]))

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
