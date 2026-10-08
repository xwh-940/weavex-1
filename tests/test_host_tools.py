"""Host tool effects, Semantic Address, and Question budget invariants."""

from __future__ import annotations

import json

import pytest
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.protocol import (
    Decision,
    SemanticHandle,
    answer_payload,
    question_payload,
)
from weavex.provider import (
    OpenAICompatibleModel,
    NativeModelInput,
    ScriptedModel,
    semantic_input_data,
)
from weavex.prompts import (
    AGENT_INSTRUCTIONS,
)
from weavex.world import ObjectAddress, ReferenceAddress, SemanticWorld


@pytest.mark.asyncio
async def test_world_capability_uses_the_selected_question_budget() -> None:
    created_for: list[str] = []

    def script(value: NativeModelInput) -> Decision:
        ids = [item.act_id for item in value.answer_responsibilities]
        if len(created_for) < 2:
            selected = ids[1 - len(created_for)]
            created_for.append(selected)
            return Decision(
                "call",
                name="world_add_object",
                arguments={
                    "question_id": selected,
                    "content": f"meaning for {selected}",
                },
            )
        if ids:
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(f"done {ids[0]}"),
                reply_to=ids[0],
            )
        return Decision("call", name="yield_turn")

    runtime = build_default_runtime(ScriptedModel(script, auto_yield=False))
    broker = runtime.actors["broker"]
    made = runtime.create_agent(
        broker,
        purpose="advance two bounded responsibilities",
        instructions="",
        budget={"world_ops": 2},
        capabilities=frozenset({"world_add_object"}),
    )
    agent = runtime.actors[str(made["actor_id"])]
    first_root = runtime.inject_human("first root")
    second_root = runtime.inject_human("second root")
    runtime.queue.clear()
    first = runtime.send_from_actor(
        broker,
        first_root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("first bounded responsibility"),
        derived_from=(first_root.act_id,),
    )
    second = runtime.send_from_actor(
        broker,
        second_root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("second bounded responsibility"),
        derived_from=(second_root.act_id,),
    )

    await runtime.step()

    assert created_for == [second.act_id, first.act_id]
    assert agent.question_budgets == {
        first.act_id: {"world_ops": 1},
        second.act_id: {"world_ops": 1},
    }
    local_objects = runtime.local_worlds.view_for(agent.spec.actor_id)["objects"]
    assert {item["content"] for item in local_objects} == {
        f"meaning for {second.act_id}",
        f"meaning for {first.act_id}",
    }
    assert [
        act.reply_to
        for act in runtime.threads[first.thread_id] + runtime.threads[second.thread_id]
        if act.kind == "answer"
    ] == [first.act_id, second.act_id]


@pytest.mark.asyncio
async def test_agent_retries_an_address_with_the_wrong_local_world_kind() -> None:
    world = SemanticWorld()
    seed = world.add_object("known semantic subject", object_id="object:seed")

    def script(value: NativeModelInput) -> Decision:
        if not value.observations:
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(
                    "wrong kind first",
                    semantic_addresses=(SemanticHandle("reference", seed.object_id),),
                ),
            )
        assert value.observations[-1].source == "reply_act"
        assert "not in the Agent Local World" in value.observations[-1].error
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload(
                "corrected kind",
                semantic_addresses=(SemanticHandle("object", seed.object_id),),
            ),
        )

    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="correct one address",
        instructions="Answer with a known address.",
        budget={},
    )
    actor = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human(
        "Start",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
    )
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload(
            "Return the known subject",
            semantic_addresses=(SemanticHandle("object", seed.object_id),),
        ),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()

    await runtime.turn_driver.activate(runtime.turn_host(actor, question))

    answer = runtime.threads[root.thread_id][-1]
    assert answer.payload == answer_payload(
        "corrected kind",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
    )
    rejected = [
        trace
        for trace in runtime.traces
        if trace.kind == "act.observation" and trace.payload["ok"] is False
    ]
    assert rejected[-1].payload["source"] == "reply_act"
    assert rejected[-1].payload["ok"] is False


