"""Initial facts enter the same Actor, Question, and native turn contracts."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from weavex.actor import ActorSpec
from weavex.capabilities import DEFAULT_AGENT_CAPABILITIES, DEFAULT_BROKER_CAPABILITIES
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.initialization import InitialActor, initialize_runtime
from weavex.prompts import AGENT_INSTRUCTIONS, BROKER_INSTRUCTIONS
from weavex.protocol import Decision, SemanticHandle, answer_payload, question_payload
from weavex.provider import ScriptedModel

from weavex.world import SemanticWorld


def declarations():
    world = SemanticWorld()
    handles = tuple(
        SemanticHandle("object", world.add_object(text).object_id)
        for text in ("左侧材料", "共有材料", "右侧材料")
    )
    actors = tuple(
        InitialActor(
            ActorSpec(name, "agent", AGENT_INSTRUCTIONS, DEFAULT_AGENT_CAPABILITIES),
            question_payload("当前材料支持怎样的理解？", semantic_addresses=window),
            {"world_ops": 1},
        )
        for name, window in (("agent-1", handles[:2]), ("agent-2", handles[1:]))
    ) + tuple(
        InitialActor(
            ActorSpec(
                name,
                "broker",
                BROKER_INSTRUCTIONS,
                DEFAULT_BROKER_CAPABILITIES,
                initial_member_ids=members,
            ),
            question_payload(
                "当前来源能够支持怎样的理解？", semantic_addresses=(handles[1],)
            ),
        )
        for name, members in (
            ("broker", ("agent-1",)),
            ("broker-1", ("agent-1", "agent-2")),
        )
    )
    return world, actors, handles


def initialized(script=None):
    world, actors, handles = declarations()
    model = ScriptedModel(script or (lambda _: Decision("stop")))
    runtime = build_default_runtime(model, world=world, initial_actors=actors)
    roots = {act.recipient: act for act in runtime.queue}
    return runtime, roots, handles


async def receive(runtime, question):
    host = runtime.turn_host(runtime.actors[question.recipient], question)
    result = await host.execute(ToolProposal("receive", "receive_input"))
    assert result.observation.ok
    return host, result.observation.value


@pytest.mark.asyncio
async def test_every_initial_actor_uses_native_input_and_answers_without_human():
    runtime, roots, handles = initialized(
        lambda _: Decision(
            "reply", act_kind="answer", payload=answer_payload("当前判断")
        )
    )
    assert len(runtime.actors) == len(roots) == 4
    assert {act.sender for act in roots.values()} == {"initialization"}
    assert all(act.reply_to is None and not act.derived_from for act in roots.values())
    assert not runtime.local_worlds.handles_for_actor("agent-1")
    assert not any(event.kind == "actor.created" for event in runtime.traces)

    assert await runtime.run_until_idle() == 4
    assert not runtime.queue
    assert await runtime.run_until_idle() == 0
    for actor_id, root in roots.items():
        (answer,) = runtime.inquiry.direct_answers(root)
        assert answer.sender == actor_id and answer.recipient == "initialization"
        assert runtime.answer_responsibilities(actor_id) == ()
        messages = runtime.dialogue_for(actor_id)
        assert [item["role"] for item in messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
            "tool",
        ]
        assert messages[0]["content"] == '{"kind":"actor_turn"}'
        received = json.loads(messages[2]["content"])["value"]
        assert received["arrival"]["act_id"] == root.act_id
        assert "thread_id" not in json.dumps(received)
        if runtime.actors[actor_id].spec.role == "agent":
            assert answer not in runtime.inquiry.visible_acts_across_inquiries("broker")
            assert answer not in runtime.inquiry.visible_acts_across_inquiries(
                "broker-1"
            )
    assert runtime.local_worlds.handles_for_actor("agent-1") == frozenset(handles[:2])
    assert runtime.local_worlds.handles_for_actor("agent-2") == frozenset(handles[1:])
    assert not runtime.local_worlds.handles_for_actor("broker")
    assert not runtime.local_worlds.observations_for("agent-1")
    assert runtime.actors["agent-1"].budget_for(roots["agent-1"].act_id) == {
        "world_ops": 1
    }
    assert runtime.host_costs()["totals"]["dynamic_actor_creations"]["total"] == 0
    assert all(
        state.spec.created_by == "initialization" for state in runtime.actors.values()
    )


def test_dormant_initial_actor_is_registered_without_a_synthetic_root_question():
    world = SemanticWorld()
    declarations = (
        InitialActor(
            ActorSpec(
                "broker",
                "broker",
                BROKER_INSTRUCTIONS,
                DEFAULT_BROKER_CAPABILITIES,
                initial_member_ids=("agent-1",),
            ),
            question_payload("Coordinate an explicitly active study."),
        ),
        InitialActor(
            ActorSpec(
                "agent-1", "agent", AGENT_INSTRUCTIONS, DEFAULT_AGENT_CAPABILITIES
            ),
            budget={"world_ops": 2},
        ),
    )
    runtime = build_default_runtime(
        ScriptedModel(lambda _: Decision("stop")),
        world=world,
        initial_actors=declarations,
    )
    assert set(runtime.actors) == {"broker", "agent-1"}
    assert [arrival.recipient for arrival in runtime.queue] == ["broker"]
    assert runtime.inquiry.direct_questions("agent-1") == ()


@pytest.mark.asyncio
async def test_initial_group_has_no_invented_origin_or_shared_history():
    runtime, roots, _ = initialized()
    host, value = await receive(runtime, roots["agent-1"])
    assert value["external_contexts"] == [
        {"context_id": name, "guaranteed_shared_context": {"origin_question": ""}}
        for name in ("broker", "broker-1")
    ]
    assert {act.act_id for act in host.situation().visible_acts} == {
        roots["agent-1"].act_id
    }
    await (
        runtime.step()
    )  # A private initialization turn creates no public source for a Broker.
    human = runtime.inject_human("后来的 Human Question", recipient="broker-1")
    broker = runtime.actors["broker-1"]
    local = runtime.coordination_local_world(broker, human)
    assert local["context"]["origin"] == "initialization"
    assert (
        local["context"]["source_actor_id"]
        == local["context"]["source_question_id"]
        == ""
    )
    assert local["candidates"] == []
    assert not local["history"]
    assert roots["agent-1"] not in runtime.inquiry.visible_acts_across_inquiries(
        "broker-1"
    )
    assert "form_group" not in runtime.available_capabilities(broker)
    with pytest.raises(ValueError, match="already formed"):
        runtime.form_group(broker, human, ())

    gap = runtime.send_from_actor(
        runtime.actors["agent-1"],
        roots["agent-1"],
        recipient="",
        kind="question",
        payload=question_payload("新的独立缺口"),
        new_context=True,
    )
    new_broker = runtime.actors[gap.recipient]
    assert new_broker.spec.initial_member_ids is None
    new_local = runtime.coordination_local_world(new_broker, gap)
    assert not new_local["context"]["formed"]
    assert new_local["context"]["source_question_id"] == gap.act_id
    assert {item["actor_id"] for item in new_local["candidates"]} == {"agent-2"}
    runtime.form_group(new_broker, gap, ("agent-2",))
    assert runtime.context_members(new_broker.spec.actor_id) == {"agent-1", "agent-2"}


@pytest.mark.asyncio
async def test_initialization_is_only_a_return_endpoint_for_its_real_question():
    runtime, roots, _ = initialized()
    root = roots["agent-1"]
    host, _ = await receive(runtime, root)
    assert "send_incremental_act" in {
        tool["function"]["name"] for tool in host.tool_schemas()
    }
    before = sum(map(len, runtime.threads.values()))
    invalid = (
        ToolProposal(
            "increment",
            "send_incremental_act",
            {"reply_to": root.act_id, "content": "继续"},
        ),
        ToolProposal(
            "ask",
            "ask_in_existing_context",
            {
                "question_id": root.act_id,
                "context_id": "initialization",
                **question_payload("再次发题"),
            },
        ),
        ToolProposal(
            "wrong-owner",
            "answer_question",
            {
                "question_id": roots["agent-2"].act_id,
                "grounding_inputs": [],
                **answer_payload("不可回答别人的责任"),
            },
        ),
    )
    for proposal in invalid:
        assert not (await host.execute(proposal)).observation.ok
    assert sum(map(len, runtime.threads.values())) == before
    broker_question = runtime.send_from_actor(
        runtime.actors["broker"],
        roots["broker"],
        recipient="agent-1",
        kind="question",
        payload=question_payload("需要一个公开结果"),
        derived_from=(roots["broker"].act_id,),
    )
    with pytest.raises(PermissionError, match="own direct root"):
        runtime.send_from_actor(
            runtime.actors["agent-1"],
            broker_question,
            recipient="initialization",
            kind="answer",
            payload=answer_payload("错误返回"),
            reply_to=broker_question.act_id,
        )
    with pytest.raises(ValueError, match="fresh Runtime"):
        initialize_runtime(runtime, ())
    with pytest.raises(ValueError, match="already closed"):
        runtime.actor_registry.register_initial(
            declarations()[1][0].spec, initial_budget={}
        )


@pytest.mark.asyncio
async def test_external_answer_continues_the_initial_question_without_new_capacity():
    runtime, roots, handles = initialized()
    root = roots["agent-1"]
    host, _ = await receive(runtime, root)
    read = await host.execute(
        ToolProposal(
            "read",
            "world_references_from",
            {
                "question_id": root.act_id,
                "object_id": handles[0].semantic_id,
            },
        )
    )
    assert read.observation.ok
    assert read.observation.capability_boundary_change["semantic_world_operations"] == {
        "before": 1,
        "after": 0,
    }
    ask = await host.execute(
        ToolProposal(
            "ask",
            "ask_in_existing_context",
            {
                "question_id": root.act_id,
                "context_id": "broker",
                **question_payload("需要外部理解的缺口"),
            },
        )
    )
    assert ask.observation.ok
    gap = runtime.inquiry.act(ask.observation.value["act_id"])
    broker_host, _ = await receive(runtime, gap)
    result = await broker_host.execute(
        ToolProposal(
            "return",
            "answer_question",
            {
                "question_id": gap.act_id,
                "grounding_inputs": [],
                **answer_payload("外部结果"),
            },
        )
    )
    assert result.observation.ok
    answer = runtime.inquiry.act(result.observation.value["act_id"])
    resumed, value = await receive(runtime, answer)
    assert (
        value["capability_boundaries"][root.act_id]["semantic_world_operations"][
            "remaining_capacity"
        ]
        == 0
    )
    assert any(
        tool["function"]["name"].startswith("world_") for tool in resumed.tool_schemas()
    )
    assert set(runtime.actors["agent-1"].question_budgets) == {root.act_id}
    assert runtime.local_worlds.handles_for_actor("agent-1") == frozenset(handles[:2])
    completed = await resumed.execute(
        ToolProposal(
            "answer",
            "answer_question",
            {
                "question_id": root.act_id,
                "grounding_inputs": [],
                **answer_payload("当前结果"),
            },
        )
    )
    assert completed.observation.ok
    assert (
        runtime.inquiry.act(completed.observation.value["act_id"]).recipient
        == "initialization"
    )


@pytest.mark.parametrize(
    "invalid", ("members", "address", "budget", "capability", "duplicate")
)
def test_initial_declarations_are_validated_before_any_installation(invalid):
    world, actors, _ = declarations()
    if invalid == "members":
        actors = (
            *actors[:-1],
            replace(
                actors[-1],
                spec=replace(actors[-1].spec, initial_member_ids=("missing",)),
            ),
        )
    elif invalid == "address":
        actors = (
            replace(
                actors[0],
                question=question_payload(
                    "无效地址",
                    semantic_addresses=(SemanticHandle("object", "missing"),),
                ),
            ),
            *actors[1:],
        )
    elif invalid == "budget":
        actors = (replace(actors[0], budget={"world_ops": True}), *actors[1:])
    elif invalid == "capability":
        actors = (
            replace(
                actors[0],
                spec=replace(actors[0].spec, capabilities=frozenset({"create_actor"})),
            ),
            *actors[1:],
        )
    else:
        actors = (*actors, actors[0])
    from weavex.capabilities import build_default_tools
    from weavex.local_world import LocalWorlds
    from weavex.runtime import PrototypeRuntime
    from weavex.runner import NativeTurnDriver

    tools = build_default_tools()
    runtime = PrototypeRuntime(
        NativeTurnDriver(ScriptedModel(lambda _: Decision("stop")), tools),
        tools,
        local_worlds=LocalWorlds(world),
    )
    with pytest.raises((ValueError, PermissionError)):
        initialize_runtime(runtime, actors)
    assert (
        not runtime.actors
        and not runtime.threads
        and not runtime.queue
        and not runtime.traces
    )


def test_reset_keeps_declared_groups_but_does_not_replay_initial_questions():
    runtime, _, _ = initialized()
    runtime.create_broker()
    runtime.reset()
    assert set(runtime.actors) == {"agent-1", "agent-2", "broker", "broker-1"}
    assert runtime.context_members("broker-1") == {"agent-1", "agent-2"}
    assert not runtime.threads and not runtime.queue and not runtime.traces
    assert all(not actor.question_budgets for actor in runtime.actors.values())
    assert not runtime.local_worlds.handles_for_actor("agent-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("judgment", ("answer_question", "retain_answer"))
async def test_initial_answer_can_be_rejudged_after_real_neighborhood_change(judgment):
    def script(value):
        question = value.answer_responsibilities[0]
        arguments = {
            "question_id": question.act_id,
            "grounding_inputs": [
                {
                    "kind": "grounding",
                    "id": value.situation.responsibility_frontiers[question.act_id][
                        "latest_grounding_id"
                    ],
                }
            ],
        }
        if judgment == "answer_question":
            arguments.update(answer_payload("变化后的公开结果"))
        return ToolProposal("", judgment, arguments)

    # The real native turn after the delta judges the previously declared root.
    runtime, roots, handles = initialized()
    root = roots["agent-1"]
    host, _ = await receive(runtime, root)
    read = await host.execute(
        ToolProposal(
            "read",
            "world_references_from",
            {
                "question_id": root.act_id,
                "object_id": handles[1].semantic_id,
            },
        )
    )
    observation_id = read.observation.value["observation"]["observation_id"]
    first = await host.execute(
        ToolProposal(
            "first",
            "answer_question",
            {
                "question_id": root.act_id,
                "grounding_inputs": [{"kind": "observation", "id": observation_id}],
                **answer_payload("此前的公开结果"),
            },
        )
    )
    assert first.observation.ok
    assert runtime.answer_responsibilities("agent-1") == ()
    runtime.queue.clear()
    writer, _ = await receive(runtime, roots["agent-2"])
    changed = await writer.execute(
        ToolProposal(
            "write",
            "world_add_reference",
            {
                "question_id": roots["agent-2"].act_id,
                "source_object_id": handles[1].semantic_id,
                "target": {"kind": "object", "id": handles[2].semantic_id},
            },
        )
    )
    assert changed.observation.ok
    assert len(runtime.queue) == 1

    from weavex.arrival import LocalWorldDelta

    assert isinstance(runtime.queue[0], LocalWorldDelta)

    # ScriptedModel auto-yields after public Answers; retain needs an explicit yield.
    def judge(value):
        if value.observations and value.observations[-1].source == "retain_answer":
            return Decision("stop")
        assert isinstance(value.trigger, LocalWorldDelta)
        return script(value)

    runtime.turn_driver.model = ScriptedModel(judge)
    await runtime.step()
    assert not runtime.queue
    assert runtime.answer_responsibilities("agent-1") == ()
    answers = runtime.inquiry.direct_answers(root)
    assert len(answers) == (2 if judgment == "answer_question" else 1)
    assert all(answer.recipient == "initialization" for answer in answers)
    assert answers[0].payload["answer"] == "此前的公开结果"
    assert runtime.local_worlds.handles_for_actor("agent-1") >= frozenset(handles)
    assert runtime.actors["agent-1"].budget_for(root.act_id) == {"world_ops": 0}
