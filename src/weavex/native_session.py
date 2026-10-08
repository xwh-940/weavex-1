"""Native dialogue and delivery cursors; never a second Local World authority."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace

from .dialogue import DialogueMessage
from .protocol import Observation


@dataclass
class NativeActorSession:
    dialogue: list[DialogueMessage] = field(default_factory=list)
    call_sequence: int = 0
    projected_act_ids: set[str] = field(default_factory=set)
    projected_objects: set[str] = field(default_factory=set)
    projected_references: set[str] = field(default_factory=set)
    projected_groundings: set[str] = field(default_factory=set)
    projected_actors: set[str] = field(default_factory=set)
    projected_contexts: set[str] = field(default_factory=set)
    observation_origins: dict[str, set[str]] = field(default_factory=dict)
    local_world_projected: bool = False

    def new_call_id(self, actor_id: str) -> str:
        self.call_sequence += 1
        return f"{actor_id}-call-{self.call_sequence}"

    @staticmethod
    def _new_items(items: list, key: str, seen: set[str]) -> list:
        result = [item for item in items if str(item[key]) not in seen]
        seen.update(str(item[key]) for item in result)
        return result

    def _observations(self, items: list) -> list:
        result = []
        for item in items:
            key = str(item["observation_id"])
            origins = item.get("established_from_question_ids", [])
            prior = self.observation_origins.get(key)
            if prior is None:
                result.append(item)
            else:
                added = [value for value in origins if value not in prior]
                if added:
                    result.append(
                        {"observation_id": key, "question_origins_added": added}
                    )
            self.observation_origins.setdefault(key, set()).update(origins)
        return result

    def _remember_change(self, change: dict) -> None:
        self.projected_objects.update(
            str(item["object_id"]) for item in change.get("objects_added", [])
        )
        self.projected_references.update(
            str(item["reference_id"]) for item in change.get("references_added", [])
        )

    def _remember_world_effect(self, value: dict) -> None:
        if "object_id" in value and "content" in value:
            self.projected_objects.add(str(value["object_id"]))
        if "reference_id" in value and "source_object_id" in value:
            self.projected_references.add(str(value["reference_id"]))
        self.projected_references.update(
            str(item["reference_id"]) for item in value.get("references", [])
        )
        self._remember_change(value.get("local_world_change", {}))
        if "observation" in value:
            self._observations([value["observation"]])
        change = value.get("observed_neighborhood_change", {})
        self._remember_change(change.get("local_world_change", {}))
        self._observations(change.get("observation_relations", []))
        self.projected_act_ids.update(
            str(item["act_id"])
            for item in change.get("answer_responsibilities_added", [])
        )

    def _local_world(self, value: dict) -> dict:
        value["projection"] = "delta" if self.local_world_projected else "initial"
        self.local_world_projected = True
        for name, key, seen in (
            ("objects", "object_id", self.projected_objects),
            ("references", "reference_id", self.projected_references),
            ("groundings", "grounding_id", self.projected_groundings),
            ("actors", "actor_id", self.projected_actors),
        ):
            if name in value:
                value[name] = self._new_items(value[name], key, seen)
        if "observed_neighborhoods" in value:
            value["observed_neighborhoods"] = self._observations(
                value["observed_neighborhoods"]
            )
        # Candidates and small context/open-responsibility facts are current state,
        # not lifetime membership. Their empty lists must explicitly remain visible.
        return value

    def project_observation(self, observation: Observation) -> Observation:
        if not observation.ok or not isinstance(observation.value, dict):
            return observation
        value = deepcopy(observation.value)
        if observation.source != "receive_input":
            if observation.source.startswith("world_"):
                self._remember_world_effect(value)
            return observation
        previously_seen = set(self.projected_act_ids)

        def act(item: dict) -> dict:
            identity = str(item.get("act_id", ""))
            if identity and "payload" in item:
                self.projected_act_ids.add(identity)
                if identity in previously_seen:
                    item.pop("payload")
                    item["previously_projected"] = True
            return item

        value["answer_responsibilities"] = [
            act(item) for item in value.get("answer_responsibilities", [])
        ]
        value["visible_acts"] = [
            act(item)
            for item in value.get("visible_acts", [])
            if str(item.get("act_id", "")) not in previously_seen
        ]
        arrival = value.get("arrival", {})
        if "act_id" in arrival:
            act(arrival)
        else:
            self._remember_change(arrival.get("local_world_change", {}))
            self._observations(arrival.get("observation_relations", []))
        if "local_world" in value:
            value["local_world"] = self._local_world(value["local_world"])
        if "external_contexts" in value:
            value["external_contexts"] = self._new_items(
                value["external_contexts"], "context_id", self.projected_contexts
            )
        return replace(observation, value=value)


__all__ = ["NativeActorSession"]
