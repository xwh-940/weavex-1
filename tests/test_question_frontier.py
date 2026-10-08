"""Persistent Question gaps and sparse public Answer nodes."""

from __future__ import annotations

import pytest

from weavex.arrival import LocalWorldDelta, NeighborhoodObservation
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.grounding import GroundingInput
from weavex.protocol import Decision, SemanticHandle, answer_payload, question_payload
from weavex.provider import ScriptedModel
from weavex.situation import situated_input_data
from weavex.question_frontier import FrontierChange


def _runtime_with_agent():
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    agent_id = str(
        runtime.create_agent(
            broker,
            purpose="judge iterative results",
            instructions="",
            budget={},
            capabilities=frozenset(),
        )["actor_id"]
    )
    root = runtime.inject_human("root")
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent_id,
        kind="question",
        payload=question_payload("form a result"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    return runtime, runtime.actors[agent_id], question


def _delta(agent_id: str, question_id: str, index: int) -> LocalWorldDelta:
    return LocalWorldDelta(
        f"delta-{index}",
        agent_id,
        f"reference:{index}",
        (
            NeighborhoodObservation(
                "observation-1",
                SemanticHandle("object", "object:anchor"),
                "incoming",
                (question_id,),
            ),
        ),
    )


def _question_grounding(question) -> tuple[GroundingInput, ...]:
    return (GroundingInput("act", question.act_id),)


def _mark_change(runtime, agent, question, index: int) -> None:
    root = runtime.latest_grounding_id(agent.spec.actor_id, question.act_id)
    runtime.question_frontiers.observe_change(
        agent.spec.actor_id,
        question.act_id,
        FrontierChange(
            f"delta-{index}",
            "semantic_neighborhood_change",
            root,
        ),
    )


def test_answer_nodes_are_immutable_and_a_changed_frontier_reopens_only_a_gap() -> None:
    runtime, agent, question = _runtime_with_agent()
    first = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )
    runtime.queue.clear()

    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()

    _mark_change(runtime, agent, question, 1)

    assert runtime.answer_responsibilities(agent.spec.actor_id) == (question,)
    second = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A1"),
        reply_to=question.act_id,
        grounding_inputs=(
            GroundingInput(
                "grounding",
                runtime.latest_grounding_id(agent.spec.actor_id, question.act_id),
            ),
        ),
    )

    assert runtime.inquiry.direct_answers(question) == (first, second)
    assert first.payload == answer_payload("A0")
    assert second.payload == answer_payload("A1")
    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()


def test_incremental_exposes_a_gap_without_advancing_its_answer_frontier() -> None:
    runtime, agent, question = _runtime_with_agent()

    incremental = runtime.send_from_actor(
        agent,
        question,
        recipient="broker",
        kind="incremental",
        payload={"content": "The requested result is not yet available."},
        reply_to=question.act_id,
    )

    assert incremental.kind == "incremental"
    assert incremental.reply_to == question.act_id
    assert runtime.inquiry.direct_answers(question) == ()
    assert runtime.answer_responsibilities(agent.spec.actor_id) == (question,)
    frontier = runtime.question_frontiers.state(
        agent.spec.actor_id,
        question.act_id,
    )
    assert frontier.latest_answer_id == ""
    assert frontier.latest_judged_change_id == ""

    answered = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("the requested result is now complete"),
        reply_to=question.act_id,
        grounding_inputs=(GroundingInput("act", incremental.act_id),),
    )

    assert runtime.inquiry.direct_answers(question) == (answered,)
    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()


