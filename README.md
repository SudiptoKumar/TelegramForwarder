# Telegram Channel Publisher v4

This version does not use `forwardMessage` or `copyMessage`.

It takes the actual `channel_post` data contained in Telegram's update and
publishes the content directly to `@NewsroomHQ` using the appropriate
`send*` Bot API method.

Source channels:
- @BusinessNewsroom
- @GamingNewsroom
- @TheTechNewsroom
- @EntertainmentNewsroom

Destination:
- @NewsroomHQ

The scheduled workflow runs hourly:
`0 * * * *`

Setup:
1. Put the files in a GitHub repository.
2. Add repository Actions secret `BOT_TOKEN`.
3. Ensure the bot is an administrator in all source channels and can post in
   @NewsroomHQ.
4. Keep Telegram webhook URL empty.
5. Run the workflow manually.
6. Create a NEW post after the run starts/finishes and run the workflow again.
7. Verify `DIRECT PUBLISH PASS` and the post in @NewsroomHQ.

This is a re-publisher, not a Telegram-native forward. The destination will
contain a newly published message rather than Telegram's "Forwarded from"
header.

Supported common post types:
text, photo, video, animation, document, audio, voice, video note, sticker,
contact, location, venue.

Poll/dice and other unsupported types are recorded in `skipped.json`.
