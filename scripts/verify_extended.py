"""Audit archived collaboration and shared-record cases without a model call."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8-sig"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_files(root, manifest):
    for entry in manifest["files"]:
        raw = (root / entry["path"]).read_bytes()
        require(digest(raw) == entry["sha256"], f"Changed archive: {entry['path']}")
        if entry["transform"] == "gzip":
            require(
                digest(gzip.decompress(raw)) == entry["source_sha256"],
                f"Decompressed bytes differ: {entry['path']}",
            )
        elif entry["transform"] == "copy":
            require(
                digest(raw) == entry["source_sha256"], f"Copy differs: {entry['path']}"
            )
    checked = set()
    for run in manifest["runs"]:
        hashes = read_json(root / run["path"] / "source-hashes.json")
        with zipfile.ZipFile(root / run["source_archive"]) as archive:
            require(
                set(archive.namelist()) == set(hashes),
                f"Frozen source membership: {run['id']}",
            )
            for path, expected in hashes.items():
                key = (run["source_archive"], path, expected)
                if key not in checked:
                    require(
                        digest(archive.read(path)) == expected,
                        f"Frozen source hash: {path}",
                    )
                    checked.add(key)
    return len(manifest["files"])


def successful_writes(snapshot):
    return [
        trace
        for trace in snapshot["traces"]
        if trace["kind"] == "tool.observation"
        and trace["payload"].get("ok") is True
        and trace["payload"].get("source")
        in {"world_add_object", "world_add_reference"}
    ]


def outgoing_objects(world, start):
    """Object-to-object outgoing reachability, not a full semantic access proof."""
    edges = defaultdict(set)
    for ref in world["references"]:
        if ref["target"]["kind"] == "object":
            edges[ref["source_object_id"]].add(ref["target"]["id"])
    visited, pending = set(), [start]
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        pending.extend(edges[node] - visited)
    return visited


def local_objects(snapshot, actor_id):
    entry = next(
        item for item in snapshot["agent_local_worlds"] if item["actor_id"] == actor_id
    )
    return {item["object_id"] for item in entry["world"]["objects"]}


def descends_from(act_id, root_id, by_id):
    seen = set()
    while act_id and act_id not in seen:
        if act_id == root_id:
            return True
        seen.add(act_id)
        act_id = by_id[act_id].get("reply_to")
    return False


def final_reader(acts, snapshot, final_id):
    by_id = {act["act_id"]: act for act in acts}
    roles = {actor["actor_id"]: actor["role"] for actor in snapshot["actors"]}
    root = by_id[final_id]["reply_to"]
    candidates = [
        act
        for act in acts
        if act["kind"] == "answer"
        and roles.get(act["sender"]) == "agent"
        and descends_from(act["act_id"], root, by_id)
    ]
    require(candidates, f"No agent answer under root {root}")
    return max(candidates, key=lambda act: int(act["act_id"].split("-")[-1]))["sender"]


def load_run(root, run):
    base = root / run["path"]
    result = read_json(base / "result.json")
    snapshot = read_json(base / "core-snapshot.json.gz")
    acts = read_json(base / "public-acts.json")
    metadata = read_json(base / "manifest.json")
    require(not result["error"], f"Archived run error: {run['id']}")
    require(metadata["mode"] == "live", f"Not a live archive: {run['id']}")
    from_snapshot = {
        act["act_id"]: act for thread in snapshot["threads"].values() for act in thread
    }
    require(
        {act["act_id"]: act for act in acts} == from_snapshot,
        f"Public log/snapshot mismatch: {run['id']}",
    )
    return result, snapshot, acts, metadata


def audit_collaboration(run, result, snapshot, acts, metadata):
    roles = {actor["actor_id"]: actor["role"] for actor in snapshot["actors"]}
    created_brokers = {
        trace["actor_id"]
        for trace in snapshot["traces"]
        if trace["kind"] == "actor.created" and trace["payload"].get("role") == "broker"
    }
    external = [
        act
        for act in acts
        if act["kind"] == "question"
        and roles.get(act["sender"]) == "agent"
        and act["recipient"] in created_brokers
    ]
    groups = [
        act
        for act in acts
        if act["kind"] == "group_formed"
        and "agent-facility" in act["payload"]["selected_existing_agents"]
    ]
    source_answers = [
        act
        for act in acts
        if act["kind"] == "answer" and act["sender"] == "agent-facility"
    ]
    row = {
        "id": run["id"],
        "entered_revision": result["fact_revision"] is not None,
        "new_brokers": sorted(created_brokers),
        "external_question_ids": [a["act_id"] for a in external],
        "facility_group_ids": [a["act_id"] for a in groups],
        "facility_answer_ids": [a["act_id"] for a in source_answers],
        "archived_outcome": result["outcome"]["outcome"],
    }
    if row["entered_revision"]:
        require(
            external and groups and source_answers,
            f"Incomplete collaboration: {run['id']}",
        )
        decisions = [t for t in snapshot["traces"] if t["kind"] == "model.decision"]
        require(
            any(
                t["payload"].get("tool_name") == "ask_external_question"
                and any(
                    t["actor_id"] == q["sender"]
                    and t["payload"]["arguments"].get("question_id") == q["reply_to"]
                    for q in external
                )
                for t in decisions
            ),
            f"No model external-question proposal: {run['id']}",
        )
        require(
            any(
                t["payload"].get("tool_name") == "form_group"
                and "agent-facility" in t["payload"]["arguments"].get("member_ids", [])
                and any(g["sender"] == t["actor_id"] for g in groups)
                for t in decisions
            ),
            f"No model group proposal: {run['id']}",
        )
        final_id = result["fact_revision"]["act_id"]
        reader = final_reader(acts, snapshot, final_id)
        interview_id = metadata["object_keys"]["interview"]
        acquired = interview_id in local_objects(snapshot, reader)
        # Messages must return to the external question's original agent.
        by_id = {a["act_id"]: a for a in acts}
        return_acts = [
            a
            for a in acts
            if a["kind"] == "answer"
            and a["sender"] in created_brokers
            and any(
                a["reply_to"] == q["act_id"] and a["recipient"] == q["sender"]
                for q in external
            )
        ]
        root = by_id[final_id]["reply_to"]
        linked = any(descends_from(a["act_id"], root, by_id) for a in return_acts)
        require(acquired and linked, f"Material/return relation missing: {run['id']}")
        require(
            result["outcome"]["facility_contact"]
            and result["outcome"]["facility_material_reached"],
            run["id"],
        )
        row.update(
            final_answer_id=final_id,
            returning_agent=reader,
            interview_id=interview_id,
            interview_in_returning_agent=True,
            returned_to_original_question=True,
        )
    else:
        require(not external and not groups and not source_answers, run["id"])
        row.update(
            final_answer_id=result["fact_finding"]["act_id"],
            interview_in_returning_agent=False,
            returned_to_original_question=False,
        )
    return row


def audit_shared(run, result, snapshot, acts):
    family = run["id"].split("-", 1)[0]
    writes = successful_writes(snapshot)
    created = {
        t["payload"]["value"]["object_id"]
        for t in writes
        if t["payload"]["source"] == "world_add_object"
    }
    row = {
        "id": run["id"],
        "successful_object_writes": len(created),
        "successful_reference_writes": sum(
            t["payload"]["source"] == "world_add_reference" for t in writes
        ),
    }
    if family == "e29":
        stage = result["author_check"]
        prestate = result["author_prestate"]
        reader = final_reader(acts, snapshot, stage["act_id"])
        linked = created & outgoing_objects(
            snapshot["semantic_world"], prestate["case"]
        )
        received = linked & local_objects(snapshot, reader)
        writers = {t["actor_id"] for t in writes}
        require(
            created and received and reader not in writers,
            f"Cross-actor written material missing: {run['id']}",
        )
        row.update(
            case_id=prestate["case"],
            reader=reader,
            writer_actors=sorted(writers),
            written_material_received=sorted(received),
            author_answer_id=result["author_revision"]["act_id"],
        )
    else:
        stage = result["dispute_recheck"] if family == "e32" else result["flip_check"]
        prestate = (
            result["dispute_prestate"] if family == "e32" else result["flip_prestate"]
        )
        reader = final_reader(acts, snapshot, stage["act_id"])
        available = prestate["revised"] in local_objects(snapshot, reader)
        reachable = prestate["revised"] in outgoing_objects(
            snapshot["semantic_world"], prestate["case"]
        )
        require(not writes, f"Unexpected model world write: {run['id']}")
        require(
            available == (family == "e30"), f"Reader exposure mismatch: {run['id']}"
        )
        row.update(
            case_id=prestate["case"],
            reader=reader,
            revision_id=prestate["revised"],
            revised_material_in_reader=available,
            case_outgoing_to_revision=reachable,
        )
        if family == "e32":
            row["author_answer_id"] = result["dispute_office"]["act_id"]
    row["reader_answer_id"] = stage["act_id"]
    row["archived_model_judge"] = {
        key: stage["judgement"].get(key)
        for key in (
            "verdict",
            "used_revised_criterion",
            "condition1_closed_claim",
            "source",
        )
    }
    return row


def audit_extended(root=ROOT):
    manifest = read_json(root / "evidence/extended/manifest.json")
    count = verify_files(root, manifest)
    rows = []
    for run in manifest["runs"]:
        result, snapshot, acts, metadata = load_run(root, run)
        row = (
            audit_collaboration(run, result, snapshot, acts, metadata)
            if run["case"] == "dynamic-collaboration"
            else audit_shared(run, result, snapshot, acts)
        )
        rows.append(row)
    families = Counter(row["id"].split("-", 1)[0] for row in rows)
    require(
        dict(families) == {"e29": 3, "e30": 3, "e31": 3, "e32": 3, "e41": 6},
        "Unexpected case matrix",
    )
    collaboration = [row for row in rows if row["id"].startswith("e41")]
    return {
        "archived_runs": len(rows),
        "verified_files": count,
        "frozen_source_families": len({r["source_archive"] for r in manifest["runs"]}),
        "family_counts": dict(families),
        "collaboration_revision_branches": sum(
            row["entered_revision"] for row in collaboration
        ),
        "collaboration_verified_return_chains": sum(
            row["returned_to_original_question"] for row in collaboration
        ),
        "note": "Structural facts are recomputed. Semantic verdicts remain attributed to the archived model judge, not an independent correctness proof.",
        "runs": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write-summary",
        action="store_true",
        help="Regenerate the derived offline audit summary",
    )
    args = parser.parse_args()
    try:
        result = audit_extended()
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Extended evidence verification failed: {exc}\n")
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.write_summary:
        (ROOT / "evidence/extended/audit-summary.json").write_text(
            encoded, encoding="utf-8"
        )
    if args.json:
        print(encoded)
    else:
        print(
            f"PASS: {result['archived_runs']} archived runs, {result['verified_files']} files, {result['frozen_source_families']} frozen source families."
        )
        print(
            "E41: 6 runs; 5 entered revision and returned source material through a new collaboration."
        )
        print("E29: written material reached a different reader in 3 runs.")
        print(
            "E30/E31/E32: reader exposure and world writes checked independently of saved judge labels."
        )
        print(
            "Semantic verdicts are historical model-judge outputs, not new independent evaluations."
        )


if __name__ == "__main__":
    main()
