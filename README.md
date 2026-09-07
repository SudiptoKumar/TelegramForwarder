# Telethon + GitHub Actions Hourly Forwarder v4

## Purpose

Every hour, check these eight Telegram source channels and natively forward
new posts to `@NewsroomHQ`:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`
- `@ScienceNewsroom`
- `@CareerNewsroom`
- `@ComicsNewsroom`
- `@TheSportsNewsroom`

## Important authentication requirement

This project uses **Telethon with a regular Telegram user account session**.
It does not use a Telegram bot token for history polling.

Telegram's `messages.getHistory` method is used by Telethon's `iter_messages()`
for normal history reads, and Telegram documents that method as user-only.
Therefore a bot-authenticated Telethon client is not a reliable architecture for
this hourly history-polling design.

Create a Telethon `StringSession` once and store the complete value as the
GitHub Actions secret `TELETHON_SESSION`.

Required secrets:

- `API_ID`
- `API_HASH`
- `TELETHON_SESSION`

Do not print or commit the session string. It is equivalent to a login credential.

## Telegram permissions

The Telegram user account in `TELETHON_SESSION` must be able to:

1. Access and read the history of all eight source channels.
2. Post/forward messages into `@NewsroomHQ`.

For private source channels, the account must be a member with access.
For the target broadcast channel, the account must have permission to post.

The workflow prints an independent diagnostic for every source. Examples:

```text
SOURCE ACCESS PASS | @BusinessNewsroom accessible
SOURCE ACCESS FAIL | source=@TheTechNewsroom | ChannelPrivateError: ...
TARGET RESOLVED | @NewsroomHQ
```

No API keys, tokens, or session strings are printed.

## First run and historical backfill

The clean repository state for a new deployment is:

```json
{
  "initialized": false,
  "channels": {},
  "footer_message_id": null
}
```

For any channel without a saved state, the forwarder starts at `last_message_id=0` and backfills its existing history. It does not take the latest message as a baseline.

Unlike the previous baseline-only implementation, the first run now **backfills existing channel history**. It processes each source oldest-first and forwards existing news posts to `@NewsroomHQ`.

Telegram service messages such as channel creation/title-change events are not news posts and cannot be forwarded with Telethon. They are skipped safely and do not block later posts.

The backfill is resumable. If forwarding fails at a particular message, that message is not marked as processed and the source stops there. The next run retries from that point.

If one channel fails, the other channels continue independently.

## Duplicate prevention

Every successfully forwarded post gets a canonical source URL such as:

```text
https://t.me/BusinessNewsroom/123
```

The URL is appended to `posted_urls.txt`. A URL already present in that file is skipped on later runs.

The per-channel `last_message_id` is also advanced only after successful forwarding or safe skipping of a non-news/service message. Both mechanisms work together so normal reruns do not duplicate posts.

## Cross-channel randomized interleaving

The forwarder does not exhaust one source channel before moving to the next.
It collects a pending batch from every accessible source, then randomly selects
which source supplies the next post. The immediately previous source is excluded
when another source has pending messages, so one channel will not produce
consecutive posts while other channels are waiting.

The order of posts within each individual source remains chronological. Every
pending post is still processed; only the cross-channel order is randomized.
If a source has more than 100 pending messages, the next oldest batch is loaded
automatically as its queue is exhausted, so a large backlog is not skipped.

Example:

```text
Business 1
Gaming 1
Tech 1
Science 1
Business 2
Sports 1
Gaming 2
Career 1
...
```

## Normal forwarding

After a channel has completed its backfill, every run:

1. Read only messages newer than that channel's saved ID.
2. Process messages oldest first.
3. Forward each message with Telethon `forward_messages()`.
4. Save that message ID only after the forward call succeeds.
5. Stop that source at the first failed message so the failed ID is retried.
6. Continue processing the other sources.

A normal run therefore has this behavior:

```text
Business fails
    -> log error
    -> continue
Gaming processes
    -> continue
Tech processes
    -> continue
Entertainment processes
    -> continue
save state
```

## State safety

`telethon_state.json` is written atomically through a temporary file and rename.
The file is validated and normalized when loaded.

The message ID is advanced only after a successful `forward_messages()` call.
This prevents a failed Telegram request from being silently marked as processed.

The existing per-channel message-ID deduplication model is retained.

## Persistent final specialty-channel message

After every processing cycle, the forwarder keeps exactly one custom specialty-channel message as the last message in `@NewsroomHQ`. It stores that message's destination Telegram `message_id` in `telethon_state.json`. At the start of the next cycle, the previous footer is deleted by that exact ID. The eight source channels are then processed, and the same formatted footer is posted again at the end.

The footer uses the same structure as the configured Newsroom message: the eight clickable specialty-channel usernames, the quoted slogan, and `Stay informed. Stay ahead. 🚀`. If a previous footer was manually deleted, its stored ID can safely be recreated. A real Telegram error while deleting or posting the footer causes a visible workflow failure.

## GitHub Actions

Workflow: `.github/workflows/forward-hourly.yml`

Schedule:

`7 * * * *`

Manual execution is enabled with `workflow_dispatch`.

The job has:

```yaml
permissions:
  contents: write
```

After the Python process finishes, the workflow stages `telethon_state.json` and `posted_urls.txt`.
It commits and pushes only when either state file changed. `git push` is not suppressed,
so a push failure makes the workflow visibly fail.

## Session setup

Generate a Telethon user `StringSession` on a trusted machine using the same
`API_ID` and `API_HASH`, complete the normal Telegram login/2FA flow, and store
the resulting string as the GitHub repository secret `TELETHON_SESSION`.

Do not put the string in this repository.

## First deployment test

1. Add `API_ID`, `API_HASH`, and `TELETHON_SESSION` to GitHub Actions secrets.
2. Confirm the user account can access all eight sources and post in `@NewsroomHQ`.
3. Run the workflow manually.
4. Confirm the first run starts backfill at message ID 0 and begins forwarding existing posts.
5. Confirm `posted_urls.txt` and `telethon_state.json` are committed and pushed.
6. Confirm the specialty-channel footer appears as the last message in `@NewsroomHQ`.
7. Publish one new test post to a source channel and run the workflow again.
8. Confirm the new post is forwarded and the previous footer is deleted/replaced at the end.
