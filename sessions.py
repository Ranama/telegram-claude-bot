"""
Find and read the Claude Code sessions stored on this PC.

Claude Code (terminal and the desktop app's Code tab) keeps every session as a JSONL
transcript in <profile>/projects/<encoded folder>/<session id>.jsonl, where <profile>
is ~/.claude or the folder in CLAUDE_CONFIG_DIR. This module lists those sessions
(newest first) and reads their folder, title and recent messages, so the bot can
show them in Telegram and continue one with `claude -p --resume <id>` in its folder.
"""
import json
import os
import re
import time
from pathlib import Path

HEAD_BYTES = 256 * 1024        # enough for the folder and the first prompt
TAIL_BYTES = 2 * 1024 * 1024   # enough for titles and the latest messages
ACTIVE_SECONDS = 90            # written this recently = probably still running on the PC


def default_profile():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def folder_name(cwd):
    """Last part of a path, for Windows or POSIX paths alike."""
    if not cwd:
        return "?"
    parts = re.split(r"[\\/]+", str(cwd).rstrip("\\/"))
    return parts[-1] or str(cwd)


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _text(message):
    """Plain text of a transcript message (skips tool calls and tool results)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text").strip()
    return ""


def _is_human_prompt(entry, text):
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return False
    stripped = text.lstrip()
    return bool(stripped) and not stripped.startswith(("<command-", "<local-command",
                                                       "<system-reminder", "Caveat:"))


def _read_lines(path):
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size <= HEAD_BYTES + TAIL_BYTES:
            chunks = [f.read()]
        else:
            head = f.read(HEAD_BYTES)
            f.seek(size - TAIL_BYTES)
            chunks = [head, f.read()]
    for chunk in chunks:
        for raw in chunk.splitlines():
            try:
                entry = json.loads(raw)
            except ValueError:
                continue  # a line cut in half at a chunk edge
            if isinstance(entry, dict):
                yield entry


def read_session(path, config_dir=None):
    """Summary of one transcript file, or None if it holds no conversation."""
    path = Path(path)
    info = {"session_id": path.stem, "path": str(path), "config_dir": config_dir,
            "mtime": path.stat().st_mtime, "cwd": None, "title": None, "first_prompt": None,
            "last_prompt": None, "turns": []}
    titles = {}
    turns = info["turns"]  # [[role, text], ...] with consecutive same-role texts merged
    for entry in _read_lines(path):
        kind = entry.get("type")
        if kind in ("user", "assistant") and entry.get("cwd") and not info["cwd"]:
            info["cwd"] = entry["cwd"]
        if kind == "custom-title" and entry.get("customTitle"):
            titles["custom"] = entry["customTitle"]
        elif kind == "ai-title" and entry.get("aiTitle"):
            titles["ai"] = entry["aiTitle"]
        elif kind == "summary" and entry.get("summary"):
            titles["summary"] = entry["summary"]
        elif kind == "last-prompt" and entry.get("lastPrompt"):
            info["last_prompt"] = entry["lastPrompt"]
        elif kind in ("user", "assistant") and not entry.get("isSidechain"):
            text = _text(entry.get("message"))
            if not text:
                continue
            if kind == "user":
                if not _is_human_prompt(entry, text):
                    continue
                info["first_prompt"] = info["first_prompt"] or text
            role = "you" if kind == "user" else "claude"
            if turns and turns[-1][0] == role:
                turns[-1][1] += "\n" + text
            else:
                turns.append([role, text])
    if not turns:
        return None
    last_user = next((t for r, t in reversed(turns) if r == "you"), None)
    info["last_prompt"] = info["last_prompt"] or last_user
    info["title"] = (titles.get("custom") or titles.get("ai") or titles.get("summary")
                     or _one_line(info["first_prompt"] or "", 60))
    info["last_reply"] = next((t for r, t in reversed(turns) if r == "claude"), "")
    del turns[:-20]
    return info


def _one_line(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def profile_roots(profiles):
    """[(projects folder, config_dir)] for each Claude profile (config_dir None = default)."""
    roots, seen = [], set()
    for config_dir in profiles:
        base = Path(os.path.expanduser(config_dir)) if config_dir else default_profile()
        key = os.path.normcase(str(base))
        if key not in seen:
            seen.add(key)
            roots.append((base / "projects", config_dir))
    return roots


def list_sessions(roots, limit=10, extra_files=()):
    """Newest sessions first. roots: [(projects folder, config_dir)];
    extra_files: [(transcript path, config_dir)] known from notifications."""
    candidates = {}
    for projects, config_dir in roots:
        if not Path(projects).is_dir():
            continue
        for path in Path(projects).glob("*/*.jsonl"):
            candidates[os.path.normcase(str(path))] = (path, config_dir)
    for path, config_dir in extra_files:
        path = Path(path)
        if path.is_file():
            for sibling in path.parent.glob("*.jsonl"):
                candidates.setdefault(os.path.normcase(str(sibling)), (sibling, config_dir))
    files = []
    for path, config_dir in candidates.values():
        try:
            files.append((path.stat().st_mtime, path, config_dir))
        except OSError:
            continue
    files.sort(key=lambda x: x[0], reverse=True)
    sessions = []
    for _, path, config_dir in files:
        try:
            info = read_session(path, config_dir)
        except OSError:
            continue
        if info:
            sessions.append(info)
            if len(sessions) >= limit:
                break
    return sessions


def describe(index, info, now=None):
    """One entry of the /list message."""
    now = now or time.time()
    age = now - info["mtime"]
    dot = "🟢" if age < ACTIVE_SECONDS else "▫️"
    line = f"{index}. {dot} {folder_name(info['cwd'])} — {_one_line(info['title'], 60)} · {ago(age)}"
    if info.get("last_prompt"):
        line += f"\n    you: {_one_line(info['last_prompt'], 70)}"
    return line


def history_text(info, count=3, limit=900):
    """The last `count` exchanges, each message shortened."""
    turns = info["turns"]
    start = len(turns)
    seen_user = 0
    while start > 0 and seen_user < count:
        start -= 1
        if turns[start][0] == "you":
            seen_user += 1
    out = []
    for role, text in turns[start:]:
        text = text.strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        out.append(("🧑 You: " if role == "you" else "🤖 Claude: ") + text)
    return "\n\n".join(out) or "(no messages yet)"
