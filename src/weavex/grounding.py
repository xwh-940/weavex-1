"""Actor-private grounding DAG shared across persistent Question processes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping

from .actor import ActorState
from .protocol import Act, SemanticHandle

if TYPE_CHECKING:
    from .inquiry import InquiryLog
    from .local_world import LocalWorlds
    from .question_frontier import QuestionFrontiers, QuestionFrontierState


GroundingInputKind = Literal[
    "observation",
    "object",
    "reference",
    "grounding",
    "act",
]
GroundingNodeKind = Literal["acquisition", "answer", "retain"]


def _text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class GroundingInput:
    """One declared structural source of an Actor's current judgment."""

    kind: GroundingInputKind
    source_id: str

    def __post_init__(self) -> None:
        if self.kind not in {
            "observation",
            "object",
            "reference",
            "grounding",
            "act",
        }:
            raise ValueError(f"unknown grounding input kind: {self.kind}")
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id"))


def grounding_input_data(value: GroundingInput) -> dict[str, str]:
    return {"kind": value.kind, "id": value.source_id}


@dataclass(frozen=True, slots=True)
class GroundingNode:
    """One immutable private basis chosen with an Answer or retain judgment."""

    grounding_id: str
    actor_id: str
    question_id: str
    node_kind: GroundingNodeKind
    public_answer_id: str
    inputs: tuple[GroundingInput, ...]


def grounding_node_data(value: GroundingNode) -> dict[str, object]:
    return {
        "grounding_id": value.grounding_id,
        "formed_for_question_id": value.question_id,
        "node_kind": value.node_kind,
        "public_answer_id": value.public_answer_id,
        "inputs": [grounding_input_data(item) for item in value.inputs],
    }


class GroundingGraph:
    """Maintain declared dependencies without judging truth or sufficiency."""

    def __init__(self) -> None:
        self._nodes: dict[str, GroundingNode] = {}
        self._sequence = 0

    def node(self, grounding_id: str) -> GroundingNode:
        try:
            return self._nodes[grounding_id]
        except KeyError as exc:
            raise KeyError(f"unknown grounding: {grounding_id}") from exc

    def validate_references(
        self,
        actor_id: str,
        inputs: tuple[GroundingInput, ...],
    ) -> None:
        if len(inputs) != len(set(inputs)):
            raise ValueError("grounding_inputs cannot contain duplicates")
        for item in inputs:
            if item.kind != "grounding":
                continue
            source = self.node(item.source_id)
            if source.actor_id != actor_id:
                raise PermissionError(
                    "an Actor may only reuse its own private grounding"
                )

    def create(
        self,
        *,
        actor_id: str,
        question_id: str,
        node_kind: GroundingNodeKind,
        public_answer_id: str,
        inputs: tuple[GroundingInput, ...],
    ) -> GroundingNode:
        self.validate_references(actor_id, inputs)
        self._sequence += 1
        node = GroundingNode(
            f"grounding-{self._sequence}",
            actor_id,
            question_id,
            node_kind,
            public_answer_id,
            inputs,
        )
        self._nodes[node.grounding_id] = node
        return node

    def ids_for_actor(self, actor_id: str) -> frozenset[str]:
        return frozenset(
            node.grounding_id
            for node in self._nodes.values()
            if node.actor_id == actor_id
        )

    def data_for_actor(self, actor_id: str) -> list[dict[str, object]]:
        return [
            grounding_node_data(node)
            for node in self._nodes.values()
            if node.actor_id == actor_id
        ]

    def depends_on_observation(
        self,
        grounding_id: str,
        observation_ids: frozenset[str],
    ) -> bool:
        pending = [grounding_id]
        visited = set()
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                continue
            visited.add(current_id)
            current = self.node(current_id)
            for item in current.inputs:
                if item.kind == "observation" and item.source_id in observation_ids:
                    return True
                if item.kind == "grounding":
                    pending.append(item.source_id)
        return False

    def depends_on_grounding(
        self,
        grounding_id: str,
        source_grounding_id: str,
    ) -> bool:
        """Whether one grounding directly or transitively inherits another."""

        pending = [grounding_id]
        visited = set()
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                continue
            visited.add(current_id)
            for item in self.node(current_id).inputs:
                if item.kind != "grounding":
                    continue
                if item.source_id == source_grounding_id:
                    return True
                pending.append(item.source_id)
        return False

    def dependent_roots(
        self,
        actor_id: str,
        roots: Mapping[str, str],
        observation_ids: frozenset[str],
    ) -> dict[str, str]:
        return {
            question_id: grounding_id
            for question_id, grounding_id in roots.items()
            if self.node(grounding_id).actor_id == actor_id
            and self.depends_on_observation(grounding_id, observation_ids)
        }

    def reset(self) -> None:
        self._nodes.clear()
        self._sequence = 0


