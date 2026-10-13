"""Tests for setup_bot.py (the one-step setup that start.bat runs)."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import setup_bot  # noqa: E402

FAKE = str(ROOT / "tests" / "fake_claude.py")
GOOD = "987654321:" + "B" * 35
REJECTED = "123456789:" + "A" * 35


def http_error(code):
    return urllib.error.HTTPError("https://api.telegram.org", code, "err", {}, None)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        setup_bot.open_log(self.dir / "setup.log")
        self.out = io.StringIO()
        self._redirect = redirect_stdout(self.out)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)
        for h in list(setup_bot.log.handlers):
            h.close()
            setup_bot.log.removeHandler(h)
        self.tmp.cleanup()

    # ---- Claude Code

    def test_uses_installed_claude(self):
        with mock.patch.object(setup_bot.bot, "claude_version", return_value="9.9.9 (Claude Code)"):
            run = mock.Mock()
            cmd = setup_bot.ensure_claude(find=lambda: "C:/x/claude.exe", run=run)
        self.assertEqual(cmd, ["C:/x/claude.exe"])
        run.assert_not_called()

    def test_installs_when_missing_and_uses_full_path(self):
        found = iter([None, "C:/Users/me/.local/bin/claude.exe"])
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(setup_bot.bot, "claude_version", return_value="9.9.9 (Claude Code)"):
            cmd = setup_bot.ensure_claude(find=lambda: next(found), run=run,
                                          install_cmd=["installer"])
        run.assert_called_once_with(["installer"])
        self.assertEqual(cmd, ["C:/Users/me/.local/bin/claude.exe"])
        self.assertIn("Installed Claude Code", self.out.getvalue())

    def test_install_failure_is_reported_not_looped(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1))
        with self.assertRaises(setup_bot.SetupError) as ctx:
            setup_bot.ensure_claude(find=lambda: None, run=run, install_cmd=["installer"])
        self.assertEqual(run.call_count, 1)
        self.assertIn("exit code 1", str(ctx.exception))

    def test_default_installer_is_official(self):
        self.assertIn("https://claude.ai/install.ps1", " ".join(setup_bot.INSTALL_WINDOWS))
        self.assertIn("https://claude.ai/install.sh", " ".join(setup_bot.INSTALL_UNIX))

    # ---- sign-in

    def test_signs_in_when_needed(self):
        marker = self.dir / "logged-in"
        accounts = {"main": {"workdir": str(self.dir), "env": {"FAKE_LOGIN_MARKER": str(marker)}}}
        setup_bot.ensure_signed_in([sys.executable, FAKE], accounts)
        self.assertTrue(marker.exists())
        self.assertIn("'main': signed in", self.out.getvalue())

    def test_already_signed_in_skips_login(self):
        run = mock.Mock()
        setup_bot.ensure_signed_in([sys.executable, FAKE], {"main": {"workdir": str(self.dir)}},
                                   run=run)
        run.assert_not_called()

    def test_sign_in_failure_is_reported(self):
        marker = self.dir / "never"
        accounts = {"main": {"workdir": str(self.dir), "env": {"FAKE_LOGIN_MARKER": str(marker)}}}
        with self.assertRaises(setup_bot.SetupError):
            setup_bot.ensure_signed_in([sys.executable, FAKE], accounts, run=mock.Mock())

    # ---- Telegram

    def test_token_prompt_validates_and_retries(self):
        answers = iter(["nonsense", REJECTED, GOOD])

        def get_me(token):
            if token == GOOD:
                return {"username": "ranama_claude_bot"}
            raise http_error(401)

        cfg = {"telegram_bot_token": "PASTE-TOKEN-FROM-BOTFATHER"}
        me = setup_bot.ensure_token(cfg, ask=lambda prompt: next(answers), get_me=get_me)
        self.assertEqual(me["username"], "ranama_claude_bot")
        self.assertEqual(cfg["telegram_bot_token"], GOOD)
        out = self.out.getvalue()
        self.assertIn("doesn't look like a bot token", out)
        self.assertIn("didn't accept that token", out)
        self.assertNotIn(GOOD, out)
        self.assertNotIn(GOOD, (self.dir / "setup.log").read_text(encoding="utf-8"))

    def test_saved_valid_token_is_not_asked_again(self):
        ask = mock.Mock()
        cfg = {"telegram_bot_token": GOOD}
        me = setup_bot.ensure_token(cfg, ask=ask, get_me=lambda t: {"username": "b"})
        self.assertEqual(me["username"], "b")
        ask.assert_not_called()

    def test_quit_from_token_prompt(self):
        with self.assertRaises(setup_bot.SetupError):
            setup_bot.ensure_token({}, ask=lambda p: "q", get_me=lambda t: None)

    def test_offline_is_an_error_not_a_loop(self):
        def get_me(token):
            raise urllib.error.URLError("no network")
        with self.assertRaises(setup_bot.SetupError):
            setup_bot.ensure_token({"telegram_bot_token": GOOD}, ask=mock.Mock(), get_me=get_me)

    def test_users_placeholder_is_replaced(self):
        cfg = {"allowed_usernames": ["YourTelegramHandle"]}
        answers = iter(["@x", "@Ranama95"])
        setup_bot.ensure_users(cfg, ask=lambda p: next(answers))
        self.assertEqual(cfg["allowed_usernames"], ["Ranama95"])

    def test_existing_users_kept(self):
        cfg = {"allowed_usernames": ["Ranama95"]}
        setup_bot.ensure_users(cfg, ask=mock.Mock(side_effect=AssertionError("asked")))
        self.assertEqual(cfg["allowed_usernames"], ["Ranama95"])

    # ---- config files

    def test_config_created_from_example_and_saved(self):
        (self.dir / "config.example.json").write_text(json.dumps({"accounts": {"main": {}}}),
                                                      encoding="utf-8")
        path = self.dir / "config.json"
        cfg = setup_bot.load_or_create_config(path)
        cfg["telegram_bot_token"] = GOOD
        setup_bot.save_config(path, cfg)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["telegram_bot_token"], GOOD)

    def test_config_with_bom_and_bad_json(self):
        path = self.dir / "config.json"
        path.write_text(json.dumps({"accounts": {"main": {}}}), encoding="utf-8-sig")
        self.assertIn("main", setup_bot.load_or_create_config(path)["accounts"])
        path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(setup_bot.SetupError):
            setup_bot.load_or_create_config(path)

    # ---- whole flow, up to starting the bot

    def test_main_runs_all_steps_then_starts_bot(self):
        cfg_path = self.dir / "config.json"
        cfg_path.write_text(json.dumps({
            "telegram_bot_token": "PASTE-TOKEN-FROM-BOTFATHER",
            "allowed_usernames": ["Ranama95"],
            "claude_bin": "claude",
            "accounts": {"main": {"workdir": str(self.dir)}},
        }), encoding="utf-8")
        with mock.patch.object(setup_bot, "ensure_claude", return_value=["C:/c/claude.exe"]), \
                mock.patch.object(setup_bot, "ensure_signed_in"), \
                mock.patch.object(setup_bot.getpass, "getpass", return_value=GOOD), \
                mock.patch.object(setup_bot, "telegram_me", return_value={"username": "my_bot"}), \
                mock.patch.object(setup_bot.bot, "start", return_value=0) as start:
            code = setup_bot.main([str(cfg_path)])
        self.assertEqual(code, 0)
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["telegram_bot_token"], GOOD)
        self.assertEqual(saved["claude_bin"], "claude")          # config stays portable
        runtime_cfg = start.call_args[0][1]
        self.assertEqual(runtime_cfg["claude_bin"], "C:/c/claude.exe")  # bot gets the full path
        self.assertIn("https://t.me/my_bot", self.out.getvalue())

    def test_main_reports_setup_errors(self):
        cfg_path = self.dir / "config.json"
        cfg_path.write_text(json.dumps({"accounts": {"main": {}}}), encoding="utf-8")
        with mock.patch.object(setup_bot, "ensure_claude",
                               side_effect=setup_bot.SetupError("no installer")), \
                mock.patch.object(setup_bot.bot, "start") as start:
            code = setup_bot.main([str(cfg_path)])
        self.assertEqual(code, 1)
        start.assert_not_called()
        self.assertIn("[X] no installer", self.out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
