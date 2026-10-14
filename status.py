"""
Status of every Claude session: one small file per session, read by /status.

  status/sessions/<session>.json  state, what it's working on, title, folder, account, summary
  status/locks/<session>.lock     exists while a command runs (created when you send
                                  Claude something, deleted when it's done)

Claude Code sessions on this computer: hook.py keeps both up to date automatically.
Cloud sessions (no hooks) write their status/sessions/<id>.json themselves — see CLAUDE.md.

States: working, needs_input, needs_approval, completed, ended (closed), and stalled
(working, but no sign of life for 20 minutes, e.g. after an interrupt).
"""
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

STATUS_DIR = "status"
STALLED_SECONDS = 20 * 60
LIST_HOURS = 24

# /status colours: 🔴 needs you, 🟡 working, 🟢 done (icon, one, several, states)
GROUPS = (("🔴", "needs you", "need you", ("needs_input", "needs_approval")),
          ("🟡", "working", "working", ("working",)),
          ("🟠", "inactive", "inactive", ("stalled",)),
          ("🟢", "done", "done", ("completed",)),
          ("⚪", "closed", "closed", ("ended",)))
COLORS = {state: icon for icon, _, _, states in GROUPS for state in states}

STATES = {  # state: (emoji in notifications, label, order in lists)
    "needs_input": ("❓", "Needs input", 0),
    "needs_approval": ("🔐", "Needs approval", 1),
    "working": ("🟡", "Working", 2),
    "stalled": ("⚠️", "No activity", 3),
    "completed": ("✅", "Completed", 4),
    "ended": ("⏹", "Closed", 5),
}
ALIASES = {"done": "completed", "complete": "completed", "finished": "completed", "running": "working",
           "in_progress": "working", "busy": "working", "waiting": "needs_input", "closed": "ended"}


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


def clock(ts, now=None):
    """'14:02' today, '13 Oct 14:02' on other days (this computer's time)."""
    t, today = time.localtime(ts), time.localtime(now or time.time())
    return time.strftime("%H:%M" if t[:3] == today[:3] else "%d %b %H:%M", t)


