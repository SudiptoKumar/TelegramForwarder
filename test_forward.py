
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import forward

def test_check_and_forward():
    forward.BOT_TOKEN = "TESTTOKEN"
    forward.API = "https://api.telegram.org/botTESTTOKEN"
    state = Path("state.json")
    state.write_text('{"offset": 0}\n', encoding="utf-8")

    updates = [
        {
            "update_id": 10,
            "channel_post": {
                "message_id": 101,
                "chat": {"id": -1001, "username": "BusinessNewsroom"},
                "text": "Business test",
            },
        },
        {
            "update_id": 11,
            "channel_post": {
                "message_id": 202,
                "chat": {"id": -1002, "username": "GamingNewsroom"},
                "text": "Gaming test",
            },
        },
        {
            "update_id": 12,
            "channel_post": {
                "message_id": 303,
                "chat": {"id": -1003, "username": "OtherChannel"},
                "text": "Ignored test",
            },
        },
    ]

    class Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = json.dumps(payload)
        def raise_for_status(self):
            pass
        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=30):
        if url.endswith("/getMe"):
            return Resp({"ok": True, "result": {"id": 1, "username": "BNewsroombot"}})
        if url.endswith("/getWebhookInfo"):
            return Resp({"ok": True, "result": {"url": "", "pending_update_count": 3}})
        if url.endswith("/getChat"):
            return Resp({"ok": True, "result": {"id": -1000, "title": "NewsroomHQ"}})
        if url.endswith("/getUpdates"):
            return Resp({"ok": True, "result": updates})
        raise AssertionError(url)

    forwarded = []
    def fake_post(url, data=None, timeout=30):
        assert url.endswith("/forwardMessage")
        forwarded.append(data)
        return Resp({"ok": True, "result": {"message_id": len(forwarded)}})

    with patch("forward.requests.get", side_effect=fake_get), \
         patch("forward.requests.post", side_effect=fake_post):
        forward.main()

    assert [x["from_chat_id"] for x in forwarded] == [-1001, -1002]
    assert [x["message_id"] for x in forwarded] == [101, 202]
    assert json.loads(state.read_text())["offset"] == 13

    state.unlink()

def test_empty_token_fails_cleanly():
    env = os.environ.copy()
    env.pop("BOT_TOKEN", None)
    result = subprocess.run(
        [sys.executable, "forward.py", "--check"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "BOT_TOKEN is empty" in result.stderr

if __name__ == "__main__":
    test_check_and_forward()
    test_empty_token_fails_cleanly()
    print("ALL TESTS PASSED")
