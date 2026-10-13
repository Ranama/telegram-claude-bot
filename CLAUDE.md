# Claude → Telegram notifications

This repo makes every Claude Code session on this computer message the user on Telegram:
✅ when a task is done (one-line summary), ❓ when Claude needs input, 🔐 when it needs
approval — each with the session (folder + title) and the Claude account. It also keeps a
lock/status file per session, which `/status` in Telegram lists.

## When the user asks you to set this up ("use this", "turn on Telegram notifications")

1. Check Python 3.8+ (`python --version`, or `py -3 --version` on Windows). No packages needed.
2. If `config.json` has no `telegram_bot_token`: ask the user to create a bot — in Telegram,
   open **@BotFather**, send `/newbot`, choose a name and a username ending in `bot` — and to
   give you the token it replies with (or to run `python install.py` themselves in a terminal).
3. Run, from this folder: `python install.py --token <token>`
   It prints a `t.me/...` link and waits up to 3 minutes: tell the user to open it and send the
   bot any message. That links their chat, installs the hooks in `~/.claude/settings.json`
   (a backup is kept next to it) and sends a test message.
   If `config.json` already has the token and chat, just run `python install.py`.
4. Tell the user: notifications now work for all Claude Code sessions on this computer
   (terminal, and the desktop app's Code tab with a Local folder). For `/status` in Telegram,
   keep `python bot.py` running (on Windows: double-click `start.bat`).

Another Claude account (a different `CLAUDE_CONFIG_DIR`): `python install.py --profile <that folder>`.
Remove everything again: `python install.py --uninstall`.

## While working on any task

You don't need to do anything: the hooks notify the user automatically when you finish or
ask something. If the user must do something outside Claude Code before you can continue
(log in somewhere, plug in a device, approve a payment), also tell them directly:
`python <this folder>/hook.py send "What you need them to do"`.

## Files

`hook.py` (the hook), `status.py` (lock + status files), `tg.py` (Telegram API),
`install.py` (setup), `bot.py` (optional, answers /status), `start.bat` (Windows: setup + bot).
Tests: `python -m unittest discover tests`.
