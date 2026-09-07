import asyncio
import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import NamedTemporaryFile

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


SPECIALTY_FOOTER_HTML = """<h2>🧭 Where do you want to go?</h2>
<p><b>Choose your next feed.</b><br>Tap a category and jump straight into the newsroom.</p>
<p><b>YOUR INTERESTS</b></p>
<tg-button-row>
  <tg-button type="url" url="https://t.me/BusinessNewsroom">💼 Business</tg-button>
  <tg-button type="url" url="https://t.me/TheTechNewsroom">💻 Technology</tg-button>
</tg-button-row>
<tg-button-row>
  <tg-button type="url" url="https://t.me/GamingNewsroom">🎮 Gaming</tg-button>
  <tg-button type="url" url="https://t.me/ScienceNewsroom">🔭 Science</tg-button>
</tg-button-row>
<tg-button-row>
  <tg-button type="url" url="https://t.me/EntertainmentNewsroom">🎬 Entertainment</tg-button>
  <tg-button type="url" url="https://t.me/CareerNewsroom">🎓 Career</tg-button>
</tg-button-row>
<tg-button-row>
  <tg-button type="url" url="https://t.me/ComicsNewsroom">🦸 Comics</tg-button>
  <tg-button type="url" url="https://t.me/TheSportsNewsroom">🏆 Sports</tg-button>
</tg-button-row>
<details>
  <summary>Explore the full Newsroom network</summary>
  <p>Business · Gaming · Technology · Science · Entertainment · Career · Comics · Sports</p>
</details>
<aside>One network. Eight ways to stay ahead.</aside>
<p><b>🚀 Start exploring.</b></p>"""

BOT_API_BASE = "https://api.telegram.org/bot"



def bot_api_call(method, data=None, timeout=30, retries=3):
    """Call the Telegram Bot API without adding another Python dependency."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"{BOT_API_BASE}{TELEGRAM_BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(
                    f"Bot API {method} failed: {payload.get('description', 'unknown error')}"
                )
            return payload["result"]
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
                payload = json.loads(body)
                description = payload.get("description", str(exc))
            except Exception:
                description = str(exc)
            last_error = RuntimeError(f"Bot API {method} HTTP {exc.code}: {description}")
            if exc.code < 500 and exc.code != 429:
                raise last_error
            if attempt < retries:
                retry_after = 2
                try:
                    retry_after = int(payload.get("parameters", {}).get("retry_after", retry_after))
                except Exception:
                    pass
                time.sleep(max(1, retry_after))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(attempt * 2)
            else:
                break

    raise last_error or RuntimeError(f"Bot API {method} failed")


def bot_delete_footer(footer_id):
    """Best-effort Bot API deletion for a footer previously sent by the bot."""
    result = bot_api_call(
        "deleteMessage",
        {"chat_id": TARGET, "message_id": int(footer_id)},
    )
    return bool(result)


def bot_send_footer():
    """Send the footer through Rich Messages using InputRichMessage.html only."""
    rich_message = json.dumps(
        {
            "html": SPECIALTY_FOOTER_HTML,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return bot_api_call(
        "sendRichMessage",
        {
            "chat_id": TARGET,
            "rich_message": rich_message,
        },
    )


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


async def delete_previous_footer(client, target, state):
    """Delete the previous footer by its exact destination message ID.

    Try the Telethon user session first for backwards compatibility with older
    footer messages. If that fails, try Bot API deletion for bot-authored footers.
    """
    footer_id = state.get("footer_message_id")
    if footer_id is None:
        return True

    footer_id = int(footer_id)
    telethon_error = None
    try:
        await client.delete_messages(target, [footer_id])
        log.info("FOOTER DELETE PASS | method=telethon | message_id=%s", footer_id)
        state["footer_message_id"] = None
        save_state(state)
        return True
    except FloodWaitError as exc:
        telethon_error = exc
        log.warning("FOOTER DELETE TELETHON FLOOD WAIT | message_id=%s | seconds=%s", footer_id, exc.seconds)
    except RPCError as exc:
        telethon_error = exc
        log.warning(
            "FOOTER DELETE TELETHON ERROR | message_id=%s | %s: %s",
            footer_id, type(exc).__name__, exc,
        )
    except Exception as exc:
        telethon_error = exc
        log.warning(
            "FOOTER DELETE TELETHON UNEXPECTED | message_id=%s | %s: %s",
            footer_id, type(exc).__name__, exc,
        )

    try:
        if bot_delete_footer(footer_id):
            log.info("FOOTER DELETE PASS | method=bot_api | message_id=%s", footer_id)
            state["footer_message_id"] = None
            save_state(state)
            return True
    except Exception as exc:
        log.warning(
            "FOOTER DELETE BOT API ERROR | message_id=%s | %s: %s",
            footer_id, type(exc).__name__, exc,
        )

    # If Telegram already says the message ID is invalid, it is safe to clear the pointer.
    if isinstance(telethon_error, RPCError) and type(telethon_error).__name__ in {
        "MessageIdInvalidError",
        "MessageDeleteForbiddenError",
        "MessageNotModifiedError",
    }:
        state["footer_message_id"] = None
        save_state(state)
        log.info("FOOTER DELETE ASSUMED ALREADY REMOVED | message_id=%s", footer_id)
        return True

    return False


async def post_footer(state):
    """Post the specialty footer through Bot API sendRichMessage and store its ID."""
    try:
        result = bot_send_footer()
        footer_id = result.get("message_id") if isinstance(result, dict) else None
        if footer_id is None:
            raise RuntimeError("Telegram Bot API returned no footer message_id")
        state["footer_message_id"] = int(footer_id)
        save_state(state)
        log.info("FOOTER POST PASS | method=bot_api | message_id=%s", footer_id)
        return True
    except Exception as exc:
        log.exception("FOOTER POST BOT API ERROR | %s: %s", type(exc).__name__, exc)
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
        footer_posted = await post_footer(state)
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
