"""Actor identity, runtime state, and one provider-facing activation view."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from .protocol import INITIALIZATION_SOURCE


def _text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


def validate_question_budget(budget: Mapping[str, int]) -> dict[str, int]:
    values = dict(budget)
    if set(values) - {"world_ops"}:
        raise ValueError("the prototype currently supports only world_ops budget")
    if not all(
        isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0
        for amount in values.values()
    ):
        raise ValueError("budget requires non-negative integer amounts")
    return values


def capability_boundary_data(budget: Mapping[str, int]) -> dict[str, object]:
    """Project Question-scoped capacity without exposing Runtime budgeting."""

    if "world_ops" not in budget:
        return {}
    return {
        "semantic_world_operations": {
            "remaining_capacity": int(budget["world_ops"]),
        }
    }


def capability_boundary_change(
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    question_id: str,
) -> dict[str, object]:
    if not before and not after:
        return {}
    before_world = dict(before.get("semantic_world_operations", {}))
    after_world = dict(after.get("semantic_world_operations", {}))
    return {
        "question_id": question_id,
        "semantic_world_operations": {
            "before": before_world.get("remaining_capacity", 0),
            "after": after_world.get("remaining_capacity", 0),
        },
    }


@dataclass(frozen=True)
class ActorSpec:
    """Persistent Actor identity and policy; Agent and Broker share this shape."""

    actor_id: str
    role: str
    instructions: str
    capabilities: frozenset[str] = frozenset()
    created_by: str = ""
    initial_member_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for label in ("actor_id", "role", "instructions"):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "created_by", self.created_by.strip())
        if self.actor_id in {"human", INITIALIZATION_SOURCE}:
            raise ValueError("external sources cannot be registered as Actors")
        if self.initial_member_ids is not None:
            if self.role != "broker":
                raise ValueError("only a Broker can declare initial group members")
            members = tuple(
                _text(value, "member_id") for value in self.initial_member_ids
            )
            if len(members) != len(set(members)):
                raise ValueError("initial group members cannot contain duplicates")
            object.__setattr__(self, "initial_member_ids", members)


@dataclass
class ActorState:
    """In-process Actor state with one budget account per independent Question."""

    spec: ActorSpec
    initial_budget: dict[str, int] = field(default_factory=dict)
    question_budgets: dict[str, dict[str, int]] = field(default_factory=dict)

    def budget_for(self, question_id: str) -> dict[str, int]:
        if question_id not in self.question_budgets:
            self.question_budgets[question_id] = dict(self.initial_budget)
        return self.question_budgets[question_id]

    def consume(self, question_id: str, budget: str) -> int:
        values = self.budget_for(question_id)
        remaining = values.get(budget, 0)
        if remaining < 1:
            raise RuntimeError(f"{budget} budget exhausted")
        values[budget] = remaining - 1
        return values[budget]


class ActorRegistry:
    """Own Actor identity, creation sequence, and resettable initial states."""

    def __init__(self, *, agent_base_instructions: str = "") -> None:
        self.agent_base_instructions = agent_base_instructions.strip()
        self.states: dict[str, ActorState] = {}
        self._agent_sequence = 0
        self._broker_sequence = 0
        self._initial: tuple[tuple[ActorSpec, dict[str, int]], ...] | None = None

    def register(
        self,
        spec: ActorSpec,
        *,
        initial_budget: Mapping[str, int] | None = None,
        creator: ActorState | None = None,
    ) -> ActorState:
        if (
            spec.created_by == INITIALIZATION_SOURCE
            or spec.initial_member_ids is not None
        ):
            raise ValueError("initial Actor facts require the initialization boundary")
        if spec.role == "agent" and (creator is None or creator.spec.role != "broker"):
            raise PermissionError("only a Broker may register an Agent")
        if spec.role == "agent" and not spec.created_by:
            spec = replace(spec, created_by=creator.spec.actor_id)
        return self._register(spec, initial_budget)

    def register_initial(
        self, spec: ActorSpec, *, initial_budget: Mapping[str, int]
    ) -> ActorState:
        if self._initial is not None:
            raise ValueError("initial Actor registration is already closed")
        if spec.created_by:
            raise ValueError("initial Actors cannot declare a prior creator")
        return self._register(
            replace(spec, created_by=INITIALIZATION_SOURCE), initial_budget
        )

    def _register(
        self, spec: ActorSpec, initial_budget: Mapping[str, int] | None
    ) -> ActorState:
        if spec.actor_id in self.states:
            raise ValueError(f"Actor already exists: {spec.actor_id}")
        state = ActorState(spec, initial_budget=dict(initial_budget or {}))
        self.states[spec.actor_id] = state
        self._sync_agent_sequence(spec.actor_id)
        self._sync_broker_sequence(spec.actor_id)
        return state

    def create_broker(self, template: ActorSpec) -> ActorState:
        if template.role != "broker":
            raise ValueError("dynamic Broker requires a Broker template")
        self._broker_sequence += 1
        spec = replace(
            template,
            actor_id=f"broker-{self._broker_sequence}",
            created_by="",
            initial_member_ids=None,
        )
        return self.register(spec)

    def create_agent(
        self,
        creator: ActorState,
        *,
        instructions: str,
        budget: Mapping[str, int],
        capabilities: frozenset[str],
    ) -> ActorState:
        if creator.spec.role != "broker":
            raise PermissionError("only a Broker may create an Agent")
        resolved_budget = validate_question_budget(budget)
        self._agent_sequence += 1
        spec = ActorSpec(
            actor_id=f"agent-{self._agent_sequence}",
            role="agent",
            instructions="\n\n".join(
                value
                for value in (self.agent_base_instructions, instructions.strip())
                if value
            ),
            capabilities=capabilities,
            created_by=creator.spec.actor_id,
        )
        return self.register(
            spec,
            initial_budget=resolved_budget,
            creator=creator,
        )

    def freeze_initial(self) -> None:
        self._initial = tuple(
            (state.spec, dict(state.initial_budget)) for state in self.states.values()
        )

    def reset(self) -> None:
        initial = (
            self._initial
            if self._initial is not None
            else tuple(
                (state.spec, dict(state.initial_budget))
                for state in self.states.values()
            )
        )
        self.states.clear()
        self._agent_sequence = 0
        self._broker_sequence = 0
        for spec, budget in initial:
            self.states[spec.actor_id] = ActorState(spec, initial_budget=dict(budget))
            self._sync_agent_sequence(spec.actor_id)
            self._sync_broker_sequence(spec.actor_id)

    def _sync_agent_sequence(self, actor_id: str) -> None:
        prefix = "agent-"
        if actor_id.startswith(prefix):
            suffix = actor_id.removeprefix(prefix)
            if suffix.isdigit():
                self._agent_sequence = max(self._agent_sequence, int(suffix))

    def _sync_broker_sequence(self, actor_id: str) -> None:
        prefix = "broker-"
        if actor_id.startswith(prefix):
            suffix = actor_id.removeprefix(prefix)
            if suffix.isdigit():
                self._broker_sequence = max(self._broker_sequence, int(suffix))


__all__ = ["ActorRegistry", "ActorSpec", "ActorState"]
