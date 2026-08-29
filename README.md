# Telegram Channel Forwarder (Fixed)

Forwards new Telegram channel posts to `@NewsroomHQ` every hour.

## Channels

Sources:
- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`

Destination:
- `@NewsroomHQ`

## Fix for the failed workflow

The supplied GitHub log showed:

```text
BOT_TOKEN:
ERROR: 404 Client Error: Not Found for url:
https://api.telegram.org/bot/getUpdates...
```

The `BOT_TOKEN` environment variable was empty. GitHub therefore constructed an invalid Telegram URL.

This version adds an explicit configuration test before forwarding.

## GitHub setup

Create:

```text
Repository -> Settings -> Secrets and variables -> Actions
```

Add a **Repository secret**:

```text
Name: BOT_TOKEN
Value: <your real BotFather token>
```

Do not include `bot` or any extra spaces in the secret value.

The workflow automatically checks:

1. `BOT_TOKEN` exists.
2. The token works with Telegram `getMe`.
3. No webhook is configured.
4. `@NewsroomHQ` can be resolved with `getChat`.

## Telegram permissions

The bot should be an administrator in all five channels.

It needs permission to post in:

```text
@NewsroomHQ
```

## Important: update delivery

This workflow uses Telegram `getUpdates`, not a webhook.

Do not configure a webhook for this bot while using this workflow.

Check manually:

```text
https://api.telegram.org/botYOUR_TOKEN/getWebhookInfo
```

The `url` must be empty.

## Test procedure

1. Add `BOT_TOKEN` as a GitHub Actions repository secret.
2. Push this repository.
3. Open:
   `Actions -> Telegram Channel Forwarder`
4. Click:
   `Run workflow`
5. The `Validate Telegram bot configuration` step should pass.
6. Create a new post in one source channel.
7. Run the workflow manually.
8. Confirm the post appears in `@NewsroomHQ`.
9. Run the workflow again without creating another post. It should report:
   `No new channel posts.`

The scheduled workflow runs at:

```text
0 * * * *
```

which means once every hour.

## State file

`state.json` stores the Telegram `update_id` offset.

The workflow commits the updated state back to the repository after a successful run.

## Security

Never commit the bot token into the repository or expose it in logs.
