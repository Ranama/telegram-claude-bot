# Claude → Telegram notifications

[![tests](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml)

Every Claude session on your computer messages you on Telegram when it's done or needs you, and `/status` shows what each one is doing.

## Quick start

**Install on a new machine** (Python 3.8+ and git; on Windows you can double-click `start.bat` instead of the last line):

```bash
git clone https://github.com/Ranama/telegram-claude-bot
cd telegram-claude-bot
python install.py
```

…or paste this into Claude Code and it does it for you:

```text
Clone https://github.com/Ranama/telegram-claude-bot and set up the Telegram notifications as its CLAUDE.md describes.
```

**Add a new session.** Claude Code sessions on that computer need nothing — they report automatically. For a **cloud session** (a chat in the Claude app, linked to your computer), paste this as its first message or into your project/personal instructions (change the path to where you cloned the repo):

```text
When you're linked to my computer: report your status for my Telegram /status as described in the "Cloud sessions" section of D:\Claude\telegram-claude-bot\CLAUDE.md — write your status file when you start a task, when you need me, and when you're done.
```

**Add another Claude account** on the same computer (its own `CLAUDE_CONFIG_DIR` folder):

```bash
python install.py --profile "<path to that account's Claude folder>"
```

Then keep the bot running for `/status`: `python bot.py` (Windows: `start.bat`).

## What you get

Messages look like this (every one with its time):

```
🟡 Started — VineTrip · “Login bug fix”
👤 you@example.com
Fix the login bug
🕒 15 Oct 14:02:10

✅ Completed — VineTrip · “Login bug fix”
👤 you@example.com
Added the login page and wrote tests for it.
🕒 15 Oct 14:10:42

❓ Needs input — VineTrip · “Login bug fix”
👤 you@example.com
Claude asks: Which database? (Postgres, SQLite)
🕒 15 Oct 14:05:31

🔐 Needs approval — shop · “Deploy script”
👤 work@example.com
Claude wants to run: npm run deploy
🕒 15 Oct 14:21:07
```

`/status` in Telegram lists all sessions of all your Claude accounts on the computer, most urgent first, with a 🔄 Refresh button:

```
📋 Claude sessions
👤 you@example.com
🔴 1 needs you · 🟡 1 working · 🟢 1 done

🔴 Login bug fix
Asks you: Which database? (Postgres, SQLite)
📁 VineTrip · started 14:02

🟡 Deploy script
Working on: Run the tests and deploy
📁 shop · started 14:20 · last done 13:55

🟢 Write a README for notes
Done: I wrote README.md with setup steps.
📁 Claude · started 12:58 · done 13:04
```

🔴 needs you (a question or an approval) · 🟡 working · 🟢 done · 🟠 no activity for 20+ min · ⚪ closed. Sessions are named by Claude Code's title, or by your first message cleaned up (file names instead of paths, no "can you please").

Every session has one small status file, `status/sessions/<id>.json`: what it's working on, its state, title, folder and account. Claude Code sessions on your computer write theirs automatically (hooks). **Cloud sessions** (e.g. chats in the Claude app) can't run hooks on your computer; when they're linked to it, they write the file themselves. Tell them to, e.g. in your project's or personal instructions (with the path of your copy):

> When you're linked to my computer: report your status for my Telegram /status as described in the "Cloud sessions" section of `D:\Claude\telegram-claude-bot\CLAUDE.md` — write your status file when you start a task, when you need me, and when you're done.

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
| UserPromptSubmit (you send a command) | created | 🟡 working | 🟡 message |
| PermissionRequest | | 🔴 needs input / approval | ❓ / 🔐 message |
| Notification (input needed) | | 🔴 needs input | ❓ message |
| Stop (Claude is done) | deleted | 🟢 completed | ✅ message |
| SessionEnd | deleted | ⚪ closed | — |

Lock files are `status/locks/<session>.lock`; the latest state per session is `status/sessions/<session>.json` (the same file cloud sessions write — see [CLAUDE.md](CLAUDE.md) for its format). Approving something on the PC counts as working again once Claude continues; a working session without activity for 20 minutes shows as 🟠 inactive (e.g. after an interrupt).

Only your chat gets messages and answers (`chat_id` in `config.json`, git-ignored with the token).

## Files

| File | |
|---|---|
| `hook.py` | The hook: status + Telegram message |
| `status.py` | Lock and status files, the `/status` list |
| `tg.py` | Telegram API, `config.json` |
| `install.py` | Setup / uninstall |
| `bot.py` | Answers `/status`; sends text files dropped into `outbox/` |
| `start.bat` | Windows: setup if needed, then `bot.py` |
| `CLAUDE.md` | Instructions for Claude |

Tests: `python -m unittest discover tests`

MIT License
