"""Tests for sessions.py: reading Claude Code transcripts."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sessions as pc  # noqa: E402


def write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def user(text, **extra):
    return dict({"type": "user", "cwd": "D:\\Claude\\VineTrip", "message": {"content": text}}, **extra)


def claude(text):
    return {"type": "assistant", "cwd": "D:\\Claude\\VineTrip",
            "message": {"content": [{"type": "text", "text": text}]}}


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_folder_title_and_turns(self):
        path = write(self.root / "D--Claude-VineTrip" / "s1.jsonl", [
            user("build the shop page"), claude("Part one."), claude("Part two."),
            user([{"type": "tool_result", "content": "x"}]),
            user("<command-name>/clear</command-name>"),
            {"type": "ai-title", "aiTitle": "Shop page"},
            {"type": "custom-title", "customTitle": "My shop"},
            user("now style it"), claude("Styled."),
        ])
        info = pc.read_session(path)
        self.assertEqual(info["session_id"], "s1")
        self.assertEqual(info["cwd"], "D:\\Claude\\VineTrip")
        self.assertEqual(info["title"], "My shop")          # custom title wins
        self.assertEqual(info["last_reply"], "Styled.")
        self.assertEqual(info["turns"][1], ["claude", "Part one.\nPart two."])
        self.assertEqual([r for r, _ in info["turns"]], ["you", "claude", "you", "claude"])
        self.assertEqual(pc.folder_name(info["cwd"]), "VineTrip")

    def test_empty_or_tool_only_sessions_are_skipped(self):
        path = write(self.root / "p" / "s2.jsonl", [{"type": "queue-operation"}])
        self.assertIsNone(pc.read_session(path))

    def test_list_newest_first_and_limit(self):
        now = time.time()
        for i in range(5):
            p = write(self.root / f"p{i}" / f"s{i}.jsonl", [user(f"task {i}"), claude("ok")])
            os.utime(p, (now - i * 100, now - i * 100))
        found = pc.list_sessions([(self.root, None)], limit=3)
        self.assertEqual([f["session_id"] for f in found], ["s0", "s1", "s2"])

    def test_extra_transcript_folders_are_included(self):
        other = Path(self.tmp.name) / "elsewhere" / "proj"
        t = write(other / "x1.jsonl", [user("from the desktop app"), claude("hi")])
        write(other / "x2.jsonl", [user("sibling"), claude("hi")])
        found = pc.list_sessions([(self.root, None)], extra_files=[(str(t), "C:/p")])
        self.assertEqual({f["session_id"] for f in found}, {"x1", "x2"})
        self.assertEqual(found[0]["config_dir"], "C:/p")

    def test_big_transcript_reads_head_and_tail(self):
        filler = claude("x" * 1000)
        entries = [user("first prompt")] + [filler] * 4000 + [user("latest prompt"), claude("latest reply")]
        info = pc.read_session(write(self.root / "p" / "big.jsonl", entries))
        self.assertEqual(info["first_prompt"], "first prompt")
        self.assertEqual(info["last_reply"], "latest reply")

    def test_discovers_app_session_folders_and_skips_caches(self):
        app = Path(self.tmp.name) / "AppData" / "Claude"
        inside = write(app / "local-agent-mode-sessions" / "abc" / ".claude" / "projects" / "p" / "s.jsonl",
                       [user("hi"), claude("hello")])
        write(app / "Cache" / "x" / "projects" / "p" / "c.jsonl", [user("no"), claude("no")])
        (app / "projects" / "empty").mkdir(parents=True)   # no transcripts: not a session folder
        found = pc.discover_project_folders([app])
        self.assertEqual(found, [inside.parent.parent])

    def test_sessions_from_elsewhere_are_view_only(self):
        info = pc.read_session(write(self.root / "p" / "vm.jsonl", [
            {"type": "user", "cwd": "/sessions/vm-only/x", "message": {"content": "hi"}}, claude("ok")]))
        self.assertFalse(info["local"])
        self.assertIn("view only", pc.describe(1, info))
        here = pc.read_session(write(self.root / "p" / "here.jsonl", [
            {"type": "user", "cwd": self.tmp.name, "message": {"content": "hi"}}, claude("ok")]))
        self.assertTrue(here["local"])

    def test_describe_and_history(self):
        info = pc.read_session(write(self.root / "p" / "s.jsonl", [
            user("one"), claude("a"), user("two"), claude("b"), user("three"), claude("c")]))
        line = pc.describe(1, info, now=info["mtime"] + 7200)
        self.assertIn("1. ▫️ VineTrip — one · 2 h ago", line)
        self.assertIn("you: three", line)
        hist = pc.history_text(info, count=2)
        self.assertTrue(hist.startswith("🧑 You: two"))
        self.assertIn("🤖 Claude: c", hist)
        self.assertEqual(pc.ago(30), "just now")
        self.assertEqual(pc.ago(86400 * 3), "3 days ago")


if __name__ == "__main__":
    unittest.main(verbosity=2)
