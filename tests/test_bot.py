"""End-to-end tests with a fake Telegram API and a fake `claude` binary."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

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

    def say(self, text, chat=CHAT_A, user=ME):
        self.uid += 1
        self.bot.handle_update({"update_id": self.uid, "message": {
            "chat": {"id": chat}, "from": {"id": user}, "text": text}})

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
        os.environ["FAKE_SLEEP"] = "0.5"
        try:
            self.say("/link personal", chat=CHAT_A)
            self.say("/link work", chat=CHAT_B)
            t0 = time.time()
            self.say("task A", chat=CHAT_A)
            self.say("task B", chat=CHAT_B)
            self.wait(CHAT_A)
            self.wait(CHAT_B)
            self.assertLess(time.time() - t0, 1.0)  # ran concurrently
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
        self.assertIn("Stopped after", self.api.last(CHAT_A))

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

    def test_long_message_split(self):
        chunks = botmod.split_message("x\n" * 5000)
        self.assertTrue(all(len(c) <= botmod.TG_LIMIT for c in chunks))
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
