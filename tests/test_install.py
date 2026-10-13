import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import CHAT, TOKEN, FakeTelegram, TempDirTest
import install
import tg


class InstallTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.settings = self.home / ".claude" / "settings.json"
        self.tg = FakeTelegram()
        self.config = self.dir / "config.json"
        for target, value in ((tg, "CONFIG"), (install, "HERE")):
            p = mock.patch.object(target, value, self.config if value == "CONFIG" else self.dir)
            p.start()
            self.addCleanup(p.stop)
        for name in ("call", "send"):
            p = mock.patch.object(tg, name, getattr(self.tg, name))
            p.start()
            self.addCleanup(p.stop)

    def read(self, path=None):
        return json.loads((path or self.settings).read_text(encoding="utf-8"))

    def run_install(self, *args):
        with mock.patch("sys.stdout", io.StringIO()) as out, mock.patch("sys.stdin", io.StringIO()):
            code = install.main(list(args))
        return code, out.getvalue()

    def test_install_with_token_and_chat(self):
        code, out = self.run_install("--token", TOKEN, "--chat-id", str(CHAT))
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.config.read_text()), {"telegram_bot_token": TOKEN, "chat_id": CHAT})
        hooks = self.read()["hooks"]
        self.assertEqual(sorted(hooks), sorted(install.EVENTS))
        self.assertTrue(hooks["UserPromptSubmit"][0]["hooks"][0]["async"])
        self.assertNotIn("async", hooks["Stop"][0]["hooks"][0])
        self.assertEqual(hooks["Notification"][0]["matcher"], "elicitation_dialog|agent_needs_input")
        self.assertIn("hook.py", hooks["Stop"][0]["hooks"][0]["command"])
        self.assertIn("set up", self.tg.sent[-1]["text"])

    def test_links_chat_from_first_message(self):
        self.tg.updates = [[], [{"update_id": 7, "message": {"chat": {"id": 42, "type": "private"},
                                                            "from": {"username": "Ranama95"}}}]]
        code, out = self.run_install("--token", TOKEN)
        self.assertEqual(code, 0, out)
        self.assertIn("Ranama95", out)
        self.assertEqual(json.loads(self.config.read_text())["chat_id"], 42)

    def test_rerun_keeps_other_settings_and_replaces_old_hooks(self):
        self.settings.parent.mkdir(parents=True)
        old = {"type": "command", "command": "python D:/Claude/telegram-claude-bot/notify.py"}
        mine = {"type": "command", "command": "echo mine"}
        self.settings.write_text(json.dumps({"model": "opus", "hooks": {
            "Stop": [{"hooks": [old, mine]}], "PreToolUse": [{"hooks": [old]}]}}), encoding="utf-8")
        self.run_install("--token", TOKEN, "--chat-id", str(CHAT))
        self.run_install("--if-needed")
        data = self.read()
        self.assertEqual(data["model"], "opus")
        stop = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop), 2)
        self.assertIn("echo mine", stop)
        self.assertNotIn("PreToolUse", data["hooks"])        # the old version's leftovers are gone
        self.assertTrue(self.settings.with_name("settings.json.before-telegram").exists())
        self.run_install("--uninstall")
        self.assertEqual(self.read()["hooks"], {"Stop": [{"hooks": [mine]}]})

    def test_more_profiles(self):
        work = self.dir / "work"
        self.run_install("--token", TOKEN, "--chat-id", str(CHAT), "--profile", str(work))
        self.assertIn("Stop", self.read(work / "settings.json")["hooks"])
        self.assertEqual(json.loads(self.config.read_text())["profiles"], [str(work)])

    def test_migrates_old_config(self):
        self.config.write_text(json.dumps({"telegram_bot_token": TOKEN, "accounts": {"main": {}},
                                           "allowed_usernames": ["Ranama95"]}), encoding="utf-8")
        (self.dir / "state.json").write_text(json.dumps({"pinned_users": {"ranama95": CHAT}}), encoding="utf-8")
        code, out = self.run_install("--if-needed")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(self.config.read_text()), {"telegram_bot_token": TOKEN, "chat_id": CHAT})
        self.assertIn("set up", self.tg.sent[-1]["text"])      # confirms the switch
        self.tg.sent.clear()
        self.run_install("--if-needed")
        self.assertEqual(self.tg.sent, [])                     # later runs stay quiet

    def test_no_token_non_interactive(self):
        code, out = self.run_install()
        self.assertEqual(code, 1)
        self.assertIn("@BotFather", out)

    def test_bad_settings_are_not_touched(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken", encoding="utf-8")
        code, out = self.run_install("--token", TOKEN, "--chat-id", str(CHAT))
        self.assertEqual(code, 1)
        self.assertEqual(self.settings.read_text(), "{broken")

    def test_windows_command(self):
        h = install.hook_command(python=Path("C:/Py thon/pythonw.exe"), script=Path("D:/x/hook.py"), windows=True)
        self.assertEqual(h["shell"], "powershell")
        self.assertEqual(h["command"], "& 'C:/Py thon/python.exe' 'D:/x/hook.py'".replace("/", os.sep)
                         if os.name == "nt" else "& 'C:/Py thon/python.exe' 'D:/x/hook.py'")

    @unittest.skipUnless(os.name == "nt", "checks how Claude Code runs hooks on Windows")
    def test_powershell_passes_stdin_and_stdout(self):
        echo = self.dir / "echo.py"
        echo.write_text("import sys, json\nprint(json.loads(sys.stdin.read())['hook_event_name'])\n")
        h = install.hook_command(python=sys.executable, script=echo, windows=True)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", h["command"]],
                           input=json.dumps({"hook_event_name": "Stop"}), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "Stop", r.stderr)
