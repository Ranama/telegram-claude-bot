#!/usr/bin/env python3
"""
Set up Telegram notifications for every Claude Code session on this computer.

  python install.py                    first time: asks for the bot token, links your chat,
                                       adds the hooks, sends a test message
  python install.py --token TOKEN      same, with the token given (no prompt)
  python install.py --profile DIR      also for another Claude account (its CLAUDE_CONFIG_DIR)
  python install.py --if-needed        quick re-check (start.bat runs this)
  python install.py --uninstall        remove the hooks again

The hooks go into Claude Code's settings.json (a backup is kept next to it), so all
sessions — terminal, desktop app (Code tab, Local) — use them automatically.
"""
import argparse
import getpass
import json
import os
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tg  # noqa: E402

HOOK = HERE / "hook.py"
WINDOWS = os.name == "nt"
# event: (matcher, run in the background, timeout)
EVENTS = {
    "UserPromptSubmit": (None, True, 30),
    "PermissionRequest": (None, True, 30),
    "Notification": ("elicitation_dialog|agent_needs_input", True, 30),
    "Stop": (None, False, 30),
    "SessionEnd": (None, False, 15),
}
OLD_SCRIPTS = ("hook.py", "notify.py")  # earlier versions of this repo used notify.py


class Problem(Exception):
    pass


def say(text=""):
    print(text, flush=True)


# ---------------------------------------------------------------- hooks in settings.json

def hook_command(python=None, script=HOOK, windows=WINDOWS):
    python = Path(python or sys.executable)
    if windows:  # PowerShell runs it and passes stdin through; console python.exe, not pythonw
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        q = lambda p: "'" + str(p).replace("'", "''") + "'"  # noqa: E731
        return {"type": "command", "shell": "powershell", "command": f"& {q(python)} {q(script)}"}
    return {"type": "command", "command": f"{shlex.quote(str(python))} {shlex.quote(str(script))}"}


def is_ours(handler):
    command = str(handler.get("command", "")).replace("\\", "/")
    return any(name in command for name in OLD_SCRIPTS) and (
        "telegram-claude-bot" in command or str(HERE).replace("\\", "/") in command)


def update_settings(path, install=True, command=None):
    """Add (or remove) our hooks in a Claude Code settings.json, keeping everything else."""
    path = Path(path)
    settings = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8-sig")
        try:
            settings = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            raise Problem(f"{path} isn't valid JSON ({e}); not changing it.")
        backup = path.with_name("settings.json.before-telegram")
        if not backup.exists():
            backup.write_text(raw, encoding="utf-8")
    hooks = settings.setdefault("hooks", {})
    for event in set(hooks) | set(EVENTS):
        groups = []
        for group in hooks.get(event) or []:
            kept = [h for h in group.get("hooks", []) if not is_ours(h)]
            if kept:
                groups.append(dict(group, hooks=kept))
        if install and event in EVENTS:
            matcher, background, timeout = EVENTS[event]
            handler = dict(command or hook_command(), timeout=timeout)
            if background:
                handler["async"] = True
            groups.append({"matcher": matcher, "hooks": [handler]} if matcher else {"hooks": [handler]})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def profiles(cfg, extra=()):
    """Claude profile folders to install into: the default one plus any added."""
    default = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    seen, out = set(), []
    for p in [default] + list(cfg.get("profiles", [])) + list(extra):
        key = os.path.normcase(os.path.abspath(os.path.expanduser(p)))
        if key not in seen:
            seen.add(key)
            out.append(os.path.expanduser(p))
    return out


# ---------------------------------------------------------------- token and chat

