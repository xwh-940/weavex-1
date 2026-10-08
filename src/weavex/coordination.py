"""Pure projections for the prototype's Broker-centered coordination groups."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .protocol import INITIALIZATION_SOURCE, Act, handle_data, protocol_addresses

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


GROUP_FORMED_KIND = "group_formed"


def _all_acts(runtime: "PrototypeRuntime") -> Iterable[Act]:
    for acts in runtime.threads.values():
        yield from acts


def _formation_act(runtime: "PrototypeRuntime", broker_id: str) -> Act | None:
    return next(
        (
            act
            for act in _all_acts(runtime)
            if act.kind == GROUP_FORMED_KIND
            and act.sender == broker_id
            and act.recipient == broker_id
        ),
        None,
    )


def _source_question(runtime: "PrototypeRuntime", broker_id: str) -> Act | None:
    if runtime.actors[broker_id].spec.initial_member_ids is not None:
        return None
    return next(
        (
            act
            for act in _all_acts(runtime)
            if act.kind == "question"
            and act.recipient == broker_id
            and act.sender != broker_id
        ),
        None,
    )


def _born_agent_ids(runtime: "PrototypeRuntime", broker_id: str) -> tuple[str, ...]:
    return tuple(
        actor_id
        for actor_id, state in runtime.actors.items()
        if state.spec.role == "agent" and state.spec.created_by == broker_id
    )


def group_data(runtime: "PrototypeRuntime", broker_id: str) -> dict[str, object]:
    broker = runtime.actors.get(broker_id)
    if broker is None or broker.spec.role != "broker":
        raise KeyError(f"unknown Broker context: {broker_id}")
    formation = _formation_act(runtime, broker_id)
    source_question = _source_question(runtime, broker_id)
    selected = (
        tuple(str(value) for value in formation.payload["selected_existing_agents"])
        if formation is not None
        else ()
    )
    born = _born_agent_ids(runtime, broker_id)
    initial = broker.spec.initial_member_ids
    members: list[str] = []
    if (
        source_question is not None
        and source_question.sender in runtime.actors
        and runtime.actors[source_question.sender].spec.role == "agent"
    ):
        members.append(source_question.sender)
    for actor_id in (*(initial or ()), *selected, *born):
        if actor_id not in members:
            members.append(actor_id)
    return {
        "context_id": broker_id,
        "broker_id": broker_id,
        "formed": initial is not None or formation is not None or bool(born),
        **({"origin": INITIALIZATION_SOURCE} if initial is not None else {}),
        "source_actor_id": source_question.sender if source_question else "",
        "source_question_id": source_question.act_id if source_question else "",
        "selected_existing_agents": list(selected),
        "born_agents": list(born),
        "member_ids": members,
    }


def broker_member_ids(runtime: "PrototypeRuntime", broker_id: str) -> frozenset[str]:
    return frozenset(
        str(value) for value in group_data(runtime, broker_id)["member_ids"]
    )


def group_is_formed(runtime: "PrototypeRuntime", broker_id: str) -> bool:
    return bool(group_data(runtime, broker_id)["formed"])


def agent_context_ids(runtime: "PrototypeRuntime", agent_id: str) -> tuple[str, ...]:
    return tuple(
        actor_id
        for actor_id, state in runtime.actors.items()
        if state.spec.role == "broker"
        and group_is_formed(runtime, actor_id)
        and agent_id in broker_member_ids(runtime, actor_id)
    )


def _project_history_act(act: Act) -> dict[str, object]:
    payload: dict[str, Any] = dict(act.payload)
    if act.kind == "question":
        context = dict(payload["interpretation_context"])
        context["semantic_addresses"] = []
        payload["interpretation_context"] = context
    elif act.kind == "answer":
        payload["semantic_addresses"] = []
    return {
        "act_id": act.act_id,
        "thread_id": act.thread_id,
        "kind": act.kind,
        "sender": act.sender,
        "recipient": act.recipient,
        "payload": payload,
        "reply_to": act.reply_to,
        "derived_from": list(act.derived_from),
    }


def _pair_history(
    runtime: "PrototypeRuntime", broker_id: str, agent_ids: frozenset[str]
) -> list[dict[str, object]]:
    return [
        _project_history_act(act)
        for act in _all_acts(runtime)
        if act.kind != GROUP_FORMED_KIND
        and broker_id in {act.sender, act.recipient}
        and bool(agent_ids.intersection({act.sender, act.recipient}))
    ]


def agent_context_cards(
    runtime: "PrototypeRuntime", agent_id: str
) -> tuple[dict[str, object], ...]:
    cards = []
    for context_id in agent_context_ids(runtime, agent_id):
        source_question = _source_question(runtime, context_id)
        cards.append(
            {
                "context_id": context_id,
                "guaranteed_shared_context": {
                    "origin_question": (
                        source_question.payload["raw_question"]
                        if source_question is not None
                        else ""
                    )
                },
            }
        )
    return tuple(cards)


def candidate_agents(
    runtime: "PrototypeRuntime",
    broker_id: str,
    trigger: Act | None,
) -> tuple[dict[str, object], ...]:
    source_question = _source_question(runtime, broker_id)
    source_id = source_question.sender if source_question is not None else ""
    active = (
        trigger
        if trigger is not None and trigger.kind == "question"
        else source_question
    )
    shared_addresses = protocol_addresses(active.kind, active.payload) if active else ()
    source_contexts = (
        agent_context_ids(runtime, source_id)
        if source_id in runtime.actors
        and runtime.actors[source_id].spec.role == "agent"
        else ()
    )
    candidates = []
    for actor_id, state in runtime.actors.items():
        if state.spec.role != "agent" or actor_id == source_id:
            continue
        candidate_handles = runtime.local_worlds.handles_for_actor(actor_id)
        matched = tuple(
            address for address in shared_addresses if address in candidate_handles
        )
        shared_context_ids = tuple(
            context_id
            for context_id in source_contexts
            if actor_id in broker_member_ids(runtime, context_id)
        )
        if not matched and not shared_context_ids:
            continue
        history = []
        for context_id in shared_context_ids:
            history.extend(
                _pair_history(
                    runtime,
                    context_id,
                    frozenset({source_id, actor_id}),
                )
            )
        candidates.append(
            {
                "actor_id": actor_id,
                "capabilities": sorted(state.spec.capabilities),
                "available_capabilities": sorted(
                    runtime.available_capabilities_for_new_question(state)
                ),
                "shared_semantic_addresses": [handle_data(value) for value in matched],
                "shared_context_ids": list(shared_context_ids),
                "related_history": history,
            }
        )
    return tuple(sorted(candidates, key=lambda value: str(value["actor_id"])))


def coordination_world(
    runtime: "PrototypeRuntime",
    broker_id: str,
    trigger: Act | None = None,
) -> dict[str, object]:
    group = group_data(runtime, broker_id)
    members = broker_member_ids(runtime, broker_id) if group["formed"] else frozenset()
    actors = [
        {
            "actor_id": actor_id,
            "role": runtime.actors[actor_id].spec.role,
            "capabilities": sorted(runtime.actors[actor_id].spec.capabilities),
            "available_capabilities": sorted(
                runtime.available_capabilities_for_new_question(
                    runtime.actors[actor_id]
                )
            ),
        }
        for actor_id in sorted(members)
        if actor_id in runtime.actors
    ]
    return {
        "domain": "coordination",
        "context": group,
        "actors": actors,
        "candidates": (
            []
            if group["formed"]
            else list(candidate_agents(runtime, broker_id, trigger))
        ),
        "history": (
            _pair_history(runtime, broker_id, members) if group["formed"] else []
        ),
    }


__all__ = [
    "GROUP_FORMED_KIND",
    "agent_context_cards",
    "agent_context_ids",
    "broker_member_ids",
    "candidate_agents",
    "coordination_world",
    "group_data",
    "group_is_formed",
]
