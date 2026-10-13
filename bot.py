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
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("claude-tg")

TG_LIMIT = 4000  # Telegram max is 4096 chars per message; leave headroom

HELP = """Claude ⇄ Telegram

Just send a message → it goes to Claude, continuing the current session.

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


def resolve_bin(name):
    """On Windows `claude` is claude.cmd; Popen needs the full path to find it."""
    return shutil.which(os.path.expanduser(name)) or name


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
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
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
            os.replace(tmp, self.path)


# ---------------------------------------------------------------- bot

class Bot:
    def __init__(self, config, api, state):
        self.cfg = config
        self.api = api
        self.state = state
        self.accounts = config["accounts"]
        self.allowed_users = {int(u) for u in config.get("allowed_user_ids", [])}
        self.allowed_chats = {int(c) for c in config.get("allowed_chat_ids", [])}
        claude_bin = config.get("claude_bin", "claude")
        # A string ("claude" or a path) or a list, e.g. ["node", "C:/path/cli.js"].
        self.claude_cmd = ([resolve_bin(claude_bin)] if isinstance(claude_bin, str)
                           else [resolve_bin(claude_bin[0])] + list(claude_bin[1:]))
        self.job_timeout = int(config.get("job_timeout_seconds", 3600))
        self.jobs = {}  # chat_id -> job dict
        self.jobs_lock = threading.Lock()

    # ----- auth

    def authorized(self, user_id, chat_id):
        if user_id not in self.allowed_users:
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
        if not self.authorized(user_id, chat_id):
            log.warning("Rejected message from user %s in chat %s", user_id, chat_id)
            if cmd == "/start":
                self.api.send(chat_id, f"Not authorized. Your Telegram user id is {user_id} — "
                                       "add it to allowed_user_ids in config.json.")
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
        if not acc or acc not in self.accounts:
            self.api.send(chat_id, f"No account linked. Use /link <account> ({', '.join(self.accounts)})")
            return None
        return acc

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
        proc = job.get("proc")
        if proc:
            kill_tree(proc)
        self.api.send(chat_id, "🛑 Stopping…")

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
        t = threading.Thread(target=self._run_job, args=(chat_id, job), daemon=True)
        job["thread"] = t
        t.start()
        self.api.send(chat_id, f"⏳ Working on it ({acc})…")

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
        env = os.environ.copy()
        if acc.get("config_dir"):
            env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(acc["config_dir"])
        env.update({k: str(v) for k, v in (acc.get("env") or {}).items()})
        cwd = os.path.expanduser(acc.get("workdir", "."))
        return cmd, env, cwd

    def _run_job(self, chat_id, job):
        account = job["account"]
        try:
            session_id = self.state.session(chat_id, account)["session_id"]
            cmd, env, cwd = self.build_command(account, session_id)
            log.info("chat %s / %s: running %s", chat_id, account, cmd)
            try:
                # Prompt goes over stdin so text starting with "-" is never read as a flag.
                proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace",
                                        **popen_kwargs())
            except FileNotFoundError as e:
                self.api.send(chat_id, f"❌ Could not start Claude: {e}")
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
                self.api.send(chat_id, f"❌ Claude exited with code {proc.returncode} ({account}).\n\n{tail}")
                return
            self._record(chat_id, account, result)
            icon = "❌" if result.get("is_error") else "✅"
            header = (f"{icon} {account} · {fmt_duration(elapsed)} · "
                      f"${float(result.get('total_cost_usd') or 0):.4f} · "
                      f"{result.get('num_turns', '?')} turns")
            body = result.get("result") or result.get("error") or f"(no text; subtype {result.get('subtype')})"
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
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token")
    if not cfg.get("telegram_bot_token") or "PASTE" in cfg["telegram_bot_token"]:
        sys.exit("Set telegram_bot_token in config.json or TELEGRAM_BOT_TOKEN.")
    if not cfg.get("allowed_user_ids"):
        sys.exit("allowed_user_ids is empty — nobody could use the bot. Add your Telegram user id.")
    if not cfg.get("accounts"):
        sys.exit("Define at least one entry under accounts.")
    return cfg


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    cfg = load_config(cfg_path)
    state = State(cfg.get("state_file", "state.json"))
    bot = Bot(cfg, TelegramAPI(cfg["telegram_bot_token"]), state)
    bot.run_forever()


if __name__ == "__main__":
    main()
