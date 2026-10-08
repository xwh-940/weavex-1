"""External demo Charter invariants without a demo-specific Actor workflow."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from weavex.defaults import build_default_runtime
from weavex.demo import ObservatoryDemo
from weavex.debug_server import create_app
from weavex.protocol import Decision, answer_payload
from weavex.provider import ScriptedModel


def runtime_for_demo():
    return build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="not stepped"))
    )


def answer_as_broker(runtime, question, text: str) -> None:
    runtime.queue.clear()
    runtime.reply_from_actor(
        runtime.actors[runtime.entry_broker_id],
        question,
        kind="answer",
        payload=answer_payload(text),
    )


def test_observatory_demo_only_prepares_world_and_human_questions() -> None:
    runtime = runtime_for_demo()
    demo = ObservatoryDemo()

    first = demo.load(runtime)

    world = runtime.snapshot()["semantic_world"]
    assert [actor.spec.actor_id for actor in runtime.actors.values()] == ["broker"]
    assert len(world["objects"]) == 2
    assert len(world["references"]) == 1
    assert list(runtime.queue) == [first]
    assert first.sender == "human"
    assert first.recipient == "broker"
    addresses = first.payload["interpretation_context"]["semantic_addresses"]
    assert len(addresses) == 1
    assert addresses[0]["kind"] == "object"
    assert demo.public_code not in first.payload["raw_question"]
    assert demo.signature not in json.dumps(
        demo.public_data(runtime), ensure_ascii=False
    )


def test_observatory_demo_advances_only_after_each_direct_answer() -> None:
    runtime = runtime_for_demo()
    demo = ObservatoryDemo()
    first = demo.load(runtime)
    runtime.queue.clear()

    with pytest.raises(ValueError, match="no direct Answer"):
        demo.advance(runtime)

    answer_as_broker(runtime, first, "first")
    second = demo.advance(runtime)
    assert second is not None
    assert second.payload["interpretation_context"]["semantic_addresses"] == []
    assert demo.signature not in second.payload["raw_question"]

    answer_as_broker(runtime, second, "second")
    third = demo.advance(runtime)
    assert third is not None
    assert demo.fresh_text in third.payload["raw_question"]
    assert demo.signature not in json.dumps(third.payload, ensure_ascii=False)

    answer_as_broker(runtime, third, "third")
    assert demo.advance(runtime) is None
    assert demo.public_data(runtime)["complete"] is True


def test_debug_api_loads_and_resets_the_demo_charter() -> None:
    client = TestClient(create_app(runtime_for_demo()))

    audience_page = client.get("/demo")
    assert audience_page.status_code == 200
    assert "失落天文台" in audience_page.text
    assert "/debug/demo.js" in audience_page.text

    loaded = client.post("/api/demo/load")

    assert loaded.status_code == 200
    state = loaded.json()["state"]
    assert state["demo"]["active"] is True
    assert state["demo"]["phase"] == 1
    assert state["debug_control"] == {"step_in_flight": False}
    assert len(state["queue"]) == 1
    assert len(state["semantic_world"]["objects"]) == 2
    blocked = client.post("/api/demo/next")
    assert blocked.status_code == 409

    reset = client.post("/api/reset")

    assert reset.status_code == 200
    assert reset.json()["demo"]["active"] is False
    assert reset.json()["semantic_world"] == {"objects": [], "references": []}
