#!/usr/bin/env python3
"""
claude-telegram: control Claude Code on several Claude accounts from Telegram.

- Each Claude account is a separate Claude Code profile (CLAUDE_CONFIG_DIR) with its own login.
- Each Telegram chat is linked to one account; only approved Telegram user ids can command it.
- Plain messages are sent to Claude, continuing the chat's current session.
- When a job finishes you get a message with the result, duration and cost.

No third-party dependencies: Python 3.9+ standard library only.
"""
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("claude-tg")

TG_LIMIT = 4000  # Telegram max is 4096 chars per message; leave headroom

HELP = """Claude ⇄ Telegram

Just send a message → it goes to Claude, continuing the current session.
(With a single account, chats are linked to it automatically.)

/accounts – list Claude accounts
/link <account> – link this chat to an account
/new – start a fresh session (next message starts it)
/continue [text] – continue the session ("Continue." if no text)
/resume <session_id> – attach to an existing session (e.g. one from your terminal)
/sessions – sessions known in this chat
/status – what is running right now
/usage – tokens, cost & turns for this session and account
/stop – stop the running job
/whoami – show your Telegram user id"""

BOT_COMMANDS = [
    ("accounts", "List Claude accounts"),
    ("link", "Link this chat to an account"),
    ("new", "Start a fresh session"),
    ("continue", "Continue the current session"),
    ("resume", "Attach to an existing session id"),
    ("sessions", "Sessions in this chat"),
    ("status", "What is running"),
    ("usage", "Usage of current session"),
    ("stop", "Stop the running job"),
    ("whoami", "Show your Telegram user id"),
    ("help", "Help"),
]


# ---------------------------------------------------------------- helpers

def split_message(text, limit=TG_LIMIT):
    text = text or "(empty)"
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def fmt_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def empty_stats():
    return {"runs": 0, "turns": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "duration_ms": 0}


