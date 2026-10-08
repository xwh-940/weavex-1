"""Host-only resource accounting over runtime facts and execution traces."""

from __future__ import annotations

import json

import pytest

from weavex.defaults import build_default_runtime
from weavex.protocol import Decision, SemanticHandle, answer_payload, question_payload
from weavex.provider import NativeModelInput, ScriptedModel
from weavex.world import ObjectAddress, SemanticWorld


@pytest.mark.asyncio
async def test_host_costs_are_derived_without_entering_actor_dialogue() -> None:
    runtime = build_default_runtime(
        ScriptedModel(
            lambda _value: Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("direct coordination answer"),
            )
        )
    )
    runtime.inject_human("answer directly")

    await runtime.run_until_idle()

    costs = runtime.host_costs()
    assert costs["schema_version"] == "host-cost-v1"
    assert costs["scope"] == "runtime_since_reset"
    assert costs["cost_model"] == "unweighted_resource_vector"
    assert costs["totals"]["activations"] == {"started": 1, "failed": 0}
    assert costs["totals"]["model"] == {
        "requests": 3,
        "decisions": 3,
        "rejections": 0,
        "usage_reported_requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "prompt_cache_hit_ratio": None,
    }
    assert costs["totals"]["host_tools"] == {
        "attempted": 3,
        "succeeded": 3,
        "failed": 0,
        "by_source": {
            "receive_input": {"attempted": 1, "succeeded": 1, "failed": 0},
            "reply_act": {"attempted": 1, "succeeded": 1, "failed": 0},
            "yield_turn": {"attempted": 1, "succeeded": 1, "failed": 0},
        },
    }
    assert costs["totals"]["act_log_growth"] == {
        "total": 2,
        "by_kind": {"answer": 1, "question": 1},
    }
    assert costs["current_footprint"]["dialogue_messages"] == 7
    assert runtime.snapshot()["host_costs"] == costs
    assert "host_costs" not in json.dumps(
        runtime.dialogue_for("broker"), ensure_ascii=False
    )

    runtime.reset()

    reset_costs = runtime.host_costs()
    assert reset_costs["totals"]["model"]["requests"] == 0
    assert reset_costs["totals"]["act_log_growth"]["total"] == 0
    assert reset_costs["totals"]["dynamic_actor_creations"]["total"] == 0


@pytest.mark.asyncio
async def test_host_costs_keep_failed_and_shifted_agent_work_visible() -> None:
    world = SemanticWorld()
    source = world.add_object("known source", object_id="object:source")
    target = world.add_object("known result", object_id="object:target")
    world.add_reference(
        source.object_id,
        ObjectAddress(target.object_id),
        meaning="source reaches the result",
        reference_id="reference:result",
    )

    def script(value: NativeModelInput) -> Decision:
        question_id = value.answer_responsibilities[0].act_id
        if not value.observations:
            return Decision(
                "call",
                name="world_references_from",
                arguments={
                    "question_id": question_id,
                    "object_id": "object:unknown",
                },
            )
        if len(value.observations) == 1:
            return Decision(
                "call",
                name="world_references_from",
                arguments={
                    "question_id": question_id,
                    "object_id": source.object_id,
                },
            )
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("found the result"),
            reply_to=question_id,
        )

    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        instructions="",
        budget={"world_ops": 2},
        capabilities=frozenset({"world_references_from"}),
    )
    agent_id = str(created["actor_id"])
    root = runtime.inject_human(
        "locate the result",
        semantic_addresses=(SemanticHandle("object", source.object_id),),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent_id,
        kind="question",
        payload=question_payload(
            "locate the result",
            semantic_addresses=(SemanticHandle("object", source.object_id),),
        ),
        derived_from=(root.act_id,),
    )

    handled = await runtime.step()

    assert handled == question
    costs = runtime.host_costs()
    assert costs["totals"]["dynamic_actor_creations"] == {
        "total": 1,
        "agents": 1,
        "brokers": 0,
        "unknown": 0,
    }
    assert costs["totals"]["semantic_world_operations"] == {
        "attempted": 2,
        "succeeded": 1,
        "failed": 1,
        "reads_succeeded": 1,
        "writes_succeeded": 0,
        "objects_created": 0,
        "references_created": 0,
    }
    assert costs["by_actor"][agent_id]["model"]["requests"] == 5
    assert costs["by_actor"][agent_id]["host_tools"]["failed"] == 1
    assert costs["by_actor"][agent_id]["semantic_world_operations"]["succeeded"] == 1
    assert costs["by_actor"]["broker"]["model"]["requests"] == 0
    assert runtime.actors[agent_id].budget_for(question.act_id) == {"world_ops": 1}
    assert costs["current_footprint"]["semantic_objects"] == 2
    assert costs["current_footprint"]["semantic_references"] == 1
    assert costs["current_footprint"]["local_object_memberships"] == 2
    assert costs["current_footprint"]["local_reference_memberships"] == 1
