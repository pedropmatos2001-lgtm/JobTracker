import json
import os
from datetime import date, timedelta

EMPTY = {"seen": {}, "health": {}, "matches": []}


def load(path):
    if not os.path.exists(path):
        return json.loads(json.dumps(EMPTY))
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    for k, v in EMPTY.items():
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


def save(path, state, today: date, keep_days=120, keep_matches=150):
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    state["seen"] = {u: m for u, m in state["seen"].items() if m.get("first_seen", "9999") >= cutoff}
    state["matches"] = sorted(state["matches"], key=lambda m: m.get("first_seen", ""), reverse=True)[:keep_matches]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
