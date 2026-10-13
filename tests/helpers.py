"""Shared test helpers: a temporary folder and a fake Telegram."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHAT = 486552230
TOKEN = "123456789:" + "A" * 35


class FakeTelegram:
    def __init__(self):
        self.sent, self.calls, self.updates = [], [], []
        self.next_id = 100

    def send(self, cfg, text, buttons=None, chat_id=None):
        self.next_id += 1
        self.sent.append({"chat": chat_id or cfg.get("chat_id"), "text": text, "buttons": buttons})
        return [self.next_id]

    def call(self, token, method, http_timeout=20, **params):
        self.calls.append((method, params))
        if method == "getMe":
            return {"username": "test_bot"}
        if method == "getUpdates":
            return self.updates.pop(0) if self.updates else []
        if method == "sendMessage":
            return self.send({"chat_id": params["chat_id"]}, params["text"]) and {"message_id": self.next_id}
        return True


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "TELEGRAM_BOT_TOKEN")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home))
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()
