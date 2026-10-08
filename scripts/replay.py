"""Read a saved model trajectory. No network, API key, or package installation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
CASES = {
    "relevant": "02-broker-human/relevant-1.json",
    "irrelevant": "02-broker-human/irrelevant-1.json",
    "failure": "04-exhausted-gap/run-3.json",
}
EXTENDED_CASES = {
    "collaboration": "e41-live-anchor-003",
    "shared-record": "e29-live-author-copy-002",
    "reachable-revision": "e30-live-flip-001",
    "isolated-copy": "e31-live-flip-copy-001",
    "stale-record": "e32-live-dispute-001",
}


def replay_extended(case, as_json=False, full=False):
    from verify_extended import audit_collaboration, audit_shared, load_run, read_json

    manifest = read_json(ROOT / "evidence/extended/manifest.json")
    run = next(item for item in manifest["runs"] if item["id"] == EXTENDED_CASES[case])
    result, snapshot, acts, metadata = load_run(ROOT, run)
    audit = (
        audit_collaboration(run, result, snapshot, acts, metadata)
        if run["case"] == "dynamic-collaboration"
        else audit_shared(run, result, snapshot, acts)
    )
    if as_json:
        print(
            json.dumps(
                {"run": run, "audit": audit, "public_acts": acts},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"HISTORICAL REPLAY | {metadata['created_utc']} | {run['id']}")
    print("Reading archived decisions and committed effects. No model is running.")
    print("Structural audit:", json.dumps(audit, ensure_ascii=False, indent=2))
    print("\nModel-selected coordination / world writes:")
    tools = {
        "ask_external_question",
        "form_group",
        "world_add_object",
        "world_add_reference",
    }
    for event in snapshot["traces"]:
        if (
            event["kind"] != "model.decision"
            or event["payload"].get("tool_name") not in tools
        ):
            continue
        payload = event["payload"]
        print(f"  seq={event['seq']} {event['actor_id']}: {payload['tool_name']}")
        if full:
            print(json.dumps(payload["arguments"], ensure_ascii=False))
    print("\nPublic message relations (full text: --full or --json):")
    for act in acts:
        print(
            f"  {act['act_id']} {act['sender']} -> {act['recipient']} [{act['kind']}] reply_to={act['reply_to']}"
        )
        if full or act["sender"] == "human" or act["recipient"] == "human":
            content = act["payload"].get(
                "answer", act["payload"].get("raw_question", "")
            )
            print(
                content
                if full or len(content) <= 500
                else content[:500] + " … [truncated]"
            )
    print(
        "\nScope: historical semantic verdicts are attributed to the original model judge."
    )
    print("Frozen source:", run["source_archive"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[*CASES, *EXTENDED_CASES], default="relevant")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show full extended-case messages and proposal arguments",
    )
    args = parser.parse_args()
    if args.case in EXTENDED_CASES:
        replay_extended(args.case, args.json, args.full)
        return
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