def validate_grounding_inputs(
    graph: GroundingGraph,
    local_worlds: "LocalWorlds",
    inquiry: "InquiryLog",
    actor: ActorState,
    inputs: tuple[GroundingInput, ...],
) -> None:
    """Validate only structural ownership and current Actor visibility."""

    actor_id = actor.spec.actor_id
    graph.validate_references(actor_id, inputs)
    local_handles = local_worlds.handles_for_actor(actor_id)
    visible_act_ids = {
        act.act_id for act in inquiry.visible_acts_across_inquiries(actor_id)
    }
    for item in inputs:
        if item.kind == "grounding":
            continue
        if item.kind == "observation":
            if actor.spec.role != "agent":
                raise PermissionError(
                    "only an Agent may ground in a Semantic observation"
                )
            try:
                local_worlds.observation(actor_id, item.source_id)
            except KeyError as exc:
                raise ValueError(
                    "grounding observation is not in this Agent Local World"
                ) from exc
            continue
        if item.kind in {"object", "reference"}:
            handle = SemanticHandle(item.kind, item.source_id)
            if actor.spec.role != "agent" or handle not in local_handles:
                raise ValueError(
                    "grounding Semantic Address is not in this Agent Local World"
                )
            continue
        if item.kind == "act" and item.source_id not in visible_act_ids:
            raise ValueError("grounding Act is not visible to this Actor")


def record_answer_grounding(
    graph: GroundingGraph,
    frontiers: "QuestionFrontiers",
    actor: ActorState,
    answer: Act,
    inputs: tuple[GroundingInput, ...],
) -> GroundingNode:
    question_id = str(answer.reply_to)
    node = graph.create(
        actor_id=actor.spec.actor_id,
        question_id=question_id,
        node_kind="answer",
        public_answer_id=answer.act_id,
        inputs=inputs,
    )
    frontiers.record_answer(
        actor.spec.actor_id,
        question_id,
        answer.act_id,
        node.grounding_id,
    )
    return node


def record_acquisition_grounding(
    graph: GroundingGraph,
    local_worlds: "LocalWorlds",
    inquiry: "InquiryLog",
    actor: ActorState,
    question_id: str,
    observation_id: str,
    local_change: Mapping[str, object],
) -> GroundingNode:
    inputs = [GroundingInput("observation", observation_id)]
    inputs.extend(
        GroundingInput("object", str(item["object_id"]))
        for item in local_change.get("objects_added", [])
    )
    inputs.extend(
        GroundingInput("reference", str(item["reference_id"]))
        for item in local_change.get("references_added", [])
    )
    resolved = tuple(dict.fromkeys(inputs))
    validate_grounding_inputs(graph, local_worlds, inquiry, actor, resolved)
    return graph.create(
        actor_id=actor.spec.actor_id,
        question_id=question_id,
        node_kind="acquisition",
        public_answer_id="",
        inputs=resolved,
    )


def retain_with_grounding(
    graph: GroundingGraph,
    frontiers: "QuestionFrontiers",
    inquiry: "InquiryLog",
    actor: ActorState,
    question_id: str,
    inputs: tuple[GroundingInput, ...],
) -> tuple["QuestionFrontierState", GroundingNode]:
    answer_id = frontiers.validate_retain(
        inquiry,
        actor.spec.actor_id,
        question_id,
    )
    node = graph.create(
        actor_id=actor.spec.actor_id,
        question_id=question_id,
        node_kind="retain",
        public_answer_id=answer_id,
        inputs=inputs,
    )
    state = frontiers.retain_answer(
        inquiry,
        actor.spec.actor_id,
        question_id,
        node.grounding_id,
    )
    return state, node


__all__ = [
    "GroundingGraph",
    "GroundingInput",
    "GroundingInputKind",
    "GroundingNode",
    "GroundingNodeKind",
    "grounding_input_data",
    "grounding_node_data",
    "record_acquisition_grounding",
    "record_answer_grounding",
    "retain_with_grounding",
    "validate_grounding_inputs",
]
