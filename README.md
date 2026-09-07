# Telegram Forwarder V1

A GitHub Actions + Telethon forwarder for the Newsroom Telegram network.

## What this project does

The workflow checks eight specialty Newsroom channels, forwards new posts to `@NewsroomHQ`, prevents duplicates, keeps the order inside each source chronological, mixes sources randomly, and keeps one interactive Newsroom footer as the final message in the target channel.

Sources:

- `@BusinessNewsroom`
- `@GamingNewsroom`
- `@TheTechNewsroom`
- `@EntertainmentNewsroom`
- `@ScienceNewsroom`
- `@CareerNewsroom`
- `@ComicsNewsroom`
- `@TheSportsNewsroom`

Target:

- `@NewsroomHQ`

## V1 footer: Rich Message + Inline Keyboard

The final Newsroom promotion is intentionally split into two Telegram-native layers:

1. **Rich Message HTML** for the editorial/premium content.
2. **`InlineKeyboardMarkup`** for the eight channel links below the Rich Message.

The Rich Message is sent through Telegram Bot API `sendRichMessage` using **`InputRichMessage.html` only**. It does not use MarkdownV2, `InputRichMessage.blocks`, `<tg-button>`, or `<tg-button-row>`.

Current promotion content:

```text
There's more to Newsroom.

Pick the feed you want next and stay close to what matters.

[ centered pull quote ]
One connected network, all the news you need.

[ 💼 Business ] [ 💻 Tech ]
[ 🎮 Gaming   ] [ 🔭 Science ]
[ 🎬 Entertainment ] [ 🎓 Career ]
[ 🦸 Comics   ] [ 🏆 Sports ]
```

The keyboard is deliberately a normal `InlineKeyboardMarkup` attached through the `reply_markup` parameter of `sendRichMessage`. Telegram's Bot API explicitly supports `reply_markup` on `sendRichMessage`, with an inline keyboard represented as rows of `InlineKeyboardButton` objects. citeturn3view0

### Mobile layout decisions

- Exactly **2 buttons per row** and **4 rows**.
- All eight buttons use the same plain URL-button structure and visual treatment.
- Emoji stays at the beginning of every label for fast scanning.
- `Entertainment` uses the full label `🎬 Entertainment`. Telegram clients decide whether that label fits on one line; the code does not use unsupported width, height, padding, or CSS tricks.
- No artificial blank lines or padding characters are inserted into button labels.
- No separate category heading, redundant instruction, or generic “Explore our specialty channels” copy is included.

Telegram documents `InlineKeyboardMarkup` as an array of button rows, and `sendRichMessage` accepts it through `reply_markup`. citeturn0search0turn3view0

## Promotion lifecycle

The promotion remains the final message in `@NewsroomHQ`.

At the beginning of a run, the previous saved footer message is deleted. At the end of the run, the new Rich Message is sent with the inline keyboard attached, and its destination `message_id` is stored for the next run.

This keeps the channel from accumulating multiple copies of the promotion.

## Authentication

Two Telegram authentication mechanisms are used for different jobs.

### 1. Telethon user session

Used for:

- Reading source-channel history.
- Detecting new source messages.
- Native forwarding.
- Deleting the previous footer when possible.

Required GitHub repository secrets:

```text
API_ID
API_HASH
TELETHON_SESSION
```

`TELETHON_SESSION` must be a **regular Telegram user StringSession**, not a bot session.

### 2. Telegram Bot API token

Used only for the interactive footer.

Required GitHub repository secret:

```text
TELEGRAM_BOT_TOKEN
```

Use your existing bot token. Do not create another bot just for this project.

The workflow passes it as:

