"""Synthetic private provider fields survive turns without entering inspection."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from weavex.defaults import build_default_runtime
from weavex.provider import OpenAICompatibleModel


class ConversationCompletions:
    def __init__(self):
        self.owner = "broker"
        self.requests = []
        self.counts = {}
        self.responses = {}
        self.fail_after_receive = False

    async def create(self, **kwargs):
        self.requests.append((self.owner, deepcopy(kwargs)))
        if self.fail_after_receive and kwargs["messages"][-1]["role"] == "tool":
            raise TimeoutError("synthetic transport failure")
        last = kwargs["messages"][-1]
        if last["role"] == "user":
            name, arguments = "receive_input", {}
        else:
            result = json.loads(last["content"])
            if (
                result["source"] == "receive_input"
                and result["value"]["answer_responsibilities"]
            ):
                question = result["value"]["answer_responsibilities"][0]
                name, arguments = (
                    "answer_question",
                    {
                        "semantic_addresses": [],
                        "answer": "result",
                        "grounding_inputs": [],
                        "question_id": question["act_id"],
                    },
                )
            else:
                name, arguments = "yield_turn", {}
        index = self.counts.get(self.owner, 0) + 1
        self.counts[self.owner] = index
        call_id = f"call-{index}"  # Deliberate collisions across Actors.
        raw = json.dumps(arguments, indent=2)
        reason = f"synthetic-private-{self.owner}-{index}"
        self.responses[(self.owner, call_id)] = (raw, reason)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="ordinary visible content",
                        reasoning_content=reason,
                        tool_calls=[
                            SimpleNamespace(
                                id=call_id,
                                function=SimpleNamespace(name=name, arguments=raw),
                            )
                        ],
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_reasoning_and_original_arguments_extend_each_actors_actual_prefix_until_reset():
    completions = ConversationCompletions()
    model = OpenAICompatibleModel(
        SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    runtime = build_default_runtime(model)
    runtime.inject_human("first")
    await runtime.step()
    first_schemas = deepcopy(completions.requests[0][1]["tools"])
    assert model._actor_transport["broker"]
    assert "synthetic-private" not in json.dumps(runtime.snapshot())
    runtime.inject_human("second")
    await runtime.step()
    broker_requests = [
        item for owner, item in completions.requests if owner == "broker"
    ]
    assert all(item["tools"] == first_schemas for item in broker_requests)
    for previous, current in zip(broker_requests, broker_requests[1:]):
        assert current["messages"][: len(previous["messages"])] == previous["messages"]
    for message in broker_requests[3]["messages"]:
        if message["role"] != "assistant":
            continue
        call = message["tool_calls"][0]
        raw, reason = completions.responses[("broker", call["id"])]
        assert call["function"]["arguments"] == raw
        assert message["reasoning_content"] == reason
    other = runtime.create_broker().spec.actor_id
    completions.owner = other
    runtime.inject_human("independent", recipient=other)
    await runtime.step()
    for owner, request in completions.requests:
        if owner == other:
            assert 'synthetic-private-broker-1"' not in json.dumps(request)
            for message in request["messages"]:
                if message["role"] == "assistant":
                    assert message["reasoning_content"].startswith(
                        f"synthetic-private-{other}-"
                    )
    assert set(model._actor_transport) == {"broker", other}
    assert "synthetic-private" not in json.dumps(runtime.snapshot())
    runtime.reset()
    assert model._actor_transport == {}
    completions.owner = "broker"
    runtime.inject_human("fresh")
    await runtime.step()
    assert len(completions.requests[-3][1]["messages"]) == 3


@pytest.mark.asyncio
async def test_activation_failure_preserves_already_received_transport_history():
    completions = ConversationCompletions()
    completions.fail_after_receive = True
    model = OpenAICompatibleModel(
        SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    runtime = build_default_runtime(model)
    runtime.inject_human("first")
    with pytest.raises(TimeoutError):
        await runtime.step()
    assert (
        model._actor_transport["broker"]["call-1"]["reasoning_content"]
        == "synthetic-private-broker-1"
    )
    assert "synthetic-private" not in json.dumps(runtime.snapshot())
    completions.fail_after_receive = False
    runtime.inject_human("later")
    await runtime.step()
    resumed = completions.requests[2][1]
    assert any(
        m.get("reasoning_content") == "synthetic-private-broker-1"
        for m in resumed["messages"]
    )
    runtime.reset()
    assert not model._actor_transport
