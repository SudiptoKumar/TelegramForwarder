# Telethon + GitHub Actions Hourly Forwarder v2

## Purpose

Every hour, check these four Telegram source channels and natively forward
new posts to `@NewsroomHQ`:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`

## Why v2

The previous hourly version started with `last_message_id=0` and attempted to
scan channel history. That caused the source-history read to fail before the
forwarding stage.

v2 has a safe first-run initialization:

1. Connect to Telegram.
2. Resolve each source.
3. Read only the latest message (`limit=1`).
4. Save that current message ID as the starting point.
5. Forward nothing from the existing history.
6. Exit.

Every later run:

1. Read only messages with IDs greater than the saved ID.
2. Process them oldest first.
3. Forward each with Telethon `forward_messages()`.
4. Save the message ID immediately after a successful forward.
5. If a message fails, do not advance beyond it.
6. The next hourly run retries it.

## No content reconstruction

This project does not use:

- `getUpdates`
- Bot API `forwardMessage`
- Bot API `copyMessage`
- AI
- HTML generation
- article extraction
- Pillow
- `sendMessage`
- `sendPhoto`
- `sendVideo`

It uses native Telethon forwarding.

## Required GitHub secrets

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`

## Workflow

`.github/workflows/forward-hourly.yml`

The schedule is approximately hourly:

`7 * * * *`

Manual execution is enabled with `workflow_dispatch`.

GitHub Actions schedule times can be delayed by GitHub.

## Important

The first run intentionally forwards **zero historical messages**. It creates
the starting point.

Create a NEW post after initialization and run the workflow again.

Expected log:

```text
CHECK | source=@BusinessNewsroom | last_message_id=...
FOUND | source=@BusinessNewsroom | new_messages=1
FORWARD ATTEMPT | source=@BusinessNewsroom | message_id=...
FORWARD PASS | source=@BusinessNewsroom | source_message_id=...
```

## Telegram access

The bot/account must be able to access the four source channels and post to
`@NewsroomHQ`.

If Telegram returns a source access error, the exact RPC error is printed in
the GitHub Actions log.

## State

State file:

`telethon_state.json`

The state is committed back to the repository after each successful forward.

Do not manually replace a Telethon message ID with a Telegram Bot API
`update_id`. They are different identifiers.