def migrate_old_config(cfg):
    """Earlier versions of this repo: keep the token, find your chat in state.json."""
    if not cfg.get("chat_id"):
        try:
            state = json.loads((HERE / "state.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            state = {}
        cfg = dict(cfg, chat_id=_single_chat(state))
    return {k: cfg[k] for k in ("telegram_bot_token", "chat_id", "profiles") if cfg.get(k)}


def _single_chat(state):
    chats = [int(c) for c, info in (state.get("chats") or {}).items() if info.get("account")]
    chats = chats or [int(u) for u in (state.get("pinned_users") or {}).values()]
    return chats[0] if len(set(chats)) == 1 else None


def get_token(cfg, given=None, interactive=True):
    token = (given or cfg.get("telegram_bot_token") or "").strip()
    if not token or "PASTE" in token:
        if not interactive:
            raise Problem("No bot token yet. Create a bot in Telegram (@BotFather → /newbot), "
                          "then run: python install.py --token <token>")
        say("Create a bot in Telegram: open @BotFather, send /newbot, pick a name and a username")
        say("ending in 'bot'. BotFather replies with a token like 123456789:AAH...")
        token = getpass.getpass("Paste the token here (it stays hidden) and press Enter: ").strip()
    try:
        me = tg.call(token, "getMe")
    except Exception as e:
        raise Problem(f"Telegram didn't accept that token ({e}).")
    return token, me


def link_chat(token, bot_name, wait=180):
    """Wait for the user to message the bot; that chat gets the notifications."""
    say(f"Now open https://t.me/{bot_name} in Telegram and send it any message (e.g. hi).")
    say(f"Waiting up to {wait // 60} minutes…")
    try:
        old = tg.call(token, "getUpdates", offset=-1)
    except Exception as e:
        raise Problem(f"Couldn't read the bot's messages ({e}). If bot.py is running, close it first.")
    offset = old[-1]["update_id"] + 1 if old else 0
    deadline = time.time() + wait
    while time.time() < deadline:
        updates = tg.call(token, "getUpdates", http_timeout=40, offset=offset, timeout=25)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if (msg.get("chat") or {}).get("type") == "private":
                tg.call(token, "getUpdates", offset=offset)  # mark as read
                sender = msg.get("from") or {}
                return msg["chat"]["id"], sender.get("username") or sender.get("first_name")
    raise Problem("No message arrived. Run python install.py again and message the bot.")


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(description="Telegram notifications for Claude Code sessions")
    p.add_argument("--token", help="bot token from @BotFather")
    p.add_argument("--chat-id", type=int, help="Telegram chat to notify (normally found automatically)")
    p.add_argument("--profile", action="append", default=[], help="another Claude profile folder")
    p.add_argument("--if-needed", action="store_true", help="only set up what's missing")
    p.add_argument("--uninstall", action="store_true", help="remove the hooks")
    args = p.parse_args(argv)

    raw = tg.load_config()
    cfg = migrate_old_config(raw)
    migrated = bool(cfg.get("chat_id")) and not raw.get("chat_id")  # from an earlier version
    try:
        if args.uninstall:
            for folder in profiles(cfg, args.profile):
                settings = Path(folder) / "settings.json"
                if settings.exists():
                    update_settings(settings, install=False)
                    say(f"Removed the hooks from {settings}")
            return 0
        token, me = get_token(cfg, args.token, interactive=sys.stdin.isatty())
        cfg["telegram_bot_token"] = token
        new_chat = migrated
        if args.chat_id:
            cfg["chat_id"], new_chat = args.chat_id, True
        elif not cfg.get("chat_id"):
            chat_id, who = link_chat(token, me["username"])
            cfg["chat_id"], new_chat = chat_id, True
            say(f"Linked to the Telegram chat of {who}.")
        if args.profile:
            cfg["profiles"] = sorted(set(cfg.get("profiles", [])) | set(args.profile))
        tg.save_config(cfg)
        for folder in profiles(cfg):
            update_settings(Path(folder) / "settings.json")
            if not args.if_needed:
                say(f"Hooks installed in {Path(folder) / 'settings.json'}")
        if new_chat or not args.if_needed:
            tg.send(cfg, "✅ Claude notifications are set up. You'll get a message here when a "
                         "Claude Code session on your computer is done or needs you. /status lists "
                         "all sessions (while bot.py runs).")
            say("Test message sent. All Claude Code sessions on this computer now notify you.")
    except Problem as e:
        say(f"[X] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
