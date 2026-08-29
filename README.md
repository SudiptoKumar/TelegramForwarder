# NewsroomHQ Rich News Aggregator v1

Sources:
- @BusinessNewsroom
- @GamingNewsroom
- @TheTechNewsroom
- @EntertainmentNewsroom

Destination:
- @NewsroomHQ

This version does not use Telegram `forwardMessage` or `copyMessage`.
It reads the content already present in each `channel_post` update and
publishes a new formatted post using standard Bot API `sendMessage`,
`sendPhoto`, `sendVideo`, etc.

The rich formatting is built by Python from the incoming source post.
This is intentionally separate from the AI/rich-message implementation in
ScienceNewsroomBot. That implementation can be integrated later if its
custom `sendRichMessage` capability is confirmed for this bot.

State is checkpointed after every successfully delivered or deliberately
skipped update. A transient/unexpected error does not advance the offset,
so that update can be retried on the next run.

The workflow state-saving step uses `if: always()`.

## Setup

Add a GitHub Actions repository secret:

BOT_TOKEN=<BotFather token>

The bot must be an administrator in all four source channels and be able to
post in @NewsroomHQ.

Keep the Telegram webhook URL empty.

## Testing

Run the workflow manually. Then create a NEW text post in a source channel
and run the workflow again. Confirm `PUBLISH PASS` and check @NewsroomHQ.

For a media test, create a new photo-with-caption post and run again.

This is a republisher, not a native Telegram forward, so there is no
"Forwarded from" header.
