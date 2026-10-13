import os
import time

from helpers import CHAT, TOKEN, FakeTelegram, TempDirTest
import bot
import status


class BotTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.tg = FakeTelegram()
        self.bot = bot.Bot({"telegram_bot_token": TOKEN, "chat_id": CHAT}, base=self.dir,
                           call=self.tg.call, send=self.tg.send)

    def msg(self, text, chat=CHAT):
        self.bot.handle({"update_id": 1, "message": {"chat": {"id": chat}, "text": text}})

    def test_status_with_refresh(self):
        status.record_event(self.dir, {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                                       "cwd": "/x/VineTrip", "prompt": "deploy it"})
        self.msg("/status")
        sent = self.tg.sent[-1]
        self.assertIn("🟡 Working — VineTrip", sent["text"])
        self.assertEqual(sent["buttons"], bot.REFRESH)
        self.bot.handle({"update_id": 2, "callback_query": {"id": "c1", "data": "status",
                         "message": {"message_id": 5, "chat": {"id": CHAT}}}})
        methods = [m for m, _ in self.tg.calls]
        self.assertEqual(methods, ["editMessageText", "answerCallbackQuery"])

    def test_only_your_chat(self):
        self.msg("/status", chat=999)
        self.bot.handle({"update_id": 3, "callback_query": {"id": "c2", "data": "status",
                         "message": {"message_id": 5, "chat": {"id": 999}}}})
        self.assertEqual(self.tg.sent, [])
        self.assertEqual([m for m, _ in self.tg.calls], ["answerCallbackQuery"])
        self.msg("hello")
        self.assertIn("/status", self.tg.sent[-1]["text"])

    def test_outbox(self):
        box = self.dir / "outbox"
        box.mkdir()
        (box / "a.txt").write_text("✅ Backup finished", encoding="utf-8")
        (box / "new.txt").write_text("still writing", encoding="utf-8")
        old = time.time() - 10
        os.utime(box / "a.txt", (old, old))
        self.assertEqual(self.bot.deliver_outbox(), 1)
        self.assertEqual(self.tg.sent[-1]["text"], "✅ Backup finished")
        self.assertEqual([p.name for p in box.iterdir()], ["new.txt"])

    def test_single_instance(self):
        first = bot.single_instance(self.dir / "bot.lock")
        self.assertIsNotNone(first)
        self.assertIsNone(bot.single_instance(self.dir / "bot.lock"))
        first.close()