def add_stats(stats, result):
    usage = result.get("usage") or {}
    stats["runs"] += 1
    stats["turns"] += int(result.get("num_turns") or 0)
    stats["cost_usd"] += float(result.get("total_cost_usd") or result.get("cost_usd") or 0)
    stats["input_tokens"] += int(usage.get("input_tokens") or 0)
    stats["output_tokens"] += int(usage.get("output_tokens") or 0)
    stats["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    stats["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
    stats["duration_ms"] += int(result.get("duration_ms") or 0)


def fmt_stats(stats):
    return (f"runs {stats['runs']} · turns {stats['turns']} · ${stats['cost_usd']:.4f}\n"
            f"tokens in {fmt_tokens(stats['input_tokens'])} · out {fmt_tokens(stats['output_tokens'])}"
            f" · cache read {fmt_tokens(stats['cache_read_tokens'])}"
            f" · cache write {fmt_tokens(stats['cache_write_tokens'])}\n"
            f"Claude time {fmt_duration(stats['duration_ms'] / 1000)}")


def parse_claude_output(stdout):
    """`claude -p --output-format json` prints one JSON object; be tolerant of extra lines."""
    stdout = (stdout or "").strip()
    try:
        obj = json.loads(stdout)
        if isinstance(obj, list):  # some versions emit an array of events
            results = [o for o in obj if isinstance(o, dict) and o.get("type") == "result"]
            return results[-1] if results else None
        return obj
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


IS_WINDOWS = os.name == "nt"


def popen_kwargs():
    """Run Claude in its own process group so /stop can kill it with its children."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def claude_install_locations():
    """Where Claude Code's installers put the `claude` command.

    The native installer's folder (~/.local/bin) is often not on PATH until a new
    terminal is opened, so it is checked explicitly.
    """
    home = Path.home()
    places = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"]
    if IS_WINDOWS:
        if os.environ.get("APPDATA"):
            places.append(Path(os.environ["APPDATA"]) / "npm" / "claude.cmd")
    else:
        places += [Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
    return places


def find_claude(name="claude"):
    """Full path of the Claude Code executable, or None if it isn't installed."""
    found = shutil.which(os.path.expanduser(name))
    if found:
        return found
    if name == "claude":
        for path in claude_install_locations():
            if path.is_file():
                return str(path)
    return None


def resolve_bin(name):
    return find_claude(name) or name


def account_env(acc):
    """Environment for running Claude Code as one configured account."""
    env = os.environ.copy()
    if acc.get("config_dir"):
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(acc["config_dir"])
    env.update({k: str(v) for k, v in (acc.get("env") or {}).items()})
    return env


def run_quiet(cmd, env=None, timeout=60):
    """Run a short command; return (exit code, combined output) or (None, error)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def claude_version(claude_cmd, env=None):
    """'2.1.300 (Claude Code)' if claude_cmd really is Claude Code, else None."""
    code, out = run_quiet(list(claude_cmd) + ["--version"], env=env)
    if code is None or "Claude Code" not in out:
        return None
    return out.splitlines()[-1].strip()


def claude_auth_status(claude_cmd, env=None):
    """(True/False, details) from `claude auth status --json`; (None, error) if unknown."""
    code, out = run_quiet(list(claude_cmd) + ["auth", "status", "--json"], env=env)
    if code is None:
        return None, out
    start = out.find("{")
    try:
        data = json.loads(out[start:out.rfind("}") + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return None, out[-300:]
    return bool(data.get("loggedIn")), data


LOGIN_ERROR = re.compile(r"/login|invalid api key|not logged in|oauth token|authentication_error|"
                         r"please log in|unauthori[sz]ed", re.I)
LOGIN_HINT = ("\n\n🔑 Claude Code isn't signed in for this account. On the PC, double-click "
              "start.bat — it signs you in (or run: claude auth login).")


class InstanceLock:
    """Stops two copies of the bot polling Telegram with the same token."""

    def __init__(self, path):
        self.path = Path(path)
        self.fh = None

    def acquire(self):
        fh = open(self.path, "a+")
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self):
        if self.fh:
            self.fh.close()
            self.fh = None


# ---------------------------------------------------------------- telegram

class TelegramAPI:
    def __init__(self, token, poll_timeout=50):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.poll_timeout = poll_timeout

    def call(self, method, **params):
        req = urllib.request.Request(self.base + method, data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.poll_timeout + 15) as r:
            res = json.loads(r.read())
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {res}")
        return res["result"]

    def get_updates(self, offset):
        return self.call("getUpdates", offset=offset, timeout=self.poll_timeout,
                         allowed_updates=["message"])

    def send(self, chat_id, text):
        for chunk in split_message(text):
            try:
                self.call("sendMessage", chat_id=chat_id, text=chunk,
                          disable_web_page_preview=True)
            except Exception as e:  # never let a send failure kill a worker
                log.error("sendMessage to %s failed: %s", chat_id, e)

    def set_commands(self, commands):
        self.call("setMyCommands",
                  commands=[{"command": c, "description": d} for c, d in commands])


# ---------------------------------------------------------------- state

class State:
    """Persisted to JSON so links, sessions and usage survive restarts."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        else:
            self.data = {}
        self.data.setdefault("offset", 0)
        self.data.setdefault("chats", {})
        self.data.setdefault("accounts", {})

    def chat(self, chat_id):
        with self.lock:
            return self.data["chats"].setdefault(str(chat_id), {"account": None, "sessions": {}})

    def session(self, chat_id, account):
        """The active session record for (chat, account)."""
        with self.lock:
            c = self.chat(chat_id)
            return c["sessions"].setdefault(account, {"session_id": None, "stats": empty_stats(),
                                                      "history": []})

    def account_stats(self, account):
        with self.lock:
            return self.data["accounts"].setdefault(account, empty_stats())

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            for _ in range(10):
                try:
                    os.replace(tmp, self.path)
                    return
                except PermissionError:  # Windows: file briefly held by antivirus/indexer
                    time.sleep(0.2)
            log.warning("Could not save %s (file is locked by another program)", self.path)


# ---------------------------------------------------------------- bot

class Bot:
    def __init__(self, config, api, state):
        self.cfg = config
        self.api = api
        self.state = state
        self.accounts = config["accounts"]
        self.allowed_users = {int(u) for u in config.get("allowed_user_ids", [])}
        # Telegram handles (without @). The first user id seen for a handle is pinned in
        # state.json, so someone who later takes over the handle is still rejected.
        self.allowed_usernames = {str(u).lstrip("@").lower()
                                  for u in config.get("allowed_usernames", [])}
        self.allowed_chats = {int(c) for c in config.get("allowed_chat_ids", [])}
        claude_bin = config.get("claude_bin", "claude")
        # A string ("claude" or a path) or a list, e.g. ["node", "C:/path/cli.js"].
        self.claude_cmd = ([resolve_bin(claude_bin)] if isinstance(claude_bin, str)
                           else [resolve_bin(claude_bin[0])] + list(claude_bin[1:]))
        self.job_timeout = int(config.get("job_timeout_seconds", 3600))
        self.jobs = {}  # chat_id -> job dict
        self.jobs_lock = threading.Lock()

    # ----- auth

    def user_allowed(self, user_id, username):
        if user_id in self.allowed_users:
            return True
        uname = (username or "").lower()
        if not uname or uname not in self.allowed_usernames:
            return False
        with self.state.lock:
            pinned = self.state.data.setdefault("pinned_users", {})
            if uname not in pinned:
                pinned[uname] = user_id
                self.state.save()
                log.info("Pinned @%s to Telegram user id %s", uname, user_id)
                return True
        return pinned[uname] == user_id

    def authorized(self, user_id, chat_id, username=None):
        if not self.user_allowed(user_id, username):
            return False
        if self.allowed_chats and chat_id not in self.allowed_chats:
            return False
        return True

    # ----- dispatch

    def handle_update(self, update):
        msg = update.get("message")
        if not msg or "text" not in msg:
            return
        chat_id = msg["chat"]["id"]
        user = msg.get("from") or {}
        user_id = user.get("id")
        text = msg["text"].strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower() if cmd.startswith("/") else None
        arg = arg.strip()

        if cmd == "/whoami":
            self.api.send(chat_id, f"Your Telegram user id: {user_id}\nThis chat id: {chat_id}")
            return
        if not self.authorized(user_id, chat_id, user.get("username")):
            log.warning("Rejected message from user %s in chat %s", user_id, chat_id)
            if cmd == "/start":
                self.api.send(chat_id, f"Not authorized. Your Telegram user id is {user_id} — "
                                       "add it to allowed_user_ids (or your @handle to "
                                       "allowed_usernames) in config.json.")
            return

        handlers = {
            "/start": self.cmd_help, "/help": self.cmd_help,
            "/accounts": self.cmd_accounts, "/link": self.cmd_link, "/use": self.cmd_link,
            "/new": self.cmd_new, "/continue": self.cmd_continue, "/resume": self.cmd_resume,
            "/sessions": self.cmd_sessions, "/status": self.cmd_status,
            "/usage": self.cmd_usage, "/stop": self.cmd_stop,
        }
        if cmd is None:
            self.start_job(chat_id, text)
        elif cmd in handlers:
            handlers[cmd](chat_id, arg)
        else:
            self.api.send(chat_id, f"Unknown command {cmd}. /help for the list.")

    # ----- commands

    def cmd_help(self, chat_id, arg):
        acc = self.state.chat(chat_id)["account"]
        extra = f"\n\nThis chat is linked to: {acc}" if acc else "\n\nLink an account first: /link <account>"
        self.api.send(chat_id, HELP + extra)

    def cmd_accounts(self, chat_id, arg):
        current = self.state.chat(chat_id)["account"]
        lines = []
        for name, acc in self.accounts.items():
            mark = "▶ " if name == current else "• "
            lines.append(f"{mark}{name} — {acc.get('workdir', '.')}"
                         + (f" ({acc['model']})" if acc.get("model") else ""))
        self.api.send(chat_id, "Accounts:\n" + "\n".join(lines))

    def cmd_link(self, chat_id, arg):
        if arg not in self.accounts:
            self.api.send(chat_id, f"Usage: /link <account>\nAvailable: {', '.join(self.accounts)}")
            return
        with self.state.lock:
            self.state.chat(chat_id)["account"] = arg
            self.state.save()
        sid = self.state.session(chat_id, arg)["session_id"]
        self.api.send(chat_id, f"🔗 Chat linked to '{arg}'."
                      + (f" Continuing session {sid[:8]}…" if sid else " Next message starts a new session."))

    def _account_or_warn(self, chat_id):
        acc = self.state.chat(chat_id)["account"]
        if acc in self.accounts:
            return acc
        if len(self.accounts) == 1:  # nothing to choose: link the only account
            acc = next(iter(self.accounts))
            with self.state.lock:
                self.state.chat(chat_id)["account"] = acc
                self.state.save()
            self.api.send(chat_id, f"🔗 This chat is linked to '{acc}'.")
            return acc
        self.api.send(chat_id, f"No account linked. Use /link <account> ({', '.join(self.accounts)})")
        return None

    def cmd_new(self, chat_id, arg):
        acc = self._account_or_warn(chat_id)
        if not acc:
            return
        with self.state.lock:
            s = self.state.session(chat_id, acc)
            if s["session_id"]:
                s["history"].append({"session_id": s["session_id"], "stats": s["stats"],
                                     "ended": int(time.time())})
            s["session_id"] = None
            s["stats"] = empty_stats()
            self.state.save()
        if arg:
            self.start_job(chat_id, arg)
        else:
            self.api.send(chat_id, f"🆕 Fresh session on '{acc}'. Your next message starts it.")

    def cmd_continue(self, chat_id, arg):
        self.start_job(chat_id, arg or "Continue.")

    def cmd_resume(self, chat_id, arg):
        acc = self._account_or_warn(chat_id)
        if not acc:
            return
        if not arg:
            self.api.send(chat_id, "Usage: /resume <session_id>")
            return
        with self.state.lock:
            s = self.state.session(chat_id, acc)
            if s["session_id"] and s["session_id"] != arg:
                s["history"].append({"session_id": s["session_id"], "stats": s["stats"],
                                     "ended": int(time.time())})
                s["stats"] = empty_stats()
            s["session_id"] = arg
            self.state.save()
        self.api.send(chat_id, f"↪️ Attached to session {arg} on '{acc}'. Send a message to continue it.")

    def cmd_sessions(self, chat_id, arg):
        c = self.state.chat(chat_id)
        if not c["sessions"]:
            self.api.send(chat_id, "No sessions yet.")
            return
        lines = []
        for acc, s in c["sessions"].items():
            cur = s["session_id"] or "(new on next message)"
            lines.append(f"{acc}: current {cur} · ${s['stats']['cost_usd']:.4f}")
            for h in s["history"][-5:]:
                lines.append(f"   past {h['session_id']} · ${h['stats']['cost_usd']:.4f}")
        self.api.send(chat_id, "Sessions:\n" + "\n".join(lines))

    def cmd_status(self, chat_id, arg):
        acc = self.state.chat(chat_id)["account"]
        lines = [f"Account: {acc or '(none linked)'}"]
        if acc:
            s = self.state.session(chat_id, acc)
            lines.append(f"Session: {s['session_id'] or '(new on next message)'}")
            lines.append(f"Workdir: {self.accounts[acc].get('workdir', '.')}")
        with self.jobs_lock:
            job = self.jobs.get(chat_id)
        if job:
            lines.append(f"⏳ Running for {fmt_duration(time.time() - job['started'])}: "
                         f"{job['prompt'][:200]}")
        else:
            lines.append("Idle.")
        with self.jobs_lock:
            others = [f"{cid} ({j['account']})" for cid, j in self.jobs.items() if cid != chat_id]
        if others:
            lines.append("Also running in: " + ", ".join(others))
        self.api.send(chat_id, "\n".join(lines))

    def cmd_usage(self, chat_id, arg):
        acc = self._account_or_warn(chat_id)
        if not acc:
            return
        s = self.state.session(chat_id, acc)
        msg = (f"📊 Session {(s['session_id'] or 'new')[:8]} on '{acc}'\n{fmt_stats(s['stats'])}\n\n"
               f"Account '{acc}' total (via this bot)\n{fmt_stats(self.state.account_stats(acc))}")
        if s.get("last_context_tokens"):
            msg += f"\n\nLast turn context: {fmt_tokens(s['last_context_tokens'])} tokens"
            if s.get("context_window"):
                pct = 100 * s["last_context_tokens"] / s["context_window"]
                msg += f" of {fmt_tokens(s['context_window'])} ({pct:.1f}%)"
        if s.get("models"):
            msg += f"\nModels: {', '.join(s['models'])}"
        self.api.send(chat_id, msg)

    def cmd_stop(self, chat_id, arg):
        with self.jobs_lock:
            job = self.jobs.get(chat_id)
        if not job:
            self.api.send(chat_id, "Nothing is running.")
            return
        job["stopped"] = True
        # Announce first: on Windows the kill is synchronous, so the worker's
        # "Stopped after …" message could otherwise arrive before this one.
        self.api.send(chat_id, "🛑 Stopping…")
        proc = job.get("proc")
        if proc:
            kill_tree(proc)

    # ----- jobs

    def start_job(self, chat_id, prompt):
        acc = self._account_or_warn(chat_id)
        if not acc:
            return
        with self.jobs_lock:
            if chat_id in self.jobs:
                self.api.send(chat_id, "⏳ Claude is still working in this chat. /status or /stop.")
                return
            job = {"account": acc, "prompt": prompt, "started": time.time(), "proc": None,
                   "stopped": False}
            self.jobs[chat_id] = job
        # Announce before starting, so a fast result can't arrive ahead of this message.
        self.api.send(chat_id, f"⏳ Working on it ({acc})…")
        t = threading.Thread(target=self._run_job, args=(chat_id, job), daemon=True)
        job["thread"] = t
        t.start()

    def build_command(self, account, session_id):
        acc = self.accounts[account]
        cmd = self.claude_cmd + ["-p", "--output-format", "json"]
        if session_id:
            cmd += ["--resume", session_id]
        if acc.get("model"):
            cmd += ["--model", acc["model"]]
        if acc.get("permission_mode"):
            cmd += ["--permission-mode", acc["permission_mode"]]
        if acc.get("allowed_tools"):
            cmd += ["--allowedTools", ",".join(acc["allowed_tools"])]
        cmd += list(acc.get("extra_args", []))
        cwd = os.path.expanduser(acc.get("workdir", "."))
        return cmd, account_env(acc), cwd

    def _run_job(self, chat_id, job):
        account = job["account"]
        try:
            session_id = self.state.session(chat_id, account)["session_id"]
            cmd, env, cwd = self.build_command(account, session_id)
            log.info("chat %s / %s: running %s", chat_id, account, cmd)
            if not os.path.isdir(cwd):
                self.api.send(chat_id, f"❌ The workdir for '{account}' doesn't exist: {cwd}")
                return
            try:
                # Prompt goes over stdin so text starting with "-" is never read as a flag.
                proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace",
                                        **popen_kwargs())
            except FileNotFoundError:
                self.api.send(chat_id, "❌ Claude Code isn't installed on the PC. Double-click "
                                       "start.bat there — it installs it.")
                return
            job["proc"] = proc
            try:
                out, err = proc.communicate(job["prompt"], timeout=self.job_timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                out, err = proc.communicate()
                self.api.send(chat_id, f"⌛ Timed out after {fmt_duration(self.job_timeout)} ({account}).")
                return
            elapsed = time.time() - job["started"]
            if job["stopped"]:
                self.api.send(chat_id, f"🛑 Stopped after {fmt_duration(elapsed)} ({account}).")
                return
            result = parse_claude_output(out)
            if result is None:
                tail = (err or out or "").strip()[-1500:]
                log.warning("chat %s / %s: claude exited %s: %s", chat_id, account, proc.returncode, tail)
                hint = LOGIN_HINT if LOGIN_ERROR.search(tail) else ""
                self.api.send(chat_id, f"❌ Claude exited with code {proc.returncode} ({account}).\n\n{tail}{hint}")
                return
            self._record(chat_id, account, result)
            icon = "❌" if result.get("is_error") else "✅"
            header = (f"{icon} {account} · {fmt_duration(elapsed)} · "
                      f"${float(result.get('total_cost_usd') or 0):.4f} · "
                      f"{result.get('num_turns', '?')} turns")
            body = result.get("result") or result.get("error") or f"(no text; subtype {result.get('subtype')})"
            if result.get("is_error"):
                log.warning("chat %s / %s: Claude reported an error: %s", chat_id, account, str(body)[:500])
                if LOGIN_ERROR.search(str(body)):
                    body = f"{body}{LOGIN_HINT}"
            else:
                log.info("chat %s / %s: done in %s", chat_id, account, fmt_duration(elapsed))
            self.api.send(chat_id, f"{header}\n\n{body}")
        except Exception as e:
            log.exception("job failed")
            self.api.send(chat_id, f"❌ Bot error: {e}")
        finally:
            with self.jobs_lock:
                self.jobs.pop(chat_id, None)

    def _record(self, chat_id, account, result):
        with self.state.lock:
            s = self.state.session(chat_id, account)
            new_sid = result.get("session_id")
            if new_sid:
                s["session_id"] = new_sid
            add_stats(s["stats"], result)
            add_stats(self.state.account_stats(account), result)
            usage = result.get("usage") or {}
            s["last_context_tokens"] = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            models = result.get("modelUsage") or {}
            if models:
                s["models"] = sorted(models)
                s["context_window"] = max(int(m.get("contextWindow") or 0) for m in models.values())
            self.state.save()

    # ----- loop

    def notify_all(self, text):
        for chat_id, c in self.state.data["chats"].items():
            if c.get("account"):
                self.api.send(int(chat_id), text)

    def check_claude(self):
        """Log whether Claude Code is installed and signed in for each account."""
        version = claude_version(self.claude_cmd)
        if not version:
            log.error("Claude Code was not found (%s). Double-click start.bat to install it.",
                      self.claude_cmd[0])
            return False
        log.info("Claude Code %s at %s", version, self.claude_cmd[0])
        ok = True
        for name, acc in self.accounts.items():
            signed_in, info = claude_auth_status(self.claude_cmd, account_env(acc))
            if signed_in:
                log.info("Account '%s': signed in (%s)", name, info.get("authMethod", "?"))
            elif signed_in is False:
                ok = False
                log.error("Account '%s': Claude Code is NOT signed in. Double-click start.bat "
                          "(or run: claude auth login).", name)
            else:
                log.warning("Account '%s': could not read sign-in status: %s", name, info)
            workdir = os.path.expanduser(acc.get("workdir", "."))
            if not os.path.isdir(workdir):
                log.error("Account '%s': workdir does not exist: %s", name, workdir)
        return ok

    def run_forever(self):
        try:
            self.api.set_commands(BOT_COMMANDS)
        except Exception as e:
            log.warning("setMyCommands failed: %s", e)
        log.info("Bot running. Accounts: %s", ", ".join(self.accounts))
        offset = self.state.data.get("offset", 0)
        while True:
            try:
                updates = self.api.get_updates(offset)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    log.error("Telegram rejected the bot token (401). Double-click start.bat "
                              "to enter a new one.")
                    raise SystemExit(2)
                if e.code == 409:
                    log.error("Another copy of this bot is running with the same token "
                              "(Telegram 409 Conflict). Close it; retrying in 30s.")
                    time.sleep(30)
                else:
                    log.warning("getUpdates failed: %s; retrying in 5s", e)
                    time.sleep(5)
                continue
            except Exception as e:
                log.warning("getUpdates failed: %s; retrying in 5s", e)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle_update(u)
                except Exception:
                    log.exception("handling update failed")
            if updates:
                with self.state.lock:
                    self.state.data["offset"] = offset
                    self.state.save()


def load_config(path):
    # utf-8-sig: Windows Notepad may save the file with a byte-order mark.
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token")
    if not cfg.get("telegram_bot_token") or "PASTE" in cfg["telegram_bot_token"]:
        sys.exit("Set telegram_bot_token in config.json or TELEGRAM_BOT_TOKEN "
                 "(on Windows, double-click start.bat - it asks for it).")
    if not cfg.get("allowed_user_ids") and not cfg.get("allowed_usernames"):
        sys.exit("Nobody is allowed to use the bot. Add your Telegram @handle to allowed_usernames "
                 "(or your numeric id to allowed_user_ids) in config.json.")
    if not cfg.get("accounts"):
        sys.exit("Define at least one entry under accounts.")
    return cfg


def setup_logging(log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)


def start(cfg_path, cfg=None):
    """Run the bot until it is stopped. Returns a process exit code."""
    cfg_path = Path(cfg_path).resolve()
    base = cfg_path.parent
    setup_logging(base / "bot.log")
    cfg = cfg or load_config(cfg_path)
    lock = InstanceLock(base / "bot.lock")
    if not lock.acquire():
        log.error("The bot is already running in another window. Use that one, or close it first.")
        return 3
    try:
        api = TelegramAPI(cfg["telegram_bot_token"])
        try:
            me = api.call("getMe")
            log.info("Telegram bot @%s is online: https://t.me/%s", me.get("username"), me.get("username"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 404):
                log.error("Telegram rejected the bot token. Double-click start.bat to enter a new one.")
                return 2
            log.warning("Could not reach Telegram yet (%s); will keep retrying.", e)
        except Exception as e:
            log.warning("Could not reach Telegram yet (%s); will keep retrying.", e)
        state_file = Path(cfg.get("state_file", "state.json"))
        state = State(state_file if state_file.is_absolute() else base / state_file)
        bot = Bot(cfg, api, state)
        bot.check_claude()
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    finally:
        lock.release()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    return start(argv[0] if argv else "config.json")


if __name__ == "__main__":
    sys.exit(main())
