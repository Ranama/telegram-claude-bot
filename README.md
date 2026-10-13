# Claude ⇄ Telegram

[![tests](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml)

Control Claude Code on one or more Claude accounts from Telegram. Send a message, Claude works on it on your computer, and you get a Telegram message when it's done — with the result, time and cost. Only Telegram users you approve can use it.

Python 3.8+ standard library only — nothing to `pip install`.

## Quick start (Windows)

1. Double-click **`start.bat`**. In that one window it:
   - installs **Claude Code** if it isn't installed yet (Anthropic's official installer from claude.ai — the Claude desktop app doesn't include the command-line version the bot needs),
   - signs Claude Code in if needed (your browser opens once: sign in and click *Authorize*),
   - asks once for your **bot token** and checks it with Telegram: in Telegram, message **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`, and paste the token it gives you (it stays hidden while you paste),
   - starts the bot and shows its `t.me/...` link.
2. Open the link in Telegram and send a message, e.g. `hello`.

Keep the window open — closing it stops the bot. Next time, `start.bat` skips the steps that are already done and starts the bot straight away. Everything is logged to `setup.log` and `bot.log` in this folder (the token is never logged).

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
| `/resume <session_id>` | Attach to an existing session, e.g. one started in your terminal |
| `/sessions` | Current and past sessions in this chat |
| `/status` | Account, session, workdir, and what's running |
| `/usage` | Runs, turns, cost, tokens, context fill % and models — for the session and the account |
| `/stop` | Stop the running job |
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
| `config.example.json` | Config template — `config.json` is created from it (git-ignored) |
| `tests/` | Tests with a fake Telegram and a fake `claude` |

## Tests

`python tests/test_bot.py` and `python tests/test_setup.py` — run on Linux and Windows, Python 3.9+ (also in GitHub Actions on every push).

## License

MIT
