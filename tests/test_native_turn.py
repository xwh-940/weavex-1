"""Native Actor turn, situated-input gate, and dialogue invariants."""

from __future__ import annotations

import json

import pytest

from tests.native_model_inputs import (
    agent_input as _input,
    broker_input as _broker_input,
    native_input as _native_input,
)

from weavex.defaults import build_default_runtime
from weavex.protocol import (
    Decision,
    Observation,
    answer_payload,
    question_payload,
)
from weavex.provider import (
    OpenAICompatibleModel,
    NativeModelInput,
    ScriptedModel,
    semantic_input_data,
)
from weavex.runner import NativeActorSession


@pytest.mark.asyncio
async def test_create_purpose_stays_only_in_the_creating_broker_dialogue() -> None:
    private_label = "PRIVATE-CREATOR-NOTE-9F31"

    def script(value: NativeModelInput) -> Decision:
        if value.local_world.get("actors") and not value.observations:
            return Decision(
                "stop",
                assistant_content=(
                    "根据后来公开结果修订理解：该位置表现为能形成来源敏感判断的持续认知位置。"
                ),
            )
        if not value.observations:
            return Decision(
                "call",
                name="create_actor",
                arguments={
                    "purpose": private_label,
                    "budget": {},
                    "capabilities": [],
                },
            )
        return Decision(
            "stop", assistant_content="保留为创建时理解，等待公开结果修订。"
        )

    runtime = build_default_runtime(ScriptedModel(script, auto_yield=False))
    runtime.inject_human("建立一个新的独立认知位置")

    await runtime.step()

    broker_dialogue = json.dumps(runtime.dialogue_for("broker"), ensure_ascii=False)
    assert private_label in broker_dialogue
    assert "等待公开结果修订" in broker_dialogue
    created_trace = next(
        item for item in runtime.traces if item.kind == "actor.created"
    )
    assert "purpose" not in created_trace.payload
    created_observation = next(
        json.loads(item["content"])
        for item in runtime.dialogue_for("broker")
        if item["role"] == "tool"
        and json.loads(item["content"])["source"] == "create_actor"
    )
    assert created_observation["value"] == {
        "actor_id": "agent-1",
        "question_delivered": False,
        "source_relation_created": False,
    }
    second_root = runtime.inject_human("公开结果改变了对此前位置的理解")
    await runtime.step()
    revised_dialogue = json.dumps(runtime.dialogue_for("broker"), ensure_ascii=False)
    assert private_label in revised_dialogue
    assert "根据后来公开结果修订理解" in revised_dialogue
    assert runtime.create_broker().spec.actor_id == "broker-1"
    question = runtime.send_from_actor(
        runtime.actors["broker"],
        second_root,
        recipient="agent-1",
        kind="question",
        payload=question_payload("独立判断当前问题"),
        derived_from=(second_root.act_id,),
    )
    projected = {
        "agent_input": semantic_input_data(
            runtime.turn_driver.model_input(
                runtime.turn_host(runtime.actors["agent-1"], question),
                (),
                (),
            )
        ),
        "creator_world": runtime.coordination_local_world(runtime.actors["broker"]),
        "other_broker_world": runtime.coordination_local_world(
            runtime.actors["broker-1"]
        ),
        "actor_descriptors": runtime.actor_descriptors(),
    }
    assert private_label not in json.dumps(projected, ensure_ascii=False)
    assert all("public_summary" not in item for item in runtime.actor_descriptors())


def test_provider_rendering_keeps_a_stable_cacheable_prefix() -> None:
    first = OpenAICompatibleModel.render_messages(_input("first"))
    second = OpenAICompatibleModel.render_messages(_input("second"))

    assert first[:2] == second[:2]
    assert (
        first[2]
        == second[2]
        == {
            "role": "user",
            "content": '{"kind":"actor_turn"}',
        }
    )
    assert semantic_input_data(_input("first")) != semantic_input_data(_input("second"))


