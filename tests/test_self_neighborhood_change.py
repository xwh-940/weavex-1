"""A writer's own observed-neighborhood change shares the same frontier semantics."""

from __future__ import annotations

import pytest

from weavex.arrival import LocalWorldDelta
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.protocol import Decision, SemanticHandle, question_payload
from weavex.provider import ScriptedModel
from weavex.situation import situated_input_data
from weavex.world import SemanticWorld


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("grounding_kind", "q1_reopens"),
    (("observation", True), ("object", False)),
)
async def test_self_write_advances_only_declared_open_neighborhood_dependencies(
    grounding_kind: str,
    q1_reopens: bool,
) -> None:
    world = SemanticWorld()
    anchor = world.add_object("observed subject", object_id="object:anchor")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors[runtime.entry_broker_id]
    actor_id = str(
        runtime.create_agent(
            broker,
            purpose="judge a subject and later add one record",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_to", "world_add_reference"}),
        )["actor_id"]
    )
    actor = runtime.actors[actor_id]
    anchor_handle = SemanticHandle("object", anchor.object_id)
    bystander_id = str(
        runtime.create_agent(
            broker,
            purpose="observe the same subject from another position",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    runtime.local_worlds.include_addresses(bystander_id, (anchor_handle,))
    runtime.local_worlds.observe_neighborhood(
        bystander_id,
        question_id="question:bystander",
        address=anchor_handle,
        direction="incoming",
    )

    root_1 = runtime.inject_human(
        "judge current records",
        semantic_addresses=(anchor_handle,),
    )
    runtime.queue.clear()
    q1 = runtime.send_from_actor(
        broker,
        root_1,
        recipient=actor_id,
        kind="question",
        payload=question_payload(
            "judge current records",
            semantic_addresses=(anchor_handle,),
        ),
        derived_from=(root_1.act_id,),
    )
    runtime.queue.clear()
    host_1 = runtime.turn_host(actor, q1)
    await host_1.execute(ToolProposal("receive-1", "receive_input"))
    read = await host_1.execute(
        ToolProposal(
            "read",
            "world_references_to",
            {
                "question_id": q1.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )
    observation_id = str(read.observation.value["observation"]["observation_id"])
    source_id = observation_id if grounding_kind == "observation" else anchor.object_id
    answered = await host_1.execute(
        ToolProposal(
            "answer-1",
            "answer_question",
            {
                "question_id": q1.act_id,
                "answer": "no direct records",
                "semantic_addresses": [],
                "grounding_inputs": [{"kind": grounding_kind, "id": source_id}],
            },
        )
    )
    q1_grounding_id = str(answered.observation.value["grounding_id"])
    assert runtime.answer_responsibilities(actor_id) == ()

    record = world.add_object("self-authored record", object_id="object:record")
    record_handle = SemanticHandle("object", record.object_id)
    root_2 = runtime.inject_human(
        "add the given record",
        semantic_addresses=(record_handle, anchor_handle),
    )
    runtime.queue.clear()
    q2 = runtime.send_from_actor(
        broker,
        root_2,
        recipient=actor_id,
        kind="question",
        payload=question_payload(
            "add the given record",
            semantic_addresses=(record_handle, anchor_handle),
        ),
        derived_from=(root_2.act_id,),
    )
    runtime.queue.clear()
    host_2 = runtime.turn_host(actor, q2)
    await host_2.execute(ToolProposal("receive-2", "receive_input"))
    schemas_before_write = {
        item["function"]["name"]: item for item in host_2.tool_schemas()
    }
    write_description = schemas_before_write["world_add_reference"]["function"][
        "description"
    ]
    assert "observed_neighborhood_change" in write_description
    assert "answer_responsibilities_added" in write_description
    assert "不会排入重复的自我 delta" in write_description
    assert "是否形成新 Answer 或 retain" in write_description
    write = await host_2.execute(
        ToolProposal(
            "write",
            "world_add_reference",
            {
                "question_id": q2.act_id,
                "source_object_id": record.object_id,
                "target": {"kind": "object", "id": anchor.object_id},
                "meaning": "direct record for the subject",
            },
        )
    )

    assert write.observation.ok is True
    effect = write.observation.value["observed_neighborhood_change"]
    assert effect["kind"] == "semantic_neighborhood_change"
    assert effect["observation_relations"][0]["observation_id"] == observation_id
    assert [
        item["reference_id"]
        for item in effect["local_world_change"]["references_added"]
    ] == [write.observation.value["reference_id"]]
    deltas = [item for item in runtime.queue if isinstance(item, LocalWorldDelta)]
    assert [item.recipient for item in deltas] == [bystander_id]
    assert all(item.recipient != actor_id for item in deltas)

    added_responsibilities = effect["answer_responsibilities_added"]
    assert [item["act_id"] for item in added_responsibilities] == (
        [q1.act_id] if q1_reopens else []
    )
    if q1_reopens:
        added = added_responsibilities[0]
        projected = next(
            item
            for item in situated_input_data(host_2.situation())[
                "answer_responsibilities"
            ]
            if item["act_id"] == q1.act_id
        )
        assert added == projected
        assert added["payload"]["raw_question"] == "judge current records"
        assert (
            added["response_frontier"]["pending_changes"][0]["through_grounding_id"]
            == q1_grounding_id
        )
    responsibilities = runtime.answer_responsibilities(actor_id)
    assert responsibilities == ((q1, q2) if q1_reopens else (q2,))
    q1_state = runtime.question_frontiers.state(actor_id, q1.act_id)
    assert bool(q1_state.pending_changes) is q1_reopens
    if q1_reopens:
        assert q1_state.pending_changes[0].through_grounding_id == q1_grounding_id

    schemas = {item["function"]["name"]: item for item in host_2.tool_schemas()}
    assert (
        "enum"
        not in schemas["retain_answer"]["function"]["parameters"]["properties"][
            "question_id"
        ]
    )
    if q1_reopens:
        assert runtime.question_frontiers.validate_retain(
            runtime.inquiry, actor_id, q1.act_id
        )
    else:
        with pytest.raises(ValueError, match="unjudged"):
            runtime.question_frontiers.validate_retain(
                runtime.inquiry, actor_id, q1.act_id
            )
