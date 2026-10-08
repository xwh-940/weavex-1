"""Executable World Understanding scenarios for the inquiry prototype."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from weavex.defaults import build_default_runtime
from weavex.debug_server import create_app
from weavex.protocol import (
    Decision,
    SemanticHandle,
    answer_payload,
    question_payload,
)
from weavex.provider import NativeModelInput, ScriptedModel
from weavex.world import ObjectAddress, SemanticWorld


def _last_source(value: NativeModelInput) -> str:
    return value.observations[-1].source if value.observations else ""


def _single_agent_script(seed_id: str, reference_meaning: str):
    def decide(value: NativeModelInput) -> Decision:
        source = _last_source(value)
        if value.actor.role == "broker" and value.trigger.sender == "human":
            if not source:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "Resolve one semantic Question",
                        "budget": {"world_ops": 1},
                        "capabilities": ["world_references_from"],
                    },
                )
            return Decision(
                "send",
                recipient=value.observations[-1].value["actor_id"],
                act_kind="question",
                payload=dict(value.trigger.payload),
                derived_from=(value.trigger.act_id,),
            )
        if value.actor.role == "agent":
            assert value.local_world["domain"] == "semantic"
            if not source:
                assert {item["object_id"] for item in value.local_world["objects"]} == {
                    seed_id
                }
                return Decision(
                    "call",
                    name="world_references_from",
                    arguments={"object_id": seed_id},
                )
            assert any(
                item["content"] == "related meaning"
                for item in value.local_world["objects"]
            )
            assert any(
                item["meaning"] == reference_meaning
                for item in value.local_world["references"]
            )
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("resolved from the semantic Local World"),
            )
        if value.actor.role == "broker" and value.trigger.kind == "answer":
            return Decision(
                "send",
                recipient="human",
                act_kind="answer",
                payload=dict(value.trigger.payload),
            )
        raise AssertionError((value.actor.role, value.trigger.kind, source))

    return decide


@pytest.mark.asyncio
async def test_question_context_materializes_only_for_broker_created_agent() -> None:
    world = SemanticWorld()
    seed = world.add_object("starting meaning", object_id="object:seed")
    related = world.add_object("related meaning", object_id="object:related")
    world.add_reference(
        seed.object_id,
        ObjectAddress(related.object_id),
        meaning="related meaning supplies the requested source-relative result",
        reference_id="reference:seed-related",
    )
    model = ScriptedModel(
        _single_agent_script(
            seed.object_id,
            "related meaning supplies the requested source-relative result",
        )
    )
    runtime = build_default_runtime(model, world=world)

    root = runtime.inject_human(
        "Resolve the root Question",
        interpretation_content="scope: one semantic relation",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
        intent="answer_depth: one conclusion",
    )
    handled = await runtime.run_until_idle()

    assert handled == 3
    assert [item.kind for item in runtime.threads[root.thread_id]] == [
        "question",
        "question",
        "answer",
        "answer",
    ]
    assert set(runtime.actors) == {"broker", "agent-1"}
    assert runtime.actors["agent-1"].initial_budget == {"world_ops": 1}
    assert all(
        call.local_world["domain"] == "coordination"
        and "objects" not in call.local_world
        for call in model.calls
        if call.actor.role == "broker"
    )
    assert all(
        not any(tool["name"].startswith("world_") for tool in call.capability_catalog)
        for call in model.calls
        if call.actor.role == "broker"
    )


def _world_change_script(target_id: str):
    def decide(value: NativeModelInput) -> Decision:
        source = _last_source(value)
        if value.actor.role == "broker" and value.trigger.sender == "human":
            if not source:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "Construct one needed meaning",
                        "budget": {"world_ops": 2},
                        "capabilities": [
                            "world_add_object",
                            "world_add_reference",
                        ],
                    },
                )
            return Decision(
                "send",
                recipient=value.observations[-1].value["actor_id"],
                act_kind="question",
                payload=dict(value.trigger.payload),
                derived_from=(value.trigger.act_id,),
            )
        if value.actor.role == "agent" and not source:
            return Decision(
                "call",
                name="world_add_object",
                arguments={"content": "meaning created to complete this Local World"},
            )
        if source == "world_add_object":
            return Decision(
                "call",
                name="world_add_reference",
                arguments={
                    "source_object_id": value.observations[-1].value["object_id"],
                    "target": {"kind": "object", "id": target_id},
                },
            )
        if value.actor.role == "agent":
            created = next(
                item.value
                for item in value.observations
                if item.source == "world_add_object"
            )
            connected = value.observations[-1].value
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(
                    "the missing meaning now exists",
                    semantic_addresses=(
                        SemanticHandle("object", created["object_id"]),
                        SemanticHandle("reference", connected["reference_id"]),
                    ),
                ),
            )
        return Decision(
            "send",
            recipient="human",
            act_kind="answer",
            payload=dict(value.trigger.payload),
        )

    return decide


@pytest.mark.asyncio
async def test_agent_world_change_naturally_completes_its_local_world() -> None:
    world = SemanticWorld()
    target = world.add_object("existing target", object_id="object:target")
    runtime = build_default_runtime(
        ScriptedModel(_world_change_script(target.object_id)),
        world=world,
    )

    runtime.inject_human(
        "Create and connect one meaning",
        interpretation_content="Use the existing target as the interpretation anchor.",
        semantic_addresses=(SemanticHandle("object", target.object_id),),
        intent="result: connected meaning",
    )
    await runtime.run_until_idle()

    snapshot = runtime.local_worlds.world.snapshot()
    assert [value.content for value in snapshot.objects.values()] == [
        "existing target",
        "meaning created to complete this Local World",
    ]
    local_data = runtime.snapshot()["agent_local_worlds"][0]
    local = local_data["world"]
    assert len(local["objects"]) == 2
    assert len(local["references"]) == 1
    assert "budget" not in local
    assert list(local_data["question_budgets"].values()) == [{"world_ops": 0}]


@pytest.mark.asyncio
async def test_reference_meaning_reaches_another_agent_from_source() -> None:
    world = SemanticWorld()
    source = world.add_object("decision record", object_id="object:decision")
    target = world.add_object("stress report token 73", object_id="object:report")
    reference_meaning = "the report is the record's reason to reject immediate release"

    def script(value: NativeModelInput) -> Decision:
        observation_source = _last_source(value)
        if value.actor.actor_id == "agent-1":
            if not observation_source:
                return Decision(
                    "call",
                    name="world_add_reference",
                    arguments={
                        "source_object_id": source.object_id,
                        "target": {"kind": "object", "id": target.object_id},
                        "meaning": reference_meaning,
                    },
                )
            reference_id = value.observations[-1].value["reference_id"]
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(
                    "the source-relative role now persists",
                    semantic_addresses=(SemanticHandle("reference", reference_id),),
                ),
            )
        if value.actor.actor_id == "agent-2":
            if not observation_source:
                assert [item["object_id"] for item in value.local_world["objects"]] == [
                    source.object_id
                ]
                assert value.local_world["references"] == []
                return Decision(
                    "call",
                    name="world_references_from",
                    arguments={"object_id": source.object_id},
                )
            assert any(
                item["meaning"] == reference_meaning
                for item in value.local_world["references"]
            )
            assert any(
                item["content"] == "stress report token 73"
                for item in value.local_world["objects"]
            )
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(
                    reference_meaning,
                    semantic_addresses=(SemanticHandle("object", target.object_id),),
                ),
            )
        raise AssertionError(value.actor.actor_id)

    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    writer_id = str(
        runtime.create_agent(
            broker,
            purpose="write one source-relative meaning",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_add_reference"}),
        )["actor_id"]
    )
    reader_id = str(
        runtime.create_agent(
            broker,
            purpose="resolve one source-local meaning",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_from"}),
        )["actor_id"]
    )

    writer_root = runtime.inject_human(
        "Make the report's role persist in the decision record",
        semantic_addresses=(
            SemanticHandle("object", source.object_id),
            SemanticHandle("object", target.object_id),
        ),
    )
    runtime.queue.clear()
    runtime.send_from_actor(
        broker,
        writer_root,
        recipient=writer_id,
        kind="question",
        payload=dict(writer_root.payload),
        derived_from=(writer_root.act_id,),
    )
    await runtime.step()
    runtime.queue.clear()

    reader_root = runtime.inject_human(
        "Recover the report's role starting only from the decision record",
        semantic_addresses=(SemanticHandle("object", source.object_id),),
    )
    runtime.queue.clear()
    runtime.send_from_actor(
        broker,
        reader_root,
        recipient=reader_id,
        kind="question",
        payload=dict(reader_root.payload),
        derived_from=(reader_root.act_id,),
    )
    await runtime.step()

    reference = next(iter(runtime.local_worlds.world.snapshot().references.values()))
    assert reference.meaning == reference_meaning
    reader_answer = next(
        act
        for act in runtime.threads[reader_root.thread_id]
        if act.kind == "answer" and act.sender == reader_id
    )
    assert reader_answer.payload == answer_payload(
        reference_meaning,
        semantic_addresses=(SemanticHandle("object", target.object_id),),
    )
    assert list(runtime.actors[writer_id].question_budgets.values()) == [
        {"world_ops": 0}
    ]
    assert list(runtime.actors[reader_id].question_budgets.values()) == [
        {"world_ops": 0}
    ]


def _distributed_context_script(root_id: str, frontier_id: str):
    def decide(value: NativeModelInput) -> Decision:
        source = _last_source(value)
        if value.actor.role == "broker" and value.trigger.sender == "human":
            if not source:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "Hold the root Inquiry",
                        "budget": {"world_ops": 1},
                        "capabilities": ["world_references_from"],
                    },
                )
            return Decision(
                "send",
                recipient="agent-1",
                act_kind="question",
                payload=dict(value.trigger.payload),
                derived_from=(value.trigger.act_id,),
            )
        if value.actor.actor_id == "agent-1" and value.trigger.kind == "question":
            if not source:
                return Decision(
                    "call",
                    name="world_references_from",
                    arguments={"object_id": root_id},
                )
            return Decision(
                "send",
                recipient="broker",
                act_kind="question",
                payload=question_payload(
                    "What interpretation resolves this frontier?",
                    interpretation_content="Interpret only the frontier meaning.",
                    semantic_addresses=(SemanticHandle("object", frontier_id),),
                    intent="answer_depth: one derived meaning",
                ),
            )
        if (
            value.actor.role == "broker"
            and value.trigger.sender == "agent-1"
            and value.trigger.kind == "question"
        ):
            if not source:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "Resolve one projected frontier",
                        "budget": {"world_ops": 1},
                        "capabilities": ["world_add_object"],
                    },
                )
            return Decision(
                "send",
                recipient="agent-2",
                act_kind="question",
                payload=dict(value.trigger.payload),
                derived_from=(value.trigger.act_id,),
            )
        if value.actor.actor_id == "agent-2":
            if not source:
                assert {item["object_id"] for item in value.local_world["objects"]} == {
                    frontier_id
                }
                return Decision(
                    "call",
                    name="world_add_object",
                    arguments={"content": "derived answer meaning"},
                )
            created_id = value.observations[-1].value["object_id"]
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(
                    "frontier interpreted",
                    semantic_addresses=(SemanticHandle("object", created_id),),
                ),
            )
        if (
            value.actor.role == "broker"
            and value.trigger.sender == "agent-2"
            and value.trigger.kind == "answer"
        ):
            return Decision(
                "reply",
                act_kind="answer",
                payload=dict(value.trigger.payload),
            )
        if value.actor.actor_id == "agent-1" and value.trigger.kind == "answer":
            assert any(
                item["content"] == "derived answer meaning"
                for item in value.local_world["objects"]
            )
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("root Question resolved after C_n+1"),
            )
        if value.actor.role == "broker" and value.trigger.sender == "agent-1":
            return Decision(
                "reply",
                act_kind="answer",
                payload=dict(value.trigger.payload),
            )
        raise AssertionError((value.actor.actor_id, value.trigger.kind, source))

    return decide


@pytest.mark.asyncio
async def test_answer_addresses_iterate_original_agent_context_through_broker() -> None:
    world = SemanticWorld()
    root_meaning = world.add_object("root meaning", object_id="object:root")
    frontier = world.add_object("frontier meaning", object_id="object:frontier")
    world.add_reference(
        root_meaning.object_id,
        ObjectAddress(frontier.object_id),
        reference_id="reference:root-frontier",
    )
    model = ScriptedModel(
        _distributed_context_script(root_meaning.object_id, frontier.object_id)
    )
    runtime = build_default_runtime(model, world=world)

    root = runtime.inject_human(
        "Resolve a distributed Inquiry",
        semantic_addresses=(SemanticHandle("object", root_meaning.object_id),),
        intent="result: integrated answer",
    )
    handled = await runtime.run_until_idle()

    assert handled == 7
    acts = runtime.threads[root.thread_id]
    assert [item.kind for item in acts] == [
        "question",
        "question",
        "question",
        "question",
        "answer",
        "answer",
        "answer",
        "answer",
    ]
    gap = acts[2]
    assert gap.payload["interpretation_context"]["semantic_addresses"] == [
        {"kind": "object", "id": frontier.object_id}
    ]
    assert gap.derived_from == (acts[1].act_id,)
    assert acts[3].reply_to == gap.act_id
    assert acts[3].derived_from == (gap.act_id,)
    assert acts[5].reply_to == gap.act_id
    assert acts[6].reply_to == acts[1].act_id
    assert acts[7].reply_to == root.act_id
    first_agent_inputs = [
        call for call in model.calls if call.actor.actor_id == "agent-1"
    ]
    assert not any(
        item["content"] == "derived answer meaning"
        for item in first_agent_inputs[-2].local_world["objects"]
    )
    assert any(
        item["content"] == "derived answer meaning"
        for item in first_agent_inputs[-1].local_world["objects"]
    )


def _incremental_script(anchor_id: str):
    def decide(value: NativeModelInput) -> Decision:
        source = _last_source(value)
        if value.actor.role == "broker" and value.trigger.sender == "human":
            if not source:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "Obtain one missing parameter, then answer",
                        "budget": {},
                        "capabilities": [],
                    },
                )
            return Decision(
                "send",
                recipient="agent-1",
                act_kind="question",
                payload=dict(value.trigger.payload),
                derived_from=(value.trigger.act_id,),
            )
        if value.actor.actor_id == "agent-1" and value.trigger.kind == "question":
            return Decision(
                "send",
                recipient="broker",
                act_kind="incremental",
                payload={"content": "Which historical period should I use?"},
            )
        if value.actor.role == "broker" and value.trigger.kind == "incremental":
            return Decision(
                "send",
                recipient="agent-1",
                act_kind="incremental",
                payload={"content": "Use only the current historical period."},
            )
        if value.actor.actor_id == "agent-1" and value.trigger.kind == "incremental":
            original = next(
                act
                for act in value.visible_acts
                if act.sender == "broker" and act.kind == "question"
            )
            assert any(
                item["object_id"] == anchor_id for item in value.local_world["objects"]
            )
            return Decision(
                "send",
                recipient="broker",
                act_kind="answer",
                payload=answer_payload("clarified answer"),
                reply_to=original.act_id,
            )
        if value.actor.role == "broker" and value.trigger.kind == "answer":
            root = next(act for act in value.visible_acts if act.sender == "human")
            return Decision(
                "send",
                recipient="human",
                act_kind="answer",
                payload=dict(value.trigger.payload),
                reply_to=root.act_id,
            )
        raise AssertionError((value.actor.actor_id, value.trigger.kind, source))

    return decide


@pytest.mark.asyncio
async def test_one_world_understanding_question_supports_incremental_exchange() -> None:
    world = SemanticWorld()
    anchor = world.add_object("historical anchor", object_id="object:history")
    runtime = build_default_runtime(
        ScriptedModel(_incremental_script(anchor.object_id)),
        world=world,
    )

    root = runtime.inject_human(
        "Explain the change in that period",
        interpretation_content="that period: the current historical period",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        intent="depth: brief explanation",
    )
    handled = await runtime.run_until_idle()

    assert handled == 5
    acts = runtime.threads[root.thread_id]
    assert [item.kind for item in acts] == [
        "question",
        "question",
        "incremental",
        "incremental",
        "answer",
        "answer",
    ]
    assert set(acts[1].payload) == {
        "raw_question",
        "interpretation_context",
        "intent",
    }
    assert acts[2].payload == {"content": "Which historical period should I use?"}
    assert acts[3].payload == {"content": "Use only the current historical period."}
    assert acts[4].reply_to == acts[1].act_id
    assert acts[5].reply_to == root.act_id
    assert all(
        "act_kind" not in json.dumps(message)
        for actor in runtime.actors.values()
        for message in runtime.dialogue_for(actor.spec.actor_id)
    )


def _conflict_script(value: NativeModelInput) -> Decision:
    source = _last_source(value)
    if value.actor.role == "broker" and value.trigger.sender == "human":
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Produce the first interpretation",
                    "budget": {},
                    "capabilities": [],
                },
            )
        return Decision(
            "send",
            recipient="agent-1",
            act_kind="question",
            payload=dict(value.trigger.payload),
            derived_from=(value.trigger.act_id,),
        )
    if value.actor.actor_id == "agent-1":
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("interpretation X"),
        )
    if value.actor.role == "broker" and value.trigger.sender == "agent-1":
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Produce an independent comparison",
                    "budget": {},
                    "capabilities": [],
                },
            )
        root = next(act for act in value.visible_acts if act.sender == "human")
        return Decision(
            "send",
            recipient="agent-2",
            act_kind="question",
            payload=dict(root.payload),
            derived_from=(root.act_id,),
        )
    if value.actor.actor_id == "agent-2":
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("interpretation not-X"),
        )
    if value.actor.role == "broker" and value.trigger.sender == "agent-2":
        answers = [
            act.payload["answer"]
            for act in value.visible_acts
            if act.kind == "answer" and act.sender.startswith("agent-")
        ]
        assert answers == ["interpretation X", "interpretation not-X"]
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Resolve one Answer conflict",
                    "budget": {},
                    "capabilities": [],
                },
            )
        source_questions = tuple(
            act.act_id
            for act in value.visible_acts
            if act.kind == "question" and act.recipient.startswith("agent-")
        )
        return Decision(
            "send",
            recipient="agent-3",
            act_kind="question",
            payload=question_payload(
                "Which interpretation survives validation?",
                interpretation_content=("conflicting_answers: " + " | ".join(answers)),
                intent="result: resolve the conflict",
            ),
            derived_from=source_questions,
        )
    if value.actor.actor_id == "agent-3":
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("validated interpretation X"),
        )
    if value.actor.role == "broker" and value.trigger.sender == "agent-3":
        root = next(act for act in value.visible_acts if act.sender == "human")
        return Decision(
            "send",
            recipient="human",
            act_kind="answer",
            payload=dict(value.trigger.payload),
            reply_to=root.act_id,
        )
    raise AssertionError((value.actor.actor_id, value.trigger.kind, source))


@pytest.mark.asyncio
async def test_broker_resolves_conflicting_answers_by_asking_another_question() -> None:
    model = ScriptedModel(_conflict_script)
    runtime = build_default_runtime(model)

    root = runtime.inject_human(
        "Choose a defensible interpretation",
        interpretation_content="Two independent readings are required.",
        intent="result: validated interpretation",
    )
    handled = await runtime.run_until_idle()

    assert handled == 7
    acts = runtime.threads[root.thread_id]
    assert [item.kind for item in acts] == [
        "question",
        "question",
        "answer",
        "question",
        "answer",
        "question",
        "answer",
        "answer",
    ]
    validation = acts[5]
    assert validation.reply_to == root.act_id
    assert validation.derived_from == (acts[1].act_id, acts[3].act_id)
    assert validation.payload["raw_question"] == (
        "Which interpretation survives validation?"
    )
    assert validation.payload["interpretation_context"]["content"] == (
        "conflicting_answers: interpretation X | interpretation not-X"
    )
    assert acts[-1].payload == answer_payload("validated interpretation X")
    assert all(
        call.local_world["domain"] == "coordination"
        for call in model.calls
        if call.actor.role == "broker"
    )


def _direct_answer_without_write_script(value: NativeModelInput) -> Decision:
    source = _last_source(value)
    if value.actor.role == "broker" and value.trigger.sender == "human":
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Answer from sufficient interpretation context",
                    "budget": {"world_ops": 2},
                    "capabilities": [],
                },
            )
        return Decision(
            "send",
            recipient=value.observations[-1].value["actor_id"],
            act_kind="question",
            payload=dict(value.trigger.payload),
            derived_from=(value.trigger.act_id,),
        )
    if value.actor.role == "agent":
        assert value.trigger.payload["interpretation_context"]["content"] == (
            "known_result: the supplied context is sufficient"
        )
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("answered without externalizing private work"),
        )
    return Decision(
        "reply",
        act_kind="answer",
        payload=dict(value.trigger.payload),
    )


@pytest.mark.asyncio
async def test_agent_may_answer_without_writing_to_osw() -> None:
    model = ScriptedModel(_direct_answer_without_write_script)
    runtime = build_default_runtime(model)

    root = runtime.inject_human(
        "Return the already supplied result",
        interpretation_content="known_result: the supplied context is sufficient",
    )
    handled = await runtime.run_until_idle()

    assert handled == 3
    assert runtime.local_worlds.snapshot_data() == {
        "objects": [],
        "references": [],
    }
    agent_input = next(call for call in model.calls if call.actor.role == "agent")
    assert agent_input.capability_catalog == ()
    assert not any(
        trace.payload.get("source", "").startswith("world_")
        for trace in runtime.traces
        if trace.kind == "tool.observation"
    )
    assert runtime.threads[root.thread_id][-1].payload == answer_payload(
        "answered without externalizing private work"
    )


def _externalized_gap_script(value: NativeModelInput) -> Decision:
    source = _last_source(value)
    if value.actor.role == "broker" and value.trigger.sender == "human":
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Develop the root Local World",
                    "budget": {"world_ops": 1},
                    "capabilities": ["world_add_object"],
                },
            )
        return Decision(
            "send",
            recipient=value.observations[-1].value["actor_id"],
            act_kind="question",
            payload=dict(value.trigger.payload),
            derived_from=(value.trigger.act_id,),
        )
    if value.actor.actor_id == "agent-1" and value.trigger.kind == "question":
        if not source:
            return Decision(
                "call",
                name="world_add_object",
                arguments={
                    "content": "addressable premise projected for an external gap"
                },
            )
        anchor_id = value.observations[-1].value["object_id"]
        return Decision(
            "send",
            recipient="broker",
            act_kind="question",
            payload=question_payload(
                "What conclusion follows from this projected premise?",
                interpretation_content="Use the addressable premise as the gap context.",
                semantic_addresses=(SemanticHandle("object", anchor_id),),
                intent="result: one resolving conclusion",
            ),
        )
    if (
        value.actor.role == "broker"
        and value.trigger.kind == "question"
        and value.trigger.sender == "agent-1"
    ):
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "Resolve one addressable external gap",
                    "budget": {"world_ops": 1},
                    "capabilities": ["world_add_object"],
                },
            )
        return Decision(
            "send",
            recipient=value.observations[-1].value["actor_id"],
            act_kind="question",
            payload=dict(value.trigger.payload),
            derived_from=(value.trigger.act_id,),
        )
    if value.actor.actor_id == "agent-2":
        if not source:
            assert any(
                item["content"] == "addressable premise projected for an external gap"
                for item in value.local_world["objects"]
            )
            return Decision(
                "call",
                name="world_add_object",
                arguments={"content": "external conclusion returned to the root Agent"},
            )
        conclusion_id = value.observations[-1].value["object_id"]
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload(
                "the projected premise is resolved",
                semantic_addresses=(SemanticHandle("object", conclusion_id),),
            ),
        )
    if (
        value.actor.role == "broker"
        and value.trigger.kind == "answer"
        and value.trigger.sender == "agent-2"
    ):
        return Decision(
            "reply",
            act_kind="answer",
            payload=dict(value.trigger.payload),
        )
    if value.actor.actor_id == "agent-1" and value.trigger.kind == "answer":
        assert any(
            item["content"] == "external conclusion returned to the root Agent"
            for item in value.local_world["objects"]
        )
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload(
                "root Question resolved after the externalized gap returned"
            ),
        )
    if value.actor.role == "broker" and value.trigger.kind == "answer":
        return Decision(
            "reply",
            act_kind="answer",
            payload=dict(value.trigger.payload),
        )
    raise AssertionError((value.actor.actor_id, value.trigger.kind, source))


@pytest.mark.asyncio
async def test_agent_may_externalize_context_for_another_agent_to_resolve() -> None:
    model = ScriptedModel(_externalized_gap_script)
    runtime = build_default_runtime(model)

    root = runtime.inject_human("Resolve a Question whose missing premise needs help")
    handled = await runtime.run_until_idle()

    assert handled == 7
    acts = runtime.threads[root.thread_id]
    gap = acts[2]
    anchor = gap.payload["interpretation_context"]["semantic_addresses"][0]
    assert anchor["kind"] == "object"
    assert acts[3].derived_from == (gap.act_id,)
    assert acts[5].reply_to == gap.act_id
    assert acts[-1].payload == answer_payload(
        "root Question resolved after the externalized gap returned"
    )
    assert [
        item["content"] for item in runtime.snapshot()["semantic_world"]["objects"]
    ] == [
        "addressable premise projected for an external gap",
        "external conclusion returned to the root Agent",
    ]
    assert [
        trace.payload["source"]
        for trace in runtime.traces
        if trace.kind == "tool.observation"
        and trace.payload["source"].startswith("world_")
    ] == ["world_add_object", "world_add_object"]


def _intent_constrained_projection_script(value: NativeModelInput) -> Decision:
    source = _last_source(value)
    if value.actor.role == "broker" and value.trigger.sender == "human":
        if not source:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": "处理一个语义投影问题",
                    "budget": {"world_ops": 1},
                    "capabilities": ["world_add_object"],
                },
            )
        return Decision(
            "send",
            recipient=value.observations[-1].value["actor_id"],
            act_kind="question",
            payload=dict(value.trigger.payload),
            derived_from=(value.trigger.act_id,),
        )
    if value.actor.role == "agent":
        intent = value.trigger.payload["intent"]
        projections = {
            "只从小明已知信息和主观视角投影，不得使用规则 R。": (
                "从小明当时的认知看，他接下来会先确认门是否锁好。",
                "小明视角下的下一步行动已经形成。",
            ),
            "只依据规则 R 投影，不采用小明的主观倾向。": (
                "依据规则 R 投影，小明接下来必须立刻离开现场。",
                "规则 R 约束下的下一步行动已经形成。",
            ),
        }
        object_content, answer = projections[intent]
        if not source:
            return Decision(
                "call",
                name="world_add_object",
                arguments={"content": object_content},
            )
        object_id = value.observations[-1].value["object_id"]
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload(
                answer,
                semantic_addresses=(SemanticHandle("object", object_id),),
            ),
        )
    if value.actor.role == "broker" and value.trigger.kind == "answer":
        return Decision(
            "reply",
            act_kind="answer",
            payload=dict(value.trigger.payload),
        )
    raise AssertionError((value.actor.actor_id, value.trigger.kind, source))


@pytest.mark.asyncio
async def test_same_question_and_context_can_project_differently_under_intent() -> None:
    runtime = build_default_runtime(
        ScriptedModel(_intent_constrained_projection_script)
    )
    question = "请投影事件 ABC 结束后小明的下一步动作。"
    context = (
        "人物：小明做事谨慎，遇到不确定情况时通常先确认风险。\n"
        "小明已知：他看见门似乎关上了，但没有亲手确认门锁。\n"
        "规则 R：事件结束后必须立刻离开；小明不知道规则 R。"
    )
    subject_intent = "只从小明已知信息和主观视角投影，不得使用规则 R。"
    rule_intent = "只依据规则 R 投影，不采用小明的主观倾向。"

    subject_root = runtime.inject_human(
        question,
        interpretation_content=context,
        intent=subject_intent,
    )
    rule_root = runtime.inject_human(
        question,
        interpretation_content=context,
        intent=rule_intent,
    )
    handled = await runtime.run_until_idle()

    assert handled == 6
    subject_acts = runtime.threads[subject_root.thread_id]
    rule_acts = runtime.threads[rule_root.thread_id]
    assert subject_acts[1].payload["raw_question"] == question
    assert rule_acts[1].payload["raw_question"] == question
    assert (
        subject_acts[1].payload["interpretation_context"]
        == (rule_acts[1].payload["interpretation_context"])
    )
    assert subject_acts[1].payload["intent"] == subject_intent
    assert rule_acts[1].payload["intent"] == rule_intent
    assert subject_acts[-1].payload["answer"] != rule_acts[-1].payload["answer"]

    subject_actor = runtime.actors[subject_acts[1].recipient]
    rule_actor = runtime.actors[rule_acts[1].recipient]
    assert subject_actor.spec.instructions == rule_actor.spec.instructions
    assert subject_actor.spec.capabilities == rule_actor.spec.capabilities
    assert subject_actor.initial_budget == rule_actor.initial_budget
    subject_local = runtime.local_worlds.view_for(subject_actor.spec.actor_id)
    rule_local = runtime.local_worlds.view_for(rule_actor.spec.actor_id)
    assert [item["content"] for item in subject_local["objects"]] == [
        "从小明当时的认知看，他接下来会先确认门是否锁好。"
    ]
    assert [item["content"] for item in rule_local["objects"]] == [
        "依据规则 R 投影，小明接下来必须立刻离开现场。"
    ]


def test_debug_api_keeps_broker_view_outside_the_semantic_world() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="inspect only"))
    )
    client = TestClient(create_app(runtime))
    assert client.app.title == "WeaveX Semantic Workbench"
    assert client.get("/api/state").json()["debug_control"] == {"step_in_flight": False}

    legacy = client.post("/api/inject", json={"content": "legacy Question"})
    assert legacy.status_code == 400
    malformed_context = client.post(
        "/api/inject",
        json={"raw_question": "Question", "interpretation_context": []},
    )
    assert malformed_context.status_code == 400

    root = client.post(
        "/api/inject",
        json={
            "raw_question": "Inspect this Question",
            "interpretation_context": {
                "content": None,
                "semantic_addresses": [],
            },
            "intent": None,
        },
    )
    assert root.status_code == 200
    assert root.json()["act"]["payload"]["interpretation_context"]["content"] == ""
    assert root.json()["act"]["payload"]["intent"] == ""
    thread_id = root.json()["act"]["thread_id"]
    view = client.get(
        "/api/actor-view",
        params={"actor_id": "broker", "thread_id": thread_id},
    )

    assert view.status_code == 200
    broker_world = view.json()["local_world"]
    assert broker_world["domain"] == "coordination"
    assert broker_world["context"]["context_id"] == "broker"
    assert broker_world["context"]["formed"] is False
    assert broker_world["actors"] == []
    assert broker_world["candidates"] == []
    first = client.post("/api/world-object", json={"content": "debug source"})
    second = client.post("/api/world-object", json={"content": "debug target"})
    source_id = first.json()["semantic_address"]["id"]
    target_id = second.json()["semantic_address"]["id"]
    assert first.json()["semantic_address"]["kind"] == "object"
    reference = client.post(
        "/api/world-reference",
        json={
            "source_object_id": source_id,
            "target": {"kind": "object", "id": target_id},
            "meaning": "target is the source's debug premise",
        },
    )
    assert reference.status_code == 200
    assert reference.json()["semantic_address"]["kind"] == "reference"
    assert len(reference.json()["state"]["semantic_world"]["references"]) == 1
    assert (
        reference.json()["state"]["semantic_world"]["references"][0]["meaning"]
        == "target is the source's debug premise"
    )
    invalid_reference = client.post(
        "/api/world-reference",
        json={
            "source_object_id": source_id,
            "target": {"kind": "object", "id": target_id},
            "meaning": 73,
        },
    )
    assert invalid_reference.status_code == 400
    assert invalid_reference.json()["detail"] == "meaning must be a string"

    stepped = client.post("/api/step")
    assert stepped.status_code == 200
    model_input = next(
        item
        for item in reversed(stepped.json()["state"]["traces"])
        if item["kind"] == "model.input"
    )
    situation = model_input["payload"]
    assert situation["common_instructions"]
    assert situation["actor_instructions"] == runtime.actors["broker"].spec.instructions
    assert (
        situation["derived_current_state"]["answer_responsibilities"][0]["act_id"]
        == root.json()["act"]["act_id"]
    )
    assert situation["activation_observations"] == []
    assert (
        json.loads(situation["messages"][-1]["content"])["value"][
            "answer_responsibilities"
        ][0]["act_id"]
        == root.json()["act"]["act_id"]
    )
    assert {item["function"]["name"] for item in situation["tools"]} >= {
        "create_actor",
        "inspect_inquiry_topology",
    }

    root_act = runtime.threads[thread_id][0]
    incremental = runtime.reply_from_actor(
        runtime.actors["broker"],
        root_act,
        kind="incremental",
        payload={"content": "Provide missing story material."},
    )
    human_reply = client.post(
        "/api/human-reply",
        json={
            "thread_id": thread_id,
            "reply_to": incremental.act_id,
            "content": "The protagonist has no host memories.",
        },
    )
    assert human_reply.status_code == 200
    reply_act = human_reply.json()["act"]
    assert reply_act["reply_to"] == incremental.act_id
    assert reply_act["kind"] == "incremental"
    assert reply_act["payload"] == {"content": "The protagonist has no host memories."}
    obsolete_kind = client.post(
        "/api/human-reply",
        json={
            "thread_id": thread_id,
            "reply_to": incremental.act_id,
            "kind": "continuation",
            "content": "An obsolete kind must not be silently ignored.",
        },
    )
    assert obsolete_kind.status_code == 400
    proactive_incremental = client.post(
        "/api/human-reply",
        json={
            "thread_id": thread_id,
            "reply_to": root_act.act_id,
            "content": "A proactive correction to my open Question.",
        },
    )
    assert proactive_incremental.status_code == 200
    assert proactive_incremental.json()["act"]["reply_to"] == root_act.act_id
    assert proactive_incremental.json()["act"]["kind"] == "incremental"

    page = client.get("/")
    assert page.status_code == 200
    assert "WeaveX Semantic Workbench" in page.text
    assert "OSW 实验夹具" in page.text
    assert 'id="questionTriple"' in page.text
    assert 'id="interpretation"' not in page.text
    assert 'id="recipient"' not in page.text
    assert 'id="operationStatus"' in page.text
    assert 'id="humanReplyTarget"' in page.text
    assert "/api/human-reply" in page.text
    assert "注入成功" in page.text
    assert "derived_from" in page.text
    assert "agentLocalWorlds" in page.text