def test_native_dialogue_projects_each_public_act_payload_only_once() -> None:
    session = NativeActorSession()
    first = Observation(
        "receive_input",
        True,
        value={
            "arrival": {
                "act_id": "act-1",
                "kind": "question",
                "payload": {"raw_question": "root"},
            },
            "answer_responsibilities": [],
            "visible_acts": [
                {
                    "act_id": "act-2",
                    "kind": "answer",
                    "payload": {"answer": "long public result"},
                }
            ],
            "local_world": {"domain": "coordination"},
        },
    )
    projected_first = session.project_observation(first)

    assert projected_first.value["arrival"] == first.value["arrival"]
    assert projected_first.value["local_world"]["projection"] == "initial"
    second = Observation(
        "receive_input",
        True,
        value={
            "arrival": {
                "act_id": "act-2",
                "kind": "answer",
                "sender": "agent-1",
                "recipient": "broker",
                "payload": {"answer": "long public result"},
            },
            "answer_responsibilities": [],
            "visible_acts": [
                {
                    "act_id": "act-1",
                    "kind": "question",
                    "payload": {"raw_question": "root"},
                },
                {
                    "act_id": "act-3",
                    "kind": "answer",
                    "payload": {"answer": "a later result"},
                },
            ],
            "local_world": {"domain": "coordination"},
        },
    )
    projected_second = session.project_observation(second)

    assert projected_second.value["arrival"] == {
        "act_id": "act-2",
        "kind": "answer",
        "sender": "agent-1",
        "recipient": "broker",
        "previously_projected": True,
    }
    assert [item["act_id"] for item in projected_second.value["visible_acts"]] == [
        "act-3"
    ]
    assert session.projected_act_ids == {"act-1", "act-2", "act-3"}


def test_thread_id_is_runtime_debug_state_not_model_context() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("keep the mechanical namespace internal")
    broker = runtime.actors["broker"]
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, root),
        (),
        runtime.tools.catalog_for(broker.spec.capabilities),
    )

    current_input = semantic_input_data(value)
    rendered = OpenAICompatibleModel.render_messages(value)

    assert runtime.act_data(root)["thread_id"] == root.thread_id
    assert runtime.snapshot()["threads"][root.thread_id][0]["thread_id"] == (
        root.thread_id
    )
    assert "thread_id" not in json.dumps(current_input)
    assert "thread_id" not in rendered[-1]["content"]
    assert "last_seen_acts" not in runtime.snapshot()["actors"][0]


@pytest.mark.asyncio
async def test_external_input_enters_only_through_receive_tool_result() -> None:
    marker = "USER-AUTHORITY-MARKER-73"
    runtime = build_default_runtime(
        ScriptedModel(
            lambda _value: Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("done"),
            )
        )
    )
    root = runtime.inject_human(marker)

    await runtime.step()

    dialogue = runtime.dialogue_for("broker")
    user_messages = [item for item in dialogue if item["role"] == "user"]
    assert user_messages == [{"role": "user", "content": '{"kind":"actor_turn"}'}]
    assert marker not in json.dumps(user_messages, ensure_ascii=False)
    receive_call = dialogue[1]
    assert receive_call["tool_calls"][0]["function"] == {
        "name": "receive_input",
        "arguments": "{}",
    }
    received = json.loads(dialogue[2]["content"])
    assert received["source"] == "receive_input"
    assert received["ok"] is True
    assert received["value"]["arrival"]["act_id"] == root.act_id
    assert received["value"]["arrival"]["payload"]["raw_question"] == marker
    assert received["value"]["answer_responsibilities"][0]["act_id"] == root.act_id


