"""Runtime routing, Act protocol, and Actor authority invariants."""

from __future__ import annotations


import pytest
from weavex.defaults import build_default_runtime
from weavex.protocol import (
    Act,
    Decision,
    answer_payload,
    question_payload,
)
from weavex.provider import (
    OpenAICompatibleModel,
    ScriptedModel,
)


@pytest.mark.asyncio
async def test_root_question_may_end_without_answer() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="no answer needed"))
    )
    root = runtime.inject_human("A notification")

    await runtime.run_until_idle()

    assert runtime.threads[root.thread_id] == [root]


@pytest.mark.asyncio
async def test_answer_is_a_new_act_and_not_a_receipt() -> None:
    runtime = build_default_runtime(
        ScriptedModel(
            lambda _value: Decision(
                "reply",
                act_kind="answer",
                payload=answer_payload("new expression"),
            )
        )
    )
    root = runtime.inject_human("Question")

    await runtime.run_until_idle()

    answer = runtime.threads[root.thread_id][-1]
    assert answer.act_id != root.act_id
    assert answer.reply_to == root.act_id
    assert answer.sender == "broker" and answer.recipient == "human"


def test_human_incremental_reply_inherits_the_original_question() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Establish S0")
    runtime.queue.clear()
    broker = runtime.actors["broker"]
    incremental = runtime.reply_from_actor(
        broker,
        root,
        kind="incremental",
        payload={"content": "Provide the missing setting."},
    )

    reply = runtime.reply_human(
        thread_id=root.thread_id,
        reply_to=incremental.act_id,
        content="The protagonist arrives without the host's memories.",
    )

    assert reply.thread_id == root.thread_id
    assert reply.reply_to == incremental.act_id
    assert reply.sender == "human" and reply.recipient == "broker"
    assert reply.payload == {
        "content": "The protagonist arrives without the host's memories."
    }
    assert list(runtime.queue) == [reply]
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, reply),
        (),
        runtime.tools.catalog_for(broker.spec.capabilities),
    )
    assert value.answer_responsibilities == (root,)

    answer = runtime.reply_from_actor(
        broker,
        reply,
        kind="answer",
        payload=answer_payload("completed after the Human incremental Act"),
    )
    assert answer.recipient == "human"
    assert answer.reply_to == root.act_id


def test_human_can_proactively_continue_own_open_question() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    root = runtime.inject_human("Return the result after I observe it.")
    runtime.queue.clear()

    continuation = runtime.reply_human(
        thread_id=root.thread_id,
        reply_to=root.act_id,
        content="The observed result is tails.",
    )

    assert continuation.thread_id == root.thread_id
    assert continuation.reply_to == root.act_id
    assert continuation.sender == "human"
    assert continuation.recipient == "broker"
    assert continuation.kind == "incremental"
    assert list(runtime.queue) == [continuation]
    assert runtime.answer_responsibilities("broker") == (root,)


def test_human_incremental_rejects_an_act_outside_its_relationship() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    made = runtime.create_agent(
        broker,
        purpose="private relation",
        instructions="",
        budget={},
    )
    root = runtime.inject_human("root")
    runtime.queue.clear()
    delegated = runtime.send_from_actor(
        broker,
        root,
        recipient=str(made["actor_id"]),
        kind="question",
        payload=question_payload("delegated"),
        derived_from=(root.act_id,),
    )

    with pytest.raises(ValueError, match="Human-visible"):
        runtime.reply_human(
            thread_id=root.thread_id,
            reply_to=delegated.act_id,
            content="not my relation",
        )


def test_only_broker_creates_agents_and_agents_do_not_talk_directly() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    first = runtime.create_agent(
        broker,
        purpose="first",
        instructions="Answer Questions.",
        budget={},
    )
    second = runtime.create_agent(
        broker,
        purpose="second",
        instructions="Answer Questions.",
        budget={},
    )
    first_actor = runtime.actors[str(first["actor_id"])]

    with pytest.raises(PermissionError, match="only a Broker"):
        runtime.create_agent(
            first_actor,
            purpose="illegal",
            instructions="Must not be created.",
            budget={},
        )

    root = runtime.inject_human("Start")
    trigger = runtime.send_from_actor(
        broker,
        root,
        recipient=str(first["actor_id"]),
        kind="question",
        payload=question_payload("Start"),
        derived_from=(root.act_id,),
    )
    with pytest.raises(PermissionError, match="communicate only"):
        runtime.send_from_actor(
            first_actor,
            trigger,
            recipient=str(second["actor_id"]),
            kind="question",
            payload=question_payload("not allowed"),
        )


