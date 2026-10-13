"""
Status of every Claude Code session on this computer, kept up to date by hook.py.

  status/locks/<session>.lock     exists while a command runs (created when you send
                                  Claude something, deleted when it's done)
  status/sessions/<session>.json  latest state, account, folder, title and summary

States: working, needs_input, needs_approval, completed, ended (closed), and stalled
(a lock without any activity for 20 minutes, e.g. after an interrupt).
"""
import json
import os
import re
import time
from pathlib import Path

STATUS_DIR = "status"
STALLED_SECONDS = 20 * 60
LIST_HOURS = 24

STATES = {  # state: (emoji, label, order in lists)
    "needs_input": ("❓", "Needs input", 0),
    "needs_approval": ("🔐", "Needs approval", 1),
    "working": ("🟡", "Working", 2),
    "stalled": ("⚠️", "No activity", 3),
    "completed": ("✅", "Completed", 4),
    "ended": ("⏹", "Closed", 5),
}


# ---------------------------------------------------------------- small helpers

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    """Atomic write: readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: someone is reading it right now
            time.sleep(0.05)
    os.replace(tmp, path)


def remove(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def one_line(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def folder_name(path):
    parts = re.split(r"[\\/]+", str(path or "").rstrip("\\/"))
    return parts[-1] or str(path or "?") or "?"


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def record_path(base, session_id):
    return Path(base) / STATUS_DIR / "sessions" / f"{_safe(session_id)}.json"


def lock_path(base, session_id):
    return Path(base) / STATUS_DIR / "locks" / f"{_safe(session_id)}.lock"


def _safe(session_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id))[:100] or "unknown"


# ---------------------------------------------------------------- account and session names

def account_label(config_dir=None):
    """The Claude account signed in to this Claude profile (email), or the profile name."""
    path = (Path(os.path.expanduser(config_dir)) / ".claude.json" if config_dir
            else Path.home() / ".claude.json")
    email = (read_json(path).get("oauthAccount") or {}).get("emailAddress")
    return email or (folder_name(config_dir) if config_dir else "default account")


def session_title(transcript_path):
    """The session's title (as shown in Claude Code), else its first prompt."""
    try:
        with open(transcript_path, "rb") as f:
            data = f.read()
    except (OSError, TypeError):
        return None
    title, first = None, None
    for raw in data.splitlines():
        if b'"custom-title"' not in raw and b'"ai-title"' not in raw and (first or b'"user"' not in raw):
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind == "custom-title" and entry.get("customTitle"):
            title = ("custom", entry["customTitle"])
        elif kind == "ai-title" and entry.get("aiTitle") and (not title or title[0] != "custom"):
            title = ("ai", entry["aiTitle"])
        elif kind == "user" and not first and not entry.get("isMeta"):
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<"):
                first = content
    return one_line(title[1], 70) if title else (one_line(first, 70) if first else None)


CODE_BLOCK = re.compile(r"```.*?```", re.S)
LIST_MARK = re.compile(r"^\s*(#+|[-*•>]|\d+[.)])\s+")


def short_summary(text, limit=220, enough=50):
    """The first sentence or two of Claude's answer, as one plain line."""
    lines = []
    for line in CODE_BLOCK.sub(" ", text or "").splitlines():
        line = LIST_MARK.sub("", line).replace("**", "").replace("__", "").replace("`", "").strip()
        if line:
            lines.append(line)
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(lines)):
        out = f"{out} {sentence}".strip()
        if len(out) >= enough:
            break
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out or "(no text)"


def approval_text(tool, args):
    args = args or {}
    if tool in ("Bash", "PowerShell"):
        return f"Claude wants to run: {one_line(args.get('command'), 200)}"
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return f"Claude wants to edit: {args.get('file_path') or args.get('notebook_path')}"
    if tool == "WebFetch":
        return f"Claude wants to open: {args.get('url')}"
    if tool == "ExitPlanMode":
        return "Claude wants you to approve its plan"
    return f"Claude wants to use {tool}"


# ---------------------------------------------------------------- recording (called by hook.py)

