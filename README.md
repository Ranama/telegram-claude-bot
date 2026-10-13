# Claude → Telegram notifications

[![tests](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml)

Every Claude Code session on your computer messages you on Telegram when it's done or needs you:

```
✅ Completed — VineTrip · “Login bug fix”
👤 you@example.com
Added the login page and wrote tests for it.

❓ Needs input — VineTrip · “Login bug fix”
👤 you@example.com
Claude asks: Which database? (Postgres, SQLite)

🔐 Needs approval — shop · “Deploy script”
👤 work@example.com
Claude wants to run: npm run deploy
```

`/status` in Telegram lists all sessions of all your Claude accounts on the computer, most urgent first, with a 🔄 Refresh button:

```
📋 Claude sessions
👤 you@example.com
🔴 1 needs you · 🟡 1 working · 🟢 1 done

🔴 Login bug fix
Asks you: Which database? (Postgres, SQLite)
📁 VineTrip · 2 min ago

🟡 Deploy script
Working on: Run the tests and deploy
📁 shop · just now

🟢 Write a README for notes
Done: I wrote README.md with setup steps.
📁 Claude · 1 h ago
```

🔴 needs you (a question or an approval) · 🟡 working · 🟢 done · 🟠 no activity for 20+ min · ⚪ closed. Sessions are named by Claude Code's title, or by your first message cleaned up (file names instead of paths, no "can you please").

Python 3.8+ standard library only. Windows, macOS, Linux.

## Set up

```bash
git clone https://github.com/Ranama/telegram-claude-bot
cd telegram-claude-bot
python install.py
```

Or open the folder in Claude Code and say **"set up the Telegram notifications"** — `CLAUDE.md` tells Claude what to do.

`install.py` asks for a bot token (Telegram → **@BotFather** → `/newbot`), waits for you to send your new bot any message (that links your chat), adds the hooks to `~/.claude/settings.json` and sends a test message. From then on all Claude Code sessions on the computer — terminal and the desktop app's Code tab (Local) — notify you. Nothing needs to keep running for that.

For `/status`, keep `python bot.py` running (Windows: double-click `start.bat`, which also runs the setup the first time).

- Another Claude account (its own `CLAUDE_CONFIG_DIR`): `python install.py --profile <folder>`
- Send yourself a message from a script: `python hook.py send "Backup finished"`
- Remove: `python install.py --uninstall`

## How it works

Claude Code [hooks](https://code.claude.com/docs/en/hooks) run `hook.py` on these events:

| Event | Lock file | State | Telegram |
|---|---|---|---|
| UserPromptSubmit (you send a command) | created | 🟡 working | — |
| PermissionRequest | | 🔴 needs input / approval | ❓ / 🔐 message |
| Notification (input needed) | | 🔴 needs input | ❓ message |
| Stop (Claude is done) | deleted | 🟢 completed | ✅ message |
| SessionEnd | deleted | ⚪ closed | — |

Lock files are `status/locks/<session>.lock`; the latest state per session is `status/sessions/<session>.json`. Approving something on the PC counts as working again once Claude continues; a lock without activity for 20 minutes shows as 🟠 inactive (e.g. after an interrupt).

Only your chat gets messages and answers (`chat_id` in `config.json`, git-ignored with the token).

## Files

| File | |
|---|---|
| `hook.py` | The hook: status + Telegram message |
| `status.py` | Lock and status files, the `/status` list |
| `tg.py` | Telegram API, `config.json` |
| `install.py` | Setup / uninstall |
| `bot.py` | Optional: answers `/status` |
| `start.bat` | Windows: setup if needed, then `bot.py` |
| `CLAUDE.md` | Instructions for Claude |

Tests: `python -m unittest discover tests`

MIT License