@pytest.mark.asyncio
async def test_broker_reads_multi_gap_question_topology_from_acts() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    agents = []
    for purpose in ("first gap", "second gap", "combined gap"):
        created = runtime.create_agent(
            broker,
            purpose=purpose,
            instructions="Express or resolve one gap.",
            budget={},
        )
        agents.append(runtime.actors[str(created["actor_id"])])

    root = runtime.inject_human("Resolve two related gaps")
    first = runtime.send_from_actor(
        broker,
        root,
        recipient=agents[0].spec.actor_id,
        kind="question",
        payload=question_payload("Find the first gap"),
        derived_from=(root.act_id,),
    )
    second = runtime.send_from_actor(
        broker,
        root,
        recipient=agents[1].spec.actor_id,
        kind="question",
        payload=question_payload("Find the second gap"),
        derived_from=(root.act_id,),
    )
    first_gap = runtime.send_from_actor(
        agents[0],
        first,
        recipient="broker",
        kind="question",
        payload=question_payload("What fills the first gap?"),
    )
    second_gap = runtime.send_from_actor(
        agents[1],
        second,
        recipient="broker",
        kind="question",
        payload=question_payload("What fills the second gap?"),
    )
    partial_answer = runtime.reply_from_actor(
        broker,
        first_gap,
        kind="answer",
        payload=answer_payload("One premise, but not enough"),
    )
    followup_gap = runtime.send_from_actor(
        agents[0],
        partial_answer,
        recipient="broker",
        kind="question",
        payload=question_payload("Which additional premise is still missing?"),
    )
    combined = runtime.send_from_actor(
        broker,
        second_gap,
        recipient=agents[2].spec.actor_id,
        kind="question",
        payload=question_payload("What explains both gaps?"),
        derived_from=(first_gap.act_id, second_gap.act_id),
    )
    nested_gap = runtime.send_from_actor(
        agents[2],
        combined,
        recipient="broker",
        kind="question",
        payload=question_payload("Which missing premise supports that explanation?"),
    )

    topology = await runtime.tools.execute(
        runtime,
        broker,
        nested_gap,
        "inspect_inquiry_topology",
        {},
    )
    questions = {item["question_id"]: item for item in topology["questions"]}

    assert combined.reply_to == second_gap.act_id
    assert combined.derived_from == (first_gap.act_id, second_gap.act_id)
    assert followup_gap.derived_from == (first_gap.act_id,)
    assert nested_gap.derived_from == (combined.act_id,)
    assert questions[first_gap.act_id]["derived_children"] == [
        followup_gap.act_id,
        combined.act_id,
    ]
    assert questions[first_gap.act_id]["answers"] == [partial_answer.act_id]
    assert questions[second_gap.act_id]["derived_children"] == [combined.act_id]
    assert questions[combined.act_id]["derived_children"] == [nested_gap.act_id]
    assert "status" not in questions[combined.act_id]
    assert topology["leaf_questions_without_direct_answers"] == [
        followup_gap.act_id,
        nested_gap.act_id,
    ]
    assert topology["multi_source_questions_without_direct_answers"] == [
        second_gap.act_id
    ]
    assert topology["candidate_source_questions_without_direct_answers"] == []
    assert {item["actor_id"] for item in topology["actors"]} == {
        "broker",
        "agent-1",
        "agent-2",
        "agent-3",
    }

    with pytest.raises(PermissionError, match="cannot call"):
        await runtime.tools.execute(
            runtime,
            agents[0],
            first,
            "inspect_inquiry_topology",
            {},
        )


