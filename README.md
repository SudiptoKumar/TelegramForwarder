# Telethon + GitHub Actions Hourly Forwarder

Forwards posts from these eight source channels to `@NewsroomHQ`:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`
- `@ScienceNewsroom`
- `@CareerNewsroom`
- `@ComicsNewsroom`
- `@TheSportsNewsroom`

## Authentication

This project uses a regular Telegram user `StringSession` because Telethon history polling requires user access.

GitHub Actions secrets:

- `API_ID`
- `API_HASH`
- `TELETHON_SESSION`

The user account must be able to read all eight source channels and post/forward into `@NewsroomHQ`.

## Backfill behavior

The first successful run does **not** skip existing history. It starts from message ID `0` for every new channel and forwards existing posts oldest-first.

The bot processes history in batches of 100 messages. After every successful forward it immediately stores:

1. the source `last_message_id`
2. the source post URL in `posted_urls.txt`
3. the same URL in `telethon_state.json`

If a forward fails, that message ID is not advanced. The next run retries it.

Therefore an interrupted run resumes instead of starting over.

## Duplicate protection

Each public source message gets a URL such as:

`https://t.me/BusinessNewsroom/123`

A URL already present in the persisted deduplication history is skipped.

`posted_urls.txt` is maintained as a human-readable deduplication record. The authoritative state is also stored in `telethon_state.json` so recovery remains possible even if a run is interrupted.

## State safety

State writes use temporary files followed by atomic replacement.

State advances only after a successful Telegram `forward_messages()` call.

A source-level failure never stops processing of the other sources.

## GitHub Actions

The workflow runs hourly and supports manual execution. It uses:

```yaml
permissions:
  contents: write
```

State is committed and pushed only when changed. `git push` failures are not suppressed.

## Important first deployment note

Because this version intentionally backfills historical posts, the first successful run can forward many messages. Keep the existing `timeout-minutes` and run manually first so you can monitor the result.

Do not delete `telethon_state.json` or `posted_urls.txt` after backfill begins. Those files prevent duplicates and allow the process to resume safely.