```yaml
TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

The bot must be able to post the footer in `@NewsroomHQ` and delete its own previous footer message. Keep the token secret and never commit it.

## Why both Telethon and Bot API are used

Telegram history polling and native forwarding are handled by the Telethon user session. Rich Message footer delivery is handled separately by the Bot API because `sendRichMessage` is the API method that provides the Rich Message HTML interface and interactive Rich Message blocks. citeturn708593search0

Forwarded source posts are not rewritten. They remain native Telegram forwards.

## Historical backfill

The first deployment does not intentionally take the newest message as a baseline.

For an uninitialized source, the saved source ID starts at `0` and the forwarder processes available history oldest-first. This allows historical posts to be backfilled.

Backfill is resumable. A source message is not treated as successfully processed until the Telegram forward operation succeeds.

A source can have more than 100 pending messages. The forwarder refills its queue automatically as the previous batch is exhausted.

## Cross-channel randomized order

Messages remain chronological inside each source, but the eight sources are interleaved randomly.

The previous source is excluded from the next choice whenever another source has pending messages. This reduces long consecutive runs from the same channel.

Example:

```text
Business 1
Gaming 1
Tech 1
Science 1
Business 2
Sports 1
Career 1
Gaming 2
...
```

This changes only cross-channel order. It does not reorder messages inside an individual source.

## Duplicate protection

Two layers are used.

### `telethon_state.json`

Stores the last successfully processed Telegram message ID for every source and the current footer message ID.

### `posted_urls.txt`

Stores the canonical source URL for every successfully forwarded post, for example:

```text
https://t.me/BusinessNewsroom/123
```

If a URL is already present, that message is skipped as a duplicate.

## Failure safety

The source message ID advances only after a successful forward or a safe non-news/service-message skip.

If a forward fails:

1. The failed source is blocked for the current run.
2. Its state is not advanced past the failed message.
3. Other accessible sources continue.
4. The next workflow run retries the failed message.

This prevents a transient Telegram failure from silently losing a source post.

## Service messages

Telegram service messages are not treated as ordinary news posts.

They are skipped safely and the source state advances past the service-message ID so later real posts can continue processing.

## Footer lifecycle

The footer is designed to remain the final message in `@NewsroomHQ`.

At the beginning of a run:

1. Read `footer_message_id` from `telethon_state.json`.
2. Delete the previous footer using that exact destination message ID.
3. Continue source processing.

At the end of a run:

1. Build the Rich HTML footer.
2. Send it with Bot API `sendRichMessage`.
3. Read the returned destination `message_id`.
4. Save that ID to `telethon_state.json`.

The next run repeats the cycle.

If an old footer was already deleted manually, the delete failure is handled as an already-removed message when Telegram reports a known invalid/deleted-message condition.

## GitHub Actions schedule

Workflow file:

```text
.github/workflows/forward-hourly.yml
```

Schedule:

```text
07 minutes past every hour
08:07 AM through 02:07 AM Bangladesh time
```

The cron expression is:

```text
7 2-20 * * *
```

because Bangladesh Standard Time is UTC+6.

Manual execution is also enabled with `workflow_dispatch`.

## Required repository secrets

Create these four GitHub **repository secrets**:

```text
API_ID
API_HASH
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

Do not use a secret named `BOT_TOKEN` for this V1 workflow unless the workflow is changed to map that name explicitly. The current workflow expects `TELEGRAM_BOT_TOKEN`.

## Telegram permissions

The Telegram user account stored in `TELETHON_SESSION` should have:

- Access to all eight source channels.
- Permission to read their history.
- Permission to post/forward into `@NewsroomHQ`.
- Permission to delete the previous footer in `@NewsroomHQ`.

The footer bot should have enough permissions in `@NewsroomHQ` to post and delete the footer messages it creates.

## Session generation

Use `generate_session.py` on a trusted machine or Google Colab.

The program asks for `API_ID` and `API_HASH`, starts the normal Telegram login flow, and prints the resulting StringSession.

Store only that session string in the GitHub `TELETHON_SESSION` secret.

Never put it in source code or commit it to the repository.

## State files

The repository may contain:

```text
telethon_state.json
posted_urls.txt
```

These are normal persistent state files and are committed by GitHub Actions after a run when they change.

Do not delete them during normal operation. Deleting them resets the source state and can cause the project to backfill history again.

## Clean first-run state

A clean state is:

```json
{
  "initialized": false,
  "channels": {},
  "footer_message_id": null
}
```

An empty `posted_urls.txt` is also valid.

## How to deploy

### Step 1: Upload the project

Place the project files in the GitHub repository root:

```text
forwarder.py
requirements.txt
generate_session.py
README.md
.github/workflows/forward-hourly.yml
```

### Step 2: Add secrets

Open:

