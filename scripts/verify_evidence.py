"""Offline checks of saved evidence. Does not call a model or rewrite records."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_manifest(root: Path):
    manifest = read_json(root / "SOURCE_MANIFEST.json")
    for item in manifest["copied_files"]:
        path = root / item["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == item["sha256"], f"Copied file changed: {item['path']}")
    return len(manifest["copied_files"])


def check_refresh(root: Path):
    base = root / "evidence/answer-refresh"
    paths = sorted(base.glob("[0-9]*/*.json"))
    require(len(paths) == 18, "Expected all 18 historical runs")
    matrix = Counter()
    for path in paths:
        data = read_json(path)
        if data["group"] not in {"agent-frontier", "broker-human"}:
            continue
        group, arm = data["group"], data["arm"]
        require(arm in {"relevant", "irrelevant"}, str(path))
        agent_answers = data.get("answers", data.get("agent_answers", []))
        human_answers = data.get("human_answers", [])
        relevant = arm == "relevant"
        expected_count = 2 if relevant else 1
        require(len(agent_answers) == expected_count, f"Agent answer count: {path}")
        require(
            data["allow_marker"] in agent_answers[0]["payload"]["answer"], str(path)
        )
        require(len({answer["reply_to"] for answer in agent_answers}) == 1, str(path))
        require(
            len({answer["act_id"] for answer in agent_answers}) == expected_count,
            str(path),
        )
        calls = data["delta_step"]["calls"]
        names = [call["name"] for call in calls]
        if relevant:
            require("answer_question" in names, f"Missing update: {path}")
            require(
                data["later_marker"] in agent_answers[-1]["payload"]["answer"],
                str(path),
            )
        else:
            require("retain_answer" in names, f"Missing private retain: {path}")
            require("answer_question" not in names, f"Unexpected repeat: {path}")
        if group == "broker-human":
            require(len(human_answers) == expected_count, f"Human answer count: {path}")
            require(
                len({answer["reply_to"] for answer in human_answers}) == 1, str(path)
            )
            if relevant:
                require(
                    data["later_marker"] in human_answers[-1]["payload"]["answer"],
                    str(path),
                )
            else:
                require(not data["broker_followup"]["handled"], str(path))
        else:
            require(not data["pending_question_ids"], str(path))
        require(
            data["initial_ok"] and data["natural"], f"Saved outcome mismatch: {path}"
        )
        matrix[f"{group}/{arm}"] += 1
    require(
        len(matrix) == 4 and all(n == 3 for n in matrix.values()),
        "Expected four arms, three runs each",
    )
    failure = read_json(base / "04-exhausted-gap/run-3.json")
    require(
        failure["retained_without_evidence"] and not failure["gap_remains"],
        "Failure case missing",
    )
    require(len(failure["answers"]) == 1, "Failure should preserve the old answer")
    require(
        any(c["name"] == "retain_answer" for c in failure["delta_step"]["calls"]),
        "Failure trace missing",
    )
    return {
        "archived_runs": 18,
        "main_matrix": dict(matrix),
        "known_incorrect_retain": 1,
    }


def check_cache(root: Path):
    base = root / "evidence/cache"
    probe = read_json(base / "diagnosis/probe-results.json")
    require(
        len(probe) == 11 and all(row["status"] == "completed" for row in probe),
        "Probe incomplete",
    )
    for row in probe:
        usage = row["usage"]
        require(
            usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
            == usage["prompt_tokens"],
            "Probe token mismatch",
        )
    regression = base / "continuity/retry-8192"
    rows = [
        json.loads(line)
        for line in (regression / "requests.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    tokens = {
        name: sum(row["usage"][name] for row in rows)
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        )
    }
    require(len(rows) == 29 and all(row["ok"] for row in rows), "Regression incomplete")
    for row in rows:
        usage = row["usage"]
        require(
            usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
            == usage["prompt_tokens"],
            "Request token mismatch",
        )
    summary = read_json(regression / "summary.json")
    for name, value in tokens.items():
        require(summary["provider_usage"][name] == value, f"Summary mismatch: {name}")
    require(
        tokens["prompt_tokens"] == 332914
        and tokens["prompt_cache_hit_tokens"] == 325248,
        "Unexpected saved totals",
    )
    schemas = defaultdict(set)
    for row in rows:
        schemas[row["actor_id"]].add(row["tools_hash"])
        require(
            row["previous_messages_unchanged"] is not False, "Changed message prefix"
        )
    require(
        len(schemas) == 3 and all(len(values) == 1 for values in schemas.values()),
        "Schema changed",
    )
    with gzip.open(regression / "snapshot.json.gz", "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    receives = {}
    for actor in snapshot["actors"]:
        counts = Counter()
        for message in actor["dialogue"]:
            if message.get("role") != "tool":
                continue
            result = json.loads(message["content"])
            if result.get("source") != "receive_input" or not result.get("ok"):
                continue
            world = result["value"]["local_world"]
            for name, key in (
                ("objects", "object_id"),
                ("references", "reference_id"),
                ("actors", "actor_id"),
                ("groundings", "grounding_id"),
            ):
                counts.update((name, item[key]) for item in world.get(name, []))
        require(
            all(count == 1 for count in counts.values()),
            f"Repeated delivery: {actor['actor_id']}",
        )
        receives[actor["actor_id"]] = len(counts)
    failures = [
        trace
        for trace in snapshot["traces"]
        if trace["kind"] in {"tool.observation", "act.observation"}
        and not trace["payload"]["ok"]
    ]
    require(
        len(failures) == summary["tool_failures"] == 1, "Expected one rejected proposal"
    )
    require(
        not any(summary["open_responsibilities"].values()),
        "Unanswered final responsibility",
    )
    initial = read_json(base / "continuity/summary.json")
    require(
        initial["requests"] == 13 and bool(initial["error"]),
        "Initial failed run must be retained",
    )
    return {
        "probe_requests": len(probe),
        "regression_requests": len(rows),
        **tokens,
        "weighted_hit_ratio": tokens["prompt_cache_hit_tokens"]
        / tokens["prompt_tokens"],
        "repeated_material_ids": 0,
        "rejected_tool_proposals": len(failures),
        "initial_failed_run_requests": initial["requests"],
    }


def verify(root=ROOT):
    from verify_extended import audit_extended

    extended = audit_extended(root)
    require(
        read_json(root / "evidence/extended/audit-summary.json") == extended,
        "Derived extended summary differs; regenerate with scripts/verify_extended.py --write-summary",
    )
    return {
        "copied_files_verified": check_manifest(root),
        "answer_refresh": check_refresh(root),
        "cache": check_cache(root),
        "extended": extended,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable results"
    )
    args = parser.parse_args()
    try:
        result = verify()
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Evidence verification failed: {exc}\n")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"PASS: {result['copied_files_verified']} copied files match recorded SHA-256 hashes."
        )
        print(
            "PASS: all 18 historical runs retained; four main arms x 3 runs agree with answer/tool records."
        )
        print("PASS: the incorrect retain case remains visible.")
        cache = result["cache"]
        print(
            f"PASS: 11 cache probes; 29 regression requests, weighted hit ratio {cache['weighted_hit_ratio']:.2%}."
        )
        print(
            "PASS: no repeated material IDs; one rejected proposal; initial failed run retained."
        )
        print(
            "PASS: 18 additional archived runs and 5 frozen source families; collaboration and shared-record structure verified."
        )
        print(
            "This audits stored records; it is not a new LLM run or a semantic correctness proof."
        )


if __name__ == "__main__":
    main()
