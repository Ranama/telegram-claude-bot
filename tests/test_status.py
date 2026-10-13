import json
import os
import time

from helpers import TempDirTest
import status


class StatusTest(TempDirTest):
    def ev(self, name, **extra):
        return dict({"hook_event_name": name, "session_id": "s1", "cwd": "D:\\Claude\\VineTrip",
                     "transcript_path": str(self.transcript)}, **extra)

    def setUp(self):
        super().setUp()
        self.transcript = self.dir / "s1.jsonl"
        self.transcript.write_text("\n".join(json.dumps(x) for x in [
            {"type": "user", "message": {"content": "fix the login bug"}},
            {"type": "ai-title", "aiTitle": "Login bug fix"}]) + "\n", encoding="utf-8")
        (self.home / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "jeppe@example.com"}}), encoding="utf-8")

    def state(self, now=None):
        rec = status.read_json(status.record_path(self.dir, "s1"))
        return status.derive_state(self.dir, rec, now)[0]

    def test_lock_lifecycle(self):
        t = time.time()
        rec = status.record_event(self.dir, self.ev("UserPromptSubmit", prompt="fix it"), now=t)
        self.assertTrue(status.lock_path(self.dir, "s1").exists())
        self.assertEqual(self.state(t + 1), "working")
        self.assertEqual((rec["account"], rec["project"], rec["title"]),
                         ("jeppe@example.com", "VineTrip", "Login bug fix"))

        rec = status.record_event(self.dir, self.ev("PermissionRequest", tool_name="AskUserQuestion",
            tool_input={"questions": [{"question": "Which DB?", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}), now=t + 2)
        self.assertEqual(rec["detail"], "Claude asks: Which DB? (Postgres, SQLite)")
        os.utime(self.transcript, (t, t))
        self.assertEqual(self.state(t + 3), "needs_input")
        os.utime(self.transcript, (t + 10, t + 10))          # answered on the PC: Claude goes on
        self.assertEqual(self.state(t + 11), "working")

        status.record_event(self.dir, self.ev("PermissionRequest", tool_name="Bash",
                                              tool_input={"command": "npm test"}), now=t + 20)
        self.assertEqual(self.state(t + 20.5), "needs_approval")

        rec = status.record_event(self.dir, self.ev("Stop", last_assistant_message="Done. Fixed it and added tests for it."), now=t + 30)
        self.assertFalse(status.lock_path(self.dir, "s1").exists())
        self.assertEqual(self.state(t + 31), "completed")
        self.assertEqual(rec["summary"], "Done. Fixed it and added tests for it.")

        status.record_event(self.dir, self.ev("SessionEnd", reason="exit"), now=t + 40)
        self.assertEqual(self.state(t + 41), "ended")

    def test_late_prompt_hook_does_not_relock_a_finished_command(self):
        t = time.time()
        status.record_event(self.dir, self.ev("Stop", last_assistant_message="ok"), now=t + 5)
        status.record_event(self.dir, self.ev("UserPromptSubmit", prompt="x"), now=t)  # ran late
        self.assertFalse(status.lock_path(self.dir, "s1").exists())
        self.assertEqual(self.state(t + 6), "completed")

    def test_sessions_that_report_themselves(self):
        """Cloud sessions have no hooks: they write status/sessions/<id>.json themselves."""
        folder = self.dir / "status" / "sessions"
        folder.mkdir(parents=True)
        def report(name, age=0, **rec):
            path = folder / f"{name}.json"
            path.write_text(json.dumps(rec), encoding="utf-8")
            os.utime(path, (time.time() - age, time.time() - age))
        report("cloud-a", session_id="cloud-a", where="cloud", title="Telegram notifier",
               project="telegram-claude-bot", account="me@x.com", state="working",
               task="Make /status list running sessions")
        report("cloud-b", where="cloud", title="Old chat", state="working", task="x", age=25 * 60)
        report("cloud-c", title="Shop", state="done", summary="Deployed.", updated=time.time() * 1000)
        report("cloud-d", title="Question", state="needs_input", detail="Which colour?", age=3 * 86400)
        (folder / "broken.json").write_text("[1, 2]", encoding="utf-8")
        (folder / "half.json").write_text('{"state": "work', encoding="utf-8")
        report("bad-time", title="Bad", state="working", updated="soon")
        items = {i["session_id"]: i for i in status.current(self.dir)}
        self.assertEqual({k: i["state"] for k, i in items.items()},
                         {"cloud-a": "working", "cloud-b": "stalled", "cloud-c": "completed",
                          "cloud-d": "needs_input"})
        text = status.status_text(list(items.values()))
        self.assertIn("🟡 <b>Telegram notifier</b>\nWorking on: Make /status list running sessions\n"
                      "<i>📁 telegram-claude-bot · ☁️ cloud · just now</i>", text)
        self.assertIn("🟠 <b>Old chat</b>", text)
        self.assertIn("🟢 <b>Shop</b>\nDone: Deployed.\n<i>just now</i>", text)  # ms → file time
        self.assertIn("🔴 <b>Question</b>\nNeeds your input: Which colour?\n<i>3 d ago</i>", text)
        self.assertIn("👤 me@x.com", text)

    def test_leftover_lock_is_cleaned_and_stalled_lock_shown(self):
        t = time.time()
        status.record_event(self.dir, self.ev("Stop", last_assistant_message="ok"), now=t)
        status.write_json(status.lock_path(self.dir, "s1"), {"started": t - 100})
        self.assertEqual(self.state(t), "completed")
        self.assertFalse(status.lock_path(self.dir, "s1").exists())
        status.record_event(self.dir, self.ev("UserPromptSubmit", prompt="long"), now=t + 1)
        os.utime(self.transcript, (t, t))
        self.assertEqual(self.state(t + 1 + status.STALLED_SECONDS + 5), "stalled")

    def test_irrelevant_notifications_change_nothing(self):
        t = time.time()
        status.record_event(self.dir, self.ev("Stop", last_assistant_message="ok"), now=t)
        status.record_event(self.dir, self.ev("Notification", notification_type="idle_prompt"), now=t + 60)
        self.assertEqual(self.state(t + 61), "completed")
        rec = status.record_event(self.dir, self.ev("Notification", notification_type="elicitation_dialog",
                                                    message="Pick a server"), now=t + 70)
        self.assertEqual((rec["state"], rec["detail"]), ("needs_input", "Pick a server"))

    def test_current_and_status_text(self):
        t = time.time()
        status.record_event(self.dir, self.ev("Stop", last_assistant_message="All done here."), now=t)
        status.record_event(self.dir, dict(self.ev("UserPromptSubmit", prompt="deploy"), session_id="s2",
                                           cwd="/x/shop", transcript_path=""), now=t)
        status.record_event(self.dir, dict(self.ev("Stop"), session_id="old", cwd="/x/old",
                                           transcript_path=""), now=t - 3 * 86400)
        items = status.current(self.dir, now=t + 5)
        self.assertEqual([i["state"] for i in items], ["working", "completed"])   # old one hidden
        text = status.status_text(items, now=t)
        self.assertTrue(text.startswith("📋 <b>Claude sessions</b>\n👤 jeppe@example.com\n"
                                        "🟡 1 working · 🟢 1 done\n"), text)
        self.assertIn("🟡 <b>Deploy</b>\nWorking on it…\n<i>📁 shop · just now</i>", text)
        self.assertIn("🟢 <b>Login bug fix</b>\nDone: All done here.\n<i>📁 VineTrip · just now</i>", text)
        self.assertIn("🕒 Updated ", text)
        self.assertIn("No sessions in the last 24 hours.", status.status_text([]))

    def test_status_text_colours_wording_and_escaping(self):
        def item(state, **extra):
            return dict({"state": state, "title": "T", "task": "", "detail": "", "summary": "",
                         "project": "P", "account": "a@x.com", "ago": "just now"}, **extra)
        items = [item("needs_input", detail="Claude asks: Which DB? (Postgres, SQLite)"),
                 item("needs_approval", detail="Claude wants to run: npm test && deploy"),
                 item("needs_approval", detail="Claude wants you to approve its plan"),
                 item("working", title="Fix <login>", task="can you please add tests for login.py?"),
                 item("stalled"), item("completed", summary="Done. I fixed it.", account="b@x.com"),
                 item("ended")]
        text = status.status_text(items)
        self.assertIn("🔴 3 need you · 🟡 1 working · 🟠 1 inactive · 🟢 1 done · ⚪ 1 closed", text)
        for line in ["🔴 <b>T</b>\nAsks you: Which DB? (Postgres, SQLite)",
                     "Needs your approval to run: npm test &amp;&amp; deploy",
                     "Needs you to approve its plan",
                     "🟡 <b>Fix &lt;login&gt;</b>\nWorking on: Add tests for login.py",
                     "🟠 <b>T</b>\nNo activity for 20+ min",
                     "🟢 <b>T</b>\nDone: I fixed it.\n<i>📁 P · 👤 b@x.com · just now</i>",
                     "⚪ <b>T</b>\nSession closed"]:
            self.assertIn(line, text)
        self.assertNotIn("\n👤 ", text)                       # two accounts: shown per session
        self.assertIn("🔴 1 needs you", status.status_text(items[:1]))

    def test_readable_title(self):
        for prompt, title in [
                ("fix the login bug", "Fix the login bug"),
                ("Can you please fix the bug in D:\\Claude\\VineTrip\\login.py? It crashes", "Fix the bug in login.py"),
                ("Look at @src/app/main.py and https://www.github.com/Ranama/x/issues/3", "Look at main.py and github.com"),
                ('open "C:\\Program Files\\My App\\settings.ini" and change the port', "Open settings.ini and change the port"),
                ("Hi!\nCan you check why the bot doesn't start?", "Check why the bot doesn't start"),
                ("Fix these issues:\n1. login crashes\n2. logout fails", "Fix these issues"),
                ("Push it to my repository: https://github.com/Ranama/x", "Push it to my repository"),
                ("```\nprint(1)\n```\nexplain this code", "Explain this code"),
                ("<command-name>/clear</command-name>", "/clear"),
                ("keep my_var as is, deploy on 1/2/2026", "Keep my_var as is, deploy on 1/2/2026"),
                ("iOS build fails", "iOS build fails"), ("Note: it's down", "Note: it's down"),
                ("my handle is @Ranama95", "My handle is @Ranama95"), ("Hi Claude!", "Hi Claude!"),
                ("", None), (None, None)]:
            self.assertEqual(status.readable_title(prompt), title, prompt)
        long = status.readable_title("word " * 40)
        self.assertTrue(long.endswith("…") and len(long) <= 50, long)
        self.assertEqual(status.readable_title(long), long)

    def test_names(self):
        prof = self.dir / "work-profile"
        prof.mkdir()
        self.assertEqual(status.account_label(str(prof)), "work-profile")
        (prof / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "w@x.com"}}), encoding="utf-8")
        self.assertEqual(status.account_label(str(prof)), "w@x.com")
        t = self.dir / "t.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in [
            {"type": "user", "isMeta": True, "message": {"content": "meta"}},
            {"type": "user", "message": {"content": "first prompt here"}},
            {"type": "ai-title", "aiTitle": "AI title"},
            {"type": "custom-title", "customTitle": "My title"}]), encoding="utf-8")
        self.assertEqual(status.session_title(str(t)), "My title")
        t.write_text(json.dumps({"type": "user", "message": {"content": "first prompt here"}}), encoding="utf-8")
        self.assertEqual(status.session_title(str(t)), "First prompt here")
        t.write_text(json.dumps({"type": "user", "message": {"content": [
            {"type": "image"}, {"type": "text", "text": "what is in D:/a/b/shot.png?"}]}}), encoding="utf-8")
        self.assertEqual(status.session_title(str(t)), "What is in shot.png?")
        self.assertIsNone(status.session_title(str(self.dir / "missing.jsonl")))
        self.assertEqual(status.folder_name("D:\\Claude\\VineTrip\\"), "VineTrip")

    def test_short_summary(self):
        self.assertEqual(status.short_summary("**Done.** I added the login page and wrote tests for it. More.\n- a"),
                         "Done. I added the login page and wrote tests for it.")
        self.assertEqual(status.short_summary("```\ncode\n```\nIt works now, and every test passes on Windows too."),
                         "It works now, and every test passes on Windows too.")
        self.assertTrue(status.short_summary("word " * 200).endswith("…"))