@pytest.mark.asyncio
async def test_identical_tool_call_is_not_blocked_by_a_semantic_guess() -> None:
    def script(value: NativeModelInput) -> Decision:
        if not value.observations:
            return Decision("call", name="inspect_inquiry_topology")
        if len(value.observations) == 1:
            assert [message["role"] for message in value.dialogue] == [
                "user",
                "assistant",
                "tool",
                "assistant",
                "tool",
            ]
            assert "thread_id" not in value.observations[0].value
            return Decision("call", name="inspect_inquiry_topology")
        assert value.observations[-1].ok is True
        return Decision("reply", act_kind="answer", payload=answer_payload("done"))

    runtime = build_default_runtime(ScriptedModel(script))
    runtime.inject_human("List once")

    await runtime.run_until_idle()

    observations = [
        item
        for item in runtime.traces
        if item.kind == "tool.observation"
        and item.payload["source"] == "inspect_inquiry_topology"
    ]
    assert [item.payload["ok"] for item in observations] == [True, True]


@pytest.mark.asyncio
async def test_world_tool_observation_updates_the_capability_boundary() -> None:
    world = SemanticWorld()
    source = world.add_object("capacity source", object_id="object:capacity-source")
    target = world.add_object(
        "capacity target token CAPACITY-RESULT",
        object_id="object:capacity-target",
    )
    world.add_reference(
        source.object_id,
        ObjectAddress(target.object_id),
        meaning="target is the capacity result for this source",
    )

    def script(value: NativeModelInput) -> Decision:
        if not value.observations:
            current = semantic_input_data(value)
            assert current["capability_boundaries"] == {
                question.act_id: {
                    "semantic_world_operations": {"remaining_capacity": 2}
                }
            }
            assert "budget" not in current
            return Decision(
                "call",
                name="world_references_from",
                arguments={"object_id": source.object_id},
            )
        observation = value.observations[-1]
        assert observation.ok is True
        assert observation.capability_boundary_change == {
            "question_id": question.act_id,
            "semantic_world_operations": {"before": 2, "after": 1},
        }
        assert value.question_budgets[question.act_id] == {"world_ops": 1}
        assert any(
            item["content"] == "capacity target token CAPACITY-RESULT"
            for item in value.local_world["objects"]
        )
        tool_result = json.loads(value.dialogue[-1]["content"])
        assert tool_result["capability_boundary_change"] == {
            "question_id": question.act_id,
            "semantic_world_operations": {"before": 2, "after": 1},
        }
        assert tool_result["value"]["local_world_change"]["objects_added"] == [
            {
                "content": "capacity target token CAPACITY-RESULT",
                "object_id": "object:capacity-target",
                "semantic_address": {
                    "id": "object:capacity-target",
                    "kind": "object",
                },
            }
        ]
        added_reference = tool_result["value"]["local_world_change"][
            "references_added"
        ][0]
        assert added_reference["meaning"] == (
            "target is the capacity result for this source"
        )
        assert "world_references_from" in {
            item["name"] for item in value.capability_catalog
        }
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("CAPACITY-RESULT"),
        )

    runtime = build_default_runtime(ScriptedModel(script), world=world)
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="读取有限能力下的结果",
        instructions="只依据实际接触的结果形成 Answer。",
        budget={"world_ops": 2},
        capabilities=frozenset({"world_references_from"}),
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human(
        "取得能力边界测试结果",
        semantic_addresses=(SemanticHandle("object", source.object_id),),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload(
            "取得给定入口的结果",
            semantic_addresses=(SemanticHandle("object", source.object_id),),
        ),
        derived_from=(root.act_id,),
    )

    await runtime.step()

    assert agent.question_budgets[question.act_id] == {"world_ops": 1}
    final_observation = next(
        json.loads(message["content"])
        for message in runtime.dialogue_for(agent.spec.actor_id)
        if message["role"] == "tool"
        and json.loads(message["content"])["source"] == "reply_act"
    )
    assert final_observation["capability_boundary_change"] == {
        "question_id": question.act_id,
        "semantic_world_operations": {"before": 1, "after": 1},
    }


