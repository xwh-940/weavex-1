"""Check the public example against actual host effects, plus evidence integrity."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["relevant", "irrelevant"])
async def test_example_delivers_update_or_private_retain(arm):
    demo = load_module("refresh_example", "examples/answer_refresh.py")
    result = await demo.run_case(arm, demo.ScriptedModel(demo.DemoDecisions()))
    assert result["passed"], result["error"]
    answers = result["human_answers"]
    assert len(answers) == (2 if arm == "relevant" else 1)
    assert len({answer["reply_to"] for answer in answers}) == 1
    assert "ALLOW-DEMO" in answers[0]["payload"]["answer"]
    if arm == "relevant":
        assert "BLOCK-DEMO" in answers[1]["payload"]["answer"]
        assert answers[0]["act_id"] != answers[1]["act_id"]
    else:
        assert result["private_retain_proposals"] == 1
    traces = result["snapshot"]["traces"]
    assert any(trace["kind"] == "local_world_delta.created" for trace in traces)
    failures = [
        trace
        for trace in traces
        if trace["kind"] in {"tool.observation", "act.observation"}
        and not trace["payload"]["ok"]
    ]
    assert failures == []


def test_evidence_manifest_detects_changed_file(tmp_path):
    verifier = load_module("verify_evidence", "scripts/verify_evidence.py")
    (tmp_path / "record.json").write_text('{"changed": true}', encoding="utf-8")
    manifest = {"copied_files": [{"path": "record.json", "sha256": "0" * 64}]}
    (tmp_path / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Copied file changed"):
        verifier.check_manifest(tmp_path)
