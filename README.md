# Telethon + GitHub Actions Hourly Forwarder v3

## Purpose

Every hour, check these four Telegram source channels and natively forward
new posts to `@NewsroomHQ`:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`

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

1. Access and read the history of all four source channels.
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

## First run and partial initialization

The state file is intentionally reset to:

```json
{
  "initialized": false,
  "channels": {}
}
```

For each accessible source, the first successful run captures only the current
latest message ID. It forwards zero historical messages.

Each channel is initialized independently. If one source is inaccessible, the
other sources still get their baselines and those baselines are persisted.
The failed source remains uninitialized and is retried on a later run.

Example partial state:

```json
{
  "initialized": false,
  "channels": {
    "@BusinessNewsroom": {
      "initialized": true,
      "last_message_id": 1234
    },
    "@GamingNewsroom": {
      "initialized": true,
      "last_message_id": 5678
    },
    "@TheTechNewsroom": {
      "initialized": false
    },
    "@EntertainmentNewsroom": {
      "initialized": true,
      "last_message_id": 9012
    }
  }
}
```

A successful baseline is never created for a channel that could not be read.

## Normal forwarding

After a channel has a valid baseline, every run:

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

After the Python process finishes, the workflow stages `telethon_state.json`.
It commits and pushes only when the file changed. `git push` is not suppressed,
so a push failure makes the workflow visibly fail.

## Session setup

Generate a Telethon user `StringSession` on a trusted machine using the same
`API_ID` and `API_HASH`, complete the normal Telegram login/2FA flow, and store
the resulting string as the GitHub repository secret `TELETHON_SESSION`.

Do not put the string in this repository.

## First deployment test

1. Add `API_ID`, `API_HASH`, and `TELETHON_SESSION` to GitHub Actions secrets.
2. Confirm the user account can access all four sources and post in `@NewsroomHQ`.
3. Run the workflow manually.
4. Confirm the first run establishes baselines and forwards zero old posts.
5. Confirm the workflow commits and pushes `telethon_state.json`.
6. Publish one new test post to a source channel.
7. Run the workflow manually again.
8. Confirm the new post appears in `@NewsroomHQ` and the corresponding
   `last_message_id` advances only after successful forwarding.
