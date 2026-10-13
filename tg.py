"""Minimal Telegram Bot API client (standard library only) and the shared config file."""
import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
LIMIT = 4000  # Telegram allows 4096 characters per message


def load_config(path=None):
    path = path or CONFIG
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token") or ""
    return cfg


def save_config(cfg, path=None):
    path = Path(path or CONFIG)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def call(token, method, http_timeout=20, **params):
    """Call a Bot API method; returns its result or raises."""
    base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    req = urllib.request.Request(f"{base}/bot{token}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=http_timeout) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method}: {res.get('description')}")
    return res["result"]


def split(text, limit=LIMIT):
    text = text or "(empty)"
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text]


def send(cfg, text, buttons=None, chat_id=None, parse_mode=None):
    """Send to your chat (parse_mode="HTML" for bold etc.). Returns the message ids."""
    ids = []
    parts = split(text)
    for i, part in enumerate(parts):
        params = {"chat_id": chat_id or cfg["chat_id"], "text": part, "disable_web_page_preview": True}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if buttons and i == len(parts) - 1:
            params["reply_markup"] = {"inline_keyboard": buttons}
        ids.append(call(cfg["telegram_bot_token"], "sendMessage", **params)["message_id"])
    return ids
