"""Shared Actor grounding and transitive Local World change sensitivity."""

from __future__ import annotations

import pytest

from weavex.arrival import LocalWorldDelta
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.grounding import GroundingGraph, GroundingInput
from weavex.protocol import Decision, SemanticHandle, question_payload
from weavex.provider import ScriptedModel
from weavex.world import ObjectAddress, SemanticWorld


def test_grounding_dag_composes_observation_sensitivity_without_semantic_judgment() -> (
    None
):
    graph = GroundingGraph()
    first = graph.create(
        actor_id="agent",
        question_id="q1",
        node_kind="answer",
        public_answer_id="a1",
        inputs=(
            GroundingInput("observation", "observation-1"),
            GroundingInput("object", "object:evidence"),
        ),
    )
    second = graph.create(
        actor_id="agent",
        question_id="q2",
        node_kind="answer",
        public_answer_id="a2",
        inputs=(GroundingInput("grounding", first.grounding_id),),
    )
    fixed = graph.create(
        actor_id="agent",
        question_id="q3",
        node_kind="answer",
        public_answer_id="a3",
        inputs=(GroundingInput("object", "object:evidence"),),
    )

    affected = graph.dependent_roots(
        "agent",
        {
            "q1": first.grounding_id,
            "q2": second.grounding_id,
            "q3": fixed.grounding_id,
        },
        frozenset({"observation-1"}),
    )

    assert affected == {
        "q1": first.grounding_id,
        "q2": second.grounding_id,
    }


def test_one_agent_neighborhood_observation_has_stable_identity_and_many_origins() -> (
    None
):
    world = SemanticWorld()
    anchor = world.add_object("anchor", object_id="object:anchor")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    actor_id = str(
        runtime.create_agent(
            broker,
            purpose="observe",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    runtime.local_worlds.include_addresses(
        actor_id,
        (SemanticHandle("object", anchor.object_id),),
    )

    first = runtime.local_worlds.observe_neighborhood(
        actor_id,
        question_id="q1",
        address=SemanticHandle("object", anchor.object_id),
        direction="incoming",
    )
    second = runtime.local_worlds.observe_neighborhood(
        actor_id,
        question_id="q2",
        address=SemanticHandle("object", anchor.object_id),
        direction="incoming",
    )

    assert second.observation_id == first.observation_id
    assert second.established_from_question_ids == ("q1", "q2")
    assert len(runtime.local_worlds.observations_for(actor_id)) == 1


@pytest.mark.asyncio
async def test_reused_grounding_reopens_two_questions_without_a_second_read() -> None:
    world = SemanticWorld()
    anchor = world.add_object("subject", object_id="object:subject")
    baseline = world.add_object("SAFE and ARCHIVE-OK", object_id="object:baseline")
    world.add_reference(
        baseline.object_id,
        ObjectAddress(anchor.object_id),
        reference_id="reference:baseline",
    )
    model = ScriptedModel(
        lambda _value: Decision("stop", reason="inspect pending gaps")
    )
    runtime = build_default_runtime(model, world=world)
    broker = runtime.actors["broker"]
    actor_id = str(
        runtime.create_agent(
            broker,
            purpose="reuse one persistent grounding graph",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_to"}),
        )["actor_id"]
    )
    actor = runtime.actors[actor_id]

    def question(raw: str):
        root = runtime.inject_human(
            raw,
            semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        )
        runtime.queue.clear()
        value = runtime.send_from_actor(
            broker,
            root,
            recipient=actor_id,
            kind="question",
            payload=question_payload(
                raw,
                semantic_addresses=(SemanticHandle("object", anchor.object_id),),
            ),
            derived_from=(root.act_id,),
        )
        runtime.queue.clear()
        return value

    q1 = question("judge safety")
    first_host = runtime.turn_host(actor, q1)
    await first_host.execute(ToolProposal("receive-q1", "receive_input"))
    read = await first_host.execute(
        ToolProposal(
            "read-q1",
            "world_references_to",
            {
                "question_id": q1.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )
    observation_id = str(read.observation.value["observation"]["observation_id"])
    acquisition_id = str(read.observation.value["acquisition_grounding_id"])
    assert runtime.groundings.node(acquisition_id).inputs[0] == GroundingInput(
        "observation", observation_id
    )
    first_answer = await first_host.execute(
        ToolProposal(
            "answer-q1",
            "answer_question",
            {
                "question_id": q1.act_id,
                "answer": "SAFE",
                "semantic_addresses": [],
                "grounding_inputs": [
                    {"kind": "observation", "id": observation_id},
                    {"kind": "object", "id": baseline.object_id},
                ],
            },
        )
    )
    g1 = str(first_answer.observation.value["grounding_id"])
    runtime.queue.clear()

    q2 = question("judge archive")
    second_host = runtime.turn_host(actor, q2)
    await second_host.execute(ToolProposal("receive-q2", "receive_input"))
    second_answer = await second_host.execute(
        ToolProposal(
            "answer-q2",
            "answer_question",
            {
                "question_id": q2.act_id,
                "answer": "ARCHIVE-OK",
                "semantic_addresses": [],
                "grounding_inputs": [{"kind": "grounding", "id": g1}],
            },
        )
    )
    g2 = str(second_answer.observation.value["grounding_id"])
    runtime.queue.clear()

    assert [
        item.established_from_question_ids
        for item in runtime.local_worlds.observations_for(actor_id)
    ] == [(q1.act_id,)]
    assert runtime.groundings.node(g2).inputs == (GroundingInput("grounding", g1),)

    later = world.add_object("RISK and ARCHIVE-BAD", object_id="object:later")
    writer_id = str(
        runtime.create_agent(
            broker,
            purpose="write",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    runtime.local_worlds.include_addresses(
        writer_id,
        (
            SemanticHandle("object", later.object_id),
            SemanticHandle("object", anchor.object_id),
        ),
    )
    reference = runtime.local_worlds.add_reference(
        writer_id,
        later.object_id,
        SemanticHandle("object", anchor.object_id),
    )
    runtime.publish_reference_change(writer_id, str(reference["reference_id"]))

    handled = await runtime.step()

    assert isinstance(handled, LocalWorldDelta)
    assert model.calls[-1].answer_responsibilities == (q1, q2)
    assert runtime.answer_responsibilities(actor_id) == (q1, q2)
    frontiers = {
        item["question_id"]: item
        for item in runtime.question_frontiers.data_for_actor(actor_id)
    }
    assert frontiers[q1.act_id]["pending_changes"][0]["through_grounding_id"] == g1
    assert frontiers[q2.act_id]["pending_changes"][0]["through_grounding_id"] == g2


def test_grounding_rejects_cross_actor_private_reuse() -> None:
    graph = GroundingGraph()
    private = graph.create(
        actor_id="first",
        question_id="q1",
        node_kind="answer",
        public_answer_id="a1",
        inputs=(),
    )

    with pytest.raises(PermissionError, match="own private grounding"):
        graph.create(
            actor_id="second",
            question_id="q2",
            node_kind="answer",
            public_answer_id="a2",
            inputs=(GroundingInput("grounding", private.grounding_id),),
        )
