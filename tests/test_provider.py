"""OpenAI-compatible provider transport and proposal mapping."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.native_model_inputs import (
    agent_input as _input,
    broker_input as _broker_input,
    native_input as _native_input,
)

from weavex.defaults import build_default_runtime
from weavex.dialogue import (
    tool_message,
)
from weavex.execution import ToolProposal
from weavex.protocol import (
    Act,
    Decision,
    Observation,
    SemanticHandle,
    answer_payload,
    question_payload,
)
from weavex.provider import (
    OpenAICompatibleModel,
    ScriptedModel,
    model_situation_data,
    semantic_input_data,
)


@pytest.mark.asyncio
async def test_broker_can_answer_non_active_frontier_after_incremental_fact() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="complete one release confirmation",
        instructions="Use the exact supplied approval code.",
        budget={},
        capabilities=frozenset(),
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("Confirm release after obtaining the approval code")
    runtime.queue.clear()
    delegated = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("Confirm release with the exact approval code"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    child = runtime.send_from_actor(
        agent,
        delegated,
        recipient="broker",
        kind="question",
        payload=question_payload("What is the exact approval code?"),
        context_id="broker",
    )
    runtime.queue.clear()

    while_child_is_active = runtime.turn_driver.model_input(
        runtime.turn_host(broker, child), (), ()
    )
    assert while_child_is_active.answer_responsibilities == (root, child)

    request = runtime.send_from_actor(
        broker,
        child,
        recipient="human",
        kind="incremental",
        payload={"content": "Provide the exact approval code."},
        reply_to=root.act_id,
    )
    human_reply = runtime.reply_human(
        thread_id=root.thread_id,
        reply_to=request.act_id,
        content="The exact approval code is CODE-417.",
    )
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, human_reply), (), ()
    )

    assert value.answer_responsibilities == (root, child)
    answer_question = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "answer_question"
    )
    assert (
        "enum"
        not in answer_question["function"]["parameters"]["properties"]["question_id"]
    )
    proposal = await OpenAICompatibleModel(
        _client(
            "answer_question",
            {
                **answer_payload("The exact approval code is CODE-417."),
                "question_id": child.act_id,
                "grounding_inputs": [],
            },
        )
    ).decide(value)
    assert proposal == ToolProposal(
        "",
        "answer_question",
        {
            **answer_payload("The exact approval code is CODE-417."),
            "question_id": child.act_id,
            "grounding_inputs": [],
        },
    )

    child_answer = runtime.reply_from_actor(
        broker,
        human_reply,
        kind="answer",
        payload=answer_payload("The exact approval code is CODE-417."),
        reply_to=child.act_id,
    )
    assert child_answer.recipient == agent.spec.actor_id
    assert child_answer.reply_to == child.act_id
    assert runtime.answer_responsibilities(broker.spec.actor_id) == (root,)

    delegated_answer = runtime.reply_from_actor(
        agent,
        child_answer,
        kind="answer",
        payload=answer_payload("Release confirmed with CODE-417."),
    )
    final = runtime.reply_from_actor(
        broker,
        delegated_answer,
        kind="answer",
        payload=dict(delegated_answer.payload),
    )
    assert delegated_answer.reply_to == delegated.act_id
    assert final.recipient == "human" and final.reply_to == root.act_id


def test_provider_uses_native_tool_messages_instead_of_refreshing_user_input() -> None:
    base = _broker_input("history")
    observation = Observation(
        "inspect_inquiry_topology",
        True,
        value={"thread_id": "thread-1", "questions": []},
    )
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        (observation,),
        base.capability_catalog,
    )

    current_input = json.loads(
        OpenAICompatibleModel.render_messages(value)[2]["content"]
    )

    assert current_input == {"kind": "actor_turn"}
    situation = model_situation_data(value)
    assert situation["derived_current_state"] == semantic_input_data(value)
    assert situation["activation_observations"] == [
        {
            "source": "inspect_inquiry_topology",
            "ok": True,
            "value": {"questions": []},
            "error": "",
        }
    ]


def test_native_tool_message_removes_host_thread_namespace_recursively() -> None:
    message = tool_message(
        "call-1",
        Observation(
            "custom_capability",
            True,
            value={
                "thread_id": "thread-private",
                "nested": [{"thread_id": "thread-private", "result": "kept"}],
            },
        ),
    )

    content = json.loads(message["content"])

    assert content["value"] == {"nested": [{"result": "kept"}]}


def _tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, object] | str,
) -> SimpleNamespace:
    arguments_json = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments_json),
    )


def _provider_response(
    *tool_calls: SimpleNamespace,
    content: str = "",
    reasoning_content: str | None = None,
    model: str | None = None,
    usage: dict[str, int] | None = None,
) -> SimpleNamespace:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=list(tool_calls),
                )
            )
        ]
    )
    if model is not None:
        response.model = model
    if usage is not None:
        response.usage = SimpleNamespace(**usage)
    return response


def _provider_client(completions: object) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


class _Completions:
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments

    async def create(self, **_kwargs: object) -> object:
        assert _kwargs["tool_choice"] == "auto"
        assert _kwargs["parallel_tool_calls"] is False
        assert _kwargs["max_tokens"] == 393_216
        assert _kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        return _provider_response(_tool_call("", self.tool_name, self.arguments))


class _ReasoningCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        first = len(self.calls) == 1
        return _provider_response(
            _tool_call(
                "call-reasoning-1" if first else "call-reasoning-2",
                "inspect_inquiry_topology" if first else "answer_question",
                (
                    {"question_id": "act-reasoning transport"}
                    if first
                    else {
                        "question_id": "act-reasoning transport",
                        "grounding_inputs": [],
                        **answer_payload("done"),
                    }
                ),
            ),
            content="先查看公开关系。" if first else "公开关系已经足够。",
            reasoning_content=(
                "provider-private activation reasoning"
                if first
                else "final activation reasoning"
            ),
        )


class _InputReasoningCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            assert "receive_input" in {
                item["function"]["name"] for item in kwargs["tools"]
            }
            assert kwargs["messages"][-1] == {
                "role": "user",
                "content": '{"kind":"actor_turn"}',
            }
            return _provider_response(
                _tool_call("call-receive-input", "receive_input", {}),
                model="deepseek-test",
                reasoning_content="选择有限输入窗口",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                },
            )
        receive = next(
            message
            for message in kwargs["messages"]
            if message.get("role") == "assistant"
            and message.get("tool_calls", [{}])[0].get("id") == "call-receive-input"
        )
        assert receive["reasoning_content"] == "选择有限输入窗口"
        received = next(
            message
            for message in kwargs["messages"]
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "call-receive-input"
        )
        assert json.loads(received["content"])["source"] == "receive_input"
        if len(self.calls) == 3:
            assert "yield_turn" in {
                item["function"]["name"] for item in kwargs["tools"]
            }
            return _provider_response(
                _tool_call("call-yield-after-answer", "yield_turn", {}),
                model="deepseek-test",
                reasoning_content="当前公开效果已经成立，让出本轮",
                usage={
                    "prompt_tokens": 160,
                    "completion_tokens": 8,
                    "total_tokens": 168,
                    "prompt_cache_hit_tokens": 120,
                    "prompt_cache_miss_tokens": 40,
                },
            )
        return _provider_response(
            _tool_call(
                "call-answer-after-input",
                "answer_question",
                {
                    "question_id": "act-1",
                    "grounding_inputs": [],
                    **answer_payload("done"),
                },
            ),
            model="deepseek-test",
            reasoning_content="依据已取得输入形成 Answer",
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 12,
                "total_tokens": 132,
                "prompt_cache_hit_tokens": 100,
                "prompt_cache_miss_tokens": 20,
            },
        )


class _RecoveringProtocolCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        call_number = len(self.calls)
        if call_number == 1:
            return _provider_response(
                _tool_call("call-receive-input", "receive_input", {}),
                reasoning_content="开放本轮有限输入",
            )
        if call_number == 2:
            return _provider_response(
                _tool_call(
                    "call-invalid-context",
                    "ask_in_existing_context",
                    {
                        "question_id": "act-2",
                        **question_payload("取得外部事实"),
                    },
                ),
                reasoning_content="修正当前 context 参数",
            )
        if call_number == 4:
            return _provider_response(
                _tool_call("call-yield-after-question", "yield_turn", {}),
                reasoning_content="让出已经产生外问的当前 turn",
            )
        messages = kwargs["messages"]
        rejected = next(
            message
            for message in messages
            if message.get("role") == "assistant"
            and message.get("tool_calls", [{}])[0].get("id") == "call-invalid-context"
        )
        assert rejected["reasoning_content"] == "修正当前 context 参数"
        failure = next(
            message
            for message in messages
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "call-invalid-context"
        )
        failure_data = json.loads(failure["content"])
        assert failure_data["ok"] is False
        assert "missing required fields" in failure_data["error"]
        return _provider_response(
            _tool_call(
                "call-corrected-context",
                "ask_in_existing_context",
                {
                    "question_id": "act-2",
                    "context_id": "broker",
                    **question_payload("取得外部事实"),
                },
            ),
            reasoning_content="",
        )


class _RecoveringRejectedCompletions:
    def __init__(
        self,
        tool_name: str,
        arguments_json: str,
        expected_category: str,
    ) -> None:
        self.tool_name = tool_name
        self.arguments_json = arguments_json
        self.expected_category = expected_category
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _provider_response(
                _tool_call("call-receive-input", "receive_input", {}),
                reasoning_content="开放本轮有限输入",
            )
        if len(self.calls) == 2:
            return _provider_response(
                _tool_call(
                    "call-rejected-envelope",
                    self.tool_name,
                    self.arguments_json,
                ),
                content="这个提案若失败，仍需依据 Observation 修正。",
                reasoning_content="修正可回放的失败工具调用",
            )
        if len(self.calls) == 4:
            return _provider_response(
                _tool_call("call-yield-after-recovery", "yield_turn", {}),
                reasoning_content="让出已经形成 Answer 的当前 turn",
            )
        messages = kwargs["messages"]
        rejected = next(
            message
            for message in messages
            if message.get("role") == "assistant"
            and message.get("tool_calls", [{}])[0].get("id") == "call-rejected-envelope"
        )
        assert rejected["reasoning_content"] == "修正可回放的失败工具调用"
        assert rejected["content"] == "这个提案若失败，仍需依据 Observation 修正。"
        assert rejected["tool_calls"][0]["function"]["arguments"] == self.arguments_json
        failure = next(
            message
            for message in messages
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "call-rejected-envelope"
        )
        failure_data = json.loads(failure["content"])
        assert failure_data["ok"] is False
        assert self.expected_category in failure_data["error"]
        return _provider_response(
            _tool_call(
                "call-recovered-answer",
                "answer_question",
                {
                    "question_id": "act-1",
                    "grounding_inputs": [],
                    **answer_payload("已恢复"),
                },
            ),
            reasoning_content="",
        )


class _NoToolCompletions:
    async def create(self, **_kwargs: object) -> object:
        return _provider_response()


def _client(tool_name: str, arguments: dict[str, object]) -> object:
    completions = _Completions(tool_name, arguments)
    return _provider_client(completions)


def test_provider_declares_million_token_context_separately_from_output() -> None:
    model = OpenAICompatibleModel(_client("stop_activation", {"reason": "unused"}))

    assert model.context_window == 1_000_000
    assert model.max_tokens == 393_216


@pytest.mark.asyncio
async def test_provider_reasoning_survives_activation_until_actor_reset() -> None:
    completions = _ReasoningCompletions()
    model = OpenAICompatibleModel(_provider_client(completions))
    base = _broker_input("reasoning transport")

    first = await model.decide(base)
    call = first
    assert first.assistant_content == "先查看公开关系。"
    user = OpenAICompatibleModel.render_messages(base)[-1]
    dialogue = (
        user,
        {
            "role": "assistant",
            "content": first.assistant_content,
            "tool_calls": [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
            ],
        },
        tool_message(
            call.call_id,
            Observation("inspect_inquiry_topology", True, value={"ok": True}),
        ),
    )
    second_input = _native_input(
        actor=base.actor,
        trigger=base.trigger,
        visible_acts=base.visible_acts,
        local_world=base.local_world,
        observations=(
            Observation("inspect_inquiry_topology", True, value={"ok": True}),
        ),
        capability_catalog=base.capability_catalog,
        dialogue=dialogue,
        dialogue_includes_turn=True,
        answer_responsibilities=(base.trigger,),
    )

    second = await model.decide(second_input)

    replayed = completions.calls[1]["messages"]
    assistant = next(
        message for message in replayed if message.get("role") == "assistant"
    )
    assert assistant["reasoning_content"] == "provider-private activation reasoning"
    assert assistant["content"] == "先查看公开关系。"
    assert "reasoning_content" not in json.dumps(dialogue)
    assert second.name == "answer_question"
    assert second.assistant_content == "公开关系已经足够。"

    model.end_activation(base.actor.actor_id, base.trigger.act_id)

    assert model._actor_transport


@pytest.mark.asyncio
async def test_receive_input_reasoning_is_private_until_runtime_reset() -> None:
    completions = _InputReasoningCompletions()
    model = OpenAICompatibleModel(_provider_client(completions))
    runtime = build_default_runtime(model)
    root = runtime.inject_human("通过有限输入窗口进入")

    handled = await runtime.step()

    assert handled == root
    assert len(completions.calls) == 3
    assert model._actor_transport
    dialogue = runtime.dialogue_for("broker")
    assert "reasoning_content" not in json.dumps(dialogue, ensure_ascii=False)
    assert "prompt_cache_hit_tokens" not in json.dumps(dialogue, ensure_ascii=False)
    assert [message["role"] for message in dialogue] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    usage_traces = [trace for trace in runtime.traces if trace.kind == "provider.usage"]
    assert [trace.payload["prompt_cache_hit_tokens"] for trace in usage_traces] == [
        0,
        100,
        120,
    ]
    assert [trace.payload["prompt_cache_miss_tokens"] for trace in usage_traces] == [
        100,
        20,
        40,
    ]
    assert all(trace.payload["cache_reported"] is True for trace in usage_traces)
    snapshot = runtime.snapshot()
    assert snapshot["provider_usage"] == {
        "requests": 3,
        "cache_reported_requests": 3,
        "prompt_tokens": 380,
        "completion_tokens": 30,
        "total_tokens": 410,
        "prompt_cache_hit_tokens": 220,
        "prompt_cache_miss_tokens": 160,
        "prompt_cache_hit_ratio": pytest.approx(220 / 380),
    }
    assert snapshot["host_costs"]["totals"]["model"] == {
        "requests": 3,
        "decisions": 3,
        "rejections": 0,
        "usage_reported_requests": 3,
        "prompt_tokens": 380,
        "completion_tokens": 30,
        "total_tokens": 410,
        "prompt_cache_hit_tokens": 220,
        "prompt_cache_miss_tokens": 160,
        "prompt_cache_hit_ratio": pytest.approx(220 / 380),
    }


class _MultipleCompletions(_Completions):
    async def create(self, **kwargs: object) -> object:
        response = await super().create(**kwargs)
        response.choices[0].message.tool_calls.append(
            _tool_call(
                "",
                "stop_activation",
                {"reason": "speculative second call"},
            )
        )
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "world_references_from",
            {"question_id": "act-protocol", "object_id": "object:seed"},
        ),
        (
            "ask_external_question",
            {
                "question_id": "act-protocol",
                **question_payload(
                    "one gap",
                    interpretation_content="minimum context",
                    semantic_addresses=(SemanticHandle("object", "object:seed"),),
                    intent="depth: short",
                ),
            },
        ),
        (
            "answer_question",
            {
                "question_id": "act-protocol",
                "grounding_inputs": [],
                **answer_payload("done"),
            },
        ),
        (
            "send_incremental_act",
            {"reply_to": "act-protocol", "content": "more context"},
        ),
    ],
)
async def test_provider_returns_backend_neutral_tool_proposals(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    model = OpenAICompatibleModel(_client(tool_name, arguments))

    proposal = await model.decide(_input("protocol"))

    assert proposal == ToolProposal("", tool_name, arguments)


@pytest.mark.asyncio
async def test_provider_maps_an_agent_selected_existing_context() -> None:
    base = _input("existing context")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        base.observations,
        base.capability_catalog,
        external_contexts=(
            {
                "context_id": "broker-7",
                "guaranteed_shared_context": {"origin_question": "earlier gap"},
            },
        ),
        answer_responsibilities=base.answer_responsibilities,
        question_budgets=base.question_budgets,
    )
    arguments = {
        "question_id": "act-existing context",
        "context_id": "broker-7",
        **question_payload("reuse one context"),
    }
    model = OpenAICompatibleModel(_client("ask_in_existing_context", arguments))

    proposal = await model.decide(value)

    assert proposal == ToolProposal(
        "",
        "ask_in_existing_context",
        arguments,
    )
    assert semantic_input_data(value)["external_contexts"] == [
        {
            "context_id": "broker-7",
            "guaranteed_shared_context": {"origin_question": "earlier gap"},
        }
    ]
    reuse = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "ask_in_existing_context"
    )
    assert "guaranteed_shared_context" in reuse["function"]["description"]
    assert "仅交付填写的文本和地址" in reuse["function"]["description"]
    assert "未来生长路线" not in reuse["function"]["description"]


@pytest.mark.asyncio
async def test_provider_leaves_structured_argument_validation_to_host() -> None:
    base = _input("invalid existing context")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        base.observations,
        base.capability_catalog,
        external_contexts=(
            {
                "context_id": "broker-7",
                "guaranteed_shared_context": {"origin_question": "earlier gap"},
            },
        ),
        answer_responsibilities=base.answer_responsibilities,
        question_budgets=base.question_budgets,
    )
    arguments = {
        "question_id": "act-invalid existing context",
        **question_payload("missing context id"),
    }
    model = OpenAICompatibleModel(_client("ask_in_existing_context", arguments))

    proposal = await model.decide(value)
    assert proposal.name == "ask_in_existing_context"
    assert proposal.arguments == arguments  # Structural validation belongs to the host.


@pytest.mark.asyncio
async def test_provider_leaves_configured_tool_validation_to_host() -> None:
    base = _input("unoffered capability")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        {},
        (),
        (),
    )
    model = OpenAICompatibleModel(
        _client("world_references_from", {"object_id": "object:seed"})
    )

    proposal = await model.decide(value)
    assert proposal.name == "world_references_from"  # Host validates configured tools.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments_json", "category"),
    [
        (
            "world_references_from",
            json.dumps({"object_id": "object:seed"}),
            "not configured",
        ),
        ("answer_question", "{invalid-json", "invalid_json"),
        ("answer_question", "[]", "argument_shape"),
    ],
)
async def test_runner_recovers_any_replayable_tool_envelope(
    tool_name: str,
    arguments_json: str,
    category: str,
) -> None:
    completions = _RecoveringRejectedCompletions(
        tool_name,
        arguments_json,
        category,
    )
    model = OpenAICompatibleModel(_provider_client(completions))
    runtime = build_default_runtime(model)
    root = runtime.inject_human("形成一个可恢复 Answer")

    handled = await runtime.step()

    assert handled == root
    assert [act.kind for act in runtime.threads[root.thread_id]] == [
        "question",
        "answer",
    ]
    assert [message["role"] for message in runtime.dialogue_for("broker")] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    if category == "not configured":
        assert any(
            t.kind == "tool.observation" and not t.payload["ok"] for t in runtime.traces
        )
        assert not any(t.kind == "model.rejection" for t in runtime.traces)
    else:
        rejection = next(t for t in runtime.traces if t.kind == "model.rejection")
        assert rejection.payload["tool_name"] == tool_name
        assert rejection.payload["arguments_json"] == arguments_json
        assert rejection.payload["category"] == category
    assert not any(trace.kind == "activation.failed" for trace in runtime.traces)
    assert model._actor_transport


@pytest.mark.asyncio
async def test_provider_keeps_a_response_without_tool_calls_as_a_hard_failure() -> None:
    model = OpenAICompatibleModel(_provider_client(_NoToolCompletions()))

    with pytest.raises(ValueError, match="at least one decision tool call"):
        await model.decide(_broker_input("missing tool call"))


@pytest.mark.asyncio
async def test_runner_returns_an_offered_protocol_rejection_to_the_actor() -> None:
    completions = _RecoveringProtocolCompletions()
    model = OpenAICompatibleModel(_provider_client(completions))
    runtime = build_default_runtime(model)
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="补全一个外部事实",
        instructions="只形成实际取得的结果。",
        budget={"world_ops": 1},
        capabilities=frozenset({"world_references_from"}),
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("取得外部事实")
    runtime.queue.clear()
    delegated = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("取得外部事实"),
        derived_from=(root.act_id,),
    )

    handled = await runtime.step()

    assert handled == delegated
    acts = runtime.threads[root.thread_id]
    assert len(acts) == 3
    external = acts[-1]
    assert external.kind == "question"
    assert external.sender == agent.spec.actor_id
    assert external.recipient == "broker"
    assert [
        message["role"] for message in runtime.dialogue_for(agent.spec.actor_id)
    ] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    first_call = runtime.dialogue_for(agent.spec.actor_id)[3]["tool_calls"][0]
    assert first_call["function"]["name"] == "ask_in_existing_context"
    assert "context_id" not in json.loads(first_call["function"]["arguments"])
    first_observation = json.loads(
        runtime.dialogue_for(agent.spec.actor_id)[4]["content"]
    )
    assert first_observation["ok"] is False
    assert "missing required fields" in first_observation["error"]
    assert first_observation["capability_boundary_change"] == {
        "question_id": delegated.act_id,
        "semantic_world_operations": {"before": 1, "after": 1},
    }
    assert not any(trace.kind == "model.rejection" for trace in runtime.traces)
    assert not any(trace.kind == "activation.failed" for trace in runtime.traces)
    assert runtime.actors[agent.spec.actor_id].question_budgets[delegated.act_id] == {
        "world_ops": 1
    }
    assert model._actor_transport


@pytest.mark.asyncio
async def test_provider_uses_only_first_call_before_observation() -> None:
    completions = _MultipleCompletions(
        "answer_question",
        {
            "question_id": "act-multiple",
            "grounding_inputs": [],
            **answer_payload("first"),
        },
    )
    client = _provider_client(completions)

    proposal = await OpenAICompatibleModel(client).decide(_input("multiple"))

    assert proposal == ToolProposal(
        "",
        "answer_question",
        {
            "question_id": "act-multiple",
            "grounding_inputs": [],
            **answer_payload("first"),
        },
    )


@pytest.mark.asyncio
async def test_provider_maps_broker_question_dependencies() -> None:
    base = _broker_input("combine")
    root = base.trigger
    first = Act(
        "act-gap-1",
        root.thread_id,
        "question",
        "broker",
        "agent-a",
        question_payload("First source"),
        reply_to=root.act_id,
        derived_from=(root.act_id,),
    )
    first_answer = Act(
        "act-answer-1",
        root.thread_id,
        "answer",
        "agent-a",
        "broker",
        answer_payload("first result"),
        reply_to=first.act_id,
    )
    second = Act(
        "act-gap-2",
        root.thread_id,
        "question",
        "broker",
        "agent-b",
        question_payload("Second source"),
        reply_to=root.act_id,
        derived_from=(root.act_id,),
    )
    second_answer = Act(
        "act-answer-2",
        root.thread_id,
        "answer",
        "agent-b",
        "broker",
        answer_payload("second result"),
        reply_to=second.act_id,
    )
    value = _native_input(
        base.actor,
        second_answer,
        (root, first, first_answer, second, second_answer),
        {
            "domain": "coordination",
            "actors": [{"actor_id": "agent-1", "role": "agent"}],
        },
        base.observations,
        base.capability_catalog,
        answer_responsibilities=(root,),
    )
    model = OpenAICompatibleModel(
        _client(
            "send_question",
            {
                "question_id": root.act_id,
                "recipient": "agent-1",
                **question_payload("Resolve both gaps"),
                "source_act_ids": [first_answer.act_id, second_answer.act_id],
            },
        )
    )

    tools = OpenAICompatibleModel.protocol_tools(value)
    send = next(item for item in tools if item["function"]["name"] == "send_question")
    assert (
        "enum"
        not in send["function"]["parameters"]["properties"]["source_act_ids"]["items"]
    )
    proposal = await model.decide(value)

    assert proposal == ToolProposal(
        "",
        "send_question",
        {
            "question_id": root.act_id,
            "recipient": "agent-1",
            **question_payload("Resolve both gaps"),
            "source_act_ids": [first_answer.act_id, second_answer.act_id],
        },
    )
    invalid = OpenAICompatibleModel(
        _client(
            "send_question",
            {
                "question_id": root.act_id,
                "recipient": "agent-1",
                **question_payload("Unknown source"),
                "source_act_ids": ["act-unknown"],
            },
        )
    )
    assert (await invalid.decide(value)).arguments["source_act_ids"] == ["act-unknown"]


@pytest.mark.asyncio
async def test_broker_schema_does_not_encode_current_inquiry_targets() -> None:
    base = _broker_input("current")
    older_question = Act(
        "act-old-question",
        "thread-old",
        "question",
        "human",
        "broker",
        question_payload("older responsibility"),
    )
    older_answer = Act(
        "act-old-answer",
        "thread-old",
        "answer",
        "agent-old",
        "broker",
        answer_payload("older result"),
        reply_to=older_question.act_id,
    )
    value = _native_input(
        base.actor,
        base.trigger,
        (base.trigger, older_question, older_answer),
        {
            "domain": "coordination",
            "actors": [{"actor_id": "agent-1", "role": "agent"}],
        },
        (),
        base.capability_catalog,
        answer_responsibilities=(base.trigger,),
    )
    send = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "send_question"
    )
    assert "anyOf" not in send["function"]["parameters"]
    assert OpenAICompatibleModel.protocol_tools(
        value
    ) == OpenAICompatibleModel.protocol_tools(base)

    model = OpenAICompatibleModel(
        _client(
            "send_question",
            {
                "question_id": base.trigger.act_id,
                "recipient": "agent-1",
                **question_payload("use an invalid older source"),
                "source_act_ids": [older_answer.act_id],
            },
        )
    )
    assert (await model.decide(value)).arguments["source_act_ids"] == [
        older_answer.act_id
    ]


@pytest.mark.asyncio
async def test_provider_maps_incremental_act_without_question_dependencies() -> None:
    base = _broker_input("continue")
    prior = Act(
        "act-gap-1",
        "thread-1",
        "incremental",
        "broker",
        "agent-1",
        {"content": "initial incremental content"},
        reply_to=base.trigger.act_id,
    )
    value = _native_input(
        base.actor,
        base.trigger,
        (prior, *base.visible_acts),
        base.local_world,
        base.observations,
        base.capability_catalog,
    )
    model = OpenAICompatibleModel(
        _client(
            "send_incremental_act",
            {
                "content": "candidate result",
                "reply_to": "act-gap-1",
            },
        )
    )

    send_tool = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "send_incremental_act"
    )
    assert "recipient" not in send_tool["function"]["parameters"]["properties"]
    assert "act_kind" not in send_tool["function"]["parameters"]["properties"]
    assert "不建立独立 Answer 责任" in send_tool["function"]["description"]
    assert "不推进判断" in send_tool["function"]["description"]
    assert "交付 content" in send_tool["function"]["description"]
    assert "参数、指代、修正或条件" in send_tool["function"]["description"]
    assert "不携带 Semantic Address" in send_tool["function"]["description"]
    assert "enum" not in send_tool["function"]["parameters"]["properties"]["reply_to"]
    assert not any(
        item["function"]["name"] == "reply_incremental_act"
        for item in OpenAICompatibleModel.protocol_tools(value)
    )

    proposal = await model.decide(value)

    assert proposal == ToolProposal(
        "",
        "send_incremental_act",
        {"content": "candidate result", "reply_to": "act-gap-1"},
    )


@pytest.mark.asyncio
async def test_provider_maps_broker_targeted_answer() -> None:
    model = OpenAICompatibleModel(
        _client(
            "answer_question",
            {
                "question_id": "act-gap-1",
                "grounding_inputs": [],
                **answer_payload("candidate result"),
            },
        )
    )

    base = _broker_input("answer")
    source_question = Act(
        "act-gap-1",
        "thread-1",
        "question",
        "agent-1",
        "broker",
        question_payload("source gap"),
    )
    answer_input = _native_input(
        base.actor,
        Act(
            "act-child-answer",
            "thread-1",
            "answer",
            "agent-1",
            "broker",
            answer_payload("resolved"),
            reply_to=base.trigger.act_id,
        ),
        (*base.visible_acts, source_question),
        base.local_world,
        base.observations,
        base.capability_catalog,
        answer_responsibilities=(base.trigger, source_question),
    )
    answer_question = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(answer_input)
        if item["function"]["name"] == "answer_question"
    )
    assert "recipient" not in answer_question["function"]["parameters"]["properties"]
    assert (
        "enum"
        not in answer_question["function"]["parameters"]["properties"]["question_id"]
    )
    proposal = await model.decide(answer_input)

    assert proposal == ToolProposal(
        "",
        "answer_question",
        {
            "question_id": "act-gap-1",
            "grounding_inputs": [],
            **answer_payload("candidate result"),
        },
    )


@pytest.mark.asyncio
async def test_provider_maps_broker_answer_without_prior_agent_answer() -> None:
    proposal = await OpenAICompatibleModel(
        _client(
            "answer_question",
            {
                "question_id": "act-answer directly",
                "grounding_inputs": [],
                **answer_payload("coordination result"),
            },
        )
    ).decide(_broker_input("answer directly"))

    assert proposal == ToolProposal(
        "",
        "answer_question",
        {
            "question_id": "act-answer directly",
            "grounding_inputs": [],
            **answer_payload("coordination result"),
        },
    )