@pytest.mark.asyncio
async def test_yield_does_not_judge_a_pending_frontier_and_later_arrival_projects_it() -> (
    None
):
    runtime, agent, question = _runtime_with_agent()
    runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )
    runtime.queue.clear()
    delta = _delta(agent.spec.actor_id, question.act_id, 1)
    _mark_change(runtime, agent, question, 1)

    first_host = runtime.turn_host(agent, delta)
    await first_host.execute(ToolProposal("receive-delta", "receive_input"))
    yielded = await first_host.execute(ToolProposal("yield", "yield_turn"))

    assert yielded.turn_ended is True
    assert runtime.answer_responsibilities(agent.spec.actor_id) == (question,)

    later_root = runtime.inject_human("another root")
    runtime.queue.clear()
    later = runtime.send_from_actor(
        runtime.actors["broker"],
        later_root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("another result"),
        derived_from=(later_root.act_id,),
    )
    runtime.queue.clear()
    later_input = runtime.build_situated_input(agent, later)

    assert later_input.answer_responsibilities == (question, later)
    projected = {
        item["act_id"]: item
        for item in situated_input_data(later_input)["answer_responsibilities"]
    }
    assert projected[question.act_id]["response_frontier"]["pending_changes"] == [
        {
            "change_id": delta.delta_id,
            "kind": "semantic_neighborhood_change",
            "through_grounding_id": runtime.latest_grounding_id(
                agent.spec.actor_id, question.act_id
            ),
        }
    ]
    assert projected[later.act_id]["response_frontier"]["gap"] == ("no_public_answer")


@pytest.mark.asyncio
async def test_retain_answer_is_private_and_rejects_a_second_settlement() -> None:
    runtime, agent, question = _runtime_with_agent()
    first = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )
    runtime.queue.clear()
    delta = _delta(agent.spec.actor_id, question.act_id, 1)
    _mark_change(runtime, agent, question, 1)
    host = runtime.turn_host(agent, delta)
    await host.execute(ToolProposal("receive", "receive_input"))

    retained = await host.execute(
        ToolProposal(
            "retain",
            "retain_answer",
            {
                "question_id": question.act_id,
                "grounding_inputs": [
                    {
                        "kind": "grounding",
                        "id": runtime.latest_grounding_id(
                            agent.spec.actor_id, question.act_id
                        ),
                    }
                ],
            },
        )
    )

    assert retained.observation.ok is True
    assert retained.observation.value["question_id"] == question.act_id
    assert retained.observation.value["retained_answer_id"] == first.act_id
    assert retained.observation.value["judged_through_change_id"] == delta.delta_id
    assert retained.observation.value["grounding_id"] == (
        runtime.latest_grounding_id(agent.spec.actor_id, question.act_id)
    )
    assert retained.observation.value["previous_grounding_id"]
    assert retained.observation.value["previous_grounding_inherited"] is True
    assert retained.observation.value["public_act_created"] is False
    assert runtime.inquiry.direct_answers(question) == (first,)
    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()
    rejected = await host.execute(
        ToolProposal(
            "retain-again",
            "retain_answer",
            {"question_id": question.act_id, "grounding_inputs": []},
        )
    )
    assert rejected.observation.ok is False


def test_retain_reports_when_the_previous_grounding_is_not_inherited() -> None:
    runtime, agent, question = _runtime_with_agent()
    runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )
    previous = runtime.latest_grounding_id(agent.spec.actor_id, question.act_id)
    _mark_change(runtime, agent, question, 1)

    retained = runtime.retain_answer(
        agent.spec.actor_id,
        question.act_id,
        (),
    )

    assert retained["previous_grounding_id"] == previous
    assert retained["previous_grounding_inherited"] is False
    assert retained["grounding_id"] != previous


def test_multiple_unjudged_deltas_collapse_only_when_actor_judges_them() -> None:
    runtime, agent, question = _runtime_with_agent()
    first = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )
    runtime.queue.clear()
    _mark_change(runtime, agent, question, 1)
    _mark_change(runtime, agent, question, 2)

    data = runtime.question_frontiers.responsibility_data(
        runtime.inquiry,
        agent.spec.actor_id,
        question.act_id,
    )
    assert data["pending_changes"] == [
        {
            "change_id": "delta-1",
            "kind": "semantic_neighborhood_change",
            "through_grounding_id": runtime.latest_grounding_id(
                agent.spec.actor_id, question.act_id
            ),
        },
        {
            "change_id": "delta-2",
            "kind": "semantic_neighborhood_change",
            "through_grounding_id": runtime.latest_grounding_id(
                agent.spec.actor_id, question.act_id
            ),
        },
    ]

    retained = runtime.retain_answer(
        agent.spec.actor_id,
        question.act_id,
        (
            GroundingInput(
                "grounding",
                runtime.latest_grounding_id(agent.spec.actor_id, question.act_id),
            ),
        ),
    )

    assert retained["retained_answer_id"] == first.act_id
    assert retained["judged_through_change_id"] == "delta-2"
    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()


