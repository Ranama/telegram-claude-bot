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


# ---------------------------------------------------------------- 4. notifications (hooks)

NOTIFY_SCRIPT = HERE / "notify.py"
NOTIFY_MATCHER = "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog"
HOOK_EVENTS = ("Stop", "Notification", "PermissionRequest")


def hook_handler(python=None, script=NOTIFY_SCRIPT, windows=None, timeout=20):
    """The hook entry that runs notify.py.

    On Windows it runs through PowerShell with the console python.exe: PowerShell waits
    for console programs and passes stdin/stdout through, which PermissionRequest needs
    (pythonw.exe is a GUI program, so PowerShell would not wait for its answer)."""
    windows = bot.IS_WINDOWS if windows is None else windows
    python = Path(python or sys.executable)
    if windows:
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        quote = lambda p: "'" + str(p).replace("'", "''") + "'"  # noqa: E731
        return {"type": "command", "shell": "powershell", "timeout": timeout,
                "command": f"& {quote(python)} {quote(script)}"}
    import shlex
    return {"type": "command", "timeout": timeout,
            "command": f"{shlex.quote(str(python))} {shlex.quote(str(script))}"}


def _is_ours(handler):
    """A hook this bot installed (also from an older copy of the folder)."""
    command = str(handler.get("command", "")).replace("\\", "/")
    return "notify.py" in command and (
        "telegram-claude-bot" in command or str(NOTIFY_SCRIPT).replace("\\", "/") in command)


def _without_ours(groups):
    kept = []
    for group in groups or []:
        hooks = [h for h in group.get("hooks", []) if not _is_ours(h)]
        if hooks:
            kept.append(dict(group, hooks=hooks))
    return kept


def settings_files(accounts):
    """Claude Code settings.json for the default profile and every account's profile."""
    dirs = [Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")]
    for acc in accounts.values():
        if acc.get("config_dir"):
            dirs.append(Path(os.path.expanduser(acc["config_dir"])))
    seen, files = set(), []
    for d in dirs:
        key = os.path.normcase(str(d.resolve()))
        if key not in seen:
            seen.add(key)
            files.append(d / "settings.json")
    return files


def update_hooks(settings_path, handler=None, permission_timeout=900):
    """Add (handler given) or remove (handler None) our hooks. Keeps other settings/hooks."""
    settings_path = Path(settings_path)
    settings = {}
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8-sig")
        try:
            settings = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            raise SetupError(f"{settings_path} isn't valid JSON ({e}); not changing it.")
        backup = settings_path.with_name("settings.json.before-telegram-bot")
        if not backup.exists():
            backup.write_text(raw, encoding="utf-8")
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = _without_ours(hooks.get(event))
        if handler:
            group = {"hooks": [handler]}
            if event == "Notification":
                group = {"matcher": NOTIFY_MATCHER, "hooks": [handler]}
            elif event == "PermissionRequest":  # waits for your tap: needs a long timeout
                group = {"hooks": [dict(handler, timeout=permission_timeout)]}
            groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)


def ensure_notifications(cfg, ask=None):
    """Ask once whether PC sessions should message you; install/refresh the hooks."""
    ask = ask or input
    if cfg.get("notify_hooks") is None:
        say("    Should Claude Code sessions you run on this PC (desktop app or terminal)")
        say("    message you on Telegram when they finish or wait for you? You can reply")
        say("    to those messages to continue the session.")
        answer = (ask("    Turn this on? [Y/n]: ") or "y").strip().lower()
        cfg["notify_hooks"] = not answer.startswith("n")
    files = settings_files(cfg["accounts"])
    handler = hook_handler() if cfg["notify_hooks"] else None
    wait = int(cfg.get("answer_timeout_seconds", 600))
    for path in files:
        try:
            update_hooks(path, handler, permission_timeout=wait + 120)
        except (SetupError, OSError) as e:
            say(f"    Couldn't update {path}: {e}")
            continue
        log.info("hooks %s in %s", "installed" if handler else "removed", path)
    if handler:
        say("    On: finished or waiting sessions will message you. (/notify off to pause)")
        say("    Tasks you start from Telegram ask for permissions there (buttons).")
        say("    Send /away on in Telegram to answer your PC sessions' questions there too.")
        say("    Sessions that are already open pick this up after a restart of the app.")
    else:
        say("    Off. (Set \"notify_hooks\": true in config.json and run start.bat to turn on.)")


# ---------------------------------------------------------------- main

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--remove-hooks":
        cfg = load_or_create_config(HERE / "config.json")
        for path in settings_files(cfg["accounts"]):
            if path.exists():
                update_hooks(path, None)
                print(f"Removed the Telegram hooks from {path}")
        cfg["notify_hooks"] = False
        save_config(HERE / "config.json", cfg)
        return 0
    cfg_path = Path(argv[0] if argv else HERE / "config.json").resolve()
    log_path = cfg_path.parent / "setup.log"
    open_log(log_path)
    log.info("==== setup start (Python %s on %s) ====", sys.version.split()[0], sys.platform)

    say("Claude <-> Telegram bot")
    say("=======================")
    try:
        cfg = load_or_create_config(cfg_path)
        say("[1/5] Claude Code")
        claude_cmd = ensure_claude()
        say("[2/5] Claude sign-in")
        ensure_signed_in(claude_cmd, cfg["accounts"])
        say("[3/5] Telegram")
        me = ensure_token(cfg)
        ensure_users(cfg)
        save_config(cfg_path, cfg)
        say("[4/5] Notifications from your Claude Code sessions")
        ensure_notifications(cfg)
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
    say("[5/5] Starting the bot")
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
