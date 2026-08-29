# Telegram Channel Forwarder v3

## Root cause from the supplied GitHub log

The bot authenticated, the webhook check passed, the destination channel was found, and all four source channels were found.

But every pending post failed with:

`400 Bad Request: message to forward not found`

The workflow itself completed because v2 deliberately skipped these failures. It therefore forwarded **0** messages.

This does not indicate a GitHub problem. Telegram is accepting the update, but its `forwardMessage` operation cannot retrieve/forward the referenced message.

## What v3 changes

For each source `channel_post`:

1. Try the genuine Telegram `forwardMessage`.
2. If Telegram returns the specific "message to forward not found"/invalid-message error, try `copyMessage`.
3. If both fail, record the update in `skipped.json` and continue.
4. Save the Telegram update offset so one bad update cannot block newer posts.

The fallback copy delivers the content to `@NewsroomHQ`, but it does not carry Telegram's forwarded-from header. A successful `forwardMessage` remains the preferred path.

## Setup

Add repository secret:

`BOT_TOKEN = <BotFather token>`

Bot should be administrator in all five channels and must be allowed to post in `@NewsroomHQ`.

Keep webhook URL empty.

## Live test

After uploading:

1. Run the workflow manually.
2. Create a **new** post in one source channel after the workflow has finished.
3. Run workflow again.
4. Look for:
   - `FORWARD PASS`, or
   - `COPY FALLBACK PASS`
5. Verify the message appears in `@NewsroomHQ`.
6. Run once more without a new post. It should report no new channel posts.

## Important

The previous run consumed the pending updates and recorded them as skipped. Therefore, testing requires a genuinely new post.

Protected Telegram content cannot be forwarded, and Telegram's Bot API documents that `forwardMessage` cannot forward protected content.
