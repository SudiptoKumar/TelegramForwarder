# Telegram Native Forwarder v6

Forwards new posts from the four source channels to `@NewsroomHQ`.

Primary operation: Telegram `forwardMessage`.

Fallback: Telegram `copyMessage`.

The script uses the exact numeric `chat.id` from each incoming `channel_post`
and the incoming `message_id`. It does not rebuild content.

State is advanced only after a post is successfully forwarded, successfully
copied, or deliberately skipped. Network errors, rate limits and Telegram 5xx
errors leave the offset unchanged so the post is retried.

The GitHub state-save step uses `if: always()`.

A live Telegram delivery test requires the private BOT_TOKEN in GitHub Actions.
This package was locally tested with mocked Telegram API responses.
