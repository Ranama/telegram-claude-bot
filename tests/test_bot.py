"""End-to-end tests with a fake Telegram API and a fake `claude` binary."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot as botmod  # noqa: E402

FAKE = str(ROOT / "tests" / "fake_claude.py")
ME, STRANGER, CHAT_A, CHAT_B = 111, 999, 5001, 5002


class FakeAPI:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def last(self, chat_id):
        msgs = [t for c, t in self.sent if c == chat_id]
        return msgs[-1] if msgs else None


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "wa").mkdir()
        (d / "wb").mkdir()
        self.cfg = {
            "allowed_user_ids": [ME], "claude_bin": [sys.executable, FAKE], "job_timeout_seconds": 30,
            "accounts": {
                "personal": {"config_dir": str(d / "prof-a"), "workdir": str(d / "wa"),
                             "permission_mode": "acceptEdits"},
                "work": {"config_dir": str(d / "prof-b"), "workdir": str(d / "wb"), "model": "sonnet"},
            },
        }
        self.state_path = d / "state.json"
        self.api = FakeAPI()
        self.bot = botmod.Bot(self.cfg, self.api, botmod.State(self.state_path))
        self.uid = 0

    def tearDown(self):
        self.tmp.cleanup()

    def say(self, text, chat=CHAT_A, user=ME, username=None):
        self.uid += 1
        sender = {"id": user}
        if username:
            sender["username"] = username
        self.bot.handle_update({"update_id": self.uid, "message": {
            "chat": {"id": chat}, "from": sender, "text": text}})

    def wait(self, chat=CHAT_A):
        for _ in range(200):
            with self.bot.jobs_lock:
                if chat not in self.bot.jobs:
                    return
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_unauthorized_user_is_ignored(self):
        self.say("hello", user=STRANGER)
        self.assertEqual(self.api.sent, [])
        self.say("/start", user=STRANGER)
        self.assertIn("Not authorized", self.api.last(CHAT_A))
        self.say("/whoami", user=STRANGER)
        self.assertIn(str(STRANGER), self.api.last(CHAT_A))

    def test_username_allowed_and_pinned(self):
        self.bot.allowed_usernames = {"ranama95"}
        self.say("/accounts", user=4242, username="Ranama95")
        self.assertIn("Accounts", self.api.last(CHAT_A))
        self.assertEqual(self.bot.state.data["pinned_users"]["ranama95"], 4242)
        n = len(self.api.sent)
        self.say("/accounts", user=777, username="Ranama95")   # handle taken over
        self.assertEqual(len(self.api.sent), n)
        self.say("/accounts", user=888, username="someoneelse")
        self.assertEqual(len(self.api.sent), n)

    def test_requires_link(self):
        self.say("do something")
        self.assertIn("No account linked", self.api.last(CHAT_A))

    def test_full_flow_session_continuation_and_usage(self):
        self.say("/link personal")
        self.say("write tests")
        self.wait()
        done = self.api.last(CHAT_A)
        self.assertTrue(done.startswith("✅ personal"), done)
        self.assertIn("prof-a", done)                  # correct account profile
        self.assertIn("resumed=False", done)
        self.assertIn("--permission-mode acceptEdits", done)
        sid = self.bot.state.session(CHAT_A, "personal")["session_id"]
        self.assertTrue(sid)

        self.say("/continue")
        self.wait()
        self.assertIn(f"--resume {sid}", self.api.last(CHAT_A))
        self.assertIn("Did: Continue.", self.api.last(CHAT_A))

        self.say("/usage")
        u = self.api.last(CHAT_A)
        self.assertIn("runs 2 · turns 6 · $0.0246", u)
        self.assertIn("Last turn context: 4.6k", u)

        self.say("/new")
        self.say("fresh task")
        self.wait()
        self.assertIn("resumed=False", self.api.last(CHAT_A))
        self.assertNotEqual(sid, self.bot.state.session(CHAT_A, "personal")["session_id"])
        self.say("/sessions")
        self.assertIn(sid, self.api.last(CHAT_A))

    def test_two_chats_two_accounts_in_parallel(self):
        os.environ["FAKE_SLEEP"] = "2"
        try:
            self.say("/link personal", chat=CHAT_A)
            self.say("/link work", chat=CHAT_B)
            t0 = time.time()
            self.say("task A", chat=CHAT_A)
            self.say("task B", chat=CHAT_B)
            self.wait(CHAT_A)
            self.wait(CHAT_B)
            self.assertLess(time.time() - t0, 3.5)  # concurrently; serial would be >= 4s
        finally:
            os.environ.pop("FAKE_SLEEP")
        self.assertIn("prof-a", self.api.last(CHAT_A))
        self.assertIn("prof-b", self.api.last(CHAT_B))
        self.assertIn("--model sonnet", self.api.last(CHAT_B))

    def test_busy_status_and_stop(self):
        os.environ["FAKE_SLEEP"] = "5"
        try:
            self.say("/link work")
            self.say("long job")
            time.sleep(0.3)
            self.say("another")
            self.assertIn("still working", self.api.last(CHAT_A))
            self.say("/status")
            self.assertIn("Running for", self.api.last(CHAT_A))
            self.say("/stop")
            self.wait()
        finally:
            os.environ.pop("FAKE_SLEEP")
        msgs = [t for c, t in self.api.sent if c == CHAT_A]
        self.assertIn("Stopping", msgs[-2])
        self.assertIn("Stopped after", msgs[-1])

    def test_claude_failure_reported(self):
        self.say("/link personal")
        self.say("please FAIL")
        self.wait()
        self.assertIn("exited with code 2", self.api.last(CHAT_A))
        self.assertIn("simulated crash", self.api.last(CHAT_A))

    def test_resume_and_state_persists(self):
        self.say("/link work")
        self.say("/resume abc-123")
        self.say("go on")
        self.wait()
        self.assertIn("--resume abc-123", self.api.last(CHAT_A))
        reloaded = botmod.State(self.state_path)
        self.assertEqual(reloaded.chat(CHAT_A)["account"], "work")
        self.assertEqual(reloaded.account_stats("work")["runs"], 1)

    def test_single_account_autolinks(self):
        cfg = dict(self.cfg, accounts={"main": self.cfg["accounts"]["personal"]})
        b = botmod.Bot(cfg, self.api, botmod.State(Path(self.tmp.name) / "s1.json"))
        self.bot = b
        self.say("hello there")
        self.wait()
        msgs = [t for c, t in self.api.sent if c == CHAT_A]
        self.assertIn("linked to 'main'", msgs[0])
        self.assertTrue(msgs[-1].startswith("✅ main"), msgs[-1])

    def test_login_error_gets_hint(self):
        self.say("/link work")
        self.say("LOGINNEEDED please")
        self.wait()
        last = self.api.last(CHAT_A)
        self.assertIn("Please run /login", last)
        self.assertIn("isn't signed in", last)

    def test_missing_workdir_reported(self):
        self.cfg["accounts"]["work"]["workdir"] = str(Path(self.tmp.name) / "nope")
        self.say("/link work")
        self.say("do it")
        self.wait()
        self.assertIn("doesn't exist", self.api.last(CHAT_A))

    def test_claude_version_and_auth_status(self):
        cmd = [sys.executable, FAKE]
        self.assertEqual(botmod.claude_version(cmd), "9.9.9 (Claude Code)")
        self.assertIsNone(botmod.claude_version([sys.executable, "-c", "print('hi')"]))
        ok, info = botmod.claude_auth_status(cmd)
        self.assertTrue(ok)
        self.assertEqual(info["authMethod"], "oauth_token")
        env = dict(os.environ, FAKE_LOGIN_MARKER=str(Path(self.tmp.name) / "marker"))
        ok, _ = botmod.claude_auth_status(cmd, env)
        self.assertFalse(ok)
        ok, _ = botmod.claude_auth_status([str(Path(self.tmp.name) / "missing-binary")])
        self.assertIsNone(ok)

    def test_find_claude_in_install_folder(self):
        home = Path(self.tmp.name) / "home"
        bindir = home / ".local" / "bin"
        bindir.mkdir(parents=True)
        exe = bindir / ("claude.exe" if os.name == "nt" else "claude")
        exe.write_text("")
        empty = Path(self.tmp.name) / "emptypath"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty), "HOME": str(home),
                                          "USERPROFILE": str(home), "APPDATA": str(home)}):
            self.assertEqual(Path(botmod.find_claude()), exe)
            self.assertEqual(Path(botmod.resolve_bin("claude")), exe)
            self.assertIsNone(botmod.find_claude("definitely-not-a-command-xyz"))

    def test_instance_lock(self):
        path = Path(self.tmp.name) / "bot.lock"
        first, second = botmod.InstanceLock(path), botmod.InstanceLock(path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_config_with_bom(self):
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps({"telegram_bot_token": "1:x", "allowed_usernames": ["a"],
                                    "accounts": {"m": {}}}), encoding="utf-8-sig")
        self.assertEqual(botmod.load_config(path)["accounts"], {"m": {}})

    def test_long_message_split(self):
        chunks = botmod.split_message("x\n" * 5000)
        self.assertTrue(all(len(c) <= botmod.TG_LIMIT for c in chunks))
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
