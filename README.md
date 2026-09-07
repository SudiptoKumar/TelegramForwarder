# Telegram Forwarder V1

A GitHub Actions + Telethon forwarder for `@NewsroomHQ` that collects posts from eight Newsroom specialty channels, backfills existing history, forwards future posts, prevents duplicates, mixes sources in randomized order, and keeps one permanent specialty-channel footer as the final message.

## What V1 does

Every scheduled run checks these eight source channels:

1. `@BusinessNewsroom`
2. `@GamingNewsroom`
3. `@TheTechNewsroom`
4. `@EntertainmentNewsroom`
5. `@ScienceNewsroom`
6. `@CareerNewsroom`
7. `@ComicsNewsroom`
8. `@TheSportsNewsroom`

Destination:

`@NewsroomHQ`

The forwarder uses a normal Telegram user session for reading channel history and forwarding messages. A separate Telegram bot token is used only for the V1 Rich Message footer.

---

## Why two Telegram credentials are used

### 1. Telethon user session

`TELETHON_SESSION` belongs to a regular Telegram user account.

This is required because this project reads Telegram channel history with Telethon's history APIs. A bot account is not used for the history-reading part of this design.

The user account must be able to read all eight source channels and post/forward into `@NewsroomHQ`.

### 2. Telegram bot token

`TELEGRAM_BOT_TOKEN` belongs to a Telegram bot that can post and delete its own footer messages in `@NewsroomHQ`.

V1 uses the Bot API `sendRichMessage` method to create the footer. The footer's slogan is rendered with Telegram's **Pull Quote** block using Rich HTML `<aside>`, which is designed for centered quotation text. The current Telegram Bot API documents `RichBlockPullQuotation` as a centered quotation and `<aside>` as its HTML equivalent. citeturn658733search0

The bot token is **not** used to read source history or replace the Telethon user session.

---

## Required GitHub Secrets

Create these four repository secrets:

| Secret | Used for |
|---|---|
| `API_ID` | Telegram API ID for Telethon |
| `API_HASH` | Telegram API hash for Telethon |
| `TELETHON_SESSION` | Logged-in regular Telegram user session |
| `TELEGRAM_BOT_TOKEN` | Bot API Rich Message footer |

Never print, commit, or place any of these secrets directly in the repository.

---

## Telegram permissions

### Telethon user account

The account inside `TELETHON_SESSION` needs permission to:

- Read history from all eight source channels.
- Access private source channels if any are private.
- Post or forward messages into `@NewsroomHQ`.
- Delete the previous footer from `@NewsroomHQ`.

### Footer bot

The bot represented by `TELEGRAM_BOT_TOKEN` needs permission to post in `@NewsroomHQ` and delete messages it is responsible for there.

The program verifies the bot token with `getMe` before processing. It never logs the token itself.

---

## How forwarding works

V1 keeps a separate `last_message_id` for every source.

For each source:

1. Find messages newer than the saved message ID.
2. Read them oldest-first.
3. Skip Telegram service messages and empty messages.
4. Check `posted_urls.txt` for an already-forwarded source URL.
5. Forward the message with Telethon.
6. Only after a successful forward, save the source URL and advance the source's last message ID.

This means a failed Telegram forward is **not** silently marked as complete. The failed message remains retryable on a later run.

---

## Historical backfill

A new source has no baseline message ID, so V1 starts at message ID `0` and backfills existing history.

It does **not** simply record the current latest message and skip everything before it.

Messages are fetched oldest-first. Large backlogs are handled in batches, so a source with more than 100 pending posts is not abandoned after the first batch.

Service messages are skipped safely because they are not ordinary news posts.

Backfill is resumable. If a source fails at message `N`, the saved state remains before that failed message, so the next run can retry it.

---

## Duplicate protection

V1 uses two complementary checks:

### Message ID state

`telethon_state.json` stores the latest successfully processed message ID for every source.

Example:

```json
{
  "@BusinessNewsroom": {
    "initialized": true,
    "last_message_id": 513
  }
}
```

### Permanent URL list

`posted_urls.txt` stores source URLs such as:

```text
https://t.me/BusinessNewsroom/513
```

When a URL is already present, that post is skipped instead of being forwarded again.

---

## Randomized cross-channel order

V1 does not empty one channel completely before checking the others.

