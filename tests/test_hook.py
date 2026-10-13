import io
import json
from unittest import mock

from helpers import CHAT, TOKEN, FakeTelegram, TempDirTest
import hook
import status


class HookTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.tg = FakeTelegram()
        self.cfg = {"telegram_bot_token": TOKEN, "chat_id": CHAT}
        (self.home / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "jeppe@example.com"}}), encoding="utf-8")

    def run_hook(self, name, cfg=None, **extra):
        event = dict({"hook_event_name": name, "session_id": "s1", "cwd": "/x/VineTrip"}, **extra)
        return hook.handle(event, base=self.dir, cfg=cfg or self.cfg, send=self.tg.send)

    def texts(self):
        return [m["text"] for m in self.tg.sent]

    def test_completed(self):
        self.run_hook("UserPromptSubmit", prompt="fix the login bug", session_title="Login bug fix")
        self.assertEqual(self.tg.sent, [])                    # starting sends nothing
        self.assertEqual(self.run_hook("Stop", last_assistant_message="Done. I fixed the bug and added a test."), 1)
        self.assertEqual(self.texts(), ["✅ Completed — VineTrip · “Login bug fix”\n👤 jeppe@example.com\n"
                                        "Done. I fixed the bug and added a test."])

    def test_needs_input_and_approval(self):
        self.run_hook("PermissionRequest", tool_name="AskUserQuestion", tool_input={"questions": [
            {"question": "Which DB?", "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]})
        self.run_hook("PermissionRequest", tool_name="Bash", tool_input={"command": "npm run deploy"})
        self.run_hook("Notification", notification_type="elicitation_dialog", message="Pick a server")
        self.assertEqual(self.texts(), [
            "❓ Needs input — VineTrip\n👤 jeppe@example.com\nClaude asks: Which DB? (Postgres, SQLite)",
            "🔐 Needs approval — VineTrip\n👤 jeppe@example.com\nClaude wants to run: npm run deploy",
            "❓ Needs input — VineTrip\n👤 jeppe@example.com\nPick a server"])

    def test_quiet_events(self):
        for kind in ("idle_prompt", "permission_prompt", "auth_success"):
            self.run_hook("Notification", notification_type=kind, message="x")
        self.run_hook("SessionEnd", reason="exit")
        self.assertEqual(self.tg.sent, [])

    def test_not_set_up_still_tracks_status(self):
        self.assertEqual(self.run_hook("UserPromptSubmit", cfg={"telegram_bot_token": ""}, prompt="x"), 0)
        self.assertTrue(status.lock_path(self.dir, "s1").exists())

    def test_main_never_fails_and_send_command(self):
        with mock.patch("sys.stdin", io.StringIO("not json")), mock.patch.object(hook, "HERE", self.dir):
            self.assertEqual(hook.main([]), 0)
        self.assertIn("hook failed", (self.dir / "hook.log").read_text(encoding="utf-8"))
        with mock.patch.object(hook.tg, "load_config", return_value=self.cfg), \
                mock.patch.object(hook.tg, "send", self.tg.send), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(hook.main(["send", "Backup", "finished"]), 0)
        self.assertEqual(self.texts(), ["Backup finished"])
