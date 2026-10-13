#!/usr/bin/env python3
"""
One-step setup and start for the Claude ⇄ Telegram bot (what start.bat runs).

    python setup_bot.py [config.json]

1. Finds Claude Code, or installs it with Anthropic's official installer.
2. Makes sure Claude Code is signed in (opens the browser once if it isn't).
3. Asks for the Telegram bot token once (hidden while you paste it), checks it with
   Telegram and saves it in config.json.
4. Starts the bot.

Every step is also written to setup.log next to config.json, so problems can be
diagnosed afterwards. The token itself is never written to the log.
"""
import getpass
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bot  # noqa: E402

PLACEHOLDER_TOKEN = "PASTE-TOKEN-FROM-BOTFATHER"
PLACEHOLDER_USER = "YourTelegramHandle"
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

INSTALL_WINDOWS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                   "[Net.ServicePointManager]::SecurityProtocol = "
                   "[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; "
                   "irm https://claude.ai/install.ps1 | iex"]
INSTALL_UNIX = ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]

log = logging.getLogger("claude-tg.setup")


class SetupError(Exception):
    pass


def say(msg=""):
    print(msg, flush=True)
    if msg:
        log.info(msg)


def open_log(path):
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


# ---------------------------------------------------------------- 1. Claude Code

def ensure_claude(find=None, install_cmd=None, run=None):
    """Return the command (as a list) that runs Claude Code, installing it if needed."""
    find = find or bot.find_claude
    run = run or subprocess.run
    path = find()
    version = bot.claude_version([path]) if path else None
    if version:
        say(f"    Found Claude Code {version}")
        log.info("claude path: %s", path)
        return [path]
    if path:
        log.warning("%s exists but is not Claude Code; installing the real one", path)

    install_cmd = install_cmd or (INSTALL_WINDOWS if bot.IS_WINDOWS else INSTALL_UNIX)
    say("    Claude Code isn't installed yet. Installing it now with Anthropic's official")
    say("    installer (https://claude.ai). This takes a minute or two and shows little")
    say("    progress - please wait.")
    log.info("running installer: %s", install_cmd)
    try:
        result = run(install_cmd)
    except OSError as e:
        raise SetupError(f"Could not start the Claude Code installer: {e}")
    log.info("installer exit code: %s", getattr(result, "returncode", "?"))

    path = find()
    version = bot.claude_version([path]) if path else None
    if not version:
        raise SetupError(
            "Claude Code still isn't available after the installer ran "
            f"(exit code {getattr(result, 'returncode', '?')}). Scroll up for the installer's "
            "message. You can also install it by hand: open PowerShell and run\n"
            "        irm https://claude.ai/install.ps1 | iex\n"
            "    and then double-click start.bat again.")
    say(f"    Installed Claude Code {version}")
    log.info("claude path: %s", path)
    return [path]


# ---------------------------------------------------------------- 2. sign-in

def _status_for_log(info):
    if isinstance(info, dict):
        return {k: info.get(k) for k in ("loggedIn", "authMethod", "apiProvider")}
    return info


def ensure_signed_in(claude_cmd, accounts, run=None):
    run = run or subprocess.run
    for name, acc in accounts.items():
        workdir = os.path.expanduser(acc.get("workdir", "."))
        if not os.path.isdir(workdir):
            say(f"    Warning: the folder for '{name}' doesn't exist: {workdir}")
        env = bot.account_env(acc)
        signed_in, info = bot.claude_auth_status(claude_cmd, env)
        log.info("auth status for %s: %s %s", name, signed_in, _status_for_log(info))
        if signed_in:
            say(f"    '{name}': signed in")
            continue
        say(f"    '{name}': Claude Code needs to sign in once.")
        say("    Your browser will open. Sign in with your Claude account and click")
        say("    'Authorize', then come back to this window.")
        try:
            run(claude_cmd + ["auth", "login"], env=env)
        except OSError as e:
            raise SetupError(f"Could not start the sign-in: {e}")
        signed_in, info = bot.claude_auth_status(claude_cmd, env)
        log.info("auth status for %s after login: %s", name, signed_in)
        if not signed_in:
            raise SetupError(
                f"Claude Code is still not signed in for '{name}'. Double-click start.bat to "
                "try again, or open PowerShell and run: claude auth login")
        say(f"    '{name}': signed in")


# ---------------------------------------------------------------- 3. Telegram

def telegram_me(token):
    return bot.TelegramAPI(token, poll_timeout=10).call("getMe")