It builds a queue for every accessible source and randomly chooses the next source. When possible, the source used immediately before is excluded from the next choice.

Example:

```text
Business 1
Gaming 1
Science 1
Business 2
Tech 1
Entertainment 1
Gaming 2
Sports 1
...
```

The order **inside each individual source** stays chronological. Only the order between different sources is randomized.

If a source has more than 100 pending messages, another batch is fetched automatically when its current queue runs out.

If one source fails, that source is blocked for the current run while the other accessible sources continue.

---

## Persistent final footer

The destination channel should always finish with the Newsroom specialty-channel footer.

The process is:

### At the start of a run

The previous footer message ID is read from `telethon_state.json` and the old footer is deleted.

V1 first tries Telethon deletion, which also allows a clean upgrade from the previous footer implementation. If necessary, it falls back to the Bot API for a bot-authored footer.

### During the run

Source posts are forwarded normally.

### At the end of the run

The footer is posted again with the Telegram Bot API `sendRichMessage` method.

The exact new footer message ID is saved back to `telethon_state.json`.

This keeps the footer at the very bottom after every successful cycle.

---

## Footer design in V1

The footer contains:

```text
🎯 Want to go deeper? Explore our specialty channels:

💼 @BusinessNewsroom
🎮 @GamingNewsroom
💻 @TheTechNewsroom
🔭 @ScienceNewsroom
🎬 @EntertainmentNewsroom
🎓 @CareerNewsroom
🦸 @ComicsNewsroom
🏆 @TheSportsNewsroom

[centered Pull Quote]
Newsroom, one network, all the news you need.
[/centered Pull Quote]

Stay informed. Stay ahead. 🚀
```

The eight usernames are clickable links.

The slogan uses Rich HTML `<aside>`, which Telegram maps to a centered Pull Quote. Standard `<blockquote>` is **not** used for this V1 footer. Telegram's current Bot API describes the Pull Quote block as a quotation with centered text. citeturn658733search0

---

## Important: the forwarded news posts are unchanged

The forwarder uses Telethon's normal native `forward_messages()` call for source posts.

V1 does not rebuild the source posts into Rich Messages, does not rewrite their text, and does not add the footer formatting to them.

Only the custom final footer uses the Bot API Rich Message system.

---

## GitHub Actions schedule

Workflow file:

```text
.github/workflows/forward-hourly.yml
```

Schedule:

```text
7 2-20 * * *
```

GitHub cron uses UTC. Bangladesh is UTC+6, so this runs at approximately:

```text
08:07, 09:07, 10:07, ... 20:07, 21:07, ... 02:07 Bangladesh time
```

In other words, the workflow runs once each hour from **8:07 AM through 2:07 AM Bangladesh time**.

Manual execution is also available through `workflow_dispatch`.

---

## State files

### `telethon_state.json`

Stores:

- Per-source initialization state.
- Per-source latest processed Telegram message ID.
- Current footer message ID.

It is written atomically so an interrupted write does not intentionally replace the file with a half-written JSON document.

### `posted_urls.txt`

Stores every successfully forwarded source post URL.

### Do not delete these during normal operation

Deleting the state files makes the program behave like a new deployment again. That can cause historical posts to be processed again.

Only reset them deliberately when you actually want a fresh deployment/backfill.

---

## Repository structure

```text
.
├── .github/
│   └── workflows/
│       └── forward-hourly.yml
├── forwarder.py
├── generate_session.py
├── requirements.txt
├── telethon_state.json
├── posted_urls.txt
└── README.md
```

`telethon_state.json` and `posted_urls.txt` may not exist until the first run. The program creates them automatically.

---

## Generate the Telethon session

Run `generate_session.py` on a trusted machine or in a trusted Google Colab environment.

It asks for:

```text
API ID
API HASH
```

Then it performs the normal Telegram login/2FA flow and prints a `TELETHON_SESSION` string.

Store that complete value as the GitHub Actions secret:

```text
TELETHON_SESSION
```

Do not commit the session string.

---

## First deployment

### Step 1: add GitHub secrets

Add:

```text
API_ID
API_HASH
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

### Step 2: check Telegram access

Confirm that the Telethon user can read all eight source channels and post/forward into `@NewsroomHQ`.

Confirm that the footer bot can post in `@NewsroomHQ`.

### Step 3: run manually

Open GitHub Actions and run:

```text
Telegram Telethon Hourly Forwarder V1
```

using **Run workflow**.

### Step 4: check the logs

A healthy run should contain messages similar to:

```text
AUTHENTICATED | username=@YourUser | id=... | bot=False
BOT AUTHENTICATED | username=@YourFooterBot | id=...
TARGET RESOLVED | @NewsroomHQ
SOURCE ACCESS PASS | @BusinessNewsroom accessible
SOURCE ACCESS PASS | @GamingNewsroom accessible
...
FOOTER POST PASS | method=bot_api_rich_message | message_id=...
FINISHED | initialized=True | forwarded=... | failed=0 | footer_posted=True | posted_urls=...
```

The exact counts will vary.

### Step 5: verify the target channel

Check that:

- Source posts arrived in mixed cross-channel order.
- No duplicate source posts were created.
- The final message is the specialty footer.
- The slogan appears as a centered Pull Quote in a Telegram client that supports Rich Messages.

### Step 6: test a new post

Publish one new post in a source channel, then manually run the workflow once more.

The workflow should:

1. Delete the previous footer.
2. Forward the new source post.
3. Recreate the footer at the bottom.
4. Save the new footer message ID.

---

## What happens when something fails

### A source channel fails

Only that source is blocked for the current run. Other accessible sources continue.

### A message forward fails

That source's state is not advanced past the failed message. The message can be retried later.

### Footer deletion fails

The run stops before forwarding if the previous footer cannot be safely removed.

This protects the rule that the footer should remain the final message rather than allowing posts to accumulate below an old footer.

### Footer posting fails

The source processing may already have completed, but the workflow exits with a failure status because the required final footer was not recreated.

The state files are still handled by the final GitHub Actions state-saving step.

### Git push fails

The workflow does not hide the push failure. A failed state push remains visible as a failed workflow.

---

## Security notes

Never put these values into Python source code:

```text
API_ID
API_HASH
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

Use GitHub Actions repository secrets.

Do not print the values in logs.

A Telethon session string is a live login credential for the Telegram user account. Treat it like a password.

A bot token is also a credential. Treat it like a password.

---

## Troubleshooting

### `Missing GitHub secrets: ...`

The workflow is not receiving one or more required secrets. Check the repository secret names and the workflow `env:` section.

### `TELETHON_SESSION belongs to a bot account`

The session was generated from a bot account. Generate the session again using a normal Telegram user account.

### `SOURCE ACCESS FAIL`

The Telethon account cannot currently access that source channel. Check membership, privacy, username, or Telegram permissions.

### `Cannot authenticate Telegram bot token`

`TELEGRAM_BOT_TOKEN` is missing, invalid, revoked, or blocked by an external network/API problem.

### `FOOTER POST TELEGRAM BOT API ERROR`

Check that the bot is present in `@NewsroomHQ` and has permission to post there. Also make sure the bot token belongs to the bot you intended to use.

### The centered quote does not look centered

V1 sends the footer through Telegram's current Rich Message API using `<aside>`. Rendering depends on the Telegram client version. The data sent by the program uses the official Pull Quote rich-message structure rather than a normal Markdown/HTML block quote. citeturn658733search0

### Duplicates appear after a repository reset

Check whether `telethon_state.json` or `posted_urls.txt` was removed or replaced. Those files are part of the deduplication state.

---

## Operational summary

```text
GitHub Actions
      │
      ▼
forwarder.py
      │
      ├── Telethon user session
      │      ├── Read 8 source histories
      │      ├── Backfill old posts
      │      ├── Detect new posts
      │      └── Native forward to @NewsroomHQ
      │
      ├── posted_urls.txt
      │      └── Duplicate protection
      │
      ├── telethon_state.json
      │      ├── Per-source message IDs
      │      └── Footer message ID
      │
      └── Telegram Bot API
             ├── Delete previous footer when needed
             └── sendRichMessage
                    └── Centered Pull Quote footer
```

## V1 definition

This package is **Telegram Forwarder V1**.

The V1 scope is intentionally narrow:

- Reliable history polling with a Telegram user session.
- Eight configured source channels.
- Historical backfill.
- Future-post forwarding.
- URL + message-ID duplicate protection.
- Randomized cross-channel interleaving.
- Resumable processing.
- Persistent final footer.
- Telegram Rich Message Pull Quote for the footer.
- GitHub Actions hourly automation.
