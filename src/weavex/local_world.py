"""One direct OSW-backed implementation of Agent-local semantic worlds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .arrival import NeighborhoodDirection, NeighborhoodObservation
from .neighborhood import NeighborhoodIndex

from .world import (
    ObjectAddress,
    ReferenceAddress,
    SemanticObject,
    SemanticReference,
    SemanticWorld,
)

from .protocol import SemanticHandle, handle_data


def object_data(value: SemanticObject) -> dict[str, str]:
    return {"object_id": value.object_id, "content": value.content}


def local_object_data(value: SemanticObject) -> dict[str, object]:
    return {
        **object_data(value),
        "semantic_address": handle_data(SemanticHandle("object", value.object_id)),
    }


def reference_data(value: SemanticReference) -> dict[str, object]:
    target = value.target_address
    return {
        "reference_id": value.reference_id,
        "source_object_id": value.source_object_id,
        "meaning": value.meaning,
        "semantic_address": handle_data(
            SemanticHandle("reference", value.reference_id)
        ),
        "target": (
            {"kind": "object", "id": target.object_id}
            if isinstance(target, ObjectAddress)
            else {"kind": "reference", "id": target.reference_id}
        ),
    }


def semantic_address(value: SemanticHandle) -> ObjectAddress | ReferenceAddress:
    if value.kind == "object":
        return ObjectAddress(value.semantic_id)
    return ReferenceAddress(value.semantic_id)


def local_world_change(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    known_objects = {str(item["object_id"]) for item in before.get("objects", [])}
    known_references = {
        str(item["reference_id"]) for item in before.get("references", [])
    }
    return {
        "objects_added": [
            item
            for item in after.get("objects", [])
            if str(item["object_id"]) not in known_objects
        ],
        "references_added": [
            item
            for item in after.get("references", [])
            if str(item["reference_id"]) not in known_references
        ],
    }


@dataclass
class LocalSemanticWorld:
    object_ids: set[str] = field(default_factory=set)
    reference_ids: set[str] = field(default_factory=set)


class LocalWorlds:
    """Own one SemanticWorld and each Agent lifetime's selected addresses."""

    def __init__(self, world: SemanticWorld | None = None) -> None:
        self.world = world or SemanticWorld()
        self._initial = self.world.snapshot()
        self._locals: dict[str, LocalSemanticWorld] = {}
        self._neighborhoods = NeighborhoodIndex()

    def local_for(self, actor_id: str) -> LocalSemanticWorld:
        if actor_id not in self._locals:
            self._locals[actor_id] = LocalSemanticWorld()
        return self._locals[actor_id]

    def include_addresses(
        self,
        actor_id: str,
        addresses: tuple[SemanticHandle, ...],
    ) -> None:
        local = self.local_for(actor_id)
        for address in addresses:
            self._materialize_address(local, address)

    def validate_addresses(
        self,
        actor_id: str,
        addresses: tuple[SemanticHandle, ...],
    ) -> None:
        local = self.local_for(actor_id)
        for address in addresses:
            known = (
                address.semantic_id in local.object_ids
                if address.kind == "object"
                else address.semantic_id in local.reference_ids
            )
            if not known:
                raise ValueError(
                    f"{address.kind} address is not in the Agent Local World: "
                    f"{address.semantic_id}"
                )

    def _include_object(self, local: LocalSemanticWorld, object_id: str) -> None:
        local.object_ids.add(self.world.get_object(object_id).object_id)

    def _include_reference(
        self,
        local: LocalSemanticWorld,
        reference_id: str,
    ) -> SemanticReference:
        value = self.world.get_reference(reference_id)
        local.reference_ids.add(value.reference_id)
        self._include_object(local, value.source_object_id)
        return value

    def _materialize_address(
        self,
        local: LocalSemanticWorld,
        handle: SemanticHandle,
    ) -> None:
        if handle.kind == "object":
            self._include_object(local, handle.semantic_id)
            return
        self._include_reference(local, handle.semantic_id)

    def _include_direct_target(
        self,
        local: LocalSemanticWorld,
        target: ObjectAddress | ReferenceAddress,
    ) -> None:
        if isinstance(target, ObjectAddress):
            self._include_object(local, target.object_id)
            return
        self._include_reference(local, target.reference_id)

    def references_from(
        self,
        actor_id: str,
        object_id: str,
        offset: int,
        limit: int,
    ) -> tuple[SemanticReference, ...]:
        self.validate_addresses(
            actor_id,
            (SemanticHandle("object", object_id),),
        )
        values = self.world.references_from(object_id)[offset : offset + limit]
        local = self.local_for(actor_id)
        for value in values:
            self._include_reference(local, value.reference_id)
            self._include_direct_target(local, value.target_address)
        return values

    def references_to(
        self,
        actor_id: str,
        address: SemanticHandle,
        offset: int,
        limit: int,
    ) -> tuple[SemanticReference, ...]:
        self.validate_addresses(actor_id, (address,))
        values = self.world.references_to(semantic_address(address))[
            offset : offset + limit
        ]
        local = self.local_for(actor_id)
        for value in values:
            self._include_reference(local, value.reference_id)
        return values

    def observe_neighborhood(
        self,
        actor_id: str,
        *,
        question_id: str,
        address: SemanticHandle,
        direction: NeighborhoodDirection,
    ) -> NeighborhoodObservation:
        self.validate_addresses(actor_id, (address,))
        return self._neighborhoods.observe(
            actor_id,
            question_id=question_id,
            address=address,
            direction=direction,
        )

    def observations_for(self, actor_id: str) -> tuple[NeighborhoodObservation, ...]:
        return self._neighborhoods.for_actor(actor_id)

    def observation(
        self, actor_id: str, observation_id: str
    ) -> NeighborhoodObservation:
        return self._neighborhoods.observation(actor_id, observation_id)

    def observers_for_reference(
        self,
        value: SemanticReference,
        *,
        exclude_actor_id: str = "",
    ) -> dict[str, tuple[NeighborhoodObservation, ...]]:
        return self._neighborhoods.observers_for_reference(
            value,
            exclude_actor_id=exclude_actor_id,
        )

    def include_reference_neighborhood(
        self,
        actor_id: str,
        reference_id: str,
    ) -> dict[str, object]:
        before = self.view_for(actor_id)
        local = self.local_for(actor_id)
        value = self._include_reference(local, reference_id)
        self._include_direct_target(local, value.target_address)
        return local_world_change(before, self.view_for(actor_id))

    def add_object(
        self,
        actor_id: str,
        content: str,
    ) -> dict[str, str]:
        value = self.world.add_object(content)
        self._include_object(self.local_for(actor_id), value.object_id)
        return object_data(value)

    def add_reference(
        self,
        actor_id: str,
        source_object_id: str,
        target: SemanticHandle,
        meaning: str = "",
    ) -> dict[str, object]:
        self.validate_addresses(
            actor_id,
            (SemanticHandle("object", source_object_id), target),
        )
        value = self.world.add_reference(
            source_object_id,
            semantic_address(target),
            meaning=meaning,
        )
        local = self.local_for(actor_id)
        self._include_reference(local, value.reference_id)
        self._include_direct_target(local, value.target_address)
        return reference_data(value)

    def view_for(self, actor_id: str) -> dict[str, object]:
        local = self.local_for(actor_id)
        snapshot = self.world.snapshot()
        value = {
            "domain": "semantic",
            "objects": [
                local_object_data(snapshot.get_object(value))
                for value in sorted(local.object_ids)
            ],
            "references": [
                reference_data(snapshot.get_reference(value))
                for value in sorted(local.reference_ids)
            ],
        }
        observations = self._neighborhoods.for_actor(actor_id)
        if observations:
            value["observed_neighborhoods"] = [
                {
                    "observation_id": item.observation_id,
                    "address": handle_data(item.address),
                    "direction": item.direction,
                    "established_from_question_ids": list(
                        item.established_from_question_ids
                    ),
                }
                for item in observations
            ]
        return value

    def snapshot_data(self) -> dict[str, object]:
        snapshot = self.world.snapshot()
        return {
            "objects": [object_data(value) for value in snapshot.objects.values()],
            "references": [
                reference_data(value) for value in snapshot.references.values()
            ],
        }

    def locals_data(self) -> list[dict[str, object]]:
        return [
            {
                "actor_id": actor_id,
                "world": self.view_for(actor_id),
            }
            for actor_id in sorted(self._locals)
        ]

    def handles_for_actor(self, actor_id: str) -> frozenset[SemanticHandle]:
        values: set[SemanticHandle] = set()
        for owner_id, local in self._locals.items():
            if owner_id != actor_id:
                continue
            values.update(SemanticHandle("object", value) for value in local.object_ids)
            values.update(
                SemanticHandle("reference", value) for value in local.reference_ids
            )
        return frozenset(values)

    def reset(self) -> None:
        world = SemanticWorld()
        for value in self._initial.objects.values():
            world.add_object(value.content, object_id=value.object_id)
        for value in self._initial.references.values():
            world.add_reference(
                value.source_object_id,
                value.target_address,
                meaning=value.meaning,
                reference_id=value.reference_id,
            )
        self.world = world
        self._locals.clear()
        self._neighborhoods.clear()