@pytest.mark.asyncio
async def test_failed_world_tool_does_not_reduce_capability_capacity() -> None:
    def script(value: NativeModelInput) -> Decision:
        if not value.observations:
            return Decision(
                "call",
                name="world_references_from",
                arguments={"object_id": "object:missing", "limit": 0},
            )
        observation = value.observations[-1]
        assert observation.ok is False
        assert observation.capability_boundary_change == {
            "question_id": question.act_id,
            "semantic_world_operations": {"before": 1, "after": 1},
        }
        assert value.question_budgets[question.act_id] == {"world_ops": 1}
        assert "world_references_from" in {
            item["name"] for item in value.capability_catalog
        }
        return Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload("失败未改变能力余量"),
        )

    runtime = build_default_runtime(ScriptedModel(script))
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="验证失败后的能力余量",
        instructions="根据 Observation 形成 Answer。",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_references_from"}),
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("验证失败调用")
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("验证能力余量不受失败调用影响"),
        derived_from=(root.act_id,),
    )

    await runtime.step()

    assert agent.question_budgets[question.act_id] == {"world_ops": 1}
    failed = next(
        json.loads(message["content"])
        for message in runtime.dialogue_for(agent.spec.actor_id)
        if message["role"] == "tool"
        and json.loads(message["content"])["source"] == "world_references_from"
    )
    assert failed["ok"] is False
    assert failed["capability_boundary_change"] == {
        "question_id": question.act_id,
        "semantic_world_operations": {"before": 1, "after": 1},
    }
    assert runtime.snapshot()["semantic_world"] == {
        "objects": [],
        "references": [],
    }


