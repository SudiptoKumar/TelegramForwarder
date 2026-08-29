# Telegram Channel Publisher v5

This version fixes the state-management problem and avoids the `forwardMessage`
and `copyMessage` operations that were failing in the previous runs.

## Sources

- @BusinessNewsroom
- @GamingNewsroom
- @TheTechNewsroom
- @EntertainmentNewsroom

## Destination

- @NewsroomHQ

## How it works

GitHub Actions runs hourly. Telegram `getUpdates` supplies `channel_post`
objects. The publisher reads the content already present in each update and
uses the corresponding `send*` Bot API method.

It does not call `forwardMessage` or `copyMessage`.

## Safe state handling

The offset is saved immediately after each successful or deliberately skipped
update.

A transient or unexpected error does NOT advance the offset. That update will
be retried on the next workflow run.

The workflow's final state step uses `if: always()` so state files are committed
even when the processing step exits non-zero. This is useful for deliberately
skipped permanent errors, while transient errors remain retryable.

## Setup

Add the repository Actions secret:

```text
BOT_TOKEN=<your BotFather token>
```

The bot must be an administrator in all four source channels and have permission
to post in @NewsroomHQ.

Keep Telegram webhook URL empty because this workflow uses getUpdates.

## Test

1. Upload the files.
2. Run the workflow manually.
3. Confirm configuration checks pass.
4. Create a NEW text post in one source channel.
5. Run the workflow manually.
6. Confirm the log contains `DIRECT PUBLISH PASS`.
7. Confirm the message appears in @NewsroomHQ.
8. Test a photo and video as well.

This creates a new message in @NewsroomHQ. It is not a Telegram-native forwarded
message with a "Forwarded from" header.

## Important limitation

Media albums are received as individual channel posts by this implementation,
so an album may be republished as separate media messages. Unsupported Telegram
post types are recorded in skipped.json instead of silently disappearing.

A real Telegram delivery test must be performed in the user's Telegram/GitHub
environment because this package does not have access to the user's bot token.