def test_reset_clears_private_question_frontiers() -> None:
    runtime, agent, question = _runtime_with_agent()
    _mark_change(runtime, agent, question, 1)

    runtime.reset()

    assert runtime.question_frontiers.data_for_actor(agent.spec.actor_id) == []
    assert runtime.groundings.data_for_actor(agent.spec.actor_id) == []


@pytest.mark.asyncio
async def test_new_agent_answer_reopens_broker_result_on_the_single_return_chain() -> (
    None
):
    runtime, agent, question = _runtime_with_agent()
    broker = runtime.actors["broker"]
    root = runtime.inquiry.act(question.reply_to or "")
    first_agent_answer = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("agent A0"),
        reply_to=question.act_id,
        grounding_inputs=_question_grounding(question),
    )

    await runtime.step()

    first_broker_answer = runtime.reply_from_actor(
        broker,
        first_agent_answer,
        kind="answer",
        payload=answer_payload("human A0"),
        reply_to=root.act_id,
        grounding_inputs=(GroundingInput("act", first_agent_answer.act_id),),
    )
    assert runtime.answer_responsibilities(broker.spec.actor_id) == ()
    _mark_change(runtime, agent, question, 1)
    second_agent_answer = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("agent A1"),
        reply_to=question.act_id,
        grounding_inputs=(
            GroundingInput(
                "grounding",
                runtime.latest_grounding_id(agent.spec.actor_id, question.act_id),
            ),
        ),
    )

    await runtime.step()

    assert runtime.answer_responsibilities(broker.spec.actor_id) == (root,)
    frontier = runtime.question_frontiers.responsibility_data(
        runtime.inquiry,
        broker.spec.actor_id,
        root.act_id,
    )
    assert frontier["latest_answer_id"] == first_broker_answer.act_id
    assert frontier["pending_changes"] == [
        {
            "change_id": second_agent_answer.act_id,
            "kind": "coordination_act",
            "through_grounding_id": runtime.latest_grounding_id(
                broker.spec.actor_id, root.act_id
            ),
        }
    ]
    second_broker_answer = runtime.reply_from_actor(
        broker,
        second_agent_answer,
        kind="answer",
        payload=answer_payload("human A1"),
        reply_to=root.act_id,
        grounding_inputs=(GroundingInput("act", second_agent_answer.act_id),),
    )

    assert runtime.inquiry.direct_answers(root) == (
        first_broker_answer,
        second_broker_answer,
    )
    assert runtime.answer_responsibilities(broker.spec.actor_id) == ()


@pytest.mark.asyncio
async def test_human_incremental_reopens_broker_gap_without_forcing_a_new_answer() -> (
    None
):
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="judge later"))
    )
    broker = runtime.actors["broker"]
    root = runtime.inject_human("return a staged result")
    runtime.queue.clear()
    first = runtime.reply_from_actor(
        broker,
        root,
        kind="answer",
        payload=answer_payload("A0"),
        reply_to=root.act_id,
        grounding_inputs=(GroundingInput("act", root.act_id),),
    )
    continuation = runtime.reply_human(
        thread_id=root.thread_id,
        reply_to=first.act_id,
        content="A new condition now applies.",
    )

    await runtime.step()

    assert runtime.answer_responsibilities(broker.spec.actor_id) == (root,)
    assert runtime.available_capabilities(broker) == broker.spec.capabilities
    retained = runtime.retain_answer(
        broker.spec.actor_id,
        root.act_id,
        (
            GroundingInput(
                "grounding",
                runtime.latest_grounding_id(broker.spec.actor_id, root.act_id),
            ),
            GroundingInput("act", continuation.act_id),
        ),
    )
    assert retained["retained_answer_id"] == first.act_id
    assert retained["judged_through_change_id"] == continuation.act_id
    assert runtime.inquiry.direct_answers(root) == (first,)
    assert runtime.answer_responsibilities(broker.spec.actor_id) == ()
    assert runtime.available_capabilities(broker) == frozenset()
