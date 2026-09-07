import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.patched import MessageService

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "").strip()
TELETHON_SESSION = os.environ.get("TELETHON_SESSION", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

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
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if missing:
        fail("Missing GitHub secrets: " + ", ".join(missing))


def default_state():
    return {
        "initialized": False,
        "channels": {},
        "footer_message_id": None,
    }


SPECIALTY_FOOTER_HTML = """<p><b>🎯 Want to go deeper? Explore our specialty channels:</b></p>
<p>💼 <a href="https://t.me/BusinessNewsroom">@BusinessNewsroom</a><br>🎮 <a href="https://t.me/GamingNewsroom">@GamingNewsroom</a><br>💻 <a href="https://t.me/TheTechNewsroom">@TheTechNewsroom</a><br>🔭 <a href="https://t.me/ScienceNewsroom">@ScienceNewsroom</a><br>🎬 <a href="https://t.me/EntertainmentNewsroom">@EntertainmentNewsroom</a><br>🎓 <a href="https://t.me/CareerNewsroom">@CareerNewsroom</a><br>🦸 <a href="https://t.me/ComicsNewsroom">@ComicsNewsroom</a><br>🏆 <a href="https://t.me/TheSportsNewsroom">@TheSportsNewsroom</a></p>
<aside>Newsroom, one network, all the news you need.</aside>
<p><b>Stay informed. Stay ahead. 🚀</b></p>"""

BOT_API_BASE = "https://api.telegram.org/bot"



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
    raw_footer_id = state.get("footer_message_id")
    try:
        normalized["footer_message_id"] = int(raw_footer_id) if raw_footer_id is not None else None
    except (TypeError, ValueError):
        normalized["footer_message_id"] = None
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


def bot_api_call(method, data, timeout=30):
    """Call a Telegram Bot API method using the configured bot token."""
    url = f"{BOT_API_BASE}{TELEGRAM_BOT_TOKEN}/{method}"
    encoded = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            if not payload.get("ok"):
                raise RuntimeError(payload.get("description", "Telegram Bot API request failed"))
            return payload.get("result")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
                description = payload.get("description", body)
                parameters = payload.get("parameters", {}) or {}
            except json.JSONDecodeError:
                description = body or str(exc)
                parameters = {}
            retry_after = parameters.get("retry_after")
            if exc.code == 429 and retry_after and attempt < 3:
                log.warning("BOT API RATE LIMIT | method=%s | retry_after=%s", method, retry_after)
                time.sleep(int(retry_after) + 1)
                continue
            if exc.code >= 500 and attempt < 3:
                log.warning("BOT API SERVER ERROR | method=%s | http=%s | attempt=%s/3", method, exc.code, attempt)
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"Bot API HTTP {exc.code}: {description}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                log.warning("BOT API NETWORK ERROR | method=%s | attempt=%s/3 | %s", method, attempt, exc)
                time.sleep(attempt * 2)
                continue
            raise RuntimeError(f"Bot API network error: {exc}") from exc


def delete_footer_with_bot(footer_id):
    """Best-effort deletion of a bot-authored footer via Bot API."""
    result = bot_api_call(
        "deleteMessage",
        {"chat_id": TARGET, "message_id": int(footer_id)},
    )
    return result is True


def post_footer_with_bot():
    """Post the footer as a Telegram Rich Message and return its message ID."""
    rich_message = {"html": SPECIALTY_FOOTER_HTML}
    result = bot_api_call(
        "sendRichMessage",
        {
            "chat_id": TARGET,
            "rich_message": json.dumps(rich_message, ensure_ascii=False),
        },
    )
    if not isinstance(result, dict) or result.get("message_id") is None:
        raise RuntimeError("Telegram Bot API returned no footer message ID")
    return int(result["message_id"])


async def delete_previous_footer(client, target, state):
    """Delete the previous footer by its exact message ID.

    The footer in V1 is posted by the Bot API. Telethon deletion is attempted first
    so the upgrade can also remove a footer created by the previous Telethon version.
    The Bot API is used as a fallback for bot-authored footers.
    """
    footer_id = state.get("footer_message_id")
    if footer_id is None:
        return True

    try:
        await client.delete_messages(target, [int(footer_id)])
        log.info("FOOTER DELETE PASS | method=telethon | message_id=%s", footer_id)
    except FloodWaitError as exc:
        log.error("FOOTER DELETE FLOOD WAIT | message_id=%s | seconds=%s", footer_id, exc.seconds)
        return False
    except RPCError as exc:
        log.warning(
            "FOOTER DELETE TELETHON ERROR | message_id=%s | %s: %s",
            footer_id, type(exc).__name__, exc,
        )
        try:
            deleted = delete_footer_with_bot(footer_id)
            if deleted:
                log.info("FOOTER DELETE PASS | method=bot_api | message_id=%s", footer_id)
            else:
                log.warning("FOOTER DELETE BOT API RETURNED FALSE | message_id=%s", footer_id)
        except Exception as bot_exc:
            name = type(exc).__name__
            if name in {"MessageIdInvalidError", "MessageDeleteForbiddenError", "MessageNotModifiedError"}:
                log.warning("FOOTER DELETE ALREADY GONE | message_id=%s", footer_id)
            else:
                log.error(
                    "FOOTER DELETE BOTH METHODS FAILED | message_id=%s | bot=%s",
                    footer_id, type(bot_exc).__name__,
                )
                return False
    except Exception as exc:
        log.warning(
            "FOOTER DELETE UNEXPECTED ERROR | message_id=%s | %s: %s",
            footer_id, type(exc).__name__, exc,
        )
        try:
            deleted = delete_footer_with_bot(footer_id)
            if not deleted:
                log.error("FOOTER DELETE BOT API RETURNED FALSE | message_id=%s", footer_id)
                return False
            log.info("FOOTER DELETE PASS | method=bot_api | message_id=%s", footer_id)
        except Exception as bot_exc:
            log.error(
                "FOOTER DELETE BOTH METHODS FAILED | message_id=%s | bot=%s",
                footer_id, type(bot_exc).__name__,
            )
            return False

    state["footer_message_id"] = None
    save_state(state)
    return True


async def post_footer(client, target, state):
    """Post the specialty footer as a centered Telegram Rich Message Pull Quote."""
    try:
        footer_id = post_footer_with_bot()
        state["footer_message_id"] = footer_id
        save_state(state)
        log.info("FOOTER POST PASS | method=bot_api_rich_message | message_id=%s", footer_id)
        return True
    except Exception as exc:
        log.error("FOOTER POST TELEGRAM BOT API ERROR | %s: %s", type(exc).__name__, exc)
    return False


async def collect_source_messages(client, state, source_username, source_entity):
    """Collect the oldest next batch of pending messages for one source.

    reverse=True is intentional: it makes Telethon return the oldest messages
    after the saved ID first, so a large historical backlog cannot be skipped.
    """
    channel_state = state["channels"].get(source_username)
    if not isinstance(channel_state, dict) or not channel_state.get("initialized"):
        channel_state = {
            "initialized": True,
            "last_message_id": 0,
        }
        state["channels"][source_username] = channel_state
        state["initialized"] = all(
            state["channels"].get(source, {}).get("initialized") is True
            for source in SOURCES
        )
        save_state(state)
        log.info(
            "BACKFILL INIT | source=%s | starting_message_id=0",
            source_username,
        )

    try:
        last_id = int(channel_state.get("last_message_id", 0))
        if last_id < 0:
            raise ValueError("last_message_id is negative")

        log.info(
            "CHECK | source=%s | last_message_id=%s",
            source_username,
            last_id,
        )

        messages = []
        async for message in client.iter_messages(
            source_entity,
            min_id=last_id,
            limit=100,
            reverse=True,
        ):
            if message.id > last_id:
                messages.append(message)

        log.info(
            "FOUND | source=%s | batch_messages=%s | after_id=%s",
            source_username,
            len(messages),
            last_id,
        )
        return messages, None

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
    return [], 1


def choose_next_source(active_sources, last_source):
    """Choose a source randomly while avoiding consecutive same-source posts."""
    choices = [source for source in active_sources if source != last_source]
    if not choices:
        choices = list(active_sources)
    return random.choice(choices)


async def process_one_message(
    client,
    state,
    target,
    source_username,
    source_entity,
    message,
    posted_urls,
):
    """Process exactly one message and return (ok, forwarded, failed)."""
    channel_state = state["channels"][source_username]
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
        return True, 0, 0

    if url in posted_urls:
        channel_state["last_message_id"] = int(message.id)
        save_state(state)
        log.info(
            "DUPLICATE SKIP | source=%s | message_id=%s | url=%s",
            source_username,
            message.id,
            url,
        )
        return True, 0, 0

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
        log.error(
            "STATE NOT ADVANCED | source=%s | failed_message_id=%s",
            source_username,
            message.id,
        )
        return False, 0, 1

    save_posted_url(url)
    posted_urls.add(url)
    channel_state["last_message_id"] = int(message.id)
    save_state(state)
    return True, 1, 0


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
            bot_info = bot_api_call("getMe", {})
            log.info(
                "BOT AUTHENTICATED | username=@%s | id=%s",
                bot_info.get("username", "unknown"),
                bot_info.get("id", "unknown"),
            )
        except Exception as exc:
            fail(f"Cannot authenticate Telegram bot token: {type(exc).__name__}: {exc}")

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
        posted_urls = load_posted_urls()

        # Remove the previous footer before forwarding so the newly posted footer can
        # always be the final message in @NewsroomHQ.
        footer_deleted = await delete_previous_footer(client, target, state)
        if not footer_deleted:
            fail("Could not remove the previous footer safely; aborting before forwarding.")

        # Build one queue per source, then choose among active sources randomly.
        # A source never gets two consecutive turns while another source has pending
        # messages, so one channel cannot flood the target with its entire backlog.
        source_queues = {}
        blocked_sources = set()
        exhausted_sources = set()

        for source_username in SOURCES:
            source_entity = sources.get(source_username)
            if source_entity is None:
                blocked_sources.add(source_username)
                total_failed += 1
                continue

            messages, failed = await collect_source_messages(
                client,
                state,
                source_username,
                source_entity,
            )
            if failed:
                blocked_sources.add(source_username)
                total_failed += failed
                continue
            source_queues[source_username] = list(messages)
            if not messages:
                exhausted_sources.add(source_username)

        last_source = None
        while True:
            active_sources = [
                source for source, queue in source_queues.items()
                if queue and source not in blocked_sources
            ]

            if not active_sources:
                # Refill sources whose current batch is exhausted. This allows the
                # first run to backfill more than 100 posts per channel without ever
                # reverting to one-channel-at-a-time ordering.
                refill_candidates = [
                    source for source in SOURCES
                    if source in source_queues
                    and source not in blocked_sources
                    and source not in exhausted_sources
                    and not source_queues[source]
                ]
                if not refill_candidates:
                    break

                for source_username in refill_candidates:
                    source_entity = sources[source_username]
                    messages, failed = await collect_source_messages(
                        client,
                        state,
                        source_username,
                        source_entity,
                    )
                    if failed:
                        blocked_sources.add(source_username)
                        total_failed += failed
                        continue
                    source_queues[source_username] = list(messages)
                    if not messages:
                        exhausted_sources.add(source_username)

                continue

            source_username = choose_next_source(active_sources, last_source)
            source_entity = sources[source_username]
            message = source_queues[source_username].pop(0)

            ok, forwarded, failed = await process_one_message(
                client,
                state,
                target,
                source_username,
                source_entity,
                message,
                posted_urls,
            )
            total_forwarded += forwarded
            total_failed += failed
            last_source = source_username

            if not ok:
                blocked_sources.add(source_username)
                log.error(
                    "SOURCE BLOCKED FOR RUN | source=%s | remaining_messages=%s",
                    source_username,
                    len(source_queues[source_username]),
                )
                continue

            if not source_queues[source_username]:
                # The queue may have been exhausted. We intentionally fetch the next
                # oldest batch before declaring the source finished.
                messages, refill_failed = await collect_source_messages(
                    client,
                    state,
                    source_username,
                    source_entity,
                )
                if refill_failed:
                    blocked_sources.add(source_username)
                    total_failed += refill_failed
                else:
                    source_queues[source_username].extend(messages)
                    if not messages:
                        exhausted_sources.add(source_username)

        state["initialized"] = all(
            state["channels"].get(username, {}).get("initialized") is True
            for username in SOURCES
        )
        save_state(state)

        # Always recreate the footer at the end of the cycle, even when one or more
        # source channels failed. This preserves the user's requested final-message behavior.
        footer_posted = await post_footer(client, target, state)
        if not footer_posted:
            total_failed += 1

        log.info(
            "FINISHED | initialized=%s | forwarded=%s | failed=%s | footer_posted=%s | posted_urls=%s",
            state["initialized"],
            total_forwarded,
            total_failed,
            footer_posted,
            len(load_posted_urls()),
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
