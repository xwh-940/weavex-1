"""Reject plausible-looking evidence when the actual material or return link is missing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "extended_audit", ROOT / "scripts/verify_extended.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def archived_run(name):
    manifest = audit.read_json(ROOT / "evidence/extended/manifest.json")
    run = next(item for item in manifest["runs"] if item["id"] == name)
    return run, audit.load_run(ROOT, run)


def test_full_archive_preserves_denominators_and_failed_propagation():
    result = audit.audit_extended(ROOT)
    assert result["family_counts"] == {"e29": 3, "e30": 3, "e31": 3, "e32": 3, "e41": 6}
    assert result["collaboration_revision_branches"] == 5
    assert result["collaboration_verified_return_chains"] == 5
    stale = [row for row in result["runs"] if row["id"].startswith("e32")]
    assert all(
        not row["revised_material_in_reader"] and row["successful_object_writes"] == 0
        for row in stale
    )


def test_dynamic_case_rejects_return_to_the_wrong_agent():
    run, (result, snapshot, acts, metadata) = archived_run("e41-live-anchor-003")
    return_act = next(
        act for act in acts if act["kind"] == "answer" and act["sender"] == "broker-1"
    )
    return_act["recipient"] = "broker-office"
    with pytest.raises(ValueError, match="Material/return relation missing"):
        audit.audit_collaboration(run, result, snapshot, acts, metadata)


def test_shared_case_rejects_a_written_object_the_reader_never_received():
    run, (result, snapshot, acts, _) = archived_run("e29-live-author-copy-002")
    original = audit.audit_shared(run, result, snapshot, acts)
    missing = set(original["written_material_received"])
    local = next(
        item["world"]
        for item in snapshot["agent_local_worlds"]
        if item["actor_id"] == original["reader"]
    )
    local["objects"] = [
        item for item in local["objects"] if item["object_id"] not in missing
    ]
    with pytest.raises(ValueError, match="Cross-actor written material missing"):
        audit.audit_shared(run, result, snapshot, acts)
