#!/usr/bin/env python3
"""Stand-in for the `claude` CLI: mimics `claude -p --output-format json`."""
import json
import os
import sys
import time
import uuid

args = sys.argv[1:]
prompt = sys.stdin.read()
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
if "FAIL" in prompt:
    print("boom: simulated crash", file=sys.stderr)
    sys.exit(2)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "session_id": sid, "num_turns": 3, "duration_ms": 1200, "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 100, "output_tokens": 250,
              "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 500},
    "result": f"Did: {prompt.strip()} | profile={os.environ.get('CLAUDE_CONFIG_DIR')} "
              f"| cwd={os.getcwd()} | resumed={'--resume' in args} | args={' '.join(args)}",
}))
