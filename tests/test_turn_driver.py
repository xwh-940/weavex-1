"""Backend conformance checks independent of the native ModelPort."""

from __future__ import annotations

import pytest

from weavex.defaults import build_default_runtime
from weavex.dialogue import (
    assistant_tool_call_message,
    tool_message,
    turn_message,
)
from weavex.execution import ToolProposal
from weavex.protocol import ModelToolCall, answer_payload, question_payload


class _RecordingTurnDriver:
    def __init__(self) -> None:
        self.activations: list[tuple[str, str]] = []
        self.dialogues: dict[str, tuple[dict[str, object], ...]] = {}
        self.was_reset = False

    async def activate(self, host) -> None:
        self.activations.append((host.actor_id, host.activation_id))
        self.dialogues[host.actor_id] = (
            {"role": "user", "content": '{"kind":"actor_turn"}'},
        )

    def dialogue_for(self, actor_id: str) -> tuple[dict[str, object], ...]:
        return self.dialogues.get(actor_id, ())

    def reset(self) -> None:
        self.dialogues.clear()
        self.was_reset = True


class _DirectProposalTurnDriver:
    """A second driver proving the host contract without any ModelPort."""

    def __init__(self) -> None:
        self.dialogues: dict[str, list[dict[str, object]]] = {}
        self.call_sequence = 0

    def dialogue_for(self, actor_id: str) -> tuple[dict[str, object], ...]:
        return tuple(dict(message) for message in self.dialogues.get(actor_id, []))

    def reset(self) -> None:
        self.dialogues.clear()
        self.call_sequence = 0

    async def _call(self, host, name: str, arguments=None):
        available = {item["function"]["name"] for item in host.tool_schemas()}
        assert name in available
        self.call_sequence += 1
        proposal = ToolProposal(
            f"direct-call-{self.call_sequence}",
            name,
            arguments or {},
        )
        dialogue = self.dialogues.setdefault(host.actor_id, [])
        assistant, call_id = assistant_tool_call_message(
            ModelToolCall(proposal.call_id, proposal.name, proposal.arguments),
            fallback_call_id=proposal.call_id,
        )
        dialogue.append(assistant)
        execution = await host.execute(proposal)
        dialogue.append(tool_message(call_id, execution.observation))
        return execution

    async def activate(self, host) -> None:
        dialogue = self.dialogues.setdefault(host.actor_id, [])
        dialogue.append(turn_message())
        schemas = host.tool_schemas()
        received = await self._call(host, "receive_input")
        assert received.observation.ok is True
        assert host.tool_schemas() == schemas
        situation = host.situation()
        question = situation.answer_responsibilities[0]
        if host.actor_spec.role == "agent":
            await self._call(
                host,
                "answer_question",
                {
                    "question_id": question.act_id,
                    "grounding_inputs": [],
                    **answer_payload("resolved"),
                },
            )
        elif situation.trigger.sender == "human":
            created = await self._call(
                host,
                "create_actor",
                {
                    "purpose": "direct proposal source",
                    "budget": {},
                    "capabilities": [],
                },
            )
            await self._call(
                host,
                "send_question",
                {
                    "question_id": question.act_id,
                    "recipient": created.observation.value["actor_id"],
                    **question_payload("resolve through the host tool surface"),
                    "source_act_ids": [question.act_id],
                },
            )
        else:
            await self._call(
                host,
                "answer_question",
                {
                    "question_id": question.act_id,
                    "grounding_inputs": [],
                    **answer_payload("integrated"),
                },
            )
        yielded = await self._call(host, "yield_turn")
        assert yielded.turn_ended is True


@pytest.mark.asyncio
async def test_runtime_delegates_activation_without_owning_driver_dialogue() -> None:
    driver = _RecordingTurnDriver()
    runtime = build_default_runtime(turn_driver=driver)
    root = runtime.inject_human("observe one backend-neutral activation")

    situated = runtime.build_situated_input(runtime.actors["broker"], root)
    assert not hasattr(situated, "dialogue")
    assert not hasattr(situated, "observations")
    assert not hasattr(situated, "capability_catalog")

    assert await runtime.step() == root
    assert driver.activations == [("broker", root.act_id)]
    assert runtime.snapshot()["actors"][0]["dialogue"] == [
        {"role": "user", "content": '{"kind":"actor_turn"}'}
    ]

    runtime.reset()

    assert driver.was_reset is True
    assert runtime.dialogue_for("broker") == ()


@pytest.mark.asyncio
async def test_direct_tool_proposal_driver_completes_the_same_actor_loop() -> None:
    driver = _DirectProposalTurnDriver()
    runtime = build_default_runtime(turn_driver=driver)
    root = runtime.inject_human("resolve without the native ModelPort")

    assert await runtime.run_until_idle() == 3

    answers = [
        act
        for act in runtime.threads[root.thread_id]
        if act.kind == "answer" and act.recipient == "human"
    ]
    assert [act.payload for act in answers] == [answer_payload("integrated")]
    assert set(runtime.actors) == {"broker", "agent-1"}
    assert all(runtime.dialogue_for(actor_id) for actor_id in runtime.actors)