@pytest.mark.asyncio
async def test_actor_may_emit_multiple_effects_before_yielding_its_turn() -> None:
    recipients: list[str] = []

    def script(value: NativeModelInput) -> Decision:
        if len(value.observations) < 2:
            recipient = recipients[len(value.observations)]
            return Decision(
                "send",
                recipient=recipient,
                act_kind="question",
                payload=question_payload(f"形成来源 {recipient} 的独立结果"),
                derived_from=(value.answer_responsibilities[0].act_id,),
            )
        assert [item.source for item in value.observations] == [
            "send_act",
            "send_act",
        ]
        return Decision("call", name="yield_turn")

    runtime = build_default_runtime(ScriptedModel(script, auto_yield=False))
    broker = runtime.actors["broker"]
    for name in ("first source", "second source"):
        created = runtime.create_agent(
            broker,
            purpose=name,
            instructions="",
            budget={},
            capabilities=frozenset(),
        )
        recipients.append(str(created["actor_id"]))
    root = runtime.inject_human("需要两个可区分来源")

    handled = await runtime.step()

    assert handled == root
    questions = [
        act
        for act in runtime.threads[root.thread_id]
        if act.kind == "question" and act.sender == broker.spec.actor_id
    ]
    assert [act.recipient for act in questions] == recipients
    assert list(runtime.queue) == questions
    assert [
        message["tool_calls"][0]["function"]["name"]
        for message in runtime.dialogue_for(broker.spec.actor_id)
        if message.get("role") == "assistant"
    ] == ["receive_input", "send_question", "send_question", "yield_turn"]
    yielded = json.loads(runtime.dialogue_for(broker.spec.actor_id)[-1]["content"])
    assert yielded == {
        "error": "",
        "ok": True,
        "source": "yield_turn",
        "value": {"turn_ended": True},
    }


@pytest.mark.asyncio
async def test_actor_targets_responsibilities_across_inquiries_in_one_turn() -> None:
    seen: list[NativeModelInput] = []

    def script(value: NativeModelInput) -> Decision:
        seen.append(value)
        open_ids = [item.act_id for item in value.answer_responsibilities]
        if len(open_ids) == 2:
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(f"answered {open_ids[-1]}"),
                reply_to=open_ids[-1],
            )
        if len(open_ids) == 1:
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload(f"answered {open_ids[0]}"),
                reply_to=open_ids[0],
            )
        return Decision("call", name="yield_turn")

    runtime = build_default_runtime(ScriptedModel(script, auto_yield=False))
    first = runtime.inject_human("first responsibility")
    second = runtime.inject_human("second responsibility")

    handled = await runtime.step()

    assert handled == first
    assert [item.act_id for item in seen[0].answer_responsibilities] == [
        first.act_id,
        second.act_id,
    ]
    first_input = semantic_input_data(seen[0])
    assert first_input["arrival"]["act_id"] == first.act_id
    assert [item["act_id"] for item in first_input["answer_responsibilities"]] == [
        first.act_id,
        second.act_id,
    ]
    assert "active_question" not in first_input
    assert [
        item["function"]["parameters"]["properties"]["question_id"]["type"]
        for item in OpenAICompatibleModel.protocol_tools(seen[0])
        if item["function"]["name"] == "answer_question"
    ] == ["string"]
    assert {
        act.reply_to
        for thread in runtime.threads.values()
        for act in thread
        if act.kind == "answer"
    } == {second.act_id, first.act_id}
    created_answer_targets = [
        trace.payload["value"]["act_id"]
        for trace in runtime.traces
        if trace.kind == "act.observation"
        and trace.payload.get("source") == "reply_act"
        and trace.payload.get("ok") is True
    ]
    answer_by_id = {
        act.act_id: act
        for thread in runtime.threads.values()
        for act in thread
        if act.kind == "answer"
    }
    assert [answer_by_id[act_id].reply_to for act_id in created_answer_targets] == [
        second.act_id,
        first.act_id,
    ]
    assert list(runtime.queue) == [second]
    dialogue_turn_calls = [
        message["tool_calls"][0]["function"]["name"]
        for message in runtime.dialogue_for("broker")
        if message.get("role") == "assistant"
    ]
    assert dialogue_turn_calls == [
        "receive_input",
        "answer_question",
        "answer_question",
        "yield_turn",
    ]

    await runtime.step()

    assert seen[-1].answer_responsibilities == ()
    assert any(
        item["function"]["name"] == "answer_question"
        for item in OpenAICompatibleModel.protocol_tools(seen[-1])
    )
    assert not any(
        trace.kind in {"act.observation", "tool.observation"}
        and trace.payload.get("ok") is False
        for trace in runtime.traces
    )


