"""One-shot initial conditions; subsequent activity belongs to the normal Runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from .actor import ActorSpec, validate_question_budget
from .capabilities import DEFAULT_AGENT_CAPABILITIES, DEFAULT_BROKER_CAPABILITIES
from .communication import emit_act
from .protocol import INITIALIZATION_SOURCE, Act, protocol_addresses

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


@dataclass(frozen=True)
class InitialActor:
    """A declaration consumed once, not an additional runtime task or state table."""

    spec: ActorSpec
    question: Mapping[str, Any] | None = None
    budget: Mapping[str, int] = field(default_factory=dict)


def initialize_runtime(
    runtime: "PrototypeRuntime", declarations: tuple[InitialActor, ...]
) -> tuple[Act, ...]:
    """Install declared Actors/groups and ordinary roots without model decisions.

    All declarations are checked before installation. Questions stay exclusively in
    InquiryLog; no templates, replay queue, or auto-renewal state is retained.
    """

    if runtime.actors or runtime.threads or runtime.queue or runtime.traces:
        raise ValueError("initialization requires a fresh Runtime")
    by_id = {item.spec.actor_id: item for item in declarations}
    if not by_id or len(by_id) != len(declarations):
        raise ValueError("initial Actors require unique identities")
    entry = by_id.get(runtime.entry_broker_id)
    if entry is None or entry.spec.role != "broker":
        raise ValueError("the Human entry must identify an initial Broker")
    world = runtime.local_worlds.world.snapshot()
    prepared = []
    for item in declarations:
        spec = item.spec
        if spec.role not in {"agent", "broker"} or spec.created_by:
            raise ValueError(
                "initial Actors require Agent/Broker roles and no prior creator"
            )
        allowed = (
            DEFAULT_AGENT_CAPABILITIES
            if spec.role == "agent"
            else DEFAULT_BROKER_CAPABILITIES
        )
        if not spec.capabilities <= allowed:
            raise PermissionError(
                "initial capabilities must respect the cognitive domain"
            )
        runtime.tools.catalog_for(spec.capabilities)
        budget = validate_question_budget(item.budget)
        if spec.role == "broker":
            if budget:
                raise ValueError("a Broker cannot receive a world operation budget")
            if spec.initial_member_ids is None:
                raise ValueError(
                    "initial Brokers must explicitly declare their members"
                )
            for member_id in spec.initial_member_ids:
                member = by_id.get(member_id)
                if member is None or member.spec.role != "agent":
                    raise ValueError(
                        "initial group members must identify initial Agents"
                    )
        payload = None if item.question is None else deepcopy(dict(item.question))
        if payload is not None:
            question = Act(
                "initial-condition",
                "initial-condition",
                "question",
                INITIALIZATION_SOURCE,
                spec.actor_id,
                payload,
            )
            for handle in protocol_addresses(question.kind, question.payload):
                if handle.kind == "object":
                    world.get_object(handle.semantic_id)
                else:
                    world.get_reference(handle.semantic_id)
        prepared.append((spec, budget, payload))

    for spec, budget, _ in prepared:
        actor = runtime.actor_registry.register_initial(spec, initial_budget=budget)
        runtime._record_trace(
            "actor.initialized",
            actor_id=spec.actor_id,
            payload=runtime.actor_data(actor),
        )
    runtime.freeze_initial_actors()
    return tuple(
        emit_act(
            runtime,
            thread_id=runtime.inquiry.new_thread_id(),
            kind="question",
            sender=INITIALIZATION_SOURCE,
            recipient=spec.actor_id,
            payload=payload,
        )
        for spec, _, payload in prepared
        if payload is not None
    )


__all__ = ["InitialActor", "initialize_runtime"]
