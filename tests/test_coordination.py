from __future__ import annotations

import pytest

from weavex.defaults import build_default_runtime
from weavex.protocol import (
    Decision,
    SemanticHandle,
    answer_payload,
    question_payload,
)
from weavex.provider import OpenAICompatibleModel, ScriptedModel
from weavex.world import SemanticWorld


@pytest.mark.asyncio
async def test_gap_question_opens_a_dynamic_broker_closed_loop() -> None:
    def script(value):
        actor_id = value.actor.actor_id
        trigger = value.trigger
        if actor_id == "broker" and trigger.sender == "human":
            if not value.local_world["actors"]:
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "root solver",
                        "budget": {},
                        "capabilities": [],
                    },
                )
            return Decision(
                "send",
                recipient="agent-1",
                act_kind="question",
                payload=trigger.payload,
                derived_from=(trigger.act_id,),
            )
        if actor_id == "agent-1" and trigger.kind == "question":
            return Decision(
                "send",
                act_kind="question",
                payload=question_payload("Resolve the missing premise"),
                new_context=True,
            )
        if actor_id == "broker-1" and trigger.kind == "question":
            if not any(
                item["actor_id"] == "agent-2" for item in value.local_world["actors"]
            ):
                return Decision(
                    "call",
                    name="create_actor",
                    arguments={
                        "purpose": "gap solver",
                        "budget": {},
                        "capabilities": [],
                    },
                )
            return Decision(
                "send",
                recipient="agent-2",
                act_kind="question",
                payload=trigger.payload,
                derived_from=(trigger.act_id,),
            )
        if actor_id == "agent-2":
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("missing premise"),
            )
        if actor_id == "broker-1" and trigger.kind == "answer":
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("gap resolved"),
            )
        if actor_id == "agent-1" and trigger.kind == "answer":
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("root resolved"),
            )
        if actor_id == "broker" and trigger.kind == "answer":
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("human result"),
            )
        raise AssertionError((actor_id, trigger.kind, trigger.sender))

    runtime = build_default_runtime(ScriptedModel(script))
    root = runtime.inject_human("Resolve a root Question")

    await runtime.run_until_idle()

    assert set(runtime.actors) == {"broker", "broker-1", "agent-1", "agent-2"}
    final = runtime.threads[root.thread_id][-1]
    assert final.sender == "broker"
    assert final.recipient == "human"
    assert final.payload == answer_payload("human result")
    assert runtime.context_members("broker-1") == frozenset({"agent-1", "agent-2"})
    assert runtime.context_ids_for_agent("agent-1") == ("broker", "broker-1")
    assert runtime.context_ids_for_agent("agent-2") == ("broker-1",)
    agent_input = next(
        value
        for value in runtime.turn_driver.model.calls
        if value.actor.actor_id == "agent-1" and value.trigger.kind == "answer"
    )
    assert all("member_ids" not in card for card in agent_input.external_contexts)
    assert all("own_history" not in card for card in agent_input.external_contexts)
    assert all(
        "source_question_id" not in card for card in agent_input.external_contexts
    )
    cards = {item["context_id"]: item for item in agent_input.external_contexts}
    assert cards == {
        "broker": {
            "context_id": "broker",
            "guaranteed_shared_context": {"origin_question": "Resolve a root Question"},
        },
        "broker-1": {
            "context_id": "broker-1",
            "guaranteed_shared_context": {
                "origin_question": "Resolve the missing premise"
            },
        },
    }


def test_incremental_act_cannot_switch_an_agents_context() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    entry = runtime.actors["broker"]
    source_id = str(
        runtime.create_agent(
            entry,
            purpose="source",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    source = runtime.actors[source_id]
    root = runtime.inject_human("first thread")
    assigned = runtime.send_from_actor(
        entry,
        root,
        recipient=source_id,
        kind="question",
        payload=question_payload("open another context"),
        derived_from=(root.act_id,),
    )
    gap = runtime.send_from_actor(
        source,
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("independent gap"),
        new_context=True,
    )
    other_broker = runtime.actors[gap.recipient]
    runtime.form_group(other_broker, gap, ())

    next_root = runtime.inject_human("second thread")
    next_assigned = runtime.send_from_actor(
        entry,
        next_root,
        recipient=source_id,
        kind="question",
        payload=question_payload("stay in the entry context"),
        derived_from=(next_root.act_id,),
    )

    with pytest.raises(PermissionError, match="visible Act counterpart"):
        runtime.send_from_actor(
            source,
            next_assigned,
            recipient=other_broker.spec.actor_id,
            kind="incremental",
            payload={"content": "must not switch context incrementally"},
        )

    incremental = runtime.send_from_actor(
        source,
        next_assigned,
        recipient=entry.spec.actor_id,
        kind="incremental",
        payload={"content": "valid incremental content"},
    )
    assert incremental.reply_to == next_assigned.act_id


def _assigned_source(runtime):
    broker = runtime.actors["broker"]
    source = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="source",
                instructions="",
                budget={},
            )["actor_id"]
        )
    ]
    first = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="first candidate",
                instructions="",
                budget={},
            )["actor_id"]
        )
    ]
    second = runtime.actors[
        str(
            runtime.create_agent(
                broker,
                purpose="second candidate",
                instructions="",
                budget={},
            )["actor_id"]
        )
    ]
    root = runtime.inject_human("root")
    assigned = runtime.send_from_actor(
        broker,
        root,
        recipient=source.spec.actor_id,
        kind="question",
        payload=question_payload("source work"),
        derived_from=(root.act_id,),
    )
    return source, first, second, assigned


