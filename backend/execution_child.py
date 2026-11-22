"""Fixed, no-network child entry point used only by the supervisor test increment."""
from __future__ import annotations
import json
import subprocess
import sys
import time
from backend.supervisor import PROTOCOL_VERSION

def _write(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n"); sys.stdout.flush()

def main():
    line = sys.stdin.readline()
    try: message = json.loads(line); packet = message["packet"]
    except Exception: return 2
    mode = packet.get("mode")
    if mode == "malformed": _write({"broken": True}); return 0
    if mode == "oversized": sys.stdout.write("x" * 9000 + "\n"); sys.stdout.flush(); return 0
    if mode == "truncated": sys.stdout.write('{"protocol_version":'); sys.stdout.flush(); return 0
    if mode == "unexpected": _write({"protocol_version": PROTOCOL_VERSION, "type": "ready", "unexpected": True}); return 0
    if mode == "crash": return 3
    if mode == "spawn_wait":
        child = subprocess.Popen([sys.executable, "-m", "backend.execution_child", "--grandchild"], shell=False)
        _write({"protocol_version": PROTOCOL_VERSION, "type": "ready", "child_pid": child.pid})
        while True: _write({"protocol_version": PROTOCOL_VERSION, "type": "heartbeat"}); time.sleep(0.05)
    _write({"protocol_version": PROTOCOL_VERSION, "type": "ready"})
    if mode == "wait":
        while True:
            _write({"protocol_version": PROTOCOL_VERSION, "type": "heartbeat"}); time.sleep(0.05)
    _write({"protocol_version": PROTOCOL_VERSION, "type": "result", "result": {"outcome": "executor_unavailable"}})
    return 0

if __name__ == "__main__":
    if "--grandchild" in sys.argv:
        while True: time.sleep(1)
    raise SystemExit(main())
