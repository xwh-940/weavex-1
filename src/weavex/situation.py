"""Role-relative situated facts projected by the WeaveX semantic host."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .actor import ActorSpec, capability_boundary_data
from .arrival import ActorArrival, local_world_delta_data
from .protocol import INITIALIZATION_SOURCE, Act


def model_data(value: Any) -> Any:
    """Remove host-only Inquiry namespace fields from model-facing data."""

    if isinstance(value, Mapping):
        return {
            str(name): model_data(item)
            for name, item in value.items()
            if name != "thread_id"
        }
    if isinstance(value, (list, tuple)):
        return [model_data(item) for item in value]
    return value


@dataclass(frozen=True)
class SituatedInput:
    """One Actor's effective situation, independent of its turn driver."""

    actor: ActorSpec
    trigger: ActorArrival
    visible_acts: tuple[Act, ...]
    local_world: Mapping[str, Any]
    external_contexts: tuple[Mapping[str, Any], ...] = ()
    answer_responsibilities: tuple[Act, ...] = ()
    responsibility_frontiers: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    question_budgets: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


def incremental_targets(situation: SituatedInput) -> dict[str, str]:
    actor_id = situation.actor.actor_id
    targets = {}
    acts = (
        (situation.trigger, *situation.visible_acts)
        if isinstance(situation.trigger, Act)
        else situation.visible_acts
    )
    for act in acts:
        if INITIALIZATION_SOURCE in {act.sender, act.recipient}:
            continue
        if act.sender == actor_id and act.recipient != actor_id:
            targets[act.act_id] = act.recipient
        elif act.recipient == actor_id and act.sender != actor_id:
            targets[act.act_id] = act.sender
    return targets


def broker_source_acts(situation: SituatedInput) -> tuple[Act, ...]:
    if situation.actor.role != "broker" or not isinstance(situation.trigger, Act):
        return ()
    ordered = (
        situation.trigger,
        *situation.answer_responsibilities,
        *situation.visible_acts,
    )
    selected = []
    seen = set()
    for act in ordered:
        if act.kind not in {"question", "answer"} or act.act_id in seen:
            continue
        seen.add(act.act_id)
        selected.append(act)
    question_ids = {act.act_id for act in selected if act.kind == "question"}
    return tuple(
        act
        for act in selected
        if act.kind == "question" or act.reply_to in question_ids
    )


def _act_data(
    act: Act,
    *,
    role: str,
    visible_ids: frozenset[str],
) -> dict[str, object]:
    value: dict[str, object] = {
        "act_id": act.act_id,
        "kind": act.kind,
        "sender": act.sender,
        "recipient": act.recipient,
        "payload": dict(act.payload),
    }
    if role == "broker":
        value["reply_to"] = act.reply_to
        value["derived_from"] = list(act.derived_from)
    elif act.reply_to in visible_ids:
        value["reply_to"] = act.reply_to
    return value


def answer_responsibility_data(
    question: Act,
    frontier: Mapping[str, object],
    *,
    role: str,
    visible_ids: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Project one current direct gap with its mechanical response frontier."""

    return {
        **_act_data(
            question,
            role=role,
            visible_ids=visible_ids,
        ),
        "response_frontier": model_data(frontier),
    }


def _model_local_world(value: SituatedInput) -> dict[str, object]:
    local_world = dict(value.local_world)
    if value.actor.role != "broker" or local_world.get("domain") != "coordination":
        return local_world
    context = dict(local_world.get("context", {}))
    context.pop("broker_id", None)
    context.pop("member_ids", None)
    local_world["context"] = context
    local_world["actors"] = [
        {name: item for name, item in dict(actor).items() if name != "role"}
        for actor in local_world.get("actors", [])
    ]
    local_world.pop("history", None)
    return local_world


def situated_input_data(value: SituatedInput) -> dict[str, object]:
    """Project the role-relative situation delivered by receive_input."""

    visible_ids = frozenset(act.act_id for act in value.visible_acts)

    def project(act: Act) -> dict[str, object]:
        return _act_data(
            act,
            role=value.actor.role,
            visible_ids=visible_ids,
        )

    responsibility_ids = {item.act_id for item in value.answer_responsibilities}
    arrival = (
        project(value.trigger)
        if isinstance(value.trigger, Act)
        else local_world_delta_data(value.trigger)
    )
    data = {
        "actor": {
            "actor_id": value.actor.actor_id,
        },
        "arrival": arrival,
        "answer_responsibilities": [
            answer_responsibility_data(
                item,
                value.responsibility_frontiers.get(item.act_id, {}),
                role=value.actor.role,
                visible_ids=visible_ids,
            )
            for item in value.answer_responsibilities
        ],
        "visible_acts": [
            project(item)
            for item in value.visible_acts
            if (
                not isinstance(value.trigger, Act)
                or item.act_id != value.trigger.act_id
            )
            and item.act_id not in responsibility_ids
        ],
        "local_world": model_data(_model_local_world(value)),
    }
    if value.actor.role == "agent":
        data["external_contexts"] = model_data(value.external_contexts)
        data["capability_boundaries"] = {
            str(question_id): capability_boundary_data(budget)
            for question_id, budget in value.question_budgets.items()
        }
    return data


__all__ = [
    "SituatedInput",
    "answer_responsibility_data",
    "broker_source_acts",
    "incremental_targets",
    "model_data",
    "situated_input_data",
]
