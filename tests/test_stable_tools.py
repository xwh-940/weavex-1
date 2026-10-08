"""Fixed grammars must leave changing execution permissions with the host."""

from copy import deepcopy

import pytest

from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.protocol import Decision, SemanticHandle, answer_payload, question_payload
from weavex.provider import ScriptedModel
from weavex.world import ObjectAddress, SemanticWorld


def setup_runtime():
    world = SemanticWorld()
    world.add_object("known", object_id="known")
    world.add_object("new target", object_id="target")
    world.add_reference("known", ObjectAddress("target"), reference_id="edge")
    runtime = build_default_runtime(
        ScriptedModel(lambda _: Decision("stop")), world=world
    )
    broker = runtime.actors["broker"]
    agent_id = runtime.create_agent(broker, instructions="", budget={"world_ops": 1})[
        "actor_id"
    ]
    root = runtime.inject_human(
        "read", semantic_addresses=(SemanticHandle("object", "known"),)
    )
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent_id,
        kind="question",
        payload=root.payload,
        derived_from=(root.act_id,),
    )
    agent = runtime.actors[agent_id]
    return runtime, broker, agent, root, question


def effects(runtime):
    return deepcopy(
        {
            "actors": runtime.actor_descriptors(),
            "world": runtime.local_worlds.snapshot_data(),
            "acts": runtime.threads,
            "budgets": {
                key: value.question_budgets for key, value in runtime.actors.items()
            },
            "queue": list(runtime.queue),
        }
    )


@pytest.mark.asyncio
async def test_fixed_schema_survives_read_exhaustion_answer_and_next_turn():
    runtime, _, agent, _, question = setup_runtime()
    host = runtime.turn_host(agent, question)
    schemas = host.tool_schemas()
    assert host.tool_schemas() == schemas
    assert (
        agent.question_budgets == {}
    )  # Reading schemas cannot receive situated facts.
    blocked = await host.execute(ToolProposal("early", "yield_turn"))
    assert not blocked.observation.ok
    assert (await host.execute(ToolProposal("receive", "receive_input"))).observation.ok
    assert host.tool_schemas() == schemas
    result = await host.execute(
        ToolProposal(
            "read",
            "world_references_from",
            {"question_id": question.act_id, "object_id": "known"},
        )
    )
    assert result.observation.ok
    assert host.tool_schemas() == schemas
    assert agent.budget_for(question.act_id)["world_ops"] == 0
    before = effects(runtime)
    failed = await host.execute(
        ToolProposal(
            "exhausted",
            "world_add_object",
            {"question_id": question.act_id, "content": "must not exist"},
        )
    )
    assert not failed.observation.ok
    assert effects(runtime) == before
    assert (
        await host.execute(
            ToolProposal(
                "answer",
                "answer_question",
                {
                    "question_id": question.act_id,
                    "grounding_inputs": [],
                    **answer_payload("known target"),
                },
            )
        )
    ).observation.ok
    assert runtime.answer_responsibilities(agent.spec.actor_id) == ()
    assert host.tool_schemas() == schemas
    before = effects(runtime)
    rejected = await host.execute(
        ToolProposal(
            "duplicate",
            "answer_question",
            {
                "question_id": question.act_id,
                "grounding_inputs": [],
                **answer_payload("duplicate"),
            },
        )
    )
    assert not rejected.observation.ok
    assert effects(runtime) == before
    assert (await host.execute(ToolProposal("end", "yield_turn"))).turn_ended
    assert not (
        await host.execute(ToolProposal("late", "receive_input"))
    ).observation.ok
    assert runtime.turn_host(agent, question).tool_schemas() == schemas


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("receive_input", {"unexpected": 1}),
        ("yield_turn", {"unexpected": 1}),
        ("world_add_object", {"content": "missing question"}),
        ("world_add_object", {"question_id": 1, "content": "wrong type"}),
        ("world_add_object", {"question_id": "unknown", "content": "unknown target"}),
        (
            "world_references_from",
            {"question_id": "QUESTION", "object_id": "known", "limit": True},
        ),
        (
            "world_add_reference",
            {
                "question_id": "QUESTION",
                "source_object_id": "known",
                "target": {"kind": "object", "id": "target", "extra": True},
            },
        ),
        ("create_actor", {"budget": {}, "capabilities": []}),
        (
            "ask_external_question",
            {"question_id": "ROOT", **question_payload("not assigned here")},
        ),
        (
            "ask_in_existing_context",
            {
                "question_id": "QUESTION",
                "context_id": "nonmember",
                **question_payload("gap"),
            },
        ),
        (
            "answer_question",
            {
                "question_id": "QUESTION",
                "grounding_inputs": [{"kind": "object", "id": "target"}],
                **answer_payload("unknown private material"),
            },
        ),
    ],
)
async def test_direct_driver_invalid_proposals_have_no_effects(name, arguments):
    runtime, _, agent, root, question = setup_runtime()
    host = runtime.turn_host(agent, question)
    await host.execute(ToolProposal("receive", "receive_input"))
    arguments = {
        key: question.act_id
        if value == "QUESTION"
        else root.act_id
        if value == "ROOT"
        else value
        for key, value in arguments.items()
    }
    before = effects(runtime)
    result = await host.execute(ToolProposal("invalid", name, arguments))
    assert not result.observation.ok
    assert effects(runtime) == before
    if "question_id" not in arguments or arguments["question_id"] != question.act_id:
        assert not result.observation.capability_boundary_change


@pytest.mark.asyncio
async def test_broker_validates_sources_members_and_gap_with_fixed_tools():
    runtime, broker, agent, root, _ = setup_runtime()
    other_root = runtime.inject_human("another Inquiry")
    host = runtime.turn_host(broker, root)
    schemas = host.tool_schemas()
    await host.execute(ToolProposal("receive", "receive_input"))
    before = effects(runtime)
    for source, recipient in [
        (other_root.act_id, agent.spec.actor_id),
        ("unknown", agent.spec.actor_id),
        (root.act_id, "missing-agent"),
    ]:
        result = await host.execute(
            ToolProposal(
                "send",
                "send_question",
                {
                    "question_id": root.act_id,
                    "recipient": recipient,
                    "source_act_ids": [source],
                    **question_payload("derived"),
                },
            )
        )
        assert not result.observation.ok
        assert effects(runtime) == before
    result = await host.execute(
        ToolProposal(
            "form", "form_group", {"question_id": root.act_id, "member_ids": []}
        )
    )
    assert not result.observation.ok  # Creating the member already formed this group.
    assert effects(runtime) == before
    for q in (root, other_root):
        result = await host.execute(
            ToolProposal(
                "answer",
                "answer_question",
                {
                    "question_id": q.act_id,
                    "grounding_inputs": [],
                    **answer_payload("result"),
                },
            )
        )
        assert result.observation.ok
    before = effects(runtime)
    denied = await host.execute(
        ToolProposal("create", "create_actor", {"budget": {}, "capabilities": []})
    )
    assert not denied.observation.ok
    assert effects(runtime) == before
    assert host.tool_schemas() == schemas
