#!/usr/bin/env python3
"""
Claude Code hook: keeps you in the loop on Telegram for Claude Code sessions on this PC
(desktop app, terminal, and the tasks you start from Telegram).

- Stop:              Claude finished — you get its full answer. Reply to continue the session.
- Notification:      Claude is waiting for you (input / permission on the PC).
- PermissionRequest: Claude wants to use a tool, or asks a multiple-choice question.
                     For tasks started from Telegram — and for every session while you're
                     /away — it comes to Telegram as buttons and the session waits (checking
                     every second) until you tap an answer. Otherwise it's asked on the PC.

Installed by setup_bot.py into ~/.claude/settings.json. Claude Code passes the event as
JSON on stdin; for PermissionRequest the decision is printed as JSON on stdout. This
script never fails Claude: problems go to notify.log and Claude just asks as usual.

Also: python notify.py --send "text"   sends you a message from any script.
"""
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bot  # noqa: E402

log = logging.getLogger("claude-tg.notify")

# Notification types that mean "Claude is waiting for you".
WAITING_TYPES = {"idle_prompt", "agent_needs_input", "elicitation_dialog", "permission_prompt"}
MAX_TEXT = 15000            # about four Telegram messages
DEFAULT_ANSWER_TIMEOUT = 600
POLL_SECONDS = 1.0

target_chats = bot.target_chats


def notify_enabled(base):
    return bot.notify_settings(base)["enabled"]


def set_notify_enabled(base, enabled):
    bot.update_notify_settings(base, enabled=bool(enabled))


def project_name(cwd):
    if not cwd:
        return "?"
    parts = re.split(r"[\\/]+", str(cwd).rstrip("\\/"))
    return parts[-1] or str(cwd)


def compose(event):
    """Telegram text for a Stop/Notification event, or None if it isn't worth a message."""
    name = event.get("hook_event_name")
    where = project_name(event.get("cwd"))
    if name == "Stop":
        text = (event.get("last_assistant_message") or "").strip() or "(no text)"
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n\n… (cut here: the full answer is in the session)"
        return (f"✅ Claude finished — {where}\n\n{text}\n\n"
                "↩️ Reply to this message to continue this session.")
    if name == "Notification":
        kind = event.get("notification_type") or ""
        if kind and kind not in WAITING_TYPES:
            return None
        message = (event.get("message") or "Claude is waiting for your input").strip()
        if kind == "permission_prompt":
            return (f"🔐 Claude needs permission — {where}\n\n{message}\n\n"
                    "Approve or deny it on the PC. (Send /away on to answer these here.)")
        return (f"❓ Claude is waiting for you — {where}\n\n{message}\n\n"
                "↩️ Reply to this message to answer.")
    return None


def load(base):
    cfg = json.loads((base / "config.json").read_text(encoding="utf-8-sig"))
    try:
        state = json.loads((base / cfg.get("state_file", "state.json")).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}
    return cfg, state


def make_api(cfg, api_factory=None):
    token = cfg.get("telegram_bot_token") or ""
    if not token or "PASTE" in token:
        log.warning("no bot token in config.json")
        return None
    return (api_factory or (lambda t: bot.TelegramAPI(t, poll_timeout=5)))(token)


def handle(event, base=HERE, api_factory=None):
    """Send the message for a Stop/Notification event. Returns the number of messages sent."""
    if os.environ.get(bot.BOT_ENV_FLAG):
        return 0  # a job the bot itself ran: the bot already reports it
    if event.get("stop_hook_active"):
        return 0  # Claude is continuing because of another Stop hook
    if not notify_enabled(base):
        return 0
    text = compose(event)
    if not text or not event.get("session_id"):
        return 0
    cfg, state = load(base)
    api = make_api(cfg, api_factory)
    if not api:
        return 0
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


# ---------------------------------------------------------------- PermissionRequest

def describe_tool(tool, args):
    """'run a command: …' — what Claude wants to do, in a few lines."""
    args = args or {}
    if tool in ("Bash", "PowerShell"):
        text = f"run a command:\n\n{str(args.get('command', ''))[:1500]}"
        if args.get("description"):
            text += f"\n\n({args['description']})"
        return text
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        verb = "create or overwrite" if tool == "Write" else "edit"
        return f"{verb} {args.get('file_path') or args.get('notebook_path')}"
    if tool == "WebFetch":
        return f"open {args.get('url')}"
    return f"use {tool}:\n\n{json.dumps(args, ensure_ascii=False)[:1200]}"


def question_text(where, q, index, total):
    head = f"❓ {where} — Claude asks" + (f" ({index + 1}/{total})" if total > 1 else "") + ":"
    lines = [head, "", q.get("question", "")]
    for option in q.get("options") or []:
        line = f"• {option.get('label')}"
        if option.get("description"):
            line += f" — {option['description']}"
        lines.append(line)
    lines.append("")
    lines.append("Pick one or more, then ✅ Done." if q.get("multiSelect") else "Tap an answer.")
    lines.append("Or reply to this message with your own answer.")
    return "\n".join(lines)


