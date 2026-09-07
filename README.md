# Telegram Forwarder V1

A GitHub Actions bot that **natively forwards new and historical posts** from 8 Newsroom specialty channels into `@NewsroomHQ`, keeps the channels interleaved, prevents duplicates, and maintains one reusable specialty-channel footer as the final message.

## 1. What this project does

The forwarder watches these source channels:

| Source | Category |
|---|---|
| `@BusinessNewsroom` | Business |
| `@GamingNewsroom` | Gaming |
| `@TheTechNewsroom` | Technology |
| `@EntertainmentNewsroom` | Entertainment |
| `@ScienceNewsroom` | Science |
| `@CareerNewsroom` | Career |
| `@ComicsNewsroom` | Comics |
| `@TheSportsNewsroom` | Sports |

All accepted posts are forwarded to:

`@NewsroomHQ`

The forwarding itself is done by a **regular Telegram user session through Telethon**. The specialty footer is sent separately through the **Telegram Bot API `sendRichMessage`** method.

## 2. V1 architecture

V1 deliberately uses two Telegram identities for two different jobs:

### Telethon user session

Used for:

- Reading source-channel history.
- Detecting new messages after the saved message ID.
- Natively forwarding source messages to `@NewsroomHQ`.
- Deleting the previous footer when possible.

A regular user session is required for this history-polling design because Telegram history methods used by Telethon are not available to bot accounts in the same way.

### Telegram Bot API bot

Used only for the footer:

- `sendRichMessage`
- `InputRichMessage.html`
- Rich HTML only
- No MarkdownV2
- No `InputRichMessage.blocks`

The footer therefore stays independent from the native forwarded news messages.

Telegram documents `InputRichMessage` as accepting exactly one of `html`, `markdown`, or `blocks`, and documents `sendRichMessage` as the method for sending rich messages. The Rich HTML system maps paragraph and heading tags to the corresponding rich blocks. citehttps://core.telegram.org/bots/api

## 3. Footer design

The footer is built as Rich HTML:

```html
<h2>🎯 Want to go deeper?</h2>
<p>Explore our specialty channels:</p>
<p>...eight clickable channel links...</p>
<aside>Newsroom, one network, all the news you need.</aside>
<h2>Stay informed. Stay ahead. 🚀</h2>
```

This is intentional.

- `<h2>` creates a Rich Message **Section Heading**.
- `<p>` creates a **Paragraph** block.
- `<aside>` creates a **Pull Quote**, which is the centered quotation style.

Telegram documents `<aside>` as the HTML representation of `InputRichBlockPullQuotation`, described as a quotation with centered text. citehttps://core.telegram.org/bots/api

The footer does **not** use `InputRichMessage.blocks`.

## 4. Required GitHub repository secrets

Create these four repository secrets:

