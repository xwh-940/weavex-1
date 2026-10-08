"""In-memory debug construction and immutable observation snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, TypeAlias
from uuid import uuid4


class StructuralError(ValueError):
    """A value would violate the stable, non-semantic grammar."""


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StructuralError(f"{label} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SemanticObject:
    object_id: str
    content: str

    def __post_init__(self) -> None:
        _require_id(self.object_id, "object id")
        if not isinstance(self.content, str):
            raise StructuralError("object content must be a string")


@dataclass(frozen=True, slots=True)
class ObjectAddress:
    object_id: str

    def __post_init__(self) -> None:
        _require_id(self.object_id, "object address")


@dataclass(frozen=True, slots=True)
class ReferenceAddress:
    reference_id: str

    def __post_init__(self) -> None:
        _require_id(self.reference_id, "reference address")


SemanticAddress: TypeAlias = ObjectAddress | ReferenceAddress


@dataclass(frozen=True, slots=True)
class SemanticReference:
    reference_id: str
    source_object_id: str
    target_address: SemanticAddress
    meaning: str = ""

    def __post_init__(self) -> None:
        _require_id(self.reference_id, "reference id")
        _require_id(self.source_object_id, "reference source object id")
        if not isinstance(self.target_address, (ObjectAddress, ReferenceAddress)):
            raise StructuralError("reference target must be a semantic address")
        if not isinstance(self.meaning, str):
            raise StructuralError("reference meaning must be a string")
        if (
            isinstance(self.target_address, ReferenceAddress)
            and self.target_address.reference_id == self.reference_id
        ):
            raise StructuralError("reference cannot target itself")


def _frozen_copy(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    """A deeply immutable copy of one SemanticWorld observation boundary."""

    objects: Mapping[str, SemanticObject]
    references: Mapping[str, SemanticReference]

    def __post_init__(self) -> None:
        object.__setattr__(self, "objects", _frozen_copy(self.objects))
        object.__setattr__(self, "references", _frozen_copy(self.references))
        _validate_state(self.objects, self.references)

    def get_object(self, object_id: str) -> SemanticObject:
        try:
            return self.objects[object_id]
        except KeyError as exc:
            raise StructuralError(
                f"semantic object does not exist: {object_id}"
            ) from exc

    def get_reference(self, reference_id: str) -> SemanticReference:
        try:
            return self.references[reference_id]
        except KeyError as exc:
            raise StructuralError(
                f"semantic reference does not exist: {reference_id}"
            ) from exc

    def references_from(self, object_id: str) -> tuple[SemanticReference, ...]:
        self.get_object(object_id)
        return _references_from(self.references, object_id)

    def references_to(self, address: SemanticAddress) -> tuple[SemanticReference, ...]:
        _require_target(self.objects, self.references, address)
        return _references_to(self.references, address)


class SemanticWorld:
    """A direct, provisional construction surface for semantic experiments."""

    def __init__(self) -> None:
        self._objects: dict[str, SemanticObject] = {}
        self._references: dict[str, SemanticReference] = {}

    def add_object(
        self,
        content: str,
        *,
        object_id: str | None = None,
    ) -> SemanticObject:
        value = SemanticObject(object_id or _new_id("object"), content)
        if value.object_id in self._objects:
            raise StructuralError(f"semantic object already exists: {value.object_id}")
        self._objects[value.object_id] = value
        return value

    def add_reference(
        self,
        source_object_id: str,
        target_address: SemanticAddress,
        *,
        meaning: str = "",
        reference_id: str | None = None,
    ) -> SemanticReference:
        value = SemanticReference(
            reference_id or _new_id("reference"),
            source_object_id,
            target_address,
            meaning,
        )
        if value.reference_id in self._references:
            raise StructuralError(
                f"semantic reference already exists: {value.reference_id}"
            )
        if value.source_object_id not in self._objects:
            raise StructuralError(
                f"reference source object does not exist: {value.source_object_id}"
            )
        _require_target(self._objects, self._references, value.target_address)
        self._references[value.reference_id] = value
        return value

    def get_object(self, object_id: str) -> SemanticObject:
        return self.snapshot().get_object(object_id)

    def get_reference(self, reference_id: str) -> SemanticReference:
        return self.snapshot().get_reference(reference_id)

    def references_from(self, object_id: str) -> tuple[SemanticReference, ...]:
        if object_id not in self._objects:
            raise StructuralError(f"semantic object does not exist: {object_id}")
        return _references_from(self._references, object_id)

    def references_to(self, address: SemanticAddress) -> tuple[SemanticReference, ...]:
        _require_target(self._objects, self._references, address)
        return _references_to(self._references, address)

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(self._objects, self._references)


def _new_id(kind: str) -> str:
    return f"{kind}:{uuid4()}"


def _require_target(
    objects: Mapping[str, SemanticObject],
    references: Mapping[str, SemanticReference],
    address: SemanticAddress,
) -> None:
    if isinstance(address, ObjectAddress):
        if address.object_id not in objects:
            raise StructuralError(
                f"reference target object does not exist: {address.object_id}"
            )
        return
    if isinstance(address, ReferenceAddress):
        if address.reference_id not in references:
            raise StructuralError(
                f"reference target reference does not exist: {address.reference_id}"
            )
        return
    raise StructuralError("reference target must be a semantic address")


def _validate_state(
    objects: Mapping[str, SemanticObject],
    references: Mapping[str, SemanticReference],
) -> None:
    for key, value in objects.items():
        if not isinstance(value, SemanticObject) or key != value.object_id:
            raise StructuralError("world object keys must match SemanticObject ids")
    for key, value in references.items():
        if not isinstance(value, SemanticReference) or key != value.reference_id:
            raise StructuralError(
                "world reference keys must match SemanticReference ids"
            )
        if value.source_object_id not in objects:
            raise StructuralError(
                f"reference source object does not exist: {value.source_object_id}"
            )
        _require_target(objects, references, value.target_address)


def _references_from(
    references: Mapping[str, SemanticReference],
    object_id: str,
) -> tuple[SemanticReference, ...]:
    return tuple(
        sorted(
            (
                value
                for value in references.values()
                if value.source_object_id == object_id
            ),
            key=lambda value: value.reference_id,
        )
    )


def _references_to(
    references: Mapping[str, SemanticReference],
    address: SemanticAddress,
) -> tuple[SemanticReference, ...]:
    return tuple(
        sorted(
            (value for value in references.values() if value.target_address == address),
            key=lambda value: value.reference_id,
        )
    )


__all__ = [
    "ObjectAddress",
    "ReferenceAddress",
    "SemanticAddress",
    "SemanticObject",
    "SemanticReference",
    "SemanticWorld",
    "StructuralError",
    "WorldSnapshot",
]
