"""Persistent semantic-neighborhood observation and delta activation."""

from __future__ import annotations

import pytest

from weavex.arrival import LocalWorldDelta
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.local_world import LocalWorlds
from weavex.protocol import (
    Decision,
    SemanticHandle,
    question_payload,
)
from weavex.provider import NativeModelInput, OpenAICompatibleModel, ScriptedModel
from weavex.situation import situated_input_data
from weavex.world import ObjectAddress, ReferenceAddress, SemanticWorld


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_evidence", "expect_public_update"),
    (
        ("RISK-DELTA: the earlier SAFE result no longer holds", True),
        ("ARCHIVE-DELTA: unrelated historical labeling only", False),
    ),
    ids=("relevant", "irrelevant"),
)
async def test_neighborhood_delta_leaves_relevance_to_the_observer(
    new_evidence: str,
    expect_public_update: bool,
) -> None:
    seen_delta_inputs: list[dict[str, object]] = []

    def script(value: NativeModelInput) -> Decision | ToolProposal:
        assert isinstance(value.trigger, LocalWorldDelta)
        if not value.answer_responsibilities:
            return Decision("stop", reason="all changed frontiers were judged")
        current = situated_input_data(value.situation)
        seen_delta_inputs.append(current)
        tool_names = {
            item["function"]["name"]
            for item in OpenAICompatibleModel.protocol_tools(value)
        }
        assert "send_incremental_act" in tool_names
        assert {"answer_question", "retain_answer"}.issubset(tool_names)
        grounding_id = str(
            value.situation.responsibility_frontiers[question.act_id][
                "latest_grounding_id"
            ]
        )
        if any(
            "RISK-DELTA" in str(item["content"])
            for item in value.local_world["objects"]
        ):
            return ToolProposal(
                "answer-delta",
                "answer_question",
                {
                    "question_id": question.act_id,
                    "answer": "RISK-DELTA changes the earlier SAFE result.",
                    "semantic_addresses": [],
                    "grounding_inputs": [
                        {"kind": "grounding", "id": grounding_id},
                        {"kind": "object", "id": "object:later-evidence"},
                    ],
                },
            )
        return ToolProposal(
            "retain-delta",
            "retain_answer",
            {
                "question_id": question.act_id,
                "grounding_inputs": [{"kind": "grounding", "id": grounding_id}],
            },
        )

    world = SemanticWorld()
    anchor = world.add_object("release subject", object_id="object:release")
    baseline = world.add_object("baseline evidence", object_id="object:baseline")
    world.add_reference(
        baseline.object_id,
        ObjectAddress(anchor.object_id),
        meaning="baseline supports the current release assessment",
        reference_id="reference:baseline",
    )
    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    observer = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="assess release evidence",
                instructions="",
                budget={"world_ops": 1},
                capabilities=frozenset({"world_references_to"}),
            )["actor_id"]
        )
    ]
    writer = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="add later release evidence",
                instructions="",
                budget={"world_ops": 1},
                capabilities=frozenset({"world_add_reference"}),
            )["actor_id"]
        )
    ]
    root = runtime.inject_human(
        "assess the release",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=observer.spec.actor_id,
        kind="question",
        payload=question_payload(
            "Assess the release from evidence directly attached to the subject.",
            semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        ),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    observer_host = runtime.turn_host(observer, question)
    await observer_host.execute(ToolProposal("receive-observer", "receive_input"))
    read = await observer_host.execute(
        ToolProposal(
            "read-incoming",
            "world_references_to",
            {
                "question_id": question.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )
    assert read.observation.ok is True
    assert read.observation.value["references"][0]["reference_id"] == (
        "reference:baseline"
    )
    observation_id = str(read.observation.value["observation"]["observation_id"])
    answered = await observer_host.execute(
        ToolProposal(
            "answer-observer",
            "answer_question",
            {
                "question_id": question.act_id,
                "answer": "SAFE under the currently attached evidence.",
                "semantic_addresses": [],
                "grounding_inputs": [
                    {"kind": "observation", "id": observation_id},
                    {"kind": "object", "id": baseline.object_id},
                ],
            },
        )
    )
    answer_id = str(answered.observation.value["act_id"])
    initial_grounding_id = str(answered.observation.value["grounding_id"])
    runtime.queue.clear()

    later = world.add_object(new_evidence, object_id="object:later-evidence")
    writer_root = runtime.inject_human(
        "attach later evidence",
        semantic_addresses=(
            SemanticHandle("object", later.object_id),
            SemanticHandle("object", anchor.object_id),
        ),
    )
    runtime.queue.clear()
    writer_question = runtime.send_from_actor(
        broker,
        writer_root,
        recipient=writer.spec.actor_id,
        kind="question",
        payload=question_payload(
            "Attach the supplied evidence to the release subject.",
            semantic_addresses=(
                SemanticHandle("object", later.object_id),
                SemanticHandle("object", anchor.object_id),
            ),
        ),
        derived_from=(writer_root.act_id,),
    )
    runtime.queue.clear()
    writer_host = runtime.turn_host(writer, writer_question)
    await writer_host.execute(ToolProposal("receive-writer", "receive_input"))
    write = await writer_host.execute(
        ToolProposal(
            "connect-evidence",
            "world_add_reference",
            {
                "question_id": writer_question.act_id,
                "source_object_id": later.object_id,
                "target": {"kind": "object", "id": anchor.object_id},
                "meaning": "later evidence attached to the release assessment",
            },
        )
    )
    assert write.observation.ok is True
    assert len(runtime.queue) == 1
    delta = runtime.queue[0]
    assert isinstance(delta, LocalWorldDelta)
    assert delta.recipient == observer.spec.actor_id
    assert observer.question_budgets[question.act_id] == {"world_ops": 0}

    handled = await runtime.step()

    assert isinstance(handled, LocalWorldDelta)
    assert handled.delta_id == delta.delta_id
    assert seen_delta_inputs[0]["arrival"]["kind"] == ("semantic_neighborhood_change")
    relation = seen_delta_inputs[0]["arrival"]["observation_relations"][0]
    assert relation == {
        "observation_id": observation_id,
        "observed_address": {"kind": "object", "id": anchor.object_id},
        "direction": "incoming",
        "established_from_question_ids": [question.act_id],
    }
    responsibility = seen_delta_inputs[0]["answer_responsibilities"][0]
    assert responsibility["act_id"] == question.act_id
    assert responsibility["response_frontier"] == {
        "gap": "local_world_changes_after_judged_frontier",
        "latest_answer_id": answer_id,
        "latest_grounding_id": initial_grounding_id,
        "latest_local_change_id": delta.delta_id,
        "latest_judged_change_id": "",
        "pending_changes": [
            {
                "change_id": delta.delta_id,
                "kind": "semantic_neighborhood_change",
                "through_grounding_id": initial_grounding_id,
            }
        ],
    }
    answers = [
        act
        for acts in runtime.threads.values()
        for act in acts
        if act.kind == "answer" and act.reply_to == question.act_id
    ]
    assert len(answers) == (2 if expect_public_update else 1)
    assert runtime.answer_responsibilities(observer.spec.actor_id) == ()
    frontier = runtime.question_frontiers.state(
        observer.spec.actor_id,
        question.act_id,
    )
    assert frontier.latest_judged_change_id == delta.delta_id
    assert frontier.latest_answer_id == answers[-1].act_id
    assert frontier.latest_grounding_id != initial_grounding_id
    assert all(
        not any(act.act_id == delta.delta_id for act in acts)
        for acts in runtime.threads.values()
    )


def test_reference_change_matches_only_exact_observed_neighborhoods() -> None:
    world = SemanticWorld()
    source = world.add_object("source", object_id="object:source")
    target = world.add_object("target", object_id="object:target")
    other = world.add_object("other", object_id="object:other")
    target_reference = world.add_reference(
        other.object_id,
        ObjectAddress(target.object_id),
        reference_id="reference:target",
    )
    locals_ = LocalWorlds(world)
    locals_.include_addresses("outgoing", (SemanticHandle("object", source.object_id),))
    locals_.observe_neighborhood(
        "outgoing",
        question_id="question:outgoing",
        address=SemanticHandle("object", source.object_id),
        direction="outgoing",
    )
    locals_.include_addresses("incoming", (SemanticHandle("object", target.object_id),))
    locals_.observe_neighborhood(
        "incoming",
        question_id="question:incoming",
        address=SemanticHandle("object", target.object_id),
        direction="incoming",
    )
    locals_.include_addresses(
        "reference-incoming",
        (SemanticHandle("reference", target_reference.reference_id),),
    )
    locals_.observe_neighborhood(
        "reference-incoming",
        question_id="question:reference",
        address=SemanticHandle("reference", target_reference.reference_id),
        direction="incoming",
    )

    first = world.add_reference(
        source.object_id,
        ObjectAddress(target.object_id),
        reference_id="reference:first-change",
    )
    first_matches = locals_.observers_for_reference(first)
    assert set(first_matches) == {"outgoing", "incoming"}

    second = world.add_reference(
        source.object_id,
        ReferenceAddress(target_reference.reference_id),
        reference_id="reference:second-change",
    )
    second_matches = locals_.observers_for_reference(
        second,
        exclude_actor_id="outgoing",
    )
    assert set(second_matches) == {"reference-incoming"}


@pytest.mark.asyncio
async def test_queued_deltas_materialize_only_when_each_arrival_is_handled() -> None:
    received_changes: list[list[str]] = []

    def script(value: NativeModelInput) -> Decision:
        assert isinstance(value.trigger, LocalWorldDelta)
        received_changes.append(
            [
                str(item["content"])
                for item in value.trigger.local_world_change["objects_added"]
            ]
        )
        return Decision("stop", reason="recorded one exact delta")

    world = SemanticWorld()
    anchor = world.add_object("watched anchor", object_id="object:watched")
    first = world.add_object("FIRST-DELTA", object_id="object:first-delta")
    second = world.add_object("SECOND-DELTA", object_id="object:second-delta")
    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    observer = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="observe queued changes",
                instructions="",
                budget={"world_ops": 1},
                capabilities=frozenset({"world_references_to"}),
            )["actor_id"]
        )
    ]
    writer = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="write queued changes",
                instructions="",
                budget={},
                capabilities=frozenset(),
            )["actor_id"]
        )
    ]
    root = runtime.inject_human(
        "observe queued changes",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=observer.spec.actor_id,
        kind="question",
        payload=question_payload(
            "Observe the incoming boundary.",
            semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        ),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    host = runtime.turn_host(observer, question)
    await host.execute(ToolProposal("receive", "receive_input"))
    await host.execute(
        ToolProposal(
            "read",
            "world_references_to",
            {
                "question_id": question.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )
    runtime.local_worlds.include_addresses(
        writer.spec.actor_id,
        (
            SemanticHandle("object", anchor.object_id),
            SemanticHandle("object", first.object_id),
            SemanticHandle("object", second.object_id),
        ),
    )
    for source in (first, second):
        reference = runtime.local_worlds.add_reference(
            writer.spec.actor_id,
            source.object_id,
            SemanticHandle("object", anchor.object_id),
        )
        runtime.publish_reference_change(
            writer.spec.actor_id,
            str(reference["reference_id"]),
        )

    before_delivery = runtime.local_worlds.view_for(observer.spec.actor_id)
    assert all(
        "DELTA" not in str(item["content"]) for item in before_delivery["objects"]
    )

    await runtime.step()
    after_first = runtime.local_worlds.view_for(observer.spec.actor_id)
    assert any(item["content"] == "FIRST-DELTA" for item in after_first["objects"])
    assert all(item["content"] != "SECOND-DELTA" for item in after_first["objects"])
    await runtime.step()

    assert received_changes == [["FIRST-DELTA"], ["SECOND-DELTA"]]


@pytest.mark.asyncio
async def test_empty_incoming_read_establishes_a_persistent_observation() -> None:
    world = SemanticWorld()
    anchor = world.add_object("empty anchor", object_id="object:empty-anchor")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    actor = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="observe an empty boundary",
                instructions="",
                budget={"world_ops": 1},
                capabilities=frozenset({"world_references_to"}),
            )["actor_id"]
        )
    ]
    root = runtime.inject_human(
        "observe the empty boundary",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload(
            "Observe the current incoming boundary.",
            semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        ),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    host = runtime.turn_host(actor, question)
    await host.execute(ToolProposal("receive", "receive_input"))

    execution = await host.execute(
        ToolProposal(
            "read-empty",
            "world_references_to",
            {
                "question_id": question.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )

    assert execution.observation.ok is True
    assert execution.observation.value["references"] == []
    assert runtime.local_worlds.view_for(actor.spec.actor_id)[
        "observed_neighborhoods"
    ] == [
        {
            "observation_id": "observation-1",
            "address": {"kind": "object", "id": anchor.object_id},
            "direction": "incoming",
            "established_from_question_ids": [question.act_id],
        }
    ]

    runtime.reset()

    assert runtime.local_worlds.locals_data() == []
