"""Generate a Telethon user StringSession for GitHub Actions.

Run this once on a trusted machine. The printed session string is a login
credential: copy it directly into the GitHub Actions secret TELETHON_SESSION
and do not commit it to the repository.
"""

import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    try:
        api_id = int(os.environ.get("API_ID", "0"))
    except ValueError as exc:
        raise SystemExit("API_ID must be an integer.") from exc

    api_hash = os.environ.get("API_HASH", "").strip()
    if not api_id or not api_hash:
        raise SystemExit("Set API_ID and API_HASH before running this script.")

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        await client.start()
        session = client.session.save()
        print("LOGIN SUCCESS")
        print("TELETHON_SESSION:")
        print(session)
        print("Store this value only in the GitHub secret TELETHON_SESSION.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