def should_ask_on_telegram(base):
    """Telegram-started tasks always ask here; PC sessions only while you're /away."""
    return bool(os.environ.get(bot.BOT_ENV_FLAG)) or bot.notify_settings(base)["away"]


def ask_on_telegram(event, base=HERE, api_factory=None, timeout=None,
                    poll=POLL_SECONDS, sleep=time.sleep, clock=time.time):
    """Show a permission request / question on Telegram and wait for the answer.

    Returns the PermissionRequest decision ({"behavior": ...}) or None (no answer in
    time, or Telegram unreachable): then Claude Code asks on the PC as usual."""
    tool = event.get("tool_name") or "?"
    args = event.get("tool_input") or {}
    cfg, state = load(base)
    api = make_api(cfg, api_factory)
    chats = target_chats(cfg, state)
    if not api or not chats:
        return None
    timeout = timeout or int(cfg.get("answer_timeout_seconds", DEFAULT_ANSWER_TIMEOUT))
    where = project_name(event.get("cwd"))
    rid = os.urandom(4).hex()
    is_question = tool == "AskUserQuestion" and args.get("questions")
    questions = (args.get("questions") or []) if is_question else []
    req = {"rid": rid, "kind": "question" if is_question else "permission", "tool_name": tool,
           "questions": questions, "session_id": event.get("session_id"),
           "cwd": event.get("cwd"), "created": int(clock()), "messages": {}, "texts": {}}
    bot.write_json(bot.pending_file(base, rid), req)   # before sending: taps can arrive at once
    for chat in chats:
        ids, texts = [], []
        if is_question:
            for qi, q in enumerate(questions):
                text = question_text(where, q, qi, len(questions))
                sent = api.send(chat, text, buttons=bot.question_buttons(rid, qi, q)) or []
                ids.append(sent[-1] if sent else None)
                texts.append(text)
        else:
            text = (f"🔐 {where} — Claude wants to {describe_tool(tool, args)}\n\n"
                    "Allow it? (Or reply with what Claude should do instead.)")
            sent = api.send(chat, text, buttons=bot.permission_buttons(rid)) or []
            ids.append(sent[-1] if sent else None)
            texts.append(text)
        req["messages"][str(chat)] = ids
        req["texts"][str(chat)] = texts
    bot.write_json(bot.pending_file(base, rid), req)
    if not any(i for ids in req["messages"].values() for i in ids):
        _cleanup(base, rid)
        log.warning("could not send %s request to Telegram", tool)
        return None
    log.info("%s %s waiting for an answer on Telegram (%s)", req["kind"], rid, tool)

    deadline = clock() + timeout
    try:
        while clock() < deadline:
            answer = bot._read_json(bot.answer_file(base, rid))
            if is_question:
                answers = answer.get("answers") or {}
                if all(q.get("question") in answers for q in questions):
                    log.info("question %s answered", rid)
                    return {"behavior": "allow", "updatedInput": dict(args, answers=answers)}
            elif answer.get("behavior") == "allow":
                log.info("permission %s allowed", rid)
                return {"behavior": "allow", "updatedInput": args}
            elif answer.get("behavior") == "deny":
                log.info("permission %s denied", rid)
                return {"behavior": "deny",
                        "message": answer.get("message") or "The user denied this from Telegram."}
            sleep(poll)
        log.info("%s %s: no answer in %ss", req["kind"], rid, timeout)
        for chat, ids in req["messages"].items():
            for mid, text in zip(ids, req["texts"].get(chat, [])):
                if mid:
                    api.edit(int(chat), mid, text + "\n\n⌛ No answer in time — it's asked on the PC now.")
        return None
    finally:
        _cleanup(base, rid)


def _cleanup(base, rid):
    for path in (bot.pending_file(base, rid), bot.answer_file(base, rid)):
        try:
            path.unlink()
        except OSError:
            pass


def handle_permission(event, base=HERE, api_factory=None, **kwargs):
    """PermissionRequest hook: the JSON to print, or None to let Claude Code ask on the PC."""
    if not should_ask_on_telegram(base):
        return None
    decision = ask_on_telegram(event, base=base, api_factory=api_factory, **kwargs)
    if not decision:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


# ---------------------------------------------------------------- scripts / entry point

def send_text(text, base=HERE, api_factory=None):
    """Message your chat(s) right away: python notify.py --send "Backup finished" """
    cfg, state = load(base)
    api = make_api(cfg, api_factory)
    if not api:
        return 0
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
        event = json.loads(raw) if raw.strip() else {}
        name = event.get("hook_event_name")
        log.info("hook %s %s (%d bytes in, from the bot: %s)", name, event.get("tool_name") or
                 event.get("notification_type") or "", len(raw), bool(os.environ.get(bot.BOT_ENV_FLAG)))
        if name == "PermissionRequest":
            output = handle_permission(event)
            if output:
                sys.stdout.write(json.dumps(output))
                sys.stdout.flush()
        else:
            handle(event)
    except Exception:
        log.exception("notify failed")
    finally:
        log.removeHandler(handler)
        handler.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