```text
API_ID
API_HASH
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

### What each one is

| Secret | Purpose |
|---|---|
| `API_ID` | Telegram application ID used by Telethon |
| `API_HASH` | Telegram application hash used by Telethon |
| `TELETHON_SESSION` | Logged-in regular Telegram user `StringSession` |
| `TELEGRAM_BOT_TOKEN` | Bot token used only for the Rich Message footer |

### Important naming rule

The workflow expects the exact name:

```text
TELEGRAM_BOT_TOKEN
```

A secret named `BOT_TOKEN` is **not the same secret name** and will not be read automatically.

If an existing `BOT_TOKEN` contains the correct bot token, create `TELEGRAM_BOT_TOKEN` with the same value and then remove `BOT_TOKEN` when nothing else in the repository uses it.

## 5. Telegram permissions

### The Telethon user account must be able to

- Read all 8 source channels.
- Access their message history.
- Post or forward messages into `@NewsroomHQ`.
- Delete the previous footer if you want the fallback cleanup through the user session to work.

### The footer bot must be able to

- Access `@NewsroomHQ`.
- Send messages to `@NewsroomHQ`.
- Delete its own footer message on later runs.

For a broadcast channel, the bot should be added with the required administrator permissions for posting and deleting messages.

## 6. First run and historical backfill

A new deployment starts with no saved source position.

For every accessible source, V1 starts from:

```text
last_message_id = 0
```

It then reads history **oldest first** and forwards pending posts.

V1 does not use the newest message as a baseline. That means existing channel history is eligible for backfill.

Backfill is resumable. If forwarding fails on a message, that source is blocked for the current run and the failed message is not advanced in state. The next workflow run can retry it.

Telegram service messages that are not normal forwardable posts are skipped safely.

## 7. Normal hourly operation

After a source has caught up, every workflow run does this:

1. Load `telethon_state.json`.
2. Connect with the Telethon user session.
3. Resolve the target and source channels independently.
4. Delete the previous stored footer.
5. Read messages newer than each source's saved `last_message_id`.
6. Interleave messages from different sources randomly.
7. Forward each message with Telethon.
8. Record its source URL in `posted_urls.txt` after success.
9. Advance that source's `last_message_id` only after safe processing.
10. Save state.
11. Send the Rich Message footer through `sendRichMessage`.
12. Store the footer's destination message ID.

The footer is therefore always recreated as the final message of the run.

## 8. Random cross-channel interleaving

V1 does not post one entire channel backlog before another.

It creates a queue for every available source and randomly chooses which source provides the next message.

When possible, the immediately previous source is excluded, so two consecutive posts do not normally come from the same channel while another source still has pending posts.

The internal order of every source remains chronological.

Example:

```text
Business 1
Gaming 1
Science 1
Business 2
Entertainment 1
Tech 1
Gaming 2
Sports 1
...
```

If a source has more than 100 pending messages, V1 refills its queue automatically instead of stopping after the first batch.

## 9. Duplicate protection

V1 uses two safeguards.

### Source message position

`telethon_state.json` stores the last successfully processed message ID for every source.

### Canonical source URL list

`posted_urls.txt` stores URLs such as:

```text
https://t.me/BusinessNewsroom/123
```

If the URL already exists, the post is skipped.

This protects against duplicate forwarding during reruns and interrupted GitHub Actions jobs.

## 10. State files

### `telethon_state.json`

Stores:

```json
{
  "initialized": true,
  "channels": {
    "@BusinessNewsroom": {
      "initialized": true,
      "last_message_id": 513
    }
  },
  "footer_message_id": 557
}
```

The actual file may contain all eight source channels.

`footer_message_id` is the destination message ID of the current footer.

### `posted_urls.txt`

One successfully forwarded source URL per line.

Both files are committed back to the repository by GitHub Actions so the next run continues from the previous state.

## 11. Footer replacement behavior

The project intentionally keeps only one managed footer.

At the beginning of a run:

```text
stored footer ID
      ↓
try Telethon deletion
      ↓
try Bot API deletion if needed
```

At the end of a run:

```text
sendRichMessage
      ↓
receive destination message_id
      ↓
save footer_message_id
```

This means the next run knows exactly which footer to remove.

If an older footer has already been deleted manually, the stored ID can be safely cleared when Telegram reports that the message no longer exists.

## 12. GitHub Actions schedule

Workflow file:

```text
.github/workflows/forward-hourly.yml
```

Schedule:

```cron
7 2-20 * * *
```

Bangladesh time is UTC+6, so this corresponds to approximately:

```text
08:07 AM through 02:07 AM Bangladesh time
```

Manual execution is also enabled with `workflow_dispatch`.

The workflow uses:

```yaml
permissions:
  contents: write
```

so it can commit the state files back to the repository.

## 13. Workflow secrets are passed like this

```yaml
env:
  API_ID: ${{ secrets.API_ID }}
  API_HASH: ${{ secrets.API_HASH }}
  TELETHON_SESSION: ${{ secrets.TELETHON_SESSION }}
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