def test_subset_group_keeps_future_agents_away_from_the_old_complement() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    source, first, second, assigned = _assigned_source(runtime)
    gap = runtime.send_from_actor(
        source,
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("new local gap"),
        new_context=True,
    )
    broker = runtime.actors[gap.recipient]
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, gap),
        (),
        runtime.tools.catalog_for(runtime.available_capabilities(broker, gap.act_id)),
    )

    assert {item["actor_id"] for item in value.local_world["candidates"]} == {
        first.spec.actor_id,
        second.spec.actor_id,
    }
    form_tool = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "form_group"
    )
    assert (
        "enum"
        not in form_tool["function"]["parameters"]["properties"]["member_ids"]["items"]
    )
    runtime.form_group(broker, gap, (first.spec.actor_id,))
    born_id = str(
        runtime.create_agent(
            broker,
            purpose="born here",
            instructions="",
            budget={},
        )["actor_id"]
    )
    assert runtime.context_members(broker.spec.actor_id) == frozenset(
        {source.spec.actor_id, first.spec.actor_id, born_id}
    )

    born_question = runtime.send_from_actor(
        broker,
        gap,
        recipient=born_id,
        kind="question",
        payload=question_payload("find another gap"),
        derived_from=(gap.act_id,),
    )
    next_gap = runtime.send_from_actor(
        runtime.actors[born_id],
        born_question,
        recipient="",
        kind="question",
        payload=question_payload("next gap"),
        new_context=True,
    )
    next_broker = runtime.actors[next_gap.recipient]
    next_candidates = runtime.turn_driver.model_input(
        runtime.turn_host(next_broker, next_gap), (), ()
    ).local_world["candidates"]

    assert {item["actor_id"] for item in next_candidates} == {
        source.spec.actor_id,
        first.spec.actor_id,
    }
    with pytest.raises(PermissionError, match="fixed group"):
        runtime.send_from_actor(
            broker,
            gap,
            recipient=second.spec.actor_id,
            kind="question",
            payload=question_payload("late invitation"),
            derived_from=(gap.act_id,),
        )
    with pytest.raises(PermissionError, match="Broker-to-Broker"):
        runtime.send_from_actor(
            broker,
            gap,
            recipient="broker",
            kind="question",
            payload=question_payload("forbidden"),
            derived_from=(gap.act_id,),
        )


