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
        self.buttons = []
        self.edits = []
        self.next_id = 100

    def send(self, chat_id, text, buttons=None):
        self.next_id += 1
        self.sent.append((chat_id, text))
        self.buttons.append(buttons)
        return [self.next_id]

    def edit(self, chat_id, message_id, text, buttons=None):
        self.edits.append((chat_id, message_id, text))
        return True


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

    def test_full_text_is_sent_and_only_huge_text_is_cut(self):
        self.handle(self.stop_event("x" * 10000))
        self.assertIn("x" * 10000, self.api.sent[0][1])       # bot.TelegramAPI splits it
        self.handle(self.stop_event("y" * 40000))
        self.assertLess(len(self.api.sent[1][1]), 16000)
        self.assertIn("cut here", self.api.sent[1][1])

    def test_without_linked_chat_uses_pinned_user(self):
        (self.base / "state.json").write_text(json.dumps({"pinned_users": {"ranama95": 42}}),
                                              encoding="utf-8")
        self.handle(self.stop_event())
        self.assertEqual(self.api.sent[0][0], 42)

    def test_send_text_for_scripts(self):
        self.assertEqual(notify.send_text("Backup finished", base=self.base,
                                          api_factory=lambda token: self.api), 1)
        self.assertEqual(self.api.sent, [(CHAT, "Backup finished")])

    # ---- PermissionRequest: answered from Telegram

    def ask(self, event, answer=None, timeout=30):
        """Run ask_on_telegram; `answer` is written as if you tapped, on the first wait."""
        ticks = {"now": 1000.0}

        def sleep(seconds):
            if answer is not None:
                for path in (self.base / "pending").glob("*.json"):
                    if not path.name.endswith(".answer.json"):
                        bot.write_json(bot.answer_file(self.base, path.stem), answer)
            ticks["now"] += seconds

        return notify.ask_on_telegram(event, base=self.base, api_factory=lambda t: self.api,
                                      timeout=timeout, sleep=sleep, clock=lambda: ticks["now"])

    def perm_event(self):
        return {"hook_event_name": "PermissionRequest", "session_id": "s", "cwd": "/x/VineTrip",
                "tool_name": "Bash", "tool_input": {"command": "npm test", "description": "Run tests"}}

    def test_permission_allowed_from_telegram(self):
        decision = self.ask(self.perm_event(), {"behavior": "allow"})
        self.assertEqual(decision, {"behavior": "allow",
                                    "updatedInput": {"command": "npm test", "description": "Run tests"}})
        text = self.api.sent[0][1]
        self.assertIn("VineTrip — Claude wants to run a command", text)
        self.assertIn("npm test", text)
        labels = [b["text"] for row in self.api.buttons[0] for b in row]
        self.assertEqual(labels, ["✅ Allow", "❌ Deny"])
        self.assertEqual(list((self.base / "pending").iterdir()), [])   # cleaned up

    def test_permission_denied_with_guidance(self):
        decision = self.ask(self.perm_event(), {"behavior": "deny", "message": "use pytest instead"})
        self.assertEqual(decision, {"behavior": "deny", "message": "use pytest instead"})

    def test_questions_answered_from_telegram(self):
        questions = [
            {"question": "Which color?", "header": "Color", "multiSelect": False,
             "options": [{"label": "Red", "description": "warm"}, {"label": "Blue", "description": "cool"}]},
            {"question": "Which sizes?", "header": "Sizes", "multiSelect": True,
             "options": [{"label": "S", "description": ""}, {"label": "M", "description": ""}]},
        ]
        event = {"hook_event_name": "PermissionRequest", "session_id": "s", "cwd": "/x/shop",
                 "tool_name": "AskUserQuestion", "tool_input": {"questions": questions}}
        answers = {"Which color?": "Blue", "Which sizes?": ["S", "M"]}
        decision = self.ask(event, {"answers": answers})
        self.assertEqual(decision["behavior"], "allow")
        self.assertEqual(decision["updatedInput"], {"questions": questions, "answers": answers})
        self.assertEqual(len(self.api.sent), 2)                      # one message per question
        self.assertIn("Which color?", self.api.sent[0][1])
        self.assertIn("Blue — cool", self.api.sent[0][1])
        self.assertIn("✅ Done", [b["text"] for row in self.api.buttons[1] for b in row])

    def test_partial_answers_keep_waiting_then_time_out(self):
        event = {"hook_event_name": "PermissionRequest", "session_id": "s", "cwd": "/x/p",
                 "tool_name": "AskUserQuestion", "tool_input": {"questions": [
                     {"question": "A?", "options": [{"label": "1"}, {"label": "2"}]},
                     {"question": "B?", "options": [{"label": "1"}, {"label": "2"}]}]}}
        self.assertIsNone(self.ask(event, {"answers": {"A?": "1"}}, timeout=5))
        self.assertTrue(all("No answer in time" in e[2] for e in self.api.edits))
        self.assertEqual(len(self.api.edits), 2)

    def test_pc_sessions_ask_on_the_pc_unless_away(self):
        self.assertIsNone(notify.handle_permission(self.perm_event(), base=self.base,
                                                   api_factory=lambda t: self.api))
        self.assertEqual(self.api.sent, [])
        bot.update_notify_settings(self.base, away=True)
        self.assertTrue(notify.should_ask_on_telegram(self.base))

    def test_telegram_tasks_always_ask_here(self):
        with mock.patch.dict(os.environ, {bot.BOT_ENV_FLAG: "1"}):
            self.assertTrue(notify.should_ask_on_telegram(self.base))

    def test_main_prints_the_decision(self):
        bot.update_notify_settings(self.base, away=True)
        out = io.StringIO()
        with mock.patch.object(notify, "HERE", self.base), \
                mock.patch.object(notify, "handle_permission",
                                  return_value={"hookSpecificOutput": {"decision": {"behavior": "allow"}}}), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(self.perm_event()))), \
                mock.patch.object(sys, "stdout", out):
            self.assertEqual(notify.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["decision"]["behavior"], "allow")

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

    def test_windows_hook_uses_powershell_and_console_python(self):
        folder = Path(self.tmp.name) / "Py thon"
        h = setup_bot.hook_handler(python=folder / "pythonw.exe",
                                   script=Path("D:/Claude/telegram-claude-bot/notify.py"), windows=True)
        self.assertEqual(h["shell"], "powershell")
        self.assertTrue(h["command"].startswith("& '"))
        self.assertIn("python.exe'", h["command"])         # console python, not pythonw
        self.assertNotIn("pythonw", h["command"])
        self.assertIn("notify.py'", h["command"])
        self.assertTrue(setup_bot._is_ours(h))

    def test_permission_hook_gets_a_long_timeout(self):
        setup_bot.update_hooks(self.settings, setup_bot.hook_handler(), permission_timeout=720)
        data = self.read()
        self.assertEqual(data["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 720)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["timeout"], 20)

    @unittest.skipUnless(os.name == "nt", "PowerShell hook check runs on Windows")
    def test_powershell_passes_stdin_and_stdout_through(self):
        """What Claude Code does on Windows: run the hook command in PowerShell with the
        event on stdin, and read the decision from stdout."""
        import subprocess
        echo = Path(self.tmp.name) / "echo_hook.py"
        echo.write_text("import sys, json\n"
                        "d = json.loads(sys.stdin.read())\n"
                        "print(json.dumps({'got': d['hook_event_name']}))\n", encoding="utf-8")
        h = setup_bot.hook_handler(python=sys.executable, script=echo, windows=True)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", h["command"]],
                           input=json.dumps({"hook_event_name": "PermissionRequest"}),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(json.loads(r.stdout.strip()), {"got": "PermissionRequest"}, r.stderr)

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
