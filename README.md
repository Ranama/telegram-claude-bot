# Claude ⇄ Telegram

[![tests](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml)

Get a Telegram message when Claude Code on your PC finishes a task (✅ with a one-line description) or needs you (❓ input or a question, 🔐 approval). Optionally, also control Claude from Telegram: start tasks, continue sessions, answer questions with buttons. Only Telegram users you approve can use it.

## Two modes

- **Notifications only** (default, `"telegram_control": false`): one short message when a session on the PC finishes or needs you. Messages you send the bot don't run anything.
- **Control from Telegram** (`"telegram_control": true` in `config.json`, then restart the bot): everything below — run tasks, `/list` and `/enter` sessions, reply to continue, answer permissions and questions with buttons, `/away`.

Python 3.8+ standard library only — nothing to `pip install`.

## Quick start (Windows)

1. Double-click **`start.bat`**. In that one window it:
   - installs **Claude Code** if it isn't installed yet (Anthropic's official installer from claude.ai — the Claude desktop app doesn't include the command-line version the bot needs),
   - signs Claude Code in if needed (your browser opens once: sign in and click *Authorize*),
   - asks once for your **bot token** and checks it with Telegram: in Telegram, message **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`, and paste the token it gives you (it stays hidden while you paste),
   - starts the bot and shows its `t.me/...` link.
2. Open the link in Telegram and send a message, e.g. `hello`.

Keep the window open — closing it stops the bot. Next time, `start.bat` skips the steps that are already done and starts the bot straight away. Everything is logged to `setup.log` and `bot.log` in this folder (the token is never logged).

## Work in your PC sessions from Telegram — control mode

Every Claude Code session on the PC — started in the desktop app's Code tab, a terminal, or by this bot — can be continued from Telegram, without starting a new one:

```
/list            💻 sessions on the PC, newest first (🟢 = active right now)
/enter 2         go into session 2 — shows Claude's last answer
fix the failing test     ← goes to that session; you get Claude's answer back
/history         the last messages of the session
/leave           back to the bot's own chat
```

Sessions are looked up in `~/.claude/projects` (and every account's profile) and in the Claude desktop app's own data folders; the folders found are listed in `bot.log` at startup. Sessions whose folder isn't on this PC (for example from the app's sandbox) are shown as *view only*. `/enter` also takes a session id (or its first characters). While you're in a session, `/continue` and `/status` apply to it, and `/new` leaves it. Claude runs in the session's own folder and Claude profile, with the bot's permission settings. If a session is 🟢 (busy on the PC right now), let it finish before you send something.

## Answer Claude from Telegram (buttons) — control mode

When Claude needs **permission** for something (run a command, edit a file) or asks you a **multiple-choice question**, you can answer in Telegram:

- 🔐 *Claude wants to run a command: `npm test`* → **✅ Allow** / **❌ Deny** — or reply with text to deny and tell Claude what to do instead.
- ❓ *Which database should I use?* → one button per option (multi-choice: tick several, then **✅ Done**) — or reply with your own answer.

The session waits, checking every second, until you answer (default up to 10 minutes, `answer_timeout_seconds` in `config.json`); after that it asks on the PC as usual.

- **Tasks you start from Telegram** always ask you here.
- **Sessions on the PC** (desktop app, terminal) ask on the PC — until you send **`/away on`**; then they ask you here too. Send `/away off` when you're back.

This uses Claude Code's `PermissionRequest` hook, installed together with the notification hooks below.

## Messages from your own Claude Code sessions

`start.bat` asks once: *"Should Claude Code sessions you run on this PC message you on Telegram?"* If you say yes, it adds two [hooks](https://code.claude.com/docs/en/hooks) to your Claude Code settings (`~/.claude/settings.json`, with a backup next to it). From then on, every Claude Code session on the PC — the desktop app's Code tab or the terminal — messages you when it:

- **finishes** (✅ with Claude's full answer — long ones are split over several messages), or
- **waits for you** (❓ waiting for your input, 🔐 needs permission — approve that one on the PC).

**Reply to the message** in Telegram and the bot continues that session with your answer (in the session's own folder and Claude profile); reply to the bot's answer to keep going. `/reply <text>` answers the most recent one. `/notify off` / `/notify on` pauses and resumes these messages; `python setup_bot.py --remove-hooks` removes the hooks. Sessions that are already open pick the hooks up after you restart them.

Note: a reply runs as a separate Claude Code process. If the desktop app still has that session open, it shows the new turns after you reopen the session.

## Send yourself a message from anything on the PC

- **Outbox:** drop a UTF-8 `.txt` file into the `outbox` folder next to `bot.py`. The running bot sends its text to your chat within a few seconds and deletes the file (after 5 failed tries it is renamed to `.failed`).
- **From a script:** `python notify.py --send "Backup finished"` sends right away (no running bot needed).

## Quick start (macOS / Linux)

```bash
python3 setup_bot.py        # same steps as start.bat, then starts the bot
```

or set up `config.json` by hand (see below) and run `python3 bot.py config.json`.

## How it fits together

- **Account** = one Claude Code login profile (its own `CLAUDE_CONFIG_DIR`) plus a working folder.
- **Chat** = one Telegram chat linked to one account. With a single account, chats are linked automatically; with several, use `/link work` (use separate chats or groups to run accounts in parallel).
- **Session** = a Claude Code conversation. Messages in a chat continue its session until you send `/new`.

## Commands

| Command | What it does |
|---|---|
| *(any text)* | Send to Claude, continuing the current session |
| `/link <account>` | Link this chat to an account |
| `/accounts` | List accounts |
| `/new [text]` | Start a fresh session (optionally with a first prompt) |
| `/continue [text]` | Continue the session ("Continue." by default) |
| `/list [n]` | Claude Code sessions on the PC, newest first |
| `/enter <number or id>` | Go into a PC session; what you send goes to it (`/resume` does the same) |
| `/history [n]` | Last messages of the session you're in |
| `/leave` | Leave the PC session |
| `/sessions` | Current and past sessions in this chat |
| `/status` | Account, session, workdir, and what's running |
| `/usage` | Runs, turns, cost, tokens, context fill % and models — for the session and the account |
| `/stop` | Stop the running job |
| `/notify on\|off` | Messages from your own Claude Code sessions on the PC |
| `/away on\|off` | PC sessions ask you here (buttons) for permissions and questions |
| `/reply <text>` | Answer the latest PC session that messaged you (or just reply to its message) |
| `/whoami` | Show your Telegram user id (works for anyone) |

## More Claude accounts

Each extra account needs its own Claude Code profile folder. Add it to `config.json` with a `config_dir`, then run `start.bat` (or `setup_bot.py`) again — it signs in every account that isn't signed in yet:

```json
"accounts": {
  "main": { "workdir": "D:/Claude", "permission_mode": "acceptEdits" },
  "work": { "config_dir": "~/.claude-work", "workdir": "D:/Work", "model": "sonnet" }
}
```

## Config reference

Top level: `telegram_bot_token`, `allowed_usernames` (Telegram handles; the first user id seen for a handle is pinned in `state.json`, so someone who later takes the handle can't use the bot) and/or `allowed_user_ids`, `allowed_chat_ids` (optional extra lock), `job_timeout_seconds`, `state_file`, `claude_bin` (a command name/path, or a list such as `["node", "C:/path/to/cli.js"]`).

Per account:

| Key | Meaning |
|---|---|
| `config_dir` | Claude Code profile folder → this is what makes it a different Claude account |
| `workdir` | Folder Claude works in |
| `model` | e.g. `sonnet`, `opus` |
| `permission_mode` | `default`, `acceptEdits`, `plan`, or `bypassPermissions` |
| `allowed_tools` | Tools allowed without asking, e.g. `["Read", "Edit", "Bash(npm test:*)"]` |
| `extra_args` | Any other `claude` CLI flags |
| `env` | Extra environment variables (e.g. `ANTHROPIC_API_KEY` for an API-key account) |

## Notes

- **Permissions**: Claude runs non-interactively, so it can't ask you to approve a tool mid-run. Anything not allowed by `permission_mode` / `allowed_tools` is denied. Start with `acceptEdits` + a small `allowed_tools` list; `bypassPermissions` lets Claude run anything on that machine — only use it in a sandbox.
- **Usage** is counted from what this bot ran. Your plan's 5-hour / weekly limits aren't exposed by the CLI in print mode, so they aren't shown.
- One job at a time per chat; different chats run in parallel. Only one copy of the bot can run per folder.
- If Claude Code gets signed out, the bot says so in Telegram; run `start.bat` again to sign back in.
- Keep `config.json` private — the token in it controls the bot.

## Files

| File | Purpose |
|---|---|
| `start.bat` | Windows: double-click to set up and start |
| `setup_bot.py` | The setup steps (install/sign in Claude Code, bot token), then starts the bot |
| `bot.py` | The bot |
| `notify.py` | Claude Code hook: messages you when a PC session finishes or waits |
| `sessions.py` | Finds and reads the Claude Code sessions stored on the PC |
| `outbox/` | Text files put here are sent to you on Telegram |
| `config.example.json` | Config template — `config.json` is created from it (git-ignored) |
| `tests/` | Tests with a fake Telegram and a fake `claude` |

## Tests

`python tests/test_bot.py`, `test_setup.py`, `test_notify.py` and `test_sessions.py` — run on Linux and Windows, Python 3.9+ (also in GitHub Actions on every push).

## License

MIT