def when(value):
    """Unix seconds from a number or an ISO date ('2026-10-15T01:57:00+02:00'); 0 if unknown."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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


CODE_BLOCK = re.compile(r"```.*?```", re.S)
LIST_MARK = re.compile(r"^\s*(#+|[-*•>]|\d+[.)])\s+")
TAG = re.compile(r"</?[A-Za-z][\w:-]*(?:\s[^<>]*)?>")                 # <command-name> etc.
URL = re.compile(r"https?://[^\s<>\"'`)\]]+")
MENTION = re.compile(r'(?<!\S)@(?:"([^"]+)"|(\S+))')                   # @file, @"a file"
PATH = re.compile(r'"[^"\n]*[\\/][^"\n]*[\\/][^"\n]*"'                 # "C:\Program Files\x"
                  r"|[^\s\"'`()<>]*[\\/][^\s\"'`()<>\\/]+[\\/][^\s\"'`()<>]*")
FILLER = re.compile(r"^(?:(?:hey|hi|hello|thanks|thank you|great|perfect|nice|cool)(?:\s+claude)?\s*[,!.:\n]\s*"
                    r"|(?:hey|hi|hello)\s+claude\s+|claude[,:]\s*)?(?:ok(?:ay)?\b[,!.]?\s*)?"
                    r"(?P<ask>(?:can|could)\s+you\s+)?(?:please\s+|pls\s+)?", re.I)  # "Hi! Can you please"


def _last_part(path):
    parts = [p for p in re.split(r"[\\/]+", path.strip("\"'")) if re.search(r"\w", p)]
    return parts[-1] if parts else ""


def _path(match):
    token = match.group(0)
    return _last_part(token) if re.search(r"[A-Za-z]", token) else token  # 1/2/2026 is a date


def _mention(match):
    name = match.group(1) or match.group(2)
    return _last_part(name) if match.group(1) or re.search(r"[\\/.]", name) else match.group(0)


def _host(match):
    host = urlsplit(match.group(0)).hostname or ""
    return host[4:] if host.startswith("www.") else host


def readable_title(text, limit=50):
    """A short, readable name from a prompt or title: file names instead of paths,
    sites instead of links, no code or markup, no "can you please"; the first sentence."""
    text = TAG.sub(" ", CODE_BLOCK.sub(" ", str(text or "")[:2000]))  # the start is enough
    text = PATH.sub(_path, MENTION.sub(_mention, URL.sub(_host, text)))
    lines = (" ".join(LIST_MARK.sub("", line).replace("**", "").replace("__", "").replace("`", "").split())
             for line in text.splitlines())
    text = "\n".join(line for line in lines if line).strip(" -:;,")
    polite = FILLER.match(text)
    if polite.end() and text[polite.end():].strip():
        text = text[polite.end():]
    text = re.split(r"(?<=[.!?])\s+|\n", text)[0]          # the first sentence or line
    head, colon, _ = text.partition(": ")
    if colon and len(head.split()) >= 3:                    # "Push it to my repo: https://…"
        text = head
    text = text.strip(" -:;,").rstrip(".")
    if polite.group("ask"):
        text = text.rstrip("?")                              # "Can you fix X?" → "Fix X"
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    if re.match(r"[a-z]+\b", text):              # "fix the bug" → "Fix the bug" (not iOS)
        text = text[0].upper() + text[1:]
    return text or None


def session_title(transcript_path):
    """The session's title (as shown in Claude Code), else its first prompt; readable."""
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
            if isinstance(content, list):          # a prompt with attachments: its text parts
                content = " ".join(b.get("text") or "" for b in content
                                   if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<"):
                first = content
    return readable_title(title[1] if title else first)


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
    title = readable_title(event.get("session_title")) or session_title(rec.get("transcript_path"))
    if title:
        rec["title"] = title

    if name == "UserPromptSubmit":                  # a new command starts: lock
        if rec.get("completed_at", 0) >= now:       # hooks may run late: never re-lock a finished one
            return rec
        rec.update({"state": "working", "started": now, "detail": "",
                    "task": one_line(event.get("prompt"), 160)})
        write_json(lock_path(base, sid), {"session_id": sid, "started": now, "task": rec["task"]})
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


def derive_state(base, rec, now=None, file_time=0):
    """The state to show now. Hook sessions: from the lock file and the transcript's activity.
    Sessions that report themselves (no lock): the state in their file."""
    now = now or time.time()
    lock = read_json(lock_path(base, rec["session_id"]))
    state = str(rec.get("state") or "completed").strip().lower().replace(" ", "_")
    state = ALIASES.get(state, state if state in STATES else "working")
    updated = float(rec.get("updated") or 0) or file_time
    if updated > now + 60:                           # a wrong clock or milliseconds: use the file's time
        updated = file_time
    activity = max(_mtime(rec.get("transcript_path")), updated)
    if lock and lock.get("started", 0) > rec.get("completed_at", 0):
        if state in ("needs_input", "needs_approval"):
            if _mtime(rec.get("transcript_path")) > rec.get("asked_at", 0) + 2:
                state = "working"                    # answered on the PC: Claude went on
        else:
            state = "working"
    elif lock:
        remove(lock_path(base, rec["session_id"]))   # left over from a finished command
    if state == "working" and now - activity > STALLED_SECONDS:
        state = "stalled"                            # no sign of life for 20 minutes
    return state, activity


def current(base, now=None, hours=LIST_HOURS):
    """Sessions active in the last `hours` (plus all still running), most urgent first."""
    now = now or time.time()
    folder = Path(base) / STATUS_DIR / "sessions"
    items = []
    for path in (folder.glob("*.json") if folder.is_dir() else []):
        rec = read_json(path)
        if not isinstance(rec, dict) or not rec.get("state"):   # broken or half-written
            continue
        rec["session_id"] = str(rec.get("session_id") or path.stem)
        try:
            state, activity = derive_state(base, rec, now, _mtime(path))
        except (TypeError, ValueError, AttributeError):  # a broken file
            continue
        if now - activity > hours * 3600 and state not in ("working", "needs_input", "needs_approval"):
            continue
        text = {key: one_line(rec.get(key), 300) for key in
                ("project", "account", "title", "task", "detail", "summary", "where")}
        text["title"] = text["title"] or readable_title(text["task"]) or "Untitled session"
        started, finished = (t if 0 < t <= now + 60 else 0 for t in
                             (when(rec.get("started")), when(rec.get("completed_at"))))
        if state in ("working", "stalled") and not started:
            started = activity                     # a cloud session that didn't say: its file's time
        if state == "completed" and not finished:
            finished = activity
        items.append(dict(text, state=state, order=STATES[state][2], activity=activity,
                          started=started, finished=finished,
                          ago=ago(now - activity), session_id=rec["session_id"]))
    items.sort(key=lambda i: (i["order"], -i["activity"]))
    return items


REWORD = ((re.compile(r"^Claude asks:\s*"), "Asks you: "),
          (re.compile(r"^Claude wants you to "), "Needs you to "),
          (re.compile(r"^Claude wants to "), "Needs your approval to "),
          (re.compile(r"^Claude needs "), "Needs "))


def _what(item):
    """What's happening, in plain words: 'Asks you: Which database?', 'Done: …'."""
    state, detail = item["state"], item.get("detail") or ""
    if state in ("needs_input", "needs_approval"):
        for pattern, words in REWORD:
            if pattern.match(detail):
                return pattern.sub(words, detail, count=1)
        what = "Needs your input" if state == "needs_input" else "Needs your approval"
        return f"{what}: {detail}" if detail else what
    if state == "working":
        task = readable_title(item.get("task"), 80)
        return f"Working on: {task}" if task and task != item.get("title") else "Working on it…"
    if state == "stalled":
        return "No activity for 20+ min — interrupted or stuck?"
    if state == "completed":                                 # "Done. I fixed it." → "Done: I fixed it."
        summary = re.sub(r"^(?:all\s+)?done(?:[.!:,]+\s*|$)|^\(no text\)$", "", item.get("summary") or "", flags=re.I)
        return f"Done: {summary}" if summary else "Done"
    return "Session closed"


def _times(item, now=None):
    """'started 14:02 · done 14:10' (or 'last done' while a newer task runs)."""
    out = [f"started {clock(item['started'], now)}"] if item.get("started") else []
    if item.get("finished"):
        done = "done" if item["state"] in ("completed", "ended") else "last done"
        out.append(f"{done} {clock(item['finished'], now)}")
    return out or [item.get("ago") or ""]


def status_text(items, limit=15, now=None):
    """The /status message (Telegram HTML): a coloured line per session, most urgent first."""
    esc = lambda t: html.escape(str(t), quote=False)  # noqa: E731
    clock = time.strftime("%H:%M:%S", time.localtime(now or time.time()))
    if not items:
        return (f"📋 <b>Claude sessions</b>\nNo sessions in the last {LIST_HOURS} hours.\n"
                "Claude Code sessions on this computer show up by themselves; cloud sessions "
                f"once they write their status file (see CLAUDE.md).\n\n🕒 Updated {clock}")
    accounts = sorted({i["account"] for i in items if i.get("account")})
    lines = ["📋 <b>Claude sessions</b>"] + ([f"👤 {esc(accounts[0])}"] if len(accounts) == 1 else [])
    lines.append(" · ".join(f"{icon} {n} {one if n == 1 else several}"
                            for icon, one, several, states in GROUPS
                            for n in [sum(i["state"] in states for i in items)] if n))
    for item in items[:limit]:
        where = ([f"📁 {esc(item['project'])}"] if item.get("project") else []) + (
            ["☁️ cloud"] if item.get("where") == "cloud" else []) + (
            [f"👤 {esc(item['account'])}"] if len(accounts) > 1 and item.get("account") else [])
        lines += ["",
                  f"{COLORS.get(item['state'], '⚪')} <b>{esc(one_line(item['title'], 60))}</b>",
                  esc(one_line(_what(item), 120)),
                  f"<i>{' · '.join(where + _times(item, now))}</i>"]
    if len(items) > limit:
        lines += ["", f"… and {len(items) - limit} more"]
    lines += ["", f"🕒 Updated {clock}"]
    return "\n".join(lines)