Do not rename `TELEGRAM_BOT_TOKEN` in the workflow unless you also change the Python configuration.

## 14. How to generate `TELETHON_SESSION`

Run `generate_session.py` on a trusted machine or Google Colab.

It will ask for:

```text
API ID
API HASH
```

Then it starts the normal Telegram login process and prints the generated `StringSession`.

Save the resulting session string as the GitHub secret:

```text
TELETHON_SESSION
```

Never commit the session string to GitHub.

## 15. Running a manual test

After the four secrets are configured:

1. Open **GitHub → Actions**.
2. Open **Telegram Telethon Hourly Forwarder**.
3. Choose **Run workflow**.
4. Watch the job log.

A healthy run should show messages similar to:

```text
AUTHENTICATED | username=@... | id=... | bot=False
TARGET RESOLVED | @NewsroomHQ
SOURCE ACCESS PASS | @BusinessNewsroom accessible
SOURCE ACCESS PASS | @GamingNewsroom accessible
...
FOOTER POST PASS | method=bot_api | message_id=...
FINISHED | ... | footer_posted=True | ...
```

The token and session value are never printed.

## 16. What a failed run means

### `Missing GitHub secrets: TELEGRAM_BOT_TOKEN`

The repository secret name is wrong or the workflow does not pass it.

Use exactly:

```text
TELEGRAM_BOT_TOKEN
```

### `TELETHON_SESSION is not authorized`

The session expired, is incomplete, or was generated incorrectly.

Generate a new user `StringSession`.

### `TELETHON_SESSION belongs to a bot account`

The session belongs to a bot rather than a normal Telegram user. Generate a user session.

### `SOURCE ACCESS FAIL`

The Telethon user cannot access that channel, or Telegram returned an access error.

The other accessible sources can still continue.

### `FORWARD FAIL`

Telegram rejected a forwarding request. V1 does not advance that message ID after the failure, so the message remains retryable.

### `FOOTER POST BOT API ERROR`

The bot token may be wrong, the bot may not be able to post in `@NewsroomHQ`, or Telegram may reject the Rich Message payload.

Check the bot's permissions and the GitHub secret name first.

## 17. Important implementation rules

Do not change these without a specific reason:

- `TELETHON_SESSION` must remain a user session.
- `TELEGRAM_BOT_TOKEN` is used only for the footer Bot API calls.
- Footer Rich Message uses `InputRichMessage.html`.
- Footer does not use MarkdownV2.
- Footer does not use `InputRichMessage.blocks`.
- Forwarded news posts remain native Telegram forwards.
- `posted_urls.txt` must be preserved between runs.
- `telethon_state.json` must be preserved between runs.

## 18. Files

```text
TelegramForwarder-V1/
├── .github/
│   └── workflows/
│       └── forward-hourly.yml
├── forwarder.py
├── generate_session.py
├── requirements.txt
└── README.md
```

## 19. Dependency

The project intentionally keeps dependencies small:

```text
Telethon>=1.40,<2
```

The Telegram Bot API calls use Python's standard library HTTP tools, so no extra `requests` package is required.

## 20. Security notes

Never commit any of these values into the repository:

```text
API_ID/API_HASH combination used with the account
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

Keep them in GitHub Actions repository secrets.

Do not print them in logs, issues, screenshots, or README files.

## 21. V1 summary

**Telegram Forwarder V1 =**

```text
8 Telegram sources
        ↓
Telethon user history polling
        ↓
chronological per-source queues
        ↓
random cross-channel interleaving
        ↓
native Telegram forwarding
        ↓
URL + message-ID deduplication
        ↓
persistent GitHub state
        ↓
Rich Message footer via Bot API
        ↓
centered Pull Quote + final CTA
```

The design keeps forwarding reliable and native while using Telegram's newer Rich Message system only where richer footer formatting is needed.