def test_schemas_are_stable_before_and_after_situated_input() -> None:
    base = _broker_input("bounded input")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        (),
        base.capability_catalog,
        input_received=False,
    )
    assert OpenAICompatibleModel.protocol_tools(
        value
    ) == OpenAICompatibleModel.protocol_tools(base)
    assert OpenAICompatibleModel.render_messages(value)[-1] == {
        "role": "user",
        "content": '{"kind":"actor_turn"}',
    }


@pytest.mark.asyncio
async def test_runner_rejects_effects_before_situated_input_is_received() -> None:
    class BoundaryModel:
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, value: NativeModelInput) -> Decision:
            self.calls += 1
            if self.calls == 1:
                return Decision("call", name="inspect_inquiry_topology")
            if not value.input_received:
                return Decision("call", name="receive_input")
            if value.answer_responsibilities:
                return Decision(
                    "reply",
                    act_kind="answer",
                    payload=answer_payload("done"),
                )
            return Decision("call", name="yield_turn")

    runtime = build_default_runtime(BoundaryModel())
    root = runtime.inject_human("验证输入前行动边界")

    handled = await runtime.step()

    assert handled == root
    failed = next(
        json.loads(message["content"])
        for message in runtime.dialogue_for("broker")
        if message["role"] == "tool"
        and json.loads(message["content"])["source"] == "inspect_inquiry_topology"
    )
    assert failed["ok"] is False
    assert "only receive_input" in failed["error"]
    assert "capability_boundary_change" not in failed
    assert not any(
        trace.kind == "tool.observation"
        and trace.payload["source"] == "inspect_inquiry_topology"
        and trace.payload["ok"] is True
        for trace in runtime.traces
    )


def test_model_projection_removes_thread_ids_from_related_history() -> None:
    base = _broker_input("candidate history")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        {
            "domain": "coordination",
            "candidates": [
                {
                    "actor_id": "agent-7",
                    "related_history": [
                        {
                            "act_id": "act-old",
                            "thread_id": "thread-old",
                            "kind": "answer",
                        }
                    ],
                }
            ],
        },
        base.observations,
        base.capability_catalog,
    )

    current_input = semantic_input_data(value)

    assert current_input["local_world"]["candidates"][0]["related_history"] == [
        {"act_id": "act-old", "kind": "answer"}
    ]


@pytest.mark.asyncio
async def test_actor_dialogue_is_append_only_across_inquiries_until_reset() -> None:
    model = ScriptedModel(
        lambda value: Decision(
            "reply",
            act_kind="answer",
            payload=answer_payload(f"answered {value.trigger.act_id}"),
        )
    )
    runtime = build_default_runtime(model)
    runtime.inject_human("first Inquiry")
    runtime.inject_human("second Inquiry")

    assert await runtime.run_until_idle() == 2

    first, second = model.calls
    first_messages = OpenAICompatibleModel.render_messages(first)
    second_messages = OpenAICompatibleModel.render_messages(second)
    assert second_messages[: len(first_messages)] == first_messages
    assert [message["role"] for message in second.dialogue] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "user",
        "assistant",
        "tool",
    ]
    assert [message["role"] for message in runtime.dialogue_for("broker")] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert all(
        "thread_id" not in json.dumps(message)
        for message in runtime.dialogue_for("broker")
    )

    runtime.reset()

    assert runtime.dialogue_for("broker") == ()