```text
GitHub → Settings → Secrets and variables → Actions → Repository secrets
```

Add:

```text
API_ID
API_HASH
TELETHON_SESSION
TELEGRAM_BOT_TOKEN
```

For `TELEGRAM_BOT_TOKEN`, paste the same existing bot token you already use for your Newsroom bot infrastructure.

### Step 3: Check permissions

Make sure the Telethon user and footer bot can work in the source channels and `@NewsroomHQ`.

### Step 4: Run manually

Open:

```text
GitHub → Actions → Telegram Telethon Hourly Forwarder → Run workflow
```

### Step 5: Check the log

A healthy run should show messages similar to:

```text
AUTHENTICATED | username=@YourUser | id=... | bot=False
TARGET RESOLVED | @NewsroomHQ
SOURCE ACCESS PASS | @BusinessNewsroom accessible
...
FORWARD PASS | source=@BusinessNewsroom | source_message_id=...
...
FOOTER POST PASS | method=bot_api | message_id=...
FINISHED | initialized=True | forwarded=... | failed=0 | footer_posted=True | posted_urls=...
```

The bot token itself is never printed.

## What a successful footer test should show

After a manual run, `@NewsroomHQ` should end with one promotion message containing:

- A strong `There's more to Newsroom.` heading.
- One short personalization/CTA sentence.
- A centered Pull Quote: `One connected network, all the news you need.`
- Four rows of two inline URL buttons.
- Exactly these eight labels:

```text
💼 Business       💻 Tech
🎮 Gaming         🔭 Science
🎬 Entertainment  🎓 Career
🦸 Comics         🏆 Sports
```

The eight button destinations are:

```text
Business       → https://t.me/BusinessNewsroom
Tech           → https://t.me/TheTechNewsroom
Gaming         → https://t.me/GamingNewsroom
Science        → https://t.me/ScienceNewsroom
Entertainment  → https://t.me/EntertainmentNewsroom
Career         → https://t.me/CareerNewsroom
Comics         → https://t.me/ComicsNewsroom
Sports         → https://t.me/TheSportsNewsroom
```

The exact button height, width, and line wrapping remain Telegram-client controlled. The implementation does not attempt to override them.

## Troubleshooting

### `TELEGRAM_BOT_TOKEN` missing

GitHub secret name must be exactly:

```text
TELEGRAM_BOT_TOKEN
```

The workflow must contain:

```yaml
TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

### `TELETHON_SESSION belongs to a bot account`

Generate a regular user StringSession. Do not use the bot token for Telethon history polling.

### Footer posting fails

Check:

- The bot token is correct.
- The bot can access `@NewsroomHQ`.
- The bot has permission to post in the target channel.
- Telegram Bot API Rich Messages are available to the bot account and current Telegram client.

### Footer deletion fails

The project first tries deletion through the Telethon user session and then through the Bot API for bot-authored footer messages. Check the permissions of the account and bot in `@NewsroomHQ`.

### A source stops while others continue

That usually means Telegram returned an error while processing that source. The failed source is intentionally blocked for the current run so other sources can continue, and its last successful message ID remains unchanged for retry on the next run.

### State push fails

The workflow requires:

```yaml
permissions:
  contents: write
```

GitHub Actions must be allowed to push to the repository branch used by the workflow.

## Security notes

Never commit:

- Telegram API credentials.
- `TELETHON_SESSION`.
- `TELEGRAM_BOT_TOKEN`.
- Login codes or 2FA passwords.

Never print the session string or bot token in logs.

## Project files

```text
TelegramForwarder-V1/
├── forwarder.py
├── requirements.txt
├── generate_session.py
├── README.md
└── .github/
    └── workflows/
        └── forward-hourly.yml
```

## V1 design summary

```text
             Telegram source channels (8)
                         │
                         ▼
                Telethon user session
                         │
              history + native forward
                         │
                         ▼
                   @NewsroomHQ
                         │
                         ├── forwarded source posts
                         │
                         └── final promotion
                                  │
                                  ▼
                         Telegram Bot API
                         sendRichMessage
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
          InputRichMessage.html        InlineKeyboardMarkup
                    │                           │
              Heading + CTA               4 × 2 grid
                    │                           │
                Pull Quote                8 channel links
```
