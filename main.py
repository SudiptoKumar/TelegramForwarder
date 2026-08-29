#!/usr/bin/env python3
"""Native Telegram channel forwarder for GitHub Actions.

No AI, scraping, rewriting, HTML generation, or message reconstruction.
Reads Telegram channel_post updates with getUpdates and forwards them to the
main channel using forwardMessage, falling back to copyMessage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

BOT_API_BASE = "https://api.telegram.org/bot{token}/{method}"
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
DEFAULT_TARGET = os.getenv("TARGET_CHANNEL", "@NewsroomHQ")
DEFAULT_SOURCES = os.getenv("SOURCE_CHANNELS", "@ScienceNewsroom")
HTTP_TIMEOUT = float(os.getenv("TELEGRAM_HTTP_TIMEOUT", "20"))
LONG_POLL_SECONDS = int(os.getenv("GET_UPDATES_TIMEOUT", "5"))
MAX_RETRY_AFTER_SLEEP = int(os.getenv("MAX_RETRY_AFTER_SLEEP", "30"))

TRANSIENT_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class TelegramError(RuntimeError):
    def __init__(self, method: str, error_code: int, description: str, parameters: Optional[dict] = None):
        self.method = method
        self.error_code = int(error_code)
        self.description = description
        self.parameters = parameters or {}
        super().__init__(f"{method}: {self.error_code} {self.description}")

    @property
    def retry_after(self) -> Optional[int]:
        value = self.parameters.get("retry_after")
        return int(value) if value is not None else None

    @property
    def is_transient(self) -> bool:
        return self.error_code in TRANSIENT_CODES or self.error_code >= 500


class NetworkError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatInfo:
    chat_id: int
    username: Optional[str]
    title: Optional[str]


class TelegramAPI:
    def __init__(self, token: str, transport: Optional[Callable[..., Any]] = None) -> None:
        self.token = token
        self._transport = transport

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = HTTP_TIMEOUT) -> Any:
        params = params or {}
        if self._transport is not None:
            return self._transport(method, params, timeout)

        url = BOT_API_BASE.format(token=self.token, method=method)
        encoded = urllib.parse.urlencode({k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()}).encode()
        request = urllib.request.Request(url, data=encoded, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise NetworkError(f"{method}: network failure: {exc}") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise NetworkError(f"{method}: invalid Telegram response (HTTP {status}): {body[:500]}") from exc

        if not payload.get("ok"):
            raise TelegramError(
                method,
                int(payload.get("error_code", status)),
                str(payload.get("description", "Unknown Telegram error")),
                payload.get("parameters") or {},
            )
        return payload.get("result")

    def delete_webhook(self) -> Any:
        return self.call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: Optional[int]) -> List[dict]:
        params: Dict[str, Any] = {
            "timeout": LONG_POLL_SECONDS,
            "limit": 100,
        }
        if offset is not None:
            params["offset"] = offset
        return list(self.call("getUpdates", params, timeout=max(HTTP_TIMEOUT, LONG_POLL_SECONDS + 5)))

    def get_chat(self, chat_ref: str) -> ChatInfo:
        result = self.call("getChat", {"chat_id": chat_ref})
        return ChatInfo(
            chat_id=int(result["id"]),
            username=result.get("username"),
            title=result.get("title"),
        )

    def forward_message(self, target_chat_id: int, source_chat_id: int, message_id: int) -> Any:
        return self.call(
            "forwardMessage",
            {
                "chat_id": target_chat_id,
                "from_chat_id": source_chat_id,
                "message_id": message_id,
            },
        )

    def copy_message(self, target_chat_id: int, source_chat_id: int, message_id: int) -> Any:
        return self.call(
            "copyMessage",
            {
                "chat_id": target_chat_id,
                "from_chat_id": source_chat_id,
                "message_id": message_id,
            },
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_offset(self) -> Optional[int]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read state file {self.path}: {exc}") from exc
        value = data.get("offset")
        return int(value) if value is not None else None

    def save_offset(self, offset: int) -> None:
        payload = {"offset": int(offset)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def parse_sources(raw: str) -> List[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("SOURCE_CHANNELS must contain at least one channel reference")
    return values


def error_text(exc: BaseException) -> str:
    if isinstance(exc, TelegramError):
        extra = f" parameters={exc.parameters}" if exc.parameters else ""
        return f"error_code={exc.error_code} description={exc.description!r}{extra}"
    return str(exc)


def is_temporary(exc: BaseException) -> bool:
    return isinstance(exc, NetworkError) or (isinstance(exc, TelegramError) and exc.is_transient)


def maybe_wait_for_retry(exc: BaseException) -> None:
    if isinstance(exc, TelegramError) and exc.retry_after:
        delay = min(exc.retry_after, MAX_RETRY_AFTER_SLEEP)
        print(f"[TELEGRAM] retry_after={delay}s before fallback attempt")
        time.sleep(delay)
    elif is_temporary(exc):
        time.sleep(1)


def iter_channel_posts(updates: Iterable[dict]) -> Iterable[Tuple[int, dict]]:
    for update in updates:
        update_id = update.get("update_id")
        channel_post = update.get("channel_post")
        if isinstance(update_id, int) and isinstance(channel_post, dict):
            yield update_id, channel_post


def extract_chat_and_message(channel_post: dict) -> Tuple[int, int, Optional[str], Optional[str]]:
    chat = channel_post.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        raise ValueError("channel_post.chat.id missing")
    if "message_id" not in channel_post:
        raise ValueError("channel_post.message_id missing")
    return (
        int(chat["id"]),
        int(channel_post["message_id"]),
        chat.get("username"),
        chat.get("title"),
    )


def should_intentionally_skip_unknown_update(update: dict) -> bool:
    return "channel_post" not in update


def forward_or_copy(
    api: TelegramAPI,
    target_chat_id: int,
    source_chat_id: int,
    message_id: int,
) -> str:
    print(
        f"[FORWARD] source_chat_id={source_chat_id} message_id={message_id} "
        f"target_chat_id={target_chat_id}"
    )
    forward_error: Optional[BaseException] = None
    try:
        api.forward_message(target_chat_id, source_chat_id, message_id)
        print("[FORWARD] forwardMessage SUCCESS")
        return "forwarded"
    except (TelegramError, NetworkError) as exc:
        forward_error = exc
        print(f"[FORWARD] forwardMessage FAILED: {error_text(exc)}")
        maybe_wait_for_retry(exc)

    try:
        api.copy_message(target_chat_id, source_chat_id, message_id)
        print("[FORWARD] copyMessage FALLBACK SUCCESS")
        return "copied"
    except (TelegramError, NetworkError) as copy_error:
        print(f"[FORWARD] copyMessage FALLBACK FAILED: {error_text(copy_error)}")
        if is_temporary(forward_error) or is_temporary(copy_error):
            raise RuntimeError(
                "Temporary Telegram failure; offset must NOT advance. "
                f"forward=({error_text(forward_error)}), copy=({error_text(copy_error)})"
            ) from copy_error
        raise RuntimeError(
            "Non-retryable Telegram failure on both forward/copy; intentional skip required. "
            f"forward=({error_text(forward_error)}), copy=({error_text(copy_error)})"
        ) from copy_error


def run_relay(api: TelegramAPI, state: StateStore, source_ids: set[int], target_chat_id: int) -> int:
    offset = state.load_offset()
    print(f"[STATE] starting offset={offset}")

    # Required for getUpdates long polling. Do not delete pending updates.
    api.delete_webhook()
    print("[TELEGRAM] webhook disabled; pending updates preserved")

    processed = 0
    while True:
        updates = api.get_updates(offset)
        if not updates:
            print(f"[DONE] no more updates; processed={processed} offset={offset}")
            return 0
        print(f"[POLL] received={len(updates)}")

        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                print("[SKIP] update without valid update_id; intentional skip")
                continue

            # Only channel_post is relevant. All other Telegram update types are intentional skips.
            if should_intentionally_skip_unknown_update(update):
                offset = update_id + 1
                state.save_offset(offset)
                print(f"[SKIP] update_id={update_id} non-channel_post; saved offset={offset}")
                continue

            try:
                source_chat_id, message_id, username, title = extract_chat_and_message(update["channel_post"])
            except ValueError as exc:
                # Malformed channel_post is not useful and cannot be forwarded.
                offset = update_id + 1
                state.save_offset(offset)
                print(f"[SKIP] update_id={update_id} malformed channel_post: {exc}; saved offset={offset}")
                continue

            label = f"@{username}" if username else (title or str(source_chat_id))
            print(
                f"[POST] update_id={update_id} source={label} "
                f"chat_id={source_chat_id} message_id={message_id}"
            )

            if source_chat_id not in source_ids:
                offset = update_id + 1
                state.save_offset(offset)
                print(f"[SKIP] unconfigured source chat_id={source_chat_id}; saved offset={offset}")
                continue

            try:
                result = forward_or_copy(api, target_chat_id, source_chat_id, message_id)
            except RuntimeError as exc:
                print(f"[ERROR] update_id={update_id} NOT ACKNOWLEDGED: {exc}")
                print("[ERROR] stopping run so the same update can retry next workflow run")
                return 2

            processed += 1
            offset = update_id + 1
            state.save_offset(offset)
            print(f"[ACK] update_id={update_id} result={result} saved offset={offset}")


def self_test() -> int:
    print("[SELF-TEST] starting")

    class FakeTransport:
        def __init__(self) -> None:
            self.calls: List[Tuple[str, Dict[str, Any]]] = []
            self.forward_fail = False
            self.copy_fail = False

        def __call__(self, method: str, params: Dict[str, Any], timeout: float) -> Any:
            self.calls.append((method, params))
            if method == "deleteWebhook":
                return True
            if method == "getChat":
                ref = str(params["chat_id"])
                if ref == "@ScienceNewsroom":
                    return {"id": -100111, "username": "ScienceNewsroom", "title": "Science News"}
                if ref == "@NewsroomHQ":
                    return {"id": -100222, "username": "NewsroomHQ", "title": "Newsroom HQ"}
                raise TelegramError(method, 400, "Bad Request: chat not found")
            if method == "getUpdates":
                return []
            if method == "forwardMessage" and self.forward_fail:
                raise TelegramError(method, 400, "Bad Request: message cannot be forwarded")
            if method == "copyMessage" and self.copy_fail:
                raise TelegramError(method, 400, "Bad Request: message cannot be copied")
            return {"message_id": 999}

    # Parsing and source configuration
    assert parse_sources("@ScienceNewsroom") == ["@ScienceNewsroom"]
    assert extract_chat_and_message({"chat": {"id": -100111}, "message_id": 44})[1] == 44

    # Successful native forwarding
    transport = FakeTransport()
    api = TelegramAPI("test", transport=transport)
    source = api.get_chat("@ScienceNewsroom")
    target = api.get_chat("@NewsroomHQ")
    api.forward_message(target.chat_id, source.chat_id, 44)
    method, params = transport.calls[-1]
    assert method == "forwardMessage"
    assert params == {"chat_id": -100222, "from_chat_id": -100111, "message_id": 44}

    # Forward failure -> copy fallback
    transport = FakeTransport()
    transport.forward_fail = True
    api = TelegramAPI("test", transport=transport)
    result = forward_or_copy(api, -100222, -100111, 45)
    assert result == "copied"
    assert [name for name, _ in transport.calls][-2:] == ["forwardMessage", "copyMessage"]

    # Temporary error classification must be detected
    temporary = TelegramError("forwardMessage", 429, "Too Many Requests", {"retry_after": 1})
    assert temporary.is_transient is True
    permanent = TelegramError("forwardMessage", 400, "Bad Request")
    assert permanent.is_transient is False

    # Temporary forward+copy failure must be surfaced and must not be acknowledged.
    class TemporaryFailureTransport(FakeTransport):
        def __call__(self, method: str, params: Dict[str, Any], timeout: float) -> Any:
            if method in {"forwardMessage", "copyMessage"}:
                raise TelegramError(method, 503, "Service Unavailable")
            return super().__call__(method, params, timeout)

    transport = TemporaryFailureTransport()
    api = TelegramAPI("test", transport=transport)
    try:
        forward_or_copy(api, -100222, -100111, 46)
        raise AssertionError("temporary failure should have raised")
    except RuntimeError as exc:
        assert "offset must NOT advance" in str(exc)

    # State save/load and atomic replacement
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        store = StateStore(path)
        assert store.load_offset() is None
        store.save_offset(12345)
        assert store.load_offset() == 12345

    # Regression: offset moves after intentional non-channel skip, not after a failed post.
    class QueueTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.get_count = 0

        def __call__(self, method: str, params: Dict[str, Any], timeout: float) -> Any:
            if method == "getUpdates":
                self.get_count += 1
                if self.get_count == 1:
                    return [
                        {"update_id": 1, "message": {"text": "ignore me"}},
                        {"update_id": 2, "channel_post": {"message_id": 10, "chat": {"id": -100111, "username": "ScienceNewsroom"}}},
                    ]
                return []
            return super().__call__(method, params, timeout)

    transport = QueueTransport()
    api = TelegramAPI("test", transport=transport)
    with tempfile.TemporaryDirectory() as td:
        store = StateStore(Path(td) / "state.json")
        result = run_relay(api, store, {-100111}, -100222)
        assert result == 0
        assert store.load_offset() == 3

    print("[SELF-TEST] PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram native channel forwarder")
    parser.add_argument("--self-test", action="store_true", help="run offline regression tests")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[FATAL] TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 1

    source_refs = parse_sources(DEFAULT_SOURCES)
    api = TelegramAPI(token)

    try:
        target = api.get_chat(DEFAULT_TARGET)
        print(f"[CONFIG] target={DEFAULT_TARGET} chat_id={target.chat_id} title={target.title!r}")

        source_ids: set[int] = set()
        for ref in source_refs:
            source = api.get_chat(ref)
            source_ids.add(source.chat_id)
            print(f"[CONFIG] source={ref} chat_id={source.chat_id} title={source.title!r}")

        return run_relay(api, StateStore(STATE_PATH), source_ids, target.chat_id)
    except (TelegramError, NetworkError, RuntimeError, ValueError) as exc:
        print(f"[FATAL] {error_text(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
