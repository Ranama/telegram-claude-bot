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
        text = status.status_text(items)
        self.assertIn("📋 Claude sessions — 🟡 1  ✅ 1", text)
        self.assertIn("✅ Completed — VineTrip · “Login bug fix”", text)
        self.assertIn("👤 jeppe@example.com", text)
        self.assertIn("All done here.", text)
        self.assertEqual(status.status_text([]), "📋 No Claude Code sessions in the last 24 hours.")

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
        self.assertEqual(status.session_title(str(t)), "first prompt here")
        self.assertIsNone(status.session_title(str(self.dir / "missing.jsonl")))
        self.assertEqual(status.folder_name("D:\\Claude\\VineTrip\\"), "VineTrip")

    def test_short_summary(self):
        self.assertEqual(status.short_summary("**Done.** I added the login page and wrote tests for it. More.\n- a"),
                         "Done. I added the login page and wrote tests for it.")
        self.assertEqual(status.short_summary("```\ncode\n```\nIt works now, and every test passes on Windows too."),
                         "It works now, and every test passes on Windows too.")
        self.assertTrue(status.short_summary("word " * 200).endswith("…"))