@pytest.mark.asyncio
async def test_scripted_incremental_reply_records_the_unified_tool_call() -> None:
    runtime = build_default_runtime(
        ScriptedModel(
            lambda _value: Decision(
                "reply",
                act_kind="incremental",
                payload={"content": "请补充批准码"},
            )
        )
    )
    root = runtime.inject_human("处理请求")

    await runtime.step()

    assistant = next(
        message
        for message in runtime.dialogue_for("broker")
        if message.get("role") == "assistant"
        and message["tool_calls"][0]["function"]["name"] == "send_incremental_act"
    )
    function = assistant["tool_calls"][0]["function"]
    assert function["name"] == "send_incremental_act"
    assert json.loads(function["arguments"]) == {
        "content": "请补充批准码",
        "reply_to": root.act_id,
    }
    assert runtime.threads[root.thread_id][-1].kind == "incremental"


@pytest.mark.asyncio
async def test_runner_clears_provider_activation_transport_state() -> None:
    class ActivationModel(ScriptedModel):
        def __init__(self) -> None:
            super().__init__(
                lambda _value: Decision(
                    "reply",
                    act_kind="answer",
                    payload=answer_payload("done"),
                )
            )
            self.ended: list[tuple[str, str]] = []

        def end_activation(self, actor_id: str, trigger_id: str) -> None:
            self.ended.append((actor_id, trigger_id))

    model = ActivationModel()
    runtime = build_default_runtime(model)
    root = runtime.inject_human("finish one activation")

    await runtime.run_until_idle()

    assert model.ended == [("broker", root.act_id)]
    assistant = next(
        message
        for message in runtime.dialogue_for("broker")
        if message["role"] == "assistant"
    )
    assert assistant["content"] == ""


@pytest.mark.asyncio
async def test_runner_persists_assistant_working_content_across_activations() -> None:
    seen: list[NativeModelInput] = []

    def script(value: NativeModelInput) -> Decision:
        seen.append(value)
        raw_question = value.answer_responsibilities[0].payload["raw_question"]
        if raw_question == "first question":
            if not value.observations:
                return Decision(
                    "call",
                    name="inspect_inquiry_topology",
                    assistant_content="当前还需要确认公开 Question 关系。",
                )
            return Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("first answer"),
                assistant_content="关系已经确认，当前 Question 可以闭合。",
            )
        return Decision("stop", reason="inspect retained dialogue")

    runtime = build_default_runtime(ScriptedModel(script))
    runtime.inject_human("first question")

    await runtime.run_until_idle()

    first_dialogue = runtime.dialogue_for("broker")
    assert [
        message["content"]
        for message in first_dialogue
        if message["role"] == "assistant"
        and message["tool_calls"][0]["function"]["name"]
        not in {"receive_input", "yield_turn"}
    ] == [
        "当前还需要确认公开 Question 关系。",
        "关系已经确认，当前 Question 可以闭合。",
    ]
    assert all(
        "tool_calls" in message
        for message in first_dialogue
        if message["role"] == "assistant"
    )

    second = runtime.inject_human("second question")
    await runtime.step()

    second_input = next(
        value for value in seen if value.trigger.act_id == second.act_id
    )
    retained = [
        message["content"]
        for message in second_input.dialogue
        if message["role"] == "assistant"
    ]
    assert "当前还需要确认公开 Question 关系。" in retained
    assert "关系已经确认，当前 Question 可以闭合。" in retained


@pytest.mark.asyncio
async def test_runner_clears_provider_transport_after_activation_failure() -> None:
    class FailingModel:
        def __init__(self) -> None:
            self.ended: list[tuple[str, str]] = []

        async def decide(self, _value: NativeModelInput) -> Decision:
            raise RuntimeError("provider failed")

        def end_activation(self, actor_id: str, trigger_id: str) -> None:
            self.ended.append((actor_id, trigger_id))

    model = FailingModel()
    runtime = build_default_runtime(model)
    root = runtime.inject_human("fail one activation")

    with pytest.raises(RuntimeError, match="provider failed"):
        await runtime.step()

    assert model.ended == [("broker", root.act_id)]
