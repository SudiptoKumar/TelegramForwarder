# Telegram Channel Forwarder v2

## Root cause found in the supplied GitHub log

The workflow authenticated successfully:

- `@BNewsroombot`
- webhook check passed
- `@NewsroomHQ` destination check passed

It then failed on:

```text
Forwarding @TheTechNewsroom message 12 -> @NewsroomHQ
ERROR: 400
Bad Request: message to forward not found
```

Because the old script stopped on this error, it never advanced the Telegram `update_id` offset. The same bad/unforwardable update can therefore block later messages on every run.

## What this version fixes

- Validates the bot token and webhook before processing.
- Checks all four source channels.
- Processes updates one by one.
- Retries a transient forwarding error once.
- Records permanently unforwardable messages in `skipped.json`.
- Advances the offset after handled/skipped updates, so one bad Telegram post cannot block newer posts.
- Keeps a GitHub Actions `concurrency` lock so two runs do not race over `state.json`.
- Runs hourly with `0 * * * *`.

## Critical Telegram limitation

Telegram's Bot API does not allow `forwardMessage` for protected content. If a source channel has content protection enabled, the bot can receive the update but Telegram can still refuse the forward. The API also documents `has_protected_content` on chats/messages for this purpose.

Therefore, if `@TheTechNewsroom` is protected, the correct fix is to disable content protection in that source channel if you control it. Otherwise Telegram will not permit a genuine forward.

## Setup

Add the repository secret:

```text
BOT_TOKEN = <real BotFather token>
```

The bot must be an administrator in:

```text
@BusinessNewsroom
@GamingNewsroom
@TheTechNewsroom
@EntertainmentNewsroom
@NewsroomHQ
```

The bot needs permission to post in `@NewsroomHQ`.

Keep the webhook URL empty:

```text
https://api.telegram.org/botYOUR_TOKEN/getWebhookInfo
```

## Test

1. Push the files.
2. Run `Actions -> Telegram Channel Forwarder -> Run workflow`.
3. Confirm `Validate Telegram configuration` passes.
4. Create a NEW post in a source channel.
5. Run the workflow manually.
6. Confirm the log contains `Forwarding ...` and `PASS`.
7. Confirm the message appears in `@NewsroomHQ`.
8. Run again with no new post. It should say `No new channel posts.`

Do not commit the BotFather token.
