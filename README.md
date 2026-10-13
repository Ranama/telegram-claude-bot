# Claude ⇄ Telegram

[![tests](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Ranama/telegram-claude-bot/actions/workflows/tests.yml)

Control Claude Code on one or more Claude accounts from Telegram. Send a message, Claude works on it on your computer/server, and you get a Telegram message when it's done — with the result, time and cost. Only Telegram users you approve can use it.

Python 3.9+ standard library only — nothing to `pip install`.

## How it fits together

- **Account** = one Claude Code login profile (its own `CLAUDE_CONFIG_DIR`) plus a working folder.
- **Chat** = one Telegram chat linked to one account (`/link work`). Use separate chats (or Telegram groups with just you + the bot) to run several accounts in parallel.
- **Session** = a Claude Code conversation. Messages in a chat continue its session until you send `/new`.

## Setup

1. **Create the bot**: in Telegram talk to `@BotFather` → `/newbot` → copy the token.
2. **Log in each Claude account into its own profile** (once per account):
   ```bash
   CLAUDE_CONFIG_DIR=~/.claude-personal claude    # then /login, then exit
   CLAUDE_CONFIG_DIR=~/.claude-work claude        # log in with the other account
   ```
   On Windows (PowerShell): `$env:CLAUDE_CONFIG_DIR="$HOME\.claude-work"; claude`
   (Leave out `config_dir` for an account to use your normal `~/.claude` login.)
3. **Configure**: `cp config.example.json config.json`, paste the token, and set up `accounts`.
4. **Allow yourself**: put your Telegram handle (without `@`) in `allowed_usernames`. The first time you message the bot, your numeric id is pinned to that handle in `state.json`, so someone who later takes the handle can't use the bot. (Alternatively put numeric ids from `/whoami` in `allowed_user_ids`.)
5. **Run**: `python3 bot.py config.json` (keep it running with `tmux`, `systemd`, or `launchd`).
   **Windows**: double-click `start.bat`. It checks Python and Claude Code are installed, creates `config.json`, and opens it in Notepad if the token is missing.
6. In Telegram: `/link personal`, then just type what you want done.

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

## Config reference (per account)

| Key | Meaning |
|---|---|
| `config_dir` | Claude Code profile folder → this is what makes it a different Claude account |
| `workdir` | Folder Claude works in |
| `model` | e.g. `sonnet`, `opus` |
| `permission_mode` | `default`, `acceptEdits`, `plan`, or `bypassPermissions` |
| `allowed_tools` | Tools allowed without asking, e.g. `["Read", "Edit", "Bash(npm test:*)"]` |
| `extra_args` | Any other `claude` CLI flags |
| `env` | Extra environment variables (e.g. `ANTHROPIC_API_KEY` for an API-key account) |

Top level: `allowed_usernames` and/or `allowed_user_ids` (at least one), `allowed_chat_ids` (optional extra lock), `job_timeout_seconds`, `state_file`, `claude_bin` (a command name/path, or a list such as `["node", "C:/path/to/cli.js"]`).

## Notes and limits of this first version

- **Permissions**: Claude runs non-interactively, so it can't ask you to approve a tool mid-run. Anything not allowed by `permission_mode` / `allowed_tools` is denied. Start with `acceptEdits` + a small `allowed_tools` list; `bypassPermissions` lets Claude run anything on that machine — only use it in a sandbox.
- **Usage** is counted from what this bot ran. Your plan's 5-hour / weekly limits aren't exposed by the CLI in print mode, so they aren't shown.
- One job at a time per chat; different chats run in parallel.
- Links, sessions and usage are kept in `state.json`, so restarts are safe.
- Security: the token in `config.json` controls the bot — keep the file private (`chmod 600 config.json`).

## Files

| File | Purpose |
|---|---|
| `bot.py` | The bot |
| `config.example.json` | Config template — copy to `config.json` (git-ignored) |
| `start.bat` | Windows launcher |
| `tests/` | End-to-end tests with a fake Telegram and a fake `claude` |

## Tests

`python3 tests/test_bot.py` — runs on Linux and Windows, Python 3.9+ (also in GitHub Actions on every push).

## License

MIT
