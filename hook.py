#!/usr/bin/env python3
"""
Claude Code hook: tells you on Telegram when a Claude Code session on this computer
is done or needs you, and keeps the lock and status files that /status reads.

  🟡 Started         UserPromptSubmit   "Fix the login bug"
  ✅ Completed       Stop               "Done. I added the login page and its tests."
  ❓ Needs input     a question         "Claude asks: Which database? (Postgres, SQLite)"
  🔐 Needs approval  a permission       "Claude wants to run: npm test"

Each message says which session (folder and title) and which Claude account.
install.py adds this script to Claude Code's settings for these events:
UserPromptSubmit, PermissionRequest, Notification, Stop, SessionEnd.

Also:  python hook.py send "text"   sends you a message yourself.
Problems are written to hook.log; the hook never interrupts Claude.
"""
import json
import logging
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import status  # noqa: E402
import tg  # noqa: E402

log = logging.getLogger("claude-telegram.hook")


def message_for(event, rec):
    """The Telegram message for this event, or None if there's nothing to tell."""
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        if rec.get("state") != "working" or rec.get("started") != rec.get("updated"):
            return None                              # ran late, after the task had finished
        emoji, label, text = "🟡", "Started", rec.get("task")
    elif name == "Stop":
        state, text = "completed", rec.get("summary")
    elif name == "PermissionRequest" or (name == "Notification" and rec.get("asked_at") == rec.get("updated")):
        state, text = rec.get("state"), rec.get("detail")
        if state not in ("needs_input", "needs_approval"):
            return None
    else:
        return None
    if name != "UserPromptSubmit":
        emoji, label, _ = status.STATES[state]
    where = rec.get("project") or "?"
    if rec.get("title"):
        where += f" · “{rec['title']}”"
    stamp = time.strftime("%d %b %H:%M:%S", time.localtime(rec.get("updated") or time.time()))
    return f"{emoji} {label} — {where}\n👤 {rec.get('account')}\n{text or ''}".rstrip() + f"\n🕒 {stamp}"


def handle(event, base=HERE, cfg=None, send=tg.send):
    """Record the event and send its message. Returns the number of messages sent."""
    rec = status.record_event(base, event)
    if not rec:
        return 0
    text = message_for(event, rec)
    cfg = cfg or tg.load_config(Path(base) / "config.json")
    if not text or not cfg.get("telegram_bot_token") or not cfg.get("chat_id"):
        return 0
    return len(send(cfg, text) or [])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["send"]:
        text = " ".join(argv[1:]) or sys.stdin.read()
        stamp = time.strftime("%d %b %H:%M:%S")
        sent = tg.send(tg.load_config(), f"{text.strip()}\n🕒 {stamp}") if text.strip() else []
        print(f"sent {len(sent)} message(s)")
        return 0 if sent else 1
    handler = logging.FileHandler(HERE / "hook.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        handle(event)
    except Exception:
        log.exception("hook failed")
    finally:
        log.removeHandler(handler)
        handler.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