def record_event(base, event, now=None, config_dir=None):
    """Update a session's status for one Claude Code hook event. Returns the record."""
    sid, name = event.get("session_id"), event.get("hook_event_name")
    if not sid or not name:
        return None
    now = now or time.time()
    rec = read_json(record_path(base, sid))
    if config_dir is None:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or None
    rec.update({"session_id": sid, "updated": now, "account": account_label(config_dir)})
    for key in ("cwd", "transcript_path"):
        if event.get(key):
            rec[key] = event[key]
    rec["project"] = folder_name(rec.get("cwd"))
    title = event.get("session_title") or session_title(rec.get("transcript_path"))
    if title:
        rec["title"] = title

    if name == "UserPromptSubmit":                  # a new command starts: lock
        rec.update({"state": "working", "started": now, "detail": "",
                    "prompt": one_line(event.get("prompt"), 160)})
        if rec.get("completed_at", 0) < now:        # hooks may run late: never re-lock a finished one
            write_json(lock_path(base, sid), {"session_id": sid, "started": now,
                                              "prompt": rec["prompt"]})
    elif name == "PermissionRequest":               # a question or an approval
        tool, args = event.get("tool_name"), event.get("tool_input") or {}
        if tool == "AskUserQuestion":
            q = (args.get("questions") or [{}])[0]
            options = ", ".join(str(o.get("label")) for o in q.get("options") or [])
            rec.update({"state": "needs_input", "asked_at": now,
                        "detail": f"Claude asks: {q.get('question', '')}" + (f" ({options})" if options else "")})
        else:
            rec.update({"state": "needs_approval", "asked_at": now, "detail": approval_text(tool, args)})
    elif name == "Notification":
        kind = event.get("notification_type") or ""
        if kind in ("elicitation_dialog", "agent_needs_input"):
            rec.update({"state": "needs_input", "asked_at": now,
                        "detail": one_line(event.get("message") or "Claude needs your input", 300)})
        else:
            return rec                               # nothing new (idle prompts, permission prompts)
    elif name == "Stop":                            # the command is done: unlock
        remove(lock_path(base, sid))
        rec.update({"state": "completed", "completed_at": now, "detail": "",
                    "summary": short_summary(event.get("last_assistant_message"))})
    elif name == "SessionEnd":                      # the session was closed
        remove(lock_path(base, sid))
        rec.update({"state": "ended", "ended_at": now, "detail": ""})
    else:
        return rec
    write_json(record_path(base, sid), rec)
    return rec


# ---------------------------------------------------------------- reading (/status)

def _mtime(path):
    try:
        return os.path.getmtime(path) if path else 0
    except OSError:
        return 0


def derive_state(base, rec, now=None):
    """The state to show now, using the lock file and the transcript's activity."""
    now = now or time.time()
    lock = read_json(lock_path(base, rec["session_id"]))
    state = rec.get("state") or "completed"
    activity = max(_mtime(rec.get("transcript_path")), rec.get("updated", 0))
    if lock and lock.get("started", 0) > rec.get("completed_at", 0):
        if state in ("needs_input", "needs_approval"):
            if _mtime(rec.get("transcript_path")) > rec.get("asked_at", 0) + 2:
                state = "working"                    # answered on the PC: Claude went on
        else:
            state = "working"
        if state == "working" and now - activity > STALLED_SECONDS:
            state = "stalled"
    elif lock:
        remove(lock_path(base, rec["session_id"]))   # left over from a finished command
    return state, activity


def current(base, now=None, hours=LIST_HOURS):
    """Sessions active in the last `hours` (plus all still running), most urgent first."""
    now = now or time.time()
    folder = Path(base) / STATUS_DIR / "sessions"
    items = []
    for path in (folder.glob("*.json") if folder.is_dir() else []):
        rec = read_json(path)
        if not rec.get("session_id"):
            continue
        state, activity = derive_state(base, rec, now)
        if now - activity > hours * 3600 and state not in ("working", "needs_input", "needs_approval"):
            continue
        emoji, label, order = STATES.get(state, STATES["completed"])
        items.append({"state": state, "emoji": emoji, "label": label, "order": order,
                      "activity": activity, "ago": ago(now - activity),
                      "project": rec.get("project") or "?", "account": rec.get("account") or "?",
                      "title": rec.get("title") or rec.get("prompt") or "(untitled)",
                      "detail": rec.get("detail") or "", "summary": rec.get("summary") or "",
                      "session_id": rec["session_id"]})
    items.sort(key=lambda i: (i["order"], -i["activity"]))
    return items


def status_text(items, limit=20):
    """The /status message in Telegram."""
    if not items:
        return "📋 No Claude Code sessions in the last 24 hours."
    counts = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    totals = "  ".join(f"{STATES[s][0]} {n}" for s, n in sorted(counts.items(), key=lambda x: STATES[x[0]][2]))
    lines = [f"📋 Claude sessions — {totals}", ""]
    for item in items[:limit]:
        lines.append(f"{item['emoji']} {item['label']} — {item['project']} · “{one_line(item['title'], 50)}”")
        lines.append(f"    👤 {item['account']} · {item['ago']}")
        extra = item["detail"] or (item["summary"] if item["state"] == "completed" else "")
        if extra:
            lines.append(f"    {one_line(extra, 120)}")
    if len(items) > limit:
        lines.append(f"… and {len(items) - limit} more")
    return "\n".join(lines)
