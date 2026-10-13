#!/usr/bin/env python3
"""Stand-in for the `claude` CLI used by the tests.

Supports `--version`, `auth status --json`, `auth login` and `-p --output-format json`.
FAKE_LOGIN_MARKER: if set, `auth status` reports signed in only once that file exists,
and `auth login` creates it.
"""
import json
import os
import sys
import time
import uuid

args = sys.argv[1:]

if "--version" in args:
    print("9.9.9 (Claude Code)")
    sys.exit(0)

marker = os.environ.get("FAKE_LOGIN_MARKER")
if args[:2] == ["auth", "status"]:
    logged_in = os.path.exists(marker) if marker else True
    print(json.dumps({"loggedIn": logged_in, "authMethod": "oauth_token" if logged_in else "none",
                      "apiProvider": "firstParty"}))
    sys.exit(0 if logged_in else 1)
if args[:2] == ["auth", "login"]:
    if marker:
        open(marker, "w").close()
    print("Login successful.")
    sys.exit(0)

prompt = sys.stdin.read()
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
if "FAIL" in prompt:
    print("boom: simulated crash", file=sys.stderr)
    sys.exit(2)
if "LOGINNEEDED" in prompt:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "session_id": sid,
                      "num_turns": 0, "total_cost_usd": 0,
                      "result": "Invalid API key · Please run /login"}))
    sys.exit(1)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "session_id": sid, "num_turns": 3, "duration_ms": 1200, "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 100, "output_tokens": 250,
              "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 500},
    "result": f"Did: {prompt.strip()} | profile={os.environ.get('CLAUDE_CONFIG_DIR')} "
              f"| cwd={os.getcwd()} | resumed={'--resume' in args} | args={' '.join(args)}",
}))
