# Telegram Science Forwarder

A native Telegram relay bot for forwarding posts from `@ScienceNewsroom` to `@NewsroomHQ`.

## What it does

```text
@ScienceNewsroom
       ↓
Telegram getUpdates
       ↓
channel_post
       ↓
actual chat.id + message_id
       ↓
forwardMessage
       ↓ on failure
copyMessage
       ↓
@NewsroomHQ
```

There is no AI, scraper, rewriting, HTML generation, media downloading, `sendMessage`, or placeholder text.

## Message preservation

The bot forwards or copies the original Telegram message through the Telegram Bot API. It does not reconstruct the message. This preserves Telegram-native text formatting, captions, links, photos, videos, documents, animations, and other supported message content as handled by Telegram.

## State and retry behavior

`state.json` stores the next Telegram update offset:

```json
{
  "offset": 123456
}
```

The offset advances only when an update is:

1. Successfully forwarded with `forwardMessage`.
2. Successfully copied with `copyMessage`.
3. Intentionally skipped because it is not a `channel_post`, comes from an unconfigured source, or is malformed.

On temporary failures such as `429`, `5xx`, or network failures, the offset is not advanced and the workflow exits without acknowledging that update. The same update can retry on the next hourly run.

Every Telegram forwarding error is logged with the HTTP/API error code, description, and parameters when Telegram supplies them.

## Webhook

The bot calls `deleteWebhook` with `drop_pending_updates=false` before polling. Pending updates are preserved. This is required because the bot uses `getUpdates`.

## GitHub Actions

Workflow file:

```text
.github/workflows/newbot.yml
```

It runs:

- Every hour with GitHub Actions `schedule`.
- Manually with `workflow_dispatch`.

After a successful run, `state.json` is committed back to the repository.

## Required Telegram setup

Use the same bot in both channels.

Source:

```text
@ScienceNewsroom
```

Target:

```text
@NewsroomHQ
```

The bot must have the necessary administrator permissions in both channels. The code resolves the configured channel usernames to numeric chat IDs at startup. For each incoming `channel_post`, it then uses the numeric `chat.id` and exact `message_id` contained in that update.

## GitHub Secret

Add one repository secret:

```text
TELEGRAM_BOT_TOKEN
```

## First run

Run the workflow manually once with `workflow_dispatch`. Check the Action log for:

```text
[CONFIG] target=@NewsroomHQ chat_id=...
[CONFIG] source=@ScienceNewsroom chat_id=...
[TELEGRAM] webhook disabled; pending updates preserved
```

Then publish a new test post in `@ScienceNewsroom` and confirm it appears in `@NewsroomHQ`.

## Local tests

```bash
python main.py --self-test
python -m py_compile main.py
```

The self-test is completely offline and does not contact Telegram.