def test_coordination_projection_distinguishes_configured_and_new_question_tools() -> (
    None
):
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    entry = runtime.actors["broker"]
    source_id = str(
        runtime.create_agent(
            entry,
            purpose="source",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    unavailable_id = str(
        runtime.create_agent(
            entry,
            purpose="configured without default Question budget",
            instructions="",
            budget={},
            capabilities=frozenset({"world_references_from"}),
        )["actor_id"]
    )
    available_id = str(
        runtime.create_agent(
            entry,
            purpose="configured and available",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_from"}),
        )["actor_id"]
    )
    root = runtime.inject_human("root")
    assigned = runtime.send_from_actor(
        entry,
        root,
        recipient=source_id,
        kind="question",
        payload=question_payload("source work"),
        derived_from=(root.act_id,),
    )
    gap = runtime.send_from_actor(
        runtime.actors[source_id],
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("read one Reference"),
        new_context=True,
    )

    candidates = runtime.turn_driver.model_input(
        runtime.turn_host(runtime.actors[gap.recipient], gap), (), ()
    ).local_world["candidates"]
    by_id = {item["actor_id"]: item for item in candidates}

    assert by_id[unavailable_id]["capabilities"] == ["world_references_from"]
    assert by_id[unavailable_id]["available_capabilities"] == []
    assert by_id[available_id]["capabilities"] == ["world_references_from"]
    assert by_id[available_id]["available_capabilities"] == ["world_references_from"]
    assert all("budget" not in item for item in candidates)
    assert runtime.actors[available_id].question_budgets == {}

    formed_members = {
        item["actor_id"]: item
        for item in runtime.coordination_local_world(entry, root)["actors"]
    }
    assert formed_members[unavailable_id]["available_capabilities"] == []
    assert formed_members[available_id]["available_capabilities"] == [
        "world_references_from"
    ]


def test_equal_initial_members_remain_distinct_contexts_and_can_be_reused() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    source, first, _second, assigned = _assigned_source(runtime)

    first_gap = runtime.send_from_actor(
        source,
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("first independent gap"),
        new_context=True,
    )
    first_broker = runtime.actors[first_gap.recipient]
    runtime.form_group(first_broker, first_gap, (first.spec.actor_id,))

    second_gap = runtime.send_from_actor(
        source,
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("second independent gap"),
        new_context=True,
    )
    second_broker = runtime.actors[second_gap.recipient]
    runtime.form_group(second_broker, second_gap, (first.spec.actor_id,))

    assert first_broker.spec.actor_id != second_broker.spec.actor_id
    assert runtime.context_members(
        first_broker.spec.actor_id
    ) == runtime.context_members(second_broker.spec.actor_id)
    count = len(runtime.actors)
    reused = runtime.send_from_actor(
        source,
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("reuse the first context"),
        context_id=first_broker.spec.actor_id,
    )
    assert reused.recipient == first_broker.spec.actor_id
    assert len(runtime.actors) == count

    first_born = str(
        runtime.create_agent(
            first_broker,
            purpose="first branch",
            instructions="",
            budget={},
        )["actor_id"]
    )
    second_born = str(
        runtime.create_agent(
            second_broker,
            purpose="second branch",
            instructions="",
            budget={},
        )["actor_id"]
    )
    assert first_broker.spec.actor_id in runtime.context_ids_for_agent(first_born)
    assert second_broker.spec.actor_id not in runtime.context_ids_for_agent(first_born)
    assert second_broker.spec.actor_id in runtime.context_ids_for_agent(second_born)
    assert first_broker.spec.actor_id not in runtime.context_ids_for_agent(second_born)


def test_explicit_gap_address_discovers_an_agent_outside_shared_groups() -> None:
    world = SemanticWorld()
    seed = world.add_object("shared meaning", object_id="object:shared")
    handle = SemanticHandle("object", seed.object_id)
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    entry = runtime.actors["broker"]
    source_id = str(
        runtime.create_agent(
            entry,
            purpose="source",
            instructions="",
            budget={},
        )["actor_id"]
    )
    other_broker = runtime.create_broker()
    candidate_id = str(
        runtime.create_agent(
            other_broker,
            purpose="address specialist",
            instructions="",
            budget={},
        )["actor_id"]
    )
    runtime.local_worlds.include_addresses(candidate_id, (handle,))
    root = runtime.inject_human("root", semantic_addresses=(handle,))
    assigned = runtime.send_from_actor(
        entry,
        root,
        recipient=source_id,
        kind="question",
        payload=question_payload("source", semantic_addresses=(handle,)),
        derived_from=(root.act_id,),
    )
    runtime.turn_driver.model_input(
        runtime.turn_host(runtime.actors[source_id], assigned), (), ()
    )
    gap = runtime.send_from_actor(
        runtime.actors[source_id],
        assigned,
        recipient="",
        kind="question",
        payload=question_payload("address gap", semantic_addresses=(handle,)),
        new_context=True,
    )
    candidates = runtime.turn_driver.model_input(
        runtime.turn_host(runtime.actors[gap.recipient], gap), (), ()
    ).local_world["candidates"]

    candidate = next(item for item in candidates if item["actor_id"] == candidate_id)
    assert candidate["shared_context_ids"] == []
    assert candidate["shared_semantic_addresses"] == [
        {"kind": "object", "id": "object:shared"}
    ]


def test_reset_removes_dynamic_contexts_and_restores_only_the_entry_broker() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    question = runtime.inject_human("new Human context", new_context=True)
    dynamic_broker = runtime.actors[question.recipient]
    runtime.create_agent(
        dynamic_broker,
        purpose="temporary member",
        instructions="",
        budget={},
    )

    assert set(runtime.actors) == {"broker", "broker-1", "agent-1"}
    assert runtime.context_members("broker-1") == frozenset({"agent-1"})

    runtime.reset()

    assert set(runtime.actors) == {"broker"}
    assert runtime.threads == {}
    assert runtime.context_ids_for_agent("agent-1") == ()


def test_human_can_reply_to_the_actual_dynamic_broker() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    question = runtime.inject_human("new Human context", new_context=True)
    broker = runtime.actors[question.recipient]
    incremental = runtime.reply_from_actor(
        broker,
        question,
        kind="incremental",
        payload={"content": "provide more context"},
    )

    reply = runtime.reply_human(
        thread_id=question.thread_id,
        reply_to=incremental.act_id,
        content="more context",
    )

    assert reply.recipient == broker.spec.actor_id
