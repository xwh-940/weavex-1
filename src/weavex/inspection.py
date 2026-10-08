"""Read-only developer projections over the in-memory runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .actor import ActorState
from .arrival import LocalWorldDelta, local_world_delta_data
from .protocol import Act
from .provider_usage import provider_usage_summary

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


def act_data(act: Act) -> dict[str, object]:
    return {
        "act_id": act.act_id,
        "thread_id": act.thread_id,
        "kind": act.kind,
        "sender": act.sender,
        "recipient": act.recipient,
        "payload": dict(act.payload),
        "reply_to": act.reply_to,
        "derived_from": list(act.derived_from),
    }


def arrival_data(value: Act | LocalWorldDelta) -> dict[str, object]:
    return (
        local_world_delta_data(value, include_recipient=True)
        if isinstance(value, LocalWorldDelta)
        else act_data(value)
    )


def actor_data(
    actor: ActorState,
    dialogue: tuple[object, ...] = (),
) -> dict[str, object]:
    return {
        "actor_id": actor.spec.actor_id,
        "role": actor.spec.role,
        "instructions": actor.spec.instructions,
        "capabilities": sorted(actor.spec.capabilities),
        "created_by": actor.spec.created_by,
        **(
            {"initial_member_ids": list(actor.spec.initial_member_ids)}
            if actor.spec.initial_member_ids is not None
            else {}
        ),
        "initial_budget": dict(actor.initial_budget),
        "dialogue": [dict(message) for message in dialogue],
    }


def actor_view(
    runtime: "PrototypeRuntime", actor_id: str, thread_id: str
) -> dict[str, object]:
    actor = runtime.actors[actor_id]
    visible_acts = runtime.visible_acts(actor_id, thread_id)
    local_world = (
        runtime.local_worlds.view_for(actor.spec.actor_id)
        if actor.spec.role == "agent"
        else runtime.coordination_local_world(
            actor,
            visible_acts[-1] if visible_acts else None,
        )
    )
    grounding_data = runtime.groundings.data_for_actor(actor_id)
    if grounding_data:
        local_world = {**local_world, "groundings": grounding_data}
    return {
        "actor": actor_data(actor, runtime.dialogue_for(actor_id)),
        "visible_acts": [act_data(item) for item in visible_acts],
        "local_world": local_world,
        "answer_responsibilities": [
            {
                **act_data(item),
                "response_frontier": runtime.question_frontiers.responsibility_data(
                    runtime.inquiry,
                    actor_id,
                    item.act_id,
                ),
            }
            for item in runtime.answer_responsibilities(actor_id)
        ],
        "question_budgets": (
            {
                question.act_id: dict(actor.budget_for(question.act_id))
                for question in runtime.answer_responsibilities(actor_id)
            }
            if actor.spec.role == "agent"
            else {}
        ),
    }


def agent_local_worlds_data(runtime: "PrototypeRuntime") -> list[dict[str, object]]:
    return [
        {
            **item,
            "question_frontiers": runtime.question_frontiers.data_for_actor(
                str(item["actor_id"])
            ),
            "groundings": runtime.groundings.data_for_actor(str(item["actor_id"])),
            "question_budgets": {
                question_id: dict(budget)
                for question_id, budget in runtime.actors[
                    str(item["actor_id"])
                ].question_budgets.items()
            },
        }
        for item in runtime.local_worlds.locals_data()
        if str(item["actor_id"]) in runtime.actors
    ]


def runtime_snapshot(runtime: "PrototypeRuntime") -> dict[str, object]:
    return {
        "actors": [
            actor_data(item, runtime.dialogue_for(item.spec.actor_id))
            for item in runtime.actors.values()
        ],
        "threads": {
            thread_id: [act_data(item) for item in acts]
            for thread_id, acts in runtime.threads.items()
        },
        "queue": [arrival_data(item) for item in runtime.queue],
        "semantic_world": runtime.local_worlds.snapshot_data(),
        "agent_local_worlds": agent_local_worlds_data(runtime),
        "provider_usage": provider_usage_summary(
            trace.payload for trace in runtime.traces if trace.kind == "provider.usage"
        ),
        "host_costs": runtime.host_costs(),
        "coordination_contexts": [
            runtime.coordination_local_world(state)["context"]
            for state in runtime.actors.values()
            if state.spec.role == "broker"
        ],
        "traces": [
            {
                "seq": item.seq,
                "kind": item.kind,
                "actor_id": item.actor_id,
                "thread_id": item.thread_id,
                "act_id": item.act_id,
                "payload": dict(item.payload),
            }
            for item in runtime.traces
        ],
    }


__all__ = [
    "act_data",
    "arrival_data",
    "actor_data",
    "actor_view",
    "agent_local_worlds_data",
    "runtime_snapshot",
]
