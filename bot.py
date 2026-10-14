#!/usr/bin/env python3
"""
Optional Telegram side: answers /status with all Claude Code sessions on this computer
(working, needs input, needs approval, completed, …) and a 🔄 Refresh button.

Notifications don't need this running — the hooks send them. Run it (start.bat on
Windows) when you want /status. It only answers your own chat (chat_id in config.json).

It also sends any text file dropped into the outbox/ folder to you, then deletes it.
"""
import importlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import status  # noqa: E402
import tg  # noqa: E402

log = logging.getLogger("claude-telegram.bot")
REFRESH = [[{"text": "🔄 Refresh", "callback_data": "status"}]]
HELP = ("I tell you when a Claude Code session on your computer is ✅ done, ❓ needs input "
        "or 🔐 needs approval.\n\n/status – all sessions right now: 🔴 needs you, "
        "🟡 working, 🟢 done")


class Bot:
    def __init__(self, cfg, base=HERE, call=tg.call, send=tg.send):
        self.cfg, self.base, self.call, self.send = cfg, Path(base), call, send
        self.token, self.chat_id = cfg["telegram_bot_token"], int(cfg["chat_id"])

    def status_text(self):
        try:
            importlib.reload(status)  # an updated status.py works without restarting the bot
        except Exception as e:
            log.warning("status.py: %s", e)
        return status.status_text(status.current(self.base))

    def handle(self, update):
        cq = update.get("callback_query")
        if cq:
            msg = cq.get("message") or {}
            if (msg.get("chat") or {}).get("id") == self.chat_id and cq.get("data") == "status":
                try:
                    self.call(self.token, "editMessageText", chat_id=self.chat_id,
                              message_id=msg.get("message_id"), text=self.status_text(),
                              parse_mode="HTML", reply_markup={"inline_keyboard": REFRESH})
                except Exception as e:  # "message is not modified" when nothing changed
                    log.info("refresh: %s", e)
            self.call(self.token, "answerCallbackQuery", callback_query_id=cq.get("id"))
            return
        msg = update.get("message") or {}
        if (msg.get("chat") or {}).get("id") != self.chat_id:
            return  # not your chat: ignore
        command = (msg.get("text") or "").split()[0].split("@")[0].lower() if msg.get("text") else ""
        if command == "/status":
            self.send(self.cfg, self.status_text(), buttons=REFRESH, parse_mode="HTML")
        else:
            self.send(self.cfg, HELP)

    def deliver_outbox(self, now=None):
        """Send text files from outbox/ (written by scripts or other tools), then delete them."""
        folder = self.base / "outbox"
        sent = 0
        for path in sorted(folder.glob("*.txt")) if folder.is_dir() else []:
            try:
                if (now or time.time()) - path.stat().st_mtime < 2:
                    continue  # still being written
                text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
                if text and "🕒" not in text:       # when it was written
                    text += "\n🕒 " + time.strftime("%d %b %H:%M:%S", time.localtime(path.stat().st_mtime))
                if text:
                    self.send(self.cfg, text)
                    sent += 1
                path.unlink()
            except Exception as e:
                log.warning("outbox %s: %s", path.name, e)
        return sent

    def run(self):
        self.call(self.token, "setMyCommands", commands=[
            {"command": "status", "description": "All Claude sessions right now"}])
        (self.base / "outbox").mkdir(exist_ok=True)

        def outbox_loop():
            while True:
                self.deliver_outbox()
                time.sleep(3)
        threading.Thread(target=outbox_loop, daemon=True).start()
        log.info("Running. Send /status to your bot.")
        offset = 0
        while True:
            try:
                updates = self.call(self.token, "getUpdates", http_timeout=60, offset=offset,
                                    timeout=45, allowed_updates=["message", "callback_query"])
            except Exception as e:
                log.warning("Telegram: %s (retrying)", e)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle(u)
                except Exception:
                    log.exception("update failed")


def single_instance(path):
    """Only one bot.py per token: a second one would make Telegram reject both."""
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(HERE / "bot.log", encoding="utf-8")])
    cfg = tg.load_config()
    if not cfg.get("telegram_bot_token") or not cfg.get("chat_id"):
        log.error("Not set up yet. Run: python install.py")
        return 1
    lock = single_instance(HERE / "bot.lock")
    if not lock:
        log.error("bot.py is already running.")
        return 1
    try:
        Bot(cfg).run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
