"""Explicit NativeModelInput constructors shared by backend-facing tests."""

from __future__ import annotations

from weavex.actor import ActorSpec
from weavex.protocol import Act, Observation, question_payload
from weavex.provider import NativeModelInput
from weavex.situation import SituatedInput


def native_input(
    actor: ActorSpec,
    trigger: Act,
    visible_acts: tuple[Act, ...],
    local_world: dict[str, object],
    observations: tuple[Observation, ...],
    capability_catalog: tuple[dict[str, object], ...],
    dialogue: tuple[dict[str, object], ...] = (),
    dialogue_includes_turn: bool = False,
    input_received: bool = True,
    external_contexts: tuple[dict[str, object], ...] = (),
    answer_responsibilities: tuple[Act, ...] = (),
    question_budgets: dict[str, dict[str, int]] | None = None,
) -> NativeModelInput:
    return NativeModelInput(
        SituatedInput(
            actor,
            trigger,
            visible_acts,
            local_world,
            external_contexts=external_contexts,
            answer_responsibilities=answer_responsibilities,
            question_budgets=question_budgets or {},
        ),
        observations,
        capability_catalog,
        dialogue=dialogue,
        dialogue_includes_turn=dialogue_includes_turn,
        input_received=input_received,
    )


def agent_input(trigger_content: str) -> NativeModelInput:
    actor = ActorSpec(
        "agent-1",
        "agent",
        "Stable functional instructions",
        capabilities=frozenset({"world_references_from"}),
    )
    trigger = Act(
        f"act-{trigger_content}",
        "thread-1",
        "question",
        "broker",
        "agent-1",
        question_payload(trigger_content),
    )
    return native_input(
        actor,
        trigger,
        (trigger,),
        {"domain": "semantic", "objects": [], "references": []},
        (),
        (
            {
                "name": "world_references_from",
                "description": "read",
                "parameters": {"object_id": {"type": "string"}},
                "required": ["object_id"],
            },
        ),
        answer_responsibilities=(trigger,),
        question_budgets={trigger.act_id: {"world_ops": 1}},
    )


def broker_input(trigger_content: str) -> NativeModelInput:
    actor = ActorSpec(
        "broker",
        "broker",
        "Coordinate one Inquiry.",
        capabilities=frozenset({"inspect_inquiry_topology"}),
    )
    trigger = Act(
        f"act-{trigger_content}",
        "thread-1",
        "question",
        "human",
        "broker",
        question_payload(trigger_content),
    )
    return native_input(
        actor,
        trigger,
        (trigger,),
        {"domain": "coordination"},
        (),
        (
            {
                "name": "inspect_inquiry_topology",
                "description": "inspect",
                "parameters": {},
                "required": [],
            },
        ),
        answer_responsibilities=(trigger,),
    )
