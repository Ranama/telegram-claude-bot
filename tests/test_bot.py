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

    def say(self, text, chat=CHAT_A, user=ME, username=None, reply_to=None):
        self.uid += 1
        sender = {"id": user}
        if username:
            sender["username"] = username
        msg = {"chat": {"id": chat}, "from": sender, "text": text}
        if reply_to:
            msg["reply_to_message"] = {"message_id": reply_to}
        self.bot.handle_update({"update_id": self.uid, "message": msg})

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

    def test_state_persists(self):
        self.say("/link work")
        self.say("go on")
        self.wait()
        reloaded = botmod.State(self.state_path)
        self.assertEqual(reloaded.chat(CHAT_A)["account"], "work")
        self.assertEqual(reloaded.account_stats("work")["runs"], 1)

    # ---- Claude Code sessions on the PC: /list, /enter, /history, /leave

    def make_transcript(self, profile, session_id, cwd, prompt, reply, title=None, age=0):
        folder = Path(profile) / "projects" / ("p-" + session_id[:4])
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "user", "cwd": cwd, "sessionId": session_id,
             "message": {"role": "user", "content": prompt}},
            {"type": "user", "cwd": cwd, "isMeta": True,
             "message": {"role": "user", "content": "<system-reminder>meta</system-reminder>"}},
            {"type": "assistant", "cwd": cwd,
             "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}},
            {"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "file text"}]}},
            {"type": "assistant", "cwd": cwd,
             "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
        ]
        if title:
            lines.append({"type": "ai-title", "aiTitle": title, "sessionId": session_id})
        path = folder / f"{session_id}.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def pc_setup(self):
        d = Path(self.tmp.name)
        default_profile = d / "default-profile"
        default_profile.mkdir()
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(default_profile)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.make_transcript(self.cfg["accounts"]["work"]["config_dir"], "aaaa1111-0000",
                             str(d / "wb"), "fix the login bug", "Fixed it: the token was expired.",
                             title="Login bug fix", age=600)
        self.make_transcript(default_profile, "bbbb2222-0000", str(d / "wa"),
                             "write the README", "README written.", age=3600)

    def test_list_enter_send_history_leave(self):
        self.pc_setup()
        d = Path(self.tmp.name)
        self.say("/list")
        listing = self.api.last(CHAT_A)
        self.assertLess(listing.index("Login bug fix"), listing.index("write the README"))
        self.assertIn("1. ", listing)
        self.assertIn("you: fix the login bug", listing)

        self.say("/enter 1")
        entered = self.api.last(CHAT_A)
        self.assertIn("You're in: wb — Login bug fix", entered)
        self.assertIn("Fixed it: the token was expired.", entered)

        self.say("now add a test for it")
        self.wait()
        done = self.api.last(CHAT_A)
        self.assertIn("--resume aaaa1111-0000", done)
        self.assertIn(f"cwd={d / 'wb'}", done)
        self.assertIn("prof-b", done)              # the session's own Claude profile
        self.assertNotIn("Reply to this message", done)

        self.say("/history")
        hist = self.api.last(CHAT_A)
        self.assertIn("You: fix the login bug", hist)
        self.assertIn("Claude: Fixed it", hist)
        self.assertNotIn("file text", hist)        # tool results are left out
        self.assertNotIn("system-reminder", hist)

        self.say("/status")
        self.assertIn("In PC session: wb", self.api.last(CHAT_A))
        self.say("/leave")
        self.assertIn("Left wb", self.api.last(CHAT_A))
        self.say("/link personal")
        self.say("hello")
        self.wait()
        self.assertIn("resumed=False", self.api.last(CHAT_A))

    def test_enter_by_id_and_resume_alias_and_new_leaves(self):
        self.pc_setup()
        self.say("/enter bbbb")
        self.assertIn("write the README", self.api.last(CHAT_A))  # untitled: first prompt
        self.say("/resume aaaa1111")
        self.assertIn("Login bug fix", self.api.last(CHAT_A))
        self.say("/enter 99")
        self.assertIn("No session '99'", self.api.last(CHAT_A))
        self.say("/link work")
        self.say("/new")
        self.assertIsNone(self.bot.state.chat(CHAT_A).get("attached"))
        self.assertTrue(any("Left the PC session" in t for _, t in self.api.sent))

    def test_recent_session_warns_before_sending(self):
        self.pc_setup()
        self.make_transcript(self.cfg["accounts"]["work"]["config_dir"], "cccc3333-0000",
                             str(Path(self.tmp.name) / "wb"), "long job", "working…", age=5)
        self.say("/list")
        self.assertIn("🟢", self.api.last(CHAT_A))
        self.say("/enter 1")
        self.assertIn("active on the PC a moment ago", self.api.last(CHAT_A))

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

    def test_reply_to_pc_session_notification_continues_it(self):
        base = Path(self.tmp.name)
        botmod.hook_session_remember(base, CHAT_A, 77, {"session_id": "pc-sess",
                                                        "cwd": str(base / "wb"), "config_dir": None})
        self.say("yes, go ahead", reply_to=77)
        self.wait()
        last = self.api.last(CHAT_A)
        self.assertIn("--resume pc-sess", last)
        self.assertIn("Did: yes, go ahead", last)
        self.assertIn(f"cwd={base / 'wb'}", last)
        self.assertIn("Reply to this message to continue", last)
        self.assertIn("profile=None", last)  # the PC session's own (default) Claude profile

    def test_reply_command_and_unknown_reply(self):
        self.say("/reply hi")
        self.assertIn("No Claude Code session", self.api.last(CHAT_A))
        botmod.hook_session_remember(Path(self.tmp.name), CHAT_A, 5, {"session_id": "last-one",
                                                                      "cwd": str(Path(self.tmp.name) / "wa")})
        self.say("/reply continue please")
        self.wait()
        self.assertIn("--resume last-one", self.api.last(CHAT_A))
        self.say("/link work")
        self.say("normal message", reply_to=999)  # reply to an unrelated message: normal job
        self.wait()
        self.assertIn("resumed=False", self.api.last(CHAT_A))

    def test_bot_runs_are_marked_for_the_hook(self):
        _, env, _ = self.bot.build_command("work", None)
        self.assertEqual(env[botmod.BOT_ENV_FLAG], "1")

    def test_notify_command(self):
        self.say("/notify off")
        self.assertIn("OFF", self.api.last(CHAT_A))
        self.say("/notify")
        self.assertIn("OFF", self.api.last(CHAT_A))
        self.say("/notify on")
        self.assertIn("ON", self.api.last(CHAT_A))

    def test_outbox_files_are_sent_then_removed(self):
        self.say("/link work")  # links the chat, so it is a target
        outbox = Path(self.tmp.name) / "outbox"
        outbox.mkdir()
        (outbox / "a.txt").write_text("✅ Backup finished", encoding="utf-8")
        (outbox / "empty.txt").write_text("  ", encoding="utf-8")
        (outbox / "ignored.json").write_text("{}", encoding="utf-8")
        fresh = outbox / "fresh.txt"
        fresh.write_text("still being written", encoding="utf-8")
        now = time.time()
        for f in ("a.txt", "empty.txt"):
            os.utime(outbox / f, (now - 10, now - 10))
        self.assertEqual(self.bot.deliver_outbox(now=now), 1)
        self.assertEqual(self.api.last(CHAT_A), "✅ Backup finished")
        self.assertEqual(sorted(p.name for p in outbox.iterdir()), ["fresh.txt", "ignored.json"])

    def test_outbox_gives_up_after_failures(self):
        self.say("/link work")
        outbox = Path(self.tmp.name) / "outbox"
        outbox.mkdir()
        msg = outbox / "m.txt"
        msg.write_text("hi", encoding="utf-8")
        os.utime(msg, (time.time() - 10, time.time() - 10))
        self.api.send = lambda chat, text: []  # Telegram unreachable
        for _ in range(botmod.OUTBOX_MAX_TRIES):
            self.bot.deliver_outbox()
        self.assertTrue((outbox / "m.failed").exists())

    def test_long_message_split(self):
        chunks = botmod.split_message("x\n" * 5000)
        self.assertTrue(all(len(c) <= botmod.TG_LIMIT for c in chunks))
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
