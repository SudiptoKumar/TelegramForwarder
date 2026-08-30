# Telethon + GitHub Actions Hourly Forwarder

## Exact purpose

Every hour, check these four Telegram channels for posts newer than the
last processed message ID and natively forward them to:

`@NewsroomHQ`

Sources:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`

## Architecture

```text
GitHub Actions starts
        |
        v
Telethon connects
        |
        v
Read messages newer than stored ID
        |
        v
forward_messages()
        |
        v
@NewsroomHQ
        |
        v
Save last successful message ID
        |
        v
Exit
```

This is deliberately a **batch process**, not `run_until_disconnected()`.
Therefore it can run as an hourly GitHub Actions job and does not require a
VPS.

## Native forwarding

The implementation uses Telethon:

```python
client.forward_messages(
    entity=target,
    messages=message,
    from_peer=source_entity,
)
```

It does not reconstruct the message and does not use the previous Bot API
`getUpdates -> forwardMessage` implementation.

No AI, HTML generation, article extraction, Pillow, or `sendMessage` is used.

## State

State is stored in:

`telethon_state.json`

Example:

```json
{
  "channels": {
    "@BusinessNewsroom": {
      "last_message_id": 123
    }
  }
}
```

The state is updated after every successful forward.

If forwarding a message fails after retries, the state is NOT advanced past
that message. The workflow exits with an error, and the next hourly run
retries it.

## GitHub secrets

Add:

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`

Never put these values in source files.

## Workflow

Location:

`.github/workflows/forward-hourly.yml`

Schedule:

`7 * * * *`

This runs approximately once per hour.

`workflow_dispatch` is also enabled for manual testing.

The state-save step uses:

```yaml
if: always()
```

## Telegram permissions

The bot must have appropriate administrator/posting permissions in all
source channels and `@NewsroomHQ`.

## Important limitation

GitHub Actions scheduled jobs are not guaranteed to start at the exact
minute and may be delayed by GitHub. This design is hourly batch processing,
not real-time forwarding.

## First live test

1. Add `API_ID`, `API_HASH`, and `BOT_TOKEN` to GitHub Actions secrets.
2. Commit this project to your repository.
3. Run `Telegram Telethon Hourly Forwarder` manually.
4. Create a NEW post in one source channel.
5. Run the workflow manually again.
6. Confirm the log contains:

```text
FOUND | source=@BusinessNewsroom | new_messages=1
FORWARD ATTEMPT | source=@BusinessNewsroom | message_id=...
FORWARD PASS | source=@BusinessNewsroom | source_message_id=...
```

7. Confirm the original post appears in `@NewsroomHQ`.

## Important first-run behavior

If `telethon_state.json` contains zero IDs, the first run will inspect the
available message history and can forward many existing messages.

For a clean production start, initialize each source's `last_message_id` to
the current latest message ID before enabling the hourly schedule, OR start
with an empty state and intentionally process the backlog.

Do not blindly use an old Bot API `update_id` as a Telethon message ID.
They are different identifiers.
