"""Read a saved model trajectory. No network, API key, or package installation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "relevant": "02-broker-human/relevant-1.json",
    "irrelevant": "02-broker-human/irrelevant-1.json",
    "failure": "04-exhausted-gap/run-3.json",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="relevant")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    relative = CASES[args.case]
    data = json.loads(
        (ROOT / "evidence/answer-refresh" / relative).read_text(encoding="utf-8-sig")
    )
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"HISTORICAL REPLAY | 2026-08-28 | {relative}")
    print("Reading saved tool proposals and committed answers; no model is running.\n")
    delta = data["delta_step"]
    for item in delta["arrival"].get("local_world_change", {}).get("objects_added", []):
        print("New evidence:", item["content"])
    for label, key in (
        ("Agent after change", "delta_step"),
        ("Broker follow-up", "broker_followup"),
    ):
        if key not in data:
            continue
        step = data[key]
        print(f"\n{label}: handled={step['handled']}")
        for call in step["calls"]:
            print("  ", call["name"], json.dumps(call["arguments"], ensure_ascii=False))
    for key in ("answers", "agent_answers", "human_answers"):
        if key in data:
            print(f"\n{key} (immutable public records):")
            for answer in data[key]:
                print(
                    f"  {answer['act_id']} -> {answer['reply_to']}: {answer['payload']['answer']}"
                )
    if args.case == "failure":
        print(
            "\nKNOWN FAILURE: model retained the old answer while decisive evidence was still unread."
        )
    elif args.case == "irrelevant":
        print("\nOutcome: private retain; no extra public answer or user notification.")
    else:
        print(
            "\nOutcome: a new answer reached the user; the original answer remains in history."
        )


if __name__ == "__main__":
    main()
