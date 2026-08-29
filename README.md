# Telegram Channel Forwarder

Forwards new posts from four Telegram source channels to `@NewsroomHQ` using a scheduled GitHub Actions workflow.

## Source channels

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`

## Destination

- `@NewsroomHQ`

## How it works

Every hour, GitHub Actions:

1. Calls Telegram `getUpdates`.
2. Reads new `channel_post` updates.
3. Checks whether the post came from one of the four configured source channels.
4. Forwards matching posts to `@NewsroomHQ`.
5. Saves the Telegram `update_id` in `state.json`.
6. Commits the updated state back to the repository.

## Setup

### 1. Add the bot to all channels

The same bot must be an administrator in:

- all four source channels
- `@NewsroomHQ`

Give the bot permission to post in the main channel.

### 2. Make sure the bot is not using a webhook

The `getUpdates` method cannot be used while an outgoing webhook is configured.

Check:

```text
https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo
```

The returned `url` should be empty.

If you previously configured a webhook and no longer need it, remove it with:

```text
https://api.telegram.org/botYOUR_BOT_TOKEN/deleteWebhook
```

### 3. Add the bot token to GitHub

Open:

`Repository → Settings → Secrets and variables → Actions`

Create a repository secret:

```text
Name: BOT_TOKEN
Value: YOUR_TELEGRAM_BOT_TOKEN
```

Never commit the bot token to the repository.

### 4. Upload these files

Push the repository contents to GitHub.

The workflow is:

```text
.github/workflows/forward.yml
forward.py
state.json
requirements.txt
.gitignore
README.md
```

### 5. Test manually

Go to:

`Repository → Actions → Telegram Channel Forwarder → Run workflow`

Before testing, create a new post in one of the four source channels.

The workflow should forward it to `@NewsroomHQ`.

## Important behavior

The workflow runs hourly:

```text
0 * * * *
```

GitHub Actions scheduled workflows are not guaranteed to start at exactly the scheduled second, so treat the hourly schedule as approximate.

Telegram keeps pending updates for a limited period. If the workflow is disabled for too long, old updates may no longer be available.

If a forwarding operation fails, the script exits without advancing the saved offset. This prevents a failed update from being silently marked as processed. A retry can therefore result in a duplicate only if Telegram accepted the forwarding but the workflow failed before saving state.

## Protected content

Telegram messages with protected content may not be forwardable. Such posts can fail when `forwardMessage` is used.

## Changing the schedule

Current schedule:

```yaml
- cron: "0 * * * *"
```

Examples:

Every 30 minutes:

```yaml
- cron: "*/30 * * * *"
```

Every 2 hours:

```yaml
- cron: "0 */2 * * *"
```

Daily at 09:00 UTC:

```yaml
- cron: "0 9 * * *"
```
