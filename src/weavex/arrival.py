"""Activation arrivals that do not grant semantic authority to the scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

from .protocol import Act, SemanticHandle, handle_data


NeighborhoodDirection = Literal["outgoing", "incoming"]


def _text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class NeighborhoodObservation:
    """One persistent Agent-local right to future direct-neighborhood additions."""

    observation_id: str
    address: SemanticHandle
    direction: NeighborhoodDirection
    established_from_question_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _text(self.observation_id, "observation_id")
        )
        if self.direction not in {"outgoing", "incoming"}:
            raise ValueError(f"unknown neighborhood direction: {self.direction}")
        if not self.established_from_question_ids:
            raise ValueError("an observation requires an establishing Question")
        origins = tuple(
            _text(value, "question_id") for value in self.established_from_question_ids
        )
        if len(origins) != len(set(origins)):
            raise ValueError("observation Question origins cannot contain duplicates")
        object.__setattr__(self, "established_from_question_ids", origins)


@dataclass(frozen=True, slots=True)
class LocalWorldDelta:
    """One immutable delivery of a Semantic World addition to an observer."""

    delta_id: str
    recipient: str
    reference_id: str
    observation_relations: tuple[NeighborhoodObservation, ...]
    local_world_change: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "delta_id", _text(self.delta_id, "delta_id"))
        object.__setattr__(self, "recipient", _text(self.recipient, "recipient"))
        object.__setattr__(
            self, "reference_id", _text(self.reference_id, "reference_id")
        )
        if not self.observation_relations:
            raise ValueError("a Local World delta requires an observation relation")
        object.__setattr__(self, "local_world_change", dict(self.local_world_change))


ActorArrival: TypeAlias = Act | LocalWorldDelta


def arrival_id(value: ActorArrival) -> str:
    return value.act_id if isinstance(value, Act) else value.delta_id


def arrival_recipient(value: ActorArrival) -> str:
    return value.recipient


def arrival_thread_id(value: ActorArrival) -> str:
    return value.thread_id if isinstance(value, Act) else ""


def neighborhood_observation_data(
    value: NeighborhoodObservation,
) -> dict[str, object]:
    return {
        "observation_id": value.observation_id,
        "observed_address": handle_data(value.address),
        "direction": value.direction,
        "established_from_question_ids": list(value.established_from_question_ids),
    }


def local_world_delta_data(
    value: LocalWorldDelta,
    *,
    include_recipient: bool = False,
) -> dict[str, object]:
    data: dict[str, object] = {
        "delta_id": value.delta_id,
        "kind": "semantic_neighborhood_change",
        "reference_id": value.reference_id,
        "local_world_change": dict(value.local_world_change),
        "observation_relations": [
            neighborhood_observation_data(relation)
            for relation in value.observation_relations
        ],
    }
    if include_recipient:
        data["recipient"] = value.recipient
    return data


__all__ = [
    "ActorArrival",
    "LocalWorldDelta",
    "NeighborhoodDirection",
    "NeighborhoodObservation",
    "arrival_id",
    "arrival_recipient",
    "arrival_thread_id",
    "local_world_delta_data",
    "neighborhood_observation_data",
]