def check_token(token, get_me=None):
    """Return the bot's getMe info, None if Telegram rejects the token, raise if offline."""
    get_me = get_me or telegram_me
    try:
        return get_me(token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return None
        raise SetupError(f"Telegram answered with an error ({e}). Try again in a minute.")
    except (urllib.error.URLError, OSError) as e:
        raise SetupError(f"Couldn't reach Telegram ({e}). Check the internet connection, "
                         "then double-click start.bat again.")


def ensure_token(cfg, ask=None, get_me=None):
    ask = ask or getpass.getpass
    token = (cfg.get("telegram_bot_token") or "").strip()
    if token and PLACEHOLDER_TOKEN not in token:
        me = check_token(token, get_me)
        if me:
            say(f"    Bot token OK: @{me.get('username')}")
            return me
        say("    Telegram no longer accepts the saved token. Let's enter a new one.")

    say("    The bot needs its own Telegram bot token (you only do this once):")
    say("      1. In Telegram, open a chat with @BotFather and send:  /newbot")
    say("      2. Pick a name, then a username ending in 'bot' (e.g. ranama_claude_bot).")
    say("      3. BotFather replies with a token like 123456789:AAH...  - copy it.")
    say("      4. Paste it below (right-click or Ctrl+V) and press Enter.")
    say("         It stays hidden while you paste - that's normal.")
    while True:
        token = (ask("    Bot token (or q to quit): ") or "").strip()
        if token.lower() in ("q", "quit", "exit"):
            raise SetupError("Setup cancelled.")
        if not TOKEN_RE.match(token):
            say("    That doesn't look like a bot token (it looks like 123456789:AAH...). Try again.")
            continue
        me = check_token(token, get_me)
        if not me:
            say("    Telegram didn't accept that token. Copy it again from @BotFather and retry.")
            continue
        cfg["telegram_bot_token"] = token
        say(f"    Bot token OK: @{me.get('username')}")
        return me


def ensure_users(cfg, ask=None):
    ask = ask or input
    names = [u for u in cfg.get("allowed_usernames", []) if u and u != PLACEHOLDER_USER]
    if names or cfg.get("allowed_user_ids"):
        cfg["allowed_usernames"] = names
        who = ", ".join("@" + n.lstrip("@") for n in names) or "the user ids in config.json"
        say(f"    Only {who} can use the bot.")
        return
    while True:
        handle = (ask("    Your Telegram username (e.g. @Ranama95): ") or "").strip().lstrip("@")
        if USERNAME_RE.match(handle):
            cfg["allowed_usernames"] = [handle]
            say(f"    Only @{handle} can use the bot.")
            return
        say("    Telegram usernames are 5-32 letters, digits or _. Try again.")


def save_config(path, cfg):
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_or_create_config(path):
    path = Path(path)
    if not path.exists():
        example = path.parent / "config.example.json"
        if not example.exists():
            raise SetupError(f"{path.name} and config.example.json are both missing.")
        shutil.copyfile(example, path)
        log.info("created %s from config.example.json", path)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise SetupError(f"{path.name} is not valid JSON ({e}). Fix it or delete it and run again.")
    if not cfg.get("accounts"):
        raise SetupError(f"{path.name} has no accounts.")
    return cfg


# ---------------------------------------------------------------- main

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cfg_path = Path(argv[0] if argv else HERE / "config.json").resolve()
    log_path = cfg_path.parent / "setup.log"
    open_log(log_path)
    log.info("==== setup start (Python %s on %s) ====", sys.version.split()[0], sys.platform)

    say("Claude <-> Telegram bot")
    say("=======================")
    try:
        cfg = load_or_create_config(cfg_path)
        say("[1/4] Claude Code")
        claude_cmd = ensure_claude()
        say("[2/4] Claude sign-in")
        ensure_signed_in(claude_cmd, cfg["accounts"])
        say("[3/4] Telegram")
        me = ensure_token(cfg)
        ensure_users(cfg)
        save_config(cfg_path, cfg)
    except SetupError as e:
        say("")
        say(f"[X] {e}")
        say(f"    (Details: {log_path})")
        return 1
    except (KeyboardInterrupt, EOFError):
        say("\nSetup cancelled.")
        return 130

    username = me.get("username")
    say("[4/4] Starting the bot")
    say("")
    say(f"    Your bot: https://t.me/{username}")
    say(f"    Open @{username} in Telegram and send it a message, e.g.: hello")
    say("    Keep this window open - closing it stops the bot.")
    say("")
    runtime_cfg = dict(cfg)
    if runtime_cfg.get("claude_bin", "claude") == "claude":
        runtime_cfg["claude_bin"] = claude_cmd[0]  # works even before PATH is refreshed
    return bot.start(cfg_path, runtime_cfg)


if __name__ == "__main__":
    sys.exit(main())
