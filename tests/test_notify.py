"""Tests for notify.py (the Claude Code hook) and the hook installation in setup_bot.py."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot  # noqa: E402
import notify  # noqa: E402
import setup_bot  # noqa: E402

CHAT = 486552230


class FakeAPI:
    def __init__(self, token=None):
        self.sent = []
        self.next_id = 100

    def send(self, chat_id, text):
        self.next_id += 1
        self.sent.append((chat_id, text))
        return [self.next_id]


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "config.json").write_text(json.dumps({
            "telegram_bot_token": "123456789:" + "A" * 35, "allowed_usernames": ["Ranama95"],
            "accounts": {"main": {}}}), encoding="utf-8")
        (self.base / "state.json").write_text(json.dumps({
            "chats": {str(CHAT): {"account": "main", "sessions": {}}},
            "pinned_users": {"ranama95": CHAT}}), encoding="utf-8")
        self.api = FakeAPI()
        env = {k: v for k, v in os.environ.items() if k != bot.BOT_ENV_FLAG}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def handle(self, event):
        return notify.handle(event, base=self.base, api_factory=lambda token: self.api)

    def stop_event(self, text="All done: tests pass.", **extra):
        return dict({"hook_event_name": "Stop", "session_id": "sess-1",
                     "cwd": str(self.base / "VineTrip"), "last_assistant_message": text}, **extra)

    def test_finished_session_is_sent_and_remembered(self):
        self.assertEqual(self.handle(self.stop_event()), 1)
        chat, text = self.api.sent[0]
        self.assertEqual(chat, CHAT)
        self.assertIn("Claude finished — VineTrip", text)
        self.assertIn("All done: tests pass.", text)
        self.assertIn("Reply to this message", text)
        entry = bot.hook_session_lookup(self.base, CHAT, 101)
        self.assertEqual(entry["session_id"], "sess-1")
        self.assertEqual(entry["cwd"], str(self.base / "VineTrip"))
        self.assertEqual(bot.hook_session_lookup(self.base, CHAT)["session_id"], "sess-1")

    def test_waiting_for_input_and_permission(self):
        self.handle({"hook_event_name": "Notification", "notification_type": "idle_prompt",
                     "session_id": "s", "cwd": "/x/proj", "message": "Claude is waiting for your input"})
        self.handle({"hook_event_name": "Notification", "notification_type": "permission_prompt",
                     "session_id": "s", "cwd": "/x/proj", "message": "Claude needs permission to use Bash"})
        self.assertIn("waiting for you — proj", self.api.sent[0][1])
        self.assertIn("Reply to this message to answer", self.api.sent[0][1])
        self.assertIn("needs permission", self.api.sent[1][1])
        self.assertIn("on the PC", self.api.sent[1][1])

    def test_uninteresting_events_are_ignored(self):
        self.assertEqual(self.handle({"hook_event_name": "Notification", "notification_type": "auth_success",
                                      "session_id": "s", "cwd": "/x"}), 0)
        self.assertEqual(self.handle({"hook_event_name": "PreToolUse", "session_id": "s"}), 0)
        self.assertEqual(self.handle(self.stop_event(stop_hook_active=True)), 0)
        self.assertEqual(self.api.sent, [])

    def test_bot_runs_are_skipped(self):
        with mock.patch.dict(os.environ, {bot.BOT_ENV_FLAG: "1"}):
            self.assertEqual(self.handle(self.stop_event()), 0)
        self.assertEqual(self.api.sent, [])

    def test_can_be_muted(self):
        notify.set_notify_enabled(self.base, False)
        self.assertEqual(self.handle(self.stop_event()), 0)
        notify.set_notify_enabled(self.base, True)
        self.assertEqual(self.handle(self.stop_event()), 1)

    def test_long_text_is_cut(self):
        self.handle(self.stop_event("x" * 10000))
        self.assertLess(len(self.api.sent[0][1]), 4000)

    def test_without_linked_chat_uses_pinned_user(self):
        (self.base / "state.json").write_text(json.dumps({"pinned_users": {"ranama95": 42}}),
                                              encoding="utf-8")
        self.handle(self.stop_event())
        self.assertEqual(self.api.sent[0][0], 42)

    def test_main_never_fails(self):
        with mock.patch.object(sys, "stdin", io.StringIO("not json")), \
                mock.patch.object(notify, "HERE", self.base):
            self.assertEqual(notify.main(), 0)


class HookInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Path(self.tmp.name) / ".claude" / "settings.json"
        self.out = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_install_keeps_other_settings_and_is_idempotent(self):
        self.settings.parent.mkdir(parents=True)
        other = {"type": "command", "command": "echo other"}
        self.settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [other]}]}}),
                                 encoding="utf-8")
        handler = setup_bot.hook_handler()
        setup_bot.update_hooks(self.settings, handler)
        setup_bot.update_hooks(self.settings, handler)
        data = self.read()
        self.assertEqual(data["model"], "opus")
        stop_cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop_cmds.count(handler["command"]), 1)
        self.assertIn("echo other", stop_cmds)
        self.assertEqual(data["hooks"]["Notification"][0]["matcher"], setup_bot.NOTIFY_MATCHER)
        self.assertTrue(self.settings.with_name("settings.json.before-telegram-bot").exists())

        setup_bot.update_hooks(self.settings, None)
        data = self.read()
        self.assertEqual([h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]], ["echo other"])
        self.assertNotIn("Notification", data["hooks"])

    def test_install_into_missing_settings_and_remove_everything(self):
        setup_bot.update_hooks(self.settings, setup_bot.hook_handler())
        self.assertIn("Stop", self.read()["hooks"])
        setup_bot.update_hooks(self.settings, None)
        self.assertNotIn("hooks", self.read())

    def test_invalid_settings_are_not_touched(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken", encoding="utf-8")
        with self.assertRaises(setup_bot.SetupError):
            setup_bot.update_hooks(self.settings, setup_bot.hook_handler())
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{broken")

    def test_windows_hook_uses_powershell_and_pythonw(self):
        folder = Path(self.tmp.name) / "Py thon"
        folder.mkdir()
        (folder / "python.exe").write_text("")
        (folder / "pythonw.exe").write_text("")
        h = setup_bot.hook_handler(python=folder / "python.exe",
                                   script=Path("D:/Claude/telegram-claude-bot/notify.py"), windows=True)
        self.assertEqual(h["shell"], "powershell")
        self.assertTrue(h["command"].startswith("& '"))
        self.assertIn("pythonw.exe", h["command"])
        self.assertIn("notify.py'", h["command"])
        self.assertTrue(setup_bot._is_ours(h))

    def test_asks_once_and_remembers_answer(self):
        cfg = {"accounts": {"main": {}}}
        with redirect_stdout(self.out), \
                mock.patch.object(setup_bot, "settings_files", return_value=[self.settings]):
            setup_bot.ensure_notifications(cfg, ask=lambda prompt: "")
            self.assertTrue(cfg["notify_hooks"])
            self.assertIn("Stop", self.read()["hooks"])
            setup_bot.ensure_notifications(cfg, ask=mock.Mock(side_effect=AssertionError("asked")))
            cfg["notify_hooks"] = False
            setup_bot.ensure_notifications(cfg)
        self.assertNotIn("hooks", self.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