@pytest.mark.asyncio
async def test_world_read_requires_source_in_agent_local_world() -> None:
    world = SemanticWorld()
    source = world.add_object("unexposed source", object_id="object:unexposed-source")
    target = world.add_object("unexposed target", object_id="object:unexposed-target")
    world.add_reference(
        source.object_id,
        ObjectAddress(target.object_id),
        reference_id="reference:unexposed",
    )
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="respect the private semantic boundary",
        instructions="",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_references_from"}),
    )
    actor = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("test an unexposed global Object")
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload("read only an Object in the Local World"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    before_world = runtime.local_worlds.snapshot_data()
    host = runtime.turn_host(actor, question)
    await host.execute(ToolProposal("receive", "receive_input"))

    execution = await host.execute(
        ToolProposal(
            "read",
            "world_references_from",
            {
                "question_id": question.act_id,
                "object_id": source.object_id,
            },
        )
    )

    assert execution.observation.ok is False
    assert "not in the Agent Local World" in execution.observation.error
    assert execution.observation.capability_boundary_change == {
        "question_id": question.act_id,
        "semantic_world_operations": {"before": 1, "after": 1},
    }
    assert actor.question_budgets[question.act_id] == {"world_ops": 1}
    assert runtime.local_worlds.view_for(actor.spec.actor_id) == {
        "domain": "semantic",
        "objects": [],
        "references": [],
    }
    assert runtime.local_worlds.snapshot_data() == before_world


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_handles", "target"),
    (
        (
            (SemanticHandle("object", "object:reference-target"),),
            SemanticHandle("object", "object:reference-target"),
        ),
        (
            (SemanticHandle("object", "object:reference-source"),),
            SemanticHandle("object", "object:reference-target"),
        ),
        (
            (SemanticHandle("object", "object:reference-source"),),
            SemanticHandle("reference", "reference:existing-target"),
        ),
    ),
    ids=(
        "source-outside-local",
        "object-target-outside-local",
        "reference-target-outside-local",
    ),
)
async def test_world_add_reference_requires_local_source_and_target(
    local_handles: tuple[SemanticHandle, ...],
    target: SemanticHandle,
) -> None:
    world = SemanticWorld()
    source = world.add_object("reference source", object_id="object:reference-source")
    target_object = world.add_object(
        "reference target",
        object_id="object:reference-target",
    )
    target_reference_source = world.add_object(
        "existing reference source",
        object_id="object:existing-reference-source",
    )
    world.add_reference(
        target_reference_source.object_id,
        ObjectAddress(target_object.object_id),
        reference_id="reference:existing-target",
    )
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="connect only locally known meanings",
        instructions="",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_add_reference"}),
    )
    actor = runtime.actors[str(created["actor_id"])]
    runtime.local_worlds.include_addresses(actor.spec.actor_id, local_handles)
    root = runtime.inject_human("test an unexposed Reference endpoint")
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload("connect only locally known meanings"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    before_local = runtime.local_worlds.view_for(actor.spec.actor_id)
    before_world = runtime.local_worlds.snapshot_data()
    host = runtime.turn_host(actor, question)
    await host.execute(ToolProposal("receive", "receive_input"))

    execution = await host.execute(
        ToolProposal(
            "connect",
            "world_add_reference",
            {
                "question_id": question.act_id,
                "source_object_id": source.object_id,
                "target": {"kind": target.kind, "id": target.semantic_id},
                "meaning": "must not cross the Local World boundary",
            },
        )
    )

    assert execution.observation.ok is False
    assert "not in the Agent Local World" in execution.observation.error
    assert execution.observation.capability_boundary_change == {
        "question_id": question.act_id,
        "semantic_world_operations": {"before": 1, "after": 1},
    }
    assert actor.question_budgets[question.act_id] == {"world_ops": 1}
    assert runtime.local_worlds.view_for(actor.spec.actor_id) == before_local
    assert runtime.local_worlds.snapshot_data() == before_world


@pytest.mark.asyncio
async def test_invalid_backend_question_target_does_not_create_a_budget_account() -> (
    None
):
    class ProposalModel:
        async def decide(self, value: NativeModelInput) -> ToolProposal:
            if not value.input_received:
                return ToolProposal("", "receive_input")
            if not value.observations:
                return ToolProposal(
                    "",
                    "world_references_from",
                    {
                        "question_id": "act:not-a-responsibility",
                        "object_id": "object:missing",
                    },
                )
            return ToolProposal("", "yield_turn")

    runtime = build_default_runtime(ProposalModel())
    broker = runtime.actors["broker"]
    actor_id = str(
        runtime.create_agent(
            broker,
            purpose="exercise host target validation",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_from"}),
        )["actor_id"]
    )
    root = runtime.inject_human("start")
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor_id,
        kind="question",
        payload=question_payload("read one known Object"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    runtime.queue.append(question)

    await runtime.step()

    actor = runtime.actors[actor_id]
    assert actor.question_budgets == {question.act_id: {"world_ops": 1}}
    failed = next(
        json.loads(message["content"])
        for message in runtime.dialogue_for(actor_id)
        if message["role"] == "tool"
        and json.loads(message["content"])["source"] == "world_references_from"
    )
    assert failed["ok"] is False
    assert "selected Act does not exist" in failed["error"]
    assert "capability_boundary_change" not in failed


@pytest.mark.asyncio
async def test_broker_cannot_execute_semantic_world_capabilities() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Do not expose OSW")

    with pytest.raises(PermissionError, match="cannot call world_add_object"):
        await runtime.tools.execute(
            runtime,
            runtime.actors["broker"],
            root,
            "world_add_object",
            {"content": "forbidden"},
        )


@pytest.mark.asyncio
async def test_world_add_object_returns_its_transferable_semantic_address() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Materialize one meaning")
    created = runtime.create_agent(
        runtime.actors["broker"],
        purpose="materialize meaning",
        instructions="Use the available world capability.",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_add_object"}),
    )
    actor = runtime.actors[str(created["actor_id"])]

    observation = await runtime.tools.execute(
        runtime,
        actor,
        root,
        "world_add_object",
        {"content": "one addressable meaning"},
    )

    assert observation == {
        "object_id": observation["object_id"],
        "content": "one addressable meaning",
        "semantic_address": {
            "kind": "object",
            "id": observation["object_id"],
        },
    }
    assert runtime.local_worlds.view_for(actor.spec.actor_id)["objects"] == [
        observation
    ]


@pytest.mark.asyncio
async def test_world_reference_results_expose_transferable_semantic_addresses() -> None:
    world = SemanticWorld()
    source = world.add_object("reference source", object_id="object:source")
    target = world.add_object("reference target", object_id="object:target")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    root = runtime.inject_human("Connect two meanings")
    created = runtime.create_agent(
        runtime.actors["broker"],
        purpose="connect meanings",
        instructions="",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_add_reference"}),
    )
    actor = runtime.actors[str(created["actor_id"])]
    runtime.local_worlds.include_addresses(
        actor.spec.actor_id,
        (
            SemanticHandle("object", source.object_id),
            SemanticHandle("object", target.object_id),
        ),
    )

    observation = await runtime.tools.execute(
        runtime,
        actor,
        root,
        "world_add_reference",
        {
            "source_object_id": source.object_id,
            "target": {"kind": "object", "id": target.object_id},
            "meaning": "target supplies the source's release evidence",
        },
    )

    assert observation["meaning"] == "target supplies the source's release evidence"
    assert observation["semantic_address"] == {
        "kind": "reference",
        "id": observation["reference_id"],
    }
    assert runtime.local_worlds.view_for(actor.spec.actor_id)["references"] == [
        observation
    ]


def test_runtime_reset_rebuilds_initial_reference_meaning() -> None:
    world = SemanticWorld()
    source = world.add_object("reset source", object_id="object:reset-source")
    target = world.add_object("reset target", object_id="object:reset-target")
    world.add_reference(
        source.object_id,
        ObjectAddress(target.object_id),
        meaning="target remains the source's reset premise",
        reference_id="reference:reset-premise",
    )
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    runtime.local_worlds.world.add_object("later mutation")

    runtime.reset()

    snapshot = runtime.local_worlds.world.snapshot()
    assert set(snapshot.objects) == {source.object_id, target.object_id}
    assert snapshot.get_reference("reference:reset-premise").meaning == (
        "target remains the source's reset premise"
    )


@pytest.mark.asyncio
async def test_reference_target_chain_requires_explicit_source_reads() -> None:
    world = SemanticWorld()
    root_object = world.add_object("root", object_id="object:chain-root")
    middle_one = world.add_object("middle one", object_id="object:chain-middle-1")
    middle_two = world.add_object("middle two", object_id="object:chain-middle-2")
    terminal = world.add_object(
        "terminal token CHAIN-END",
        object_id="object:chain-terminal",
    )
    deep = world.add_reference(
        middle_two.object_id,
        ObjectAddress(terminal.object_id),
        reference_id="reference:chain-deep",
    )
    middle = world.add_reference(
        middle_one.object_id,
        ReferenceAddress(deep.reference_id),
        reference_id="reference:chain-middle",
    )
    frontier = world.add_reference(
        root_object.object_id,
        ReferenceAddress(middle.reference_id),
        reference_id="reference:chain-frontier",
    )
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    address_actor_id = str(
        runtime.create_agent(
            broker,
            purpose="materialize one Reference address",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    reader = runtime.create_agent(
        broker,
        purpose="read a Reference target chain explicitly",
        instructions="",
        budget={"world_ops": 3},
        capabilities=frozenset({"world_references_from"}),
    )
    reader_actor = runtime.actors[str(reader["actor_id"])]
    trigger = runtime.inject_human("Inspect a Reference target chain")

    runtime.local_worlds.include_addresses(
        address_actor_id,
        (SemanticHandle("reference", frontier.reference_id),),
    )
    address_view = runtime.local_worlds.view_for(address_actor_id)
    assert {item["object_id"] for item in address_view["objects"]} == {
        root_object.object_id
    }
    assert {item["reference_id"] for item in address_view["references"]} == {
        frontier.reference_id
    }

    runtime.local_worlds.include_addresses(
        reader_actor.spec.actor_id,
        (SemanticHandle("object", root_object.object_id),),
    )
    first = await runtime.tools.execute(
        runtime,
        reader_actor,
        trigger,
        "world_references_from",
        {"object_id": root_object.object_id, "limit": 1},
    )
    first_view = runtime.local_worlds.view_for(reader_actor.spec.actor_id)
    assert {item["object_id"] for item in first_view["objects"]} == {
        root_object.object_id,
        middle_one.object_id,
    }
    assert {item["reference_id"] for item in first_view["references"]} == {
        frontier.reference_id,
        middle.reference_id,
    }
    assert first["local_world_change"]["objects_added"][0]["object_id"] == (
        middle_one.object_id
    )
    assert {
        item["reference_id"] for item in first["local_world_change"]["references_added"]
    } == {frontier.reference_id, middle.reference_id}

    await runtime.tools.execute(
        runtime,
        reader_actor,
        trigger,
        "world_references_from",
        {"object_id": middle_one.object_id, "limit": 1},
    )
    second_view = runtime.local_worlds.view_for(reader_actor.spec.actor_id)
    assert {item["object_id"] for item in second_view["objects"]} == {
        root_object.object_id,
        middle_one.object_id,
        middle_two.object_id,
    }
    assert {item["reference_id"] for item in second_view["references"]} == {
        frontier.reference_id,
        middle.reference_id,
        deep.reference_id,
    }
    assert all(
        item["object_id"] != terminal.object_id for item in second_view["objects"]
    )

    await runtime.tools.execute(
        runtime,
        reader_actor,
        trigger,
        "world_references_from",
        {"object_id": middle_two.object_id, "limit": 1},
    )
    final_view = runtime.local_worlds.view_for(reader_actor.spec.actor_id)
    assert any(
        item["object_id"] == terminal.object_id for item in final_view["objects"]
    )
    assert list(reader_actor.question_budgets.values()) == [{"world_ops": 0}]


def test_current_prototype_rejects_unimplemented_budget_types() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )

    with pytest.raises(ValueError, match="only world_ops"):
        runtime.create_agent(
            runtime.actors["broker"],
            purpose="invalid budget",
            instructions="Do not run.",
            budget={"budget_name": 8},
        )


@pytest.mark.asyncio
async def test_broker_can_limit_a_new_agent_to_needed_capabilities() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Create one read-only Agent")

    created = await runtime.tools.execute(
        runtime,
        runtime.actors["broker"],
        root,
        "create_actor",
        {
            "purpose": "read only",
            "budget": {"world_ops": 1},
            "capabilities": ["world_references_from"],
        },
    )

    actor = runtime.actors[str(created["actor_id"])]
    assert actor.spec.capabilities == frozenset({"world_references_from"})
    assert actor.spec.instructions == AGENT_INSTRUCTIONS

    with pytest.raises(ValueError, match="accepts only purpose, budget"):
        await runtime.tools.execute(
            runtime,
            runtime.actors["broker"],
            root,
            "create_actor",
            {
                "purpose": "preprogrammed reader",
                "instructions": "Inspect existing references only.",
                "budget": {},
                "capabilities": [],
            },
        )

    with pytest.raises(ValueError, match="capabilities must be a string array"):
        await runtime.tools.execute(
            runtime,
            runtime.actors["broker"],
            root,
            "create_actor",
            {
                "purpose": "ambiguous omitted capabilities",
                "budget": {},
            },
        )


@pytest.mark.asyncio
async def test_broker_created_agent_uses_only_shared_agent_instructions() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Create one base Agent")

    created = await runtime.tools.execute(
        runtime,
        runtime.actors["broker"],
        root,
        "create_actor",
        {
            "budget": {},
            "capabilities": [],
        },
    )

    actor = runtime.actors[str(created["actor_id"])]
    assert actor.spec.instructions == AGENT_INSTRUCTIONS
    assert created == {
        "actor_id": "agent-1",
        "question_delivered": False,
        "source_relation_created": False,
    }


def test_question_budget_persists_on_return_and_refreshes_for_a_new_question() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="one read",
        instructions="Read at most once.",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_references_from"}),
    )
    root = runtime.inject_human("Start")
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=str(created["actor_id"]),
        kind="question",
        payload=question_payload("Read once"),
        derived_from=(root.act_id,),
    )
    actor = runtime.actors[str(created["actor_id"])]

    before = runtime.available_capabilities(actor, question.act_id)
    actor.consume(question.act_id, "world_ops")
    after = runtime.available_capabilities(actor, question.act_id)
    exhausted_input = runtime.turn_driver.model_input(
        runtime.turn_host(actor, question),
        (),
        runtime.tools.catalog_for(after),
    )
    gap = runtime.send_from_actor(
        actor,
        question,
        recipient="broker",
        kind="question",
        payload=question_payload("Return one missing fact"),
    )
    returned = runtime.reply_from_actor(
        broker,
        gap,
        kind="answer",
        payload=answer_payload("missing fact"),
    )
    resumed_question = runtime.inquiry.question_for_trigger(returned)
    resumed_input = runtime.turn_driver.model_input(
        runtime.turn_host(actor, returned), (), ()
    )
    second = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload("Read in a new Question"),
        derived_from=(root.act_id,),
    )
    fresh = runtime.available_capabilities(actor, second.act_id)
    fresh_input = runtime.turn_driver.model_input(
        runtime.turn_host(actor, second),
        (),
        runtime.tools.catalog_for(fresh),
    )

    assert before == frozenset({"world_references_from"})
    assert after == frozenset()
    assert exhausted_input.question_budgets[question.act_id] == {"world_ops": 0}
    assert resumed_question == question
    assert resumed_input.question_budgets[question.act_id] == {"world_ops": 0}
    assert fresh == frozenset({"world_references_from"})
    assert fresh_input.question_budgets == {
        question.act_id: {"world_ops": 0},
        second.act_id: {"world_ops": 1},
    }
    assert set(actor.question_budgets) == {question.act_id, second.act_id}
    assert "budget" not in exhausted_input.local_world
    assert all(
        item["function"]["name"] != "call_capability"
        for item in OpenAICompatibleModel.protocol_tools(exhausted_input)
    )
