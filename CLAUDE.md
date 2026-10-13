# Claude → Telegram notifications

This repo makes every Claude Code session on this computer message the user on Telegram:
✅ when a task is done (one-line summary), ❓ when Claude needs input, 🔐 when it needs
approval — each with the session (folder + title) and the Claude account. It also keeps a
status file per session (`status/sessions/<id>.json`), which `/status` in Telegram lists:
🔴 needs you, 🟡 working, 🟢 done.

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

## Cloud sessions: report your status yourself

The hooks only run for Claude Code sessions on this computer. If you run in the cloud but can
write files on this computer (through its link to the Claude desktop app), `/status` only sees
you if you write your own status file: `status/sessions/<id>.json` in this folder. Choose the
id once per conversation (e.g. `cloud-` + your session id) and overwrite the same file each
time — never delete it:

    {"session_id": "<id>", "where": "cloud", "title": "<short name of this conversation>",
     "project": "<repo or folder you work in>", "account": "<the user's Claude account email>",
     "state": "working", "task": "<what you're doing right now, one line>",
     "detail": "", "summary": ""}

- When you start a task: `"state": "working"` and the `task`.
- When you need the user: `"state": "needs_input"` (or `"needs_approval"`), the question in `detail`.
- When you're done: `"state": "completed"` and one short line in `summary`.

A `working` file that hasn't changed for 20 minutes shows as 🟠 inactive. When you're done or
need the user, also write a text file to `outbox/` here; the running bot sends it to Telegram
and deletes it. Use this format:

    ✅ Completed — <project> · “<title>”      (or ❓ Needs input — …)
    👤 <account>
    <one short line>

## Files

`hook.py` (the hook), `status.py` (status + lock files, the /status list), `tg.py` (Telegram API),
`install.py` (setup), `bot.py` (answers /status, sends outbox/), `start.bat` (Windows: setup + bot).
Tests: `python -m unittest discover tests`.
