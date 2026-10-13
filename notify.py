#!/usr/bin/env python3
"""
Claude Code hook: tell you on Telegram when a Claude Code session on this PC finishes
or is waiting for you (desktop app or terminal). Reply to the message in Telegram and
the bot continues that session with your answer.

Installed by setup_bot.py into ~/.claude/settings.json as a "Stop" and a
"Notification" hook. Claude Code passes the event as JSON on stdin. This script never
blocks or fails Claude: it always exits 0 and logs problems to notify.log.
"""
import json
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bot  # noqa: E402

log = logging.getLogger("claude-tg.notify")

# Notification types that mean "Claude is waiting for you".
WAITING_TYPES = {"idle_prompt", "agent_needs_input", "elicitation_dialog", "permission_prompt"}
MAX_TEXT = 3300


def notify_enabled(base):
    try:
        return json.loads((base / "notify.json").read_text(encoding="utf-8")).get("enabled", True)
    except (OSError, ValueError):
        return True


def set_notify_enabled(base, enabled):
    (base / "notify.json").write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8")


target_chats = bot.target_chats


def project_name(cwd):
    if not cwd:
        return "?"
    return Path(cwd).name or str(cwd)


def compose(event):
    """Telegram text for a hook event, or None if it isn't worth a message."""
    name = event.get("hook_event_name")
    where = project_name(event.get("cwd"))
    if name == "Stop":
        text = (event.get("last_assistant_message") or "").strip() or "(no text)"
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "…"
        return (f"✅ Claude finished — {where}\n\n{text}\n\n"
                "↩️ Reply to this message to continue this session.")
    if name == "Notification":
        kind = event.get("notification_type") or ""
        if kind and kind not in WAITING_TYPES:
            return None
        message = (event.get("message") or "Claude is waiting for your input").strip()
        if kind == "permission_prompt":
            return (f"🔐 Claude needs permission — {where}\n\n{message}\n\n"
                    "Approve or deny it on the PC.")
        return (f"❓ Claude is waiting for you — {where}\n\n{message}\n\n"
                "↩️ Reply to this message to answer.")
    return None


def handle(event, base=HERE, api_factory=None):
    """Send the notification for one hook event. Returns the number of messages sent."""
    if os.environ.get(bot.BOT_ENV_FLAG):
        return 0  # a job the bot itself ran: the bot already reports it
    if event.get("stop_hook_active"):
        return 0  # Claude is continuing because of another Stop hook
    if not notify_enabled(base):
        return 0
    text = compose(event)
    if not text or not event.get("session_id"):
        return 0
    cfg = json.loads((base / "config.json").read_text(encoding="utf-8-sig"))
    token = cfg.get("telegram_bot_token") or ""
    if not token or "PASTE" in token:
        log.warning("no bot token in config.json")
        return 0
    try:
        state = json.loads((base / cfg.get("state_file", "state.json")).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}
    api = (api_factory or (lambda t: bot.TelegramAPI(t, poll_timeout=5)))(token)
    sent = 0
    for chat_id in target_chats(cfg, state):
        for message_id in api.send(chat_id, text) or []:
            bot.hook_session_remember(base, chat_id, message_id, {
                "session_id": event.get("session_id"),
                "cwd": event.get("cwd"),
                "transcript_path": event.get("transcript_path"),
                "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
            })
            sent += 1
    log.info("%s %s -> %d message(s) (transcript: %s)", event.get("hook_event_name"),
             event.get("notification_type") or "", sent, event.get("transcript_path"))
    return sent


def send_text(text, base=HERE, api_factory=None):
    """Message your chat(s) right away: python notify.py --send "Backup finished" """
    cfg = json.loads((base / "config.json").read_text(encoding="utf-8-sig"))
    try:
        state = json.loads((base / cfg.get("state_file", "state.json")).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}
    api = (api_factory or (lambda t: bot.TelegramAPI(t, poll_timeout=5)))(cfg["telegram_bot_token"])
    return sum(len(api.send(chat, text) or []) for chat in target_chats(cfg, state))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--send":
        text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read()
        sent = send_text(text.strip()) if text.strip() else 0
        print(f"sent {sent} message(s)")
        return 0 if sent else 1
    handler = logging.FileHandler(HERE / "notify.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        raw = sys.stdin.read()
        handle(json.loads(raw) if raw.strip() else {})
    except Exception:
        log.exception("notify failed")
    finally:
        log.removeHandler(handler)
        handler.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
