"""Agent-local persistent observation of direct semantic neighborhoods."""

from __future__ import annotations

from dataclasses import replace

from .arrival import NeighborhoodDirection, NeighborhoodObservation
from .protocol import SemanticHandle
from .world import ObjectAddress, SemanticReference


def _observation_key(
    value: NeighborhoodObservation,
) -> tuple[str, str, str]:
    return (
        value.direction,
        value.address.kind,
        value.address.semantic_id,
    )


class NeighborhoodIndex:
    """Own persistent observation relations without entering Semantic World."""

    def __init__(self) -> None:
        self._by_actor: dict[
            str, dict[tuple[str, str, str], NeighborhoodObservation]
        ] = {}
        self._sequence = 0

    def observe(
        self,
        actor_id: str,
        *,
        question_id: str,
        address: SemanticHandle,
        direction: NeighborhoodDirection,
    ) -> NeighborhoodObservation:
        key = (direction, address.kind, address.semantic_id)
        observations = self._by_actor.setdefault(actor_id, {})
        current = observations.get(key)
        if current is not None:
            if question_id not in current.established_from_question_ids:
                current = replace(
                    current,
                    established_from_question_ids=(
                        *current.established_from_question_ids,
                        question_id,
                    ),
                )
                observations[key] = current
            return current
        self._sequence += 1
        current = NeighborhoodObservation(
            f"observation-{self._sequence}",
            address,
            direction,
            (question_id,),
        )
        observations[key] = current
        return current

    def for_actor(self, actor_id: str) -> tuple[NeighborhoodObservation, ...]:
        return tuple(
            sorted(self._by_actor.get(actor_id, {}).values(), key=_observation_key)
        )

    def observation(
        self, actor_id: str, observation_id: str
    ) -> NeighborhoodObservation:
        try:
            return next(
                item
                for item in self._by_actor.get(actor_id, {}).values()
                if item.observation_id == observation_id
            )
        except StopIteration as exc:
            raise KeyError(f"unknown Agent observation: {observation_id}") from exc

    def observers_for_reference(
        self,
        value: SemanticReference,
        *,
        exclude_actor_id: str = "",
    ) -> dict[str, tuple[NeighborhoodObservation, ...]]:
        source = SemanticHandle("object", value.source_object_id)
        target = (
            SemanticHandle("object", value.target_address.object_id)
            if isinstance(value.target_address, ObjectAddress)
            else SemanticHandle("reference", value.target_address.reference_id)
        )
        matches = {}
        for actor_id, observations in self._by_actor.items():
            if actor_id == exclude_actor_id:
                continue
            selected = tuple(
                sorted(
                    (
                        item
                        for item in observations.values()
                        if (item.direction == "outgoing" and item.address == source)
                        or (item.direction == "incoming" and item.address == target)
                    ),
                    key=_observation_key,
                )
            )
            if selected:
                matches[actor_id] = selected
        return matches

    def clear(self) -> None:
        self._by_actor.clear()
        self._sequence = 0


__all__ = ["NeighborhoodIndex"]
