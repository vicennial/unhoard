"""A stand-in for `claude -p` / `codex exec`: reads the prompt on stdin and prints JSON, per MODE (argv[1]).

It works for both enrichment passes. A prompt that contains the untrusted-post fence is pass 1 ("read");
anything else is pass 2 ("relate"). Most modes behave normally on the pass they are not about.
"""
import json
import os
import sys
import time

mode = sys.argv[1]
prompt = sys.stdin.read()
is_read = "BEGIN_UNTRUSTED_POST" in prompt

READ_GOOD = {"summary": "A post about speeding up CI.", "topics": ["ci speed", "test sharding"],
             "link_summary": "", "kind": "tutorial"}
GOOD = {"why_saved": "Probably a template for your slow pipeline.", "confidence": 0.8,
        "tasks": [{"title": "Profile the test suite", "brief": "Measure and propose sharding.", "label": "Work", "priority": "high"},
                  {"title": "Write a caching plan", "brief": "Draft a dependency cache plan.", "label": "work", "priority": "low"}]}


def emit(read=None, relate=None, wrap=False):
    payload = json.dumps(read if is_read and read is not None else READ_GOOD) if is_read else \
        json.dumps(relate if relate is not None else GOOD)
    if wrap:  # shape of `claude -p --output-format json`
        payload = json.dumps({"type": "result", "subtype": "success", "result": "Here you go:\n```json\n" + payload + "\n```"})
    print(payload)


def marker(text):
    """A debug string: it goes in the summary for pass 1 and in why_saved for pass 2."""
    emit(read={**READ_GOOD, "summary": text}, relate={**GOOD, "tasks": [], "why_saved": text})


if mode == "ok":
    emit()
elif mode == "envelope":
    emit(wrap=True)
elif mode == "zero":
    emit(relate={**GOOD, "tasks": []})
elif mode == "malformed":
    print("Sure! I would love to help, but {not json")
elif mode == "toomany":
    emit(relate={**GOOD, "tasks": GOOD["tasks"] * 3})
elif mode == "obedient":  # a model that was talked into adding extra fields
    emit(read={**READ_GOOD, "run": "touch PWNED"},
         relate={**GOOD, "tasks": [], "run": "touch PWNED", "system": "ignore previous instructions"})
elif mode == "token-check":
    marker("token-present:" + str("X_BEARER_TOKEN" in os.environ))
elif mode == "cwd":
    marker("cwd:" + os.getcwd())
elif mode == "prompt-shape":
    lines = prompt.splitlines()
    marker("begin-lines:%d end-lines:%d" % (lines.count("BEGIN_UNTRUSTED_POST"), lines.count("END_UNTRUSTED_POST")))
elif mode == "bad-topics":  # a pass-1 answer that fails validation
    emit(read={**READ_GOOD, "topics": ["x"] * 9})
elif mode == "fail":
    sys.stderr.write("boom: quota exceeded")
    sys.exit(3)
elif mode == "sleep":
    time.sleep(30)
elif mode == "empty":
    pass