def test_broker_answers_multi_source_gaps_one_at_a_time() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    agents = []
    for purpose in ("first source", "second source", "combined resolver"):
        created = runtime.create_agent(
            broker,
            purpose=purpose,
            instructions="Participate in one multi-source Inquiry.",
            budget={},
        )
        agents.append(runtime.actors[str(created["actor_id"])])

    root = runtime.inject_human("Resolve both source gaps")
    first_question = runtime.send_from_actor(
        broker,
        root,
        recipient=agents[0].spec.actor_id,
        kind="question",
        payload=question_payload("Find the first source gap"),
        derived_from=(root.act_id,),
    )
    first_gap = runtime.send_from_actor(
        agents[0],
        first_question,
        recipient="broker",
        kind="question",
        payload=question_payload("Resolve the first source gap"),
    )
    second_question = runtime.send_from_actor(
        broker,
        root,
        recipient=agents[1].spec.actor_id,
        kind="question",
        payload=question_payload("Find the second source gap"),
        derived_from=(root.act_id,),
    )
    second_gap = runtime.send_from_actor(
        agents[1],
        second_question,
        recipient="broker",
        kind="question",
        payload=question_payload("Resolve the second source gap"),
    )
    combined = runtime.send_from_actor(
        broker,
        second_gap,
        recipient=agents[2].spec.actor_id,
        kind="question",
        payload=question_payload("Resolve both gaps together"),
        derived_from=(first_gap.act_id, second_gap.act_id),
    )
    combined_answer = runtime.reply_from_actor(
        agents[2],
        combined,
        kind="answer",
        payload=answer_payload("candidate result for both gaps"),
    )

    assert runtime.inquiry_topology(root.thread_id)[
        "candidate_source_questions_without_direct_answers"
    ] == [first_gap.act_id, second_gap.act_id]

    broker_input = runtime.turn_driver.model_input(
        runtime.turn_host(broker, combined_answer), (), ()
    )
    assert broker_input.answer_responsibilities == (root, first_gap, second_gap)
    answer_question_tool = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(broker_input)
        if item["function"]["name"] == "answer_question"
    )
    assert (
        "enum"
        not in answer_question_tool["function"]["parameters"]["properties"][
            "question_id"
        ]
    )
    with pytest.raises(ValueError, match="not an available Question responsibility"):
        runtime.send_from_actor(
            broker,
            combined_answer,
            recipient="human",
            kind="answer",
            payload=dict(combined_answer.payload),
            reply_to="act-unknown",
        )

    first_routed = runtime.reply_from_actor(
        broker,
        combined_answer,
        kind="answer",
        payload=dict(combined_answer.payload),
        reply_to=second_gap.act_id,
    )
    assert first_routed.recipient == agents[1].spec.actor_id
    assert first_routed.reply_to == second_gap.act_id
    second_branch_answer = runtime.reply_from_actor(
        agents[1],
        first_routed,
        kind="answer",
        payload=answer_payload("second branch integrated"),
    )
    assert runtime.inquiry_topology(root.thread_id)[
        "candidate_source_questions_without_direct_answers"
    ] == [first_gap.act_id]

    probed_root_answer = runtime.reply_from_actor(
        broker,
        second_branch_answer,
        kind="answer",
        payload=answer_payload("semantic module chose to complete"),
        reply_to=root.act_id,
    )
    assert probed_root_answer.recipient == "human"
    assert runtime.inquiry_topology(root.thread_id)[
        "candidate_source_questions_without_direct_answers"
    ] == [first_gap.act_id]

    second_routed = runtime.reply_from_actor(
        broker,
        second_branch_answer,
        kind="answer",
        payload=dict(combined_answer.payload),
        reply_to=first_gap.act_id,
    )
    assert second_routed.recipient == agents[0].spec.actor_id
    assert second_routed.reply_to == first_gap.act_id
    first_branch_answer = runtime.reply_from_actor(
        agents[0],
        second_routed,
        kind="answer",
        payload=answer_payload("first branch integrated"),
    )
    assert (
        runtime.inquiry_topology(root.thread_id)[
            "candidate_source_questions_without_direct_answers"
        ]
        == []
    )

    final = runtime.reply_from_actor(
        broker,
        first_branch_answer,
        kind="answer",
        payload=answer_payload("both branches resolved"),
    )
    assert final.recipient == "human"
    assert final.reply_to == root.act_id


def test_question_dependencies_must_reference_prior_questions() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="dependency validation",
        instructions="Answer one Question.",
        budget={},
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("Validate dependency links")
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("Answer once"),
        derived_from=(root.act_id,),
    )
    answer = runtime.reply_from_actor(
        agent,
        question,
        kind="answer",
        payload=answer_payload("done"),
    )

    with pytest.raises(ValueError, match="must reference Questions"):
        runtime.send_from_actor(
            broker,
            answer,
            recipient=agent.spec.actor_id,
            kind="question",
            payload=question_payload("Invalid derivation"),
            derived_from=(answer.act_id,),
        )
    with pytest.raises(ValueError, match="same thread"):
        runtime.send_from_actor(
            broker,
            answer,
            recipient=agent.spec.actor_id,
            kind="question",
            payload=question_payload("Unknown derivation"),
            derived_from=("act-unknown",),
        )


def test_broker_question_requires_an_explicit_semantic_source() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="receive one Question",
        instructions="",
        budget={},
    )
    root = runtime.inject_human("Start")

    with pytest.raises(ValueError, match="explicit non-empty derived_from"):
        runtime.send_from_actor(
            broker,
            root,
            recipient=str(created["actor_id"]),
            kind="question",
            payload=question_payload("Answer once"),
        )


def test_human_questions_must_enter_through_broker() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    created = runtime.create_agent(
        runtime.actors["broker"],
        purpose="not a Human entrypoint",
        instructions="Answer Questions.",
        budget={},
    )

    with pytest.raises(PermissionError, match="through the Broker"):
        runtime.inject_human(
            "Do not bypass the Broker",
            recipient=str(created["actor_id"]),
        )


def test_question_and_answer_acts_require_minimal_protocol_shapes() -> None:
    with pytest.raises(ValueError, match="question requires"):
        Act(
            "act-invalid-question",
            "thread-1",
            "question",
            "broker",
            "agent-1",
            {"content": "not a WU request"},
        )


def test_question_and_answer_semantic_content_fields_are_strings() -> None:
    assert question_payload("one Question") == {
        "raw_question": "one Question",
        "interpretation_context": {
            "content": "",
            "semantic_addresses": [],
        },
        "intent": "",
    }
    with pytest.raises(ValueError, match="raw_question must be a string"):
        question_payload({"question": "structured"})
    with pytest.raises(
        ValueError, match="interpretation_context.content must be a string"
    ):
        question_payload("one Question", interpretation_content={"scope": "one"})
    with pytest.raises(ValueError, match="intent must be a string"):
        question_payload("one Question", intent={"depth": "short"})
    with pytest.raises(ValueError, match="answer must be a string"):
        answer_payload({"result": "structured"})


def test_act_and_decision_kinds_are_normalized_to_lowercase() -> None:
    answer = Act(
        "act-answer",
        "thread-1",
        "Answer",
        "agent-1",
        "broker",
        answer_payload("done"),
    )
    decision = Decision(
        "send",
        recipient="broker",
        act_kind="Question",
        payload=question_payload("one gap"),
    )

    assert answer.kind == "answer"
    assert decision.act_kind == "question"
    with pytest.raises(ValueError, match="answer requires"):
        Act(
            "act-invalid-answer",
            "thread-1",
            "answer",
            "agent-1",
            "broker",
            {"answer": "missing address array"},
        )


def test_agent_question_after_an_answer_remains_child_of_parent_question() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    actor_id = str(
        runtime.create_agent(
            broker,
            purpose="resolve sequential gaps",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_from"}),
        )["actor_id"]
    )
    actor = runtime.actors[actor_id]
    root = runtime.inject_human("Start")
    parent = runtime.send_from_actor(
        broker,
        root,
        recipient=actor_id,
        kind="question",
        payload=question_payload("Resolve the parent"),
        derived_from=(root.act_id,),
    )
    actor.consume(parent.act_id, "world_ops")
    first_gap = runtime.send_from_actor(
        actor,
        parent,
        recipient="broker",
        kind="question",
        payload=question_payload("Resolve the first gap"),
    )
    first_return = runtime.reply_from_actor(
        broker,
        first_gap,
        kind="answer",
        payload=answer_payload("The first gap reveals another gap"),
    )

    second_gap = runtime.send_from_actor(
        actor,
        first_return,
        recipient="broker",
        kind="question",
        payload=question_payload("Resolve the second gap"),
    )
    second_return = runtime.reply_from_actor(
        broker,
        second_gap,
        kind="answer",
        payload=answer_payload("second result"),
    )
    resumed = runtime.turn_driver.model_input(
        runtime.turn_host(actor, second_return), (), ()
    )

    assert first_gap.reply_to == parent.act_id
    assert first_gap.derived_from == (parent.act_id,)
    assert second_gap.reply_to == parent.act_id
    assert second_gap.derived_from == (first_gap.act_id,)
    assert runtime.inquiry.question_for_trigger(second_return) == parent
    assert resumed.answer_responsibilities == (parent,)
    assert resumed.question_budgets[parent.act_id] == {"world_ops": 0}
    assert set(actor.question_budgets) == {parent.act_id}
