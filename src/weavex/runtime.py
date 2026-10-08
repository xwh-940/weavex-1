"""In-memory Act scheduler, Actor state owner, and developer trace surface."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any, Mapping

from .actor import ActorRegistry, ActorSpec, ActorState
from .arrival import (
    ActorArrival,
    LocalWorldDelta,
    NeighborhoodObservation,
    arrival_id,
    arrival_recipient,
    arrival_thread_id,
    local_world_delta_data,
    neighborhood_observation_data,
)
from .capabilities import DEFAULT_AGENT_CAPABILITIES, ToolRegistry
from .communication import (
    inject_human as communicate_human,
    reply_from_actor as communicate_reply,
    reply_human as communicate_human_reply,
    send_from_actor as communicate_send,
)
from .coordination import (
    GROUP_FORMED_KIND,
    agent_context_cards,
    agent_context_ids,
    broker_member_ids,
    candidate_agents,
    coordination_world,
    group_is_formed,
)
from .cost_accounting import host_cost_summary
from .inspection import (
    act_data as project_act,
    arrival_data as project_arrival,
    actor_data as project_actor,
    actor_view as project_actor_view,
    runtime_snapshot,
)
from .inquiry import InquiryLog
from .grounding import (
    GroundingGraph,
    GroundingInput,
    record_acquisition_grounding,
    record_answer_grounding,
    retain_with_grounding,
    validate_grounding_inputs,
)
from .local_world import LocalWorlds
from .protocol import (
    Act,
    SemanticHandle,
    TraceEvent,
    protocol_addresses,
)
from .question_frontier import FrontierChange, QuestionFrontiers
from .execution import TurnDriver
from .situation import SituatedInput, answer_responsibility_data
from .turn_host import ActorTurnHost


class PrototypeRuntime:
    """Single-process semantic runtime; intentionally has no durable lifecycle."""

    def __init__(
        self,
        turn_driver: TurnDriver,
        tools: ToolRegistry,
        *,
        local_worlds: LocalWorlds | None = None,
        broker_id: str = "broker",
        agent_base_instructions: str = "",
    ) -> None:
        self.local_worlds = local_worlds or LocalWorlds()
        self.tools = tools
        self.turn_driver = turn_driver
        self.entry_broker_id = broker_id
        self.broker_id = broker_id
        self.actor_registry = ActorRegistry(
            agent_base_instructions=agent_base_instructions
        )
        self.inquiry = InquiryLog()
        self.groundings = GroundingGraph()
        self.question_frontiers = QuestionFrontiers()
        self.queue: deque[ActorArrival] = deque()
        self.traces: list[TraceEvent] = []
        self._trace_sequence = 0
        self._local_change_sequence = 0

    @property
    def actors(self) -> dict[str, ActorState]:
        return self.actor_registry.states

    @property
    def threads(self) -> dict[str, list[Act]]:
        return self.inquiry.threads

    def register_actor(
        self,
        spec: ActorSpec,
        *,
        initial_budget: Mapping[str, int] | None = None,
        creator: ActorState | None = None,
    ) -> ActorState:
        self.tools.catalog_for(spec.capabilities)
        return self.actor_registry.register(
            spec,
            initial_budget=initial_budget,
            creator=creator,
        )

    def freeze_initial_actors(self) -> None:
        self.actor_registry.freeze_initial()

    def create_agent(
        self,
        creator: ActorState,
        *,
        instructions: str,
        budget: Mapping[str, int],
        capabilities: frozenset[str] = DEFAULT_AGENT_CAPABILITIES,
        purpose: str = "",
    ) -> dict[str, object]:
        del purpose
        self.tools.catalog_for(capabilities)
        state = self.actor_registry.create_agent(
            creator,
            instructions=instructions,
            budget=budget,
            capabilities=capabilities,
        )
        actor_id = state.spec.actor_id
        self._record_trace(
            "actor.created",
            actor_id=actor_id,
            payload={
                "capabilities": sorted(capabilities),
                "budget": dict(state.initial_budget),
            },
        )
        return {
            "actor_id": actor_id,
            "question_delivered": False,
            "source_relation_created": False,
        }

    def create_broker(self) -> ActorState:
        template = self.actors[self.entry_broker_id].spec
        state = self.actor_registry.create_broker(template)
        self._record_trace(
            "actor.created",
            actor_id=state.spec.actor_id,
            payload={"role": "broker"},
        )
        return state

    def form_group(
        self,
        actor: ActorState,
        trigger: Act,
        member_ids: tuple[str, ...],
    ) -> dict[str, object]:
        if actor.spec.role != "broker":
            raise PermissionError("only a Broker may form a coordination group")
        if group_is_formed(self, actor.spec.actor_id):
            raise ValueError("this Broker group is already formed")
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("group member_ids cannot contain duplicates")
        candidates = {
            str(value["actor_id"])
            for value in candidate_agents(self, actor.spec.actor_id, trigger)
        }
        unknown = set(member_ids).difference(candidates)
        if unknown:
            raise ValueError(
                "group members must be current candidates: "
                + ", ".join(sorted(unknown))
            )
        act = self.inquiry.emit(
            thread_id=trigger.thread_id,
            kind=GROUP_FORMED_KIND,
            sender=actor.spec.actor_id,
            recipient=actor.spec.actor_id,
            payload={
                "source_actor_id": trigger.sender,
                "selected_existing_agents": list(member_ids),
            },
            reply_to=trigger.act_id,
        )
        self._record_trace(
            "act.created",
            actor_id=actor.spec.actor_id,
            thread_id=act.thread_id,
            act_id=act.act_id,
            payload=self.act_data(act),
        )
        return {
            "context_id": actor.spec.actor_id,
            "member_ids": list(member_ids),
        }

    def inject_human(
        self,
        content: str,
        *,
        recipient: str | None = None,
        thread_id: str | None = None,
        interpretation_content: str | None = None,
        semantic_addresses: tuple[SemanticHandle, ...] = (),
        intent: str | None = None,
        new_context: bool = False,
    ) -> Act:
        return communicate_human(
            self,
            content,
            recipient=recipient,
            thread_id=thread_id,
            interpretation_content=interpretation_content,
            semantic_addresses=semantic_addresses,
            intent=intent,
            new_context=new_context,
        )

    def send_from_actor(
        self,
        actor: ActorState,
        trigger: Act,
        *,
        recipient: str,
        kind: str,
        payload: Mapping[str, Any],
        reply_to: str = "",
        derived_from: tuple[str, ...] = (),
        context_id: str = "",
        new_context: bool = False,
        grounding_inputs: tuple[GroundingInput, ...] = (),
    ) -> Act:
        if kind.casefold() == "answer":
            validate_grounding_inputs(
                self.groundings,
                self.local_worlds,
                self.inquiry,
                actor,
                grounding_inputs,
            )
        emitted = communicate_send(
            self,
            actor,
            trigger,
            recipient=recipient,
            kind=kind,
            payload=payload,
            reply_to=reply_to,
            derived_from=derived_from,
            context_id=context_id,
            new_context=new_context,
        )
        if emitted.kind == "answer":
            record_answer_grounding(
                self.groundings,
                self.question_frontiers,
                actor,
                emitted,
                grounding_inputs,
            )
        return emitted

    def reply_human(
        self,
        *,
        thread_id: str,
        reply_to: str,
        content: str,
    ) -> Act:
        return communicate_human_reply(
            self,
            thread_id=thread_id,
            reply_to=reply_to,
            content=content,
        )

    def reply_from_actor(
        self,
        actor: ActorState,
        trigger: Act,
        *,
        kind: str,
        payload: Mapping[str, Any],
        reply_to: str = "",
        grounding_inputs: tuple[GroundingInput, ...] = (),
    ) -> Act:
        if kind.casefold() == "answer":
            validate_grounding_inputs(
                self.groundings,
                self.local_worlds,
                self.inquiry,
                actor,
                grounding_inputs,
            )
        emitted = communicate_reply(
            self,
            actor,
            trigger,
            kind=kind,
            payload=payload,
            reply_to=reply_to,
        )
        if emitted.kind == "answer":
            record_answer_grounding(
                self.groundings,
                self.question_frontiers,
                actor,
                emitted,
                grounding_inputs,
            )
        return emitted

    def answer_responsibilities(self, actor_id: str) -> tuple[Act, ...]:
        return self.question_frontiers.answer_responsibilities(
            self.inquiry,
            actor_id,
        )

    def answer_responsibility(self, actor_id: str, question_id: str) -> Act:
        return self.question_frontiers.answer_responsibility(
            self.inquiry,
            actor_id,
            question_id,
        )

    def retain_answer(
        self,
        actor_id: str,
        question_id: str,
        grounding_inputs: tuple[GroundingInput, ...] = (),
    ) -> dict[str, object]:
        actor = self.actors[actor_id]
        previous_grounding_id = self.latest_grounding_id(actor_id, question_id)
        self.question_frontiers.validate_retain(
            self.inquiry,
            actor_id,
            question_id,
        )
        validate_grounding_inputs(
            self.groundings,
            self.local_worlds,
            self.inquiry,
            actor,
            grounding_inputs,
        )
        state, node = retain_with_grounding(
            self.groundings,
            self.question_frontiers,
            self.inquiry,
            actor,
            question_id,
            grounding_inputs,
        )
        return {
            "question_id": question_id,
            "retained_answer_id": state.latest_answer_id,
            "judged_through_change_id": state.latest_judged_change_id,
            "previous_grounding_id": previous_grounding_id,
            "grounding_id": node.grounding_id,
            "previous_grounding_inherited": (
                bool(previous_grounding_id)
                and self.groundings.depends_on_grounding(
                    node.grounding_id,
                    previous_grounding_id,
                )
            ),
            "public_act_created": False,
        }

    def latest_grounding_id(self, actor_id: str, question_id: str) -> str:
        return self.question_frontiers.state(
            actor_id,
            question_id,
        ).latest_grounding_id

    def record_acquisition_grounding(
        self,
        actor: ActorState,
        question_id: str,
        observation_id: str,
        local_change: Mapping[str, object],
    ) -> str:
        return record_acquisition_grounding(
            self.groundings,
            self.local_worlds,
            self.inquiry,
            actor,
            question_id,
            observation_id,
            local_change,
        ).grounding_id

    async def step(self) -> ActorArrival | None:
        if not self.queue:
            return None
        arrival = self.queue.popleft()
        if isinstance(arrival, LocalWorldDelta):
            arrival = replace(
                arrival,
                local_world_change=self.local_worlds.include_reference_neighborhood(
                    arrival.recipient,
                    arrival.reference_id,
                ),
            )
            self._advance_neighborhood_frontiers(
                arrival.recipient,
                arrival.observation_relations,
                arrival.delta_id,
            )
        else:
            related_question = self.inquiry.direct_question_on_reply_chain(
                arrival.recipient,
                arrival,
            )
            if related_question is not None and related_question != arrival:
                self.question_frontiers.observe_act(
                    arrival.recipient,
                    related_question.act_id,
                    arrival,
                )
        actor = self.actors[arrival_recipient(arrival)]
        self.trace(
            (
                "act.handling"
                if isinstance(arrival, Act)
                else "local_world_delta.handling"
            ),
            actor=actor,
            arrival=arrival,
            payload={},
        )
        try:
            await self.turn_driver.activate(self.turn_host(actor, arrival))
        except Exception as exc:
            self.trace(
                "activation.failed",
                actor=actor,
                arrival=arrival,
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        return arrival

    async def run_until_idle(self, *, max_steps: int = 100) -> int:
        handled = 0
        while self.queue:
            if handled >= max_steps:
                raise RuntimeError("runtime exceeded the prototype step limit")
            await self.step()
            handled += 1
        return handled

    def available_capabilities(
        self, actor: ActorState, question_id: str | None = None
    ) -> frozenset[str]:
        names = actor.spec.capabilities
        if actor.spec.role == "broker":
            if not self.answer_responsibilities(actor.spec.actor_id):
                return frozenset()
            return (
                names - {"form_group"}
                if group_is_formed(self, actor.spec.actor_id)
                else names
            )
        if actor.spec.role != "agent":
            return names
        question_ids = (
            (question_id,)
            if question_id is not None
            else tuple(
                question.act_id
                for question in self.answer_responsibilities(actor.spec.actor_id)
            )
        )
        return (
            names
            if any(
                actor.question_budgets.get(value, actor.initial_budget).get(
                    "world_ops", 0
                )
                > 0
                for value in question_ids
            )
            else frozenset()
        )

    @staticmethod
    def available_capabilities_for_new_question(
        actor: ActorState,
    ) -> frozenset[str]:
        if actor.spec.role != "agent":
            return actor.spec.capabilities
        return (
            actor.spec.capabilities
            if actor.initial_budget.get("world_ops", 0) > 0
            else frozenset()
        )

    def build_situated_input(
        self,
        actor: ActorState,
        trigger: ActorArrival,
        *,
        input_received: bool = True,
    ) -> SituatedInput:
        visible_acts = self.inquiry.visible_acts_across_inquiries(actor.spec.actor_id)
        answer_responsibilities = self.answer_responsibilities(actor.spec.actor_id)
        if input_received and actor.spec.role == "agent":
            for act in visible_acts:
                self.local_worlds.include_addresses(
                    actor.spec.actor_id,
                    protocol_addresses(act.kind, act.payload),
                )
        local_world = (
            self.local_worlds.view_for(actor.spec.actor_id)
            if actor.spec.role == "agent"
            else self.coordination_local_world(
                actor,
                trigger if isinstance(trigger, Act) else None,
            )
        )
        if actor.spec.role == "broker":
            local_world = {
                **local_world,
                "open_question_responsibilities": list(
                    self.inquiry.open_question_responsibilities(
                        actor.spec.actor_id,
                    )
                ),
            }
        grounding_data = self.groundings.data_for_actor(actor.spec.actor_id)
        if grounding_data:
            local_world = {**local_world, "groundings": grounding_data}
        return SituatedInput(
            actor=actor.spec,
            trigger=trigger,
            visible_acts=visible_acts,
            local_world=local_world,
            external_contexts=(
                agent_context_cards(self, actor.spec.actor_id)
                if actor.spec.role == "agent"
                else ()
            ),
            answer_responsibilities=answer_responsibilities,
            responsibility_frontiers={
                question.act_id: self.question_frontiers.responsibility_data(
                    self.inquiry,
                    actor.spec.actor_id,
                    question.act_id,
                )
                for question in answer_responsibilities
            },
            question_budgets=(
                {
                    question.act_id: dict(actor.budget_for(question.act_id))
                    for question in answer_responsibilities
                }
                if actor.spec.role == "agent"
                else {}
            ),
        )

    def visible_acts(self, actor_id: str, thread_id: str) -> tuple[Act, ...]:
        return self.inquiry.visible_acts(actor_id, thread_id)

    def actor_descriptors(self) -> list[dict[str, object]]:
        return [
            {
                "actor_id": state.spec.actor_id,
                "role": state.spec.role,
                "capabilities": sorted(state.spec.capabilities),
                "created_by": state.spec.created_by,
                **(
                    {"initial_member_ids": list(state.spec.initial_member_ids)}
                    if state.spec.initial_member_ids is not None
                    else {}
                ),
            }
            for state in self.actors.values()
        ]

    def coordination_local_world(
        self,
        actor: ActorState | None = None,
        trigger: Act | None = None,
    ) -> dict[str, object]:
        if actor is not None:
            return coordination_world(self, actor.spec.actor_id, trigger)
        return {
            "domain": "coordination",
            "actors": self.actor_descriptors(),
        }

    def context_ids_for_agent(self, actor_id: str) -> tuple[str, ...]:
        return agent_context_ids(self, actor_id)

    def context_members(self, broker_id: str) -> frozenset[str]:
        return broker_member_ids(self, broker_id)

    def inquiry_topology(self, thread_id: str) -> dict[str, object]:
        return self.inquiry.topology(thread_id)

    def trace(
        self,
        kind: str,
        *,
        actor: ActorState,
        arrival: ActorArrival,
        payload: Mapping[str, Any],
    ) -> None:
        self._record_trace(
            kind,
            actor_id=actor.spec.actor_id,
            thread_id=arrival_thread_id(arrival),
            act_id=arrival_id(arrival),
            payload=payload,
        )

    def _advance_neighborhood_frontiers(
        self,
        actor_id: str,
        relations: tuple[NeighborhoodObservation, ...],
        change_id: str,
    ) -> tuple[str, ...]:
        dependent = self.groundings.dependent_roots(
            actor_id,
            self.question_frontiers.grounding_roots(actor_id),
            frozenset(item.observation_id for item in relations),
        )
        changed_question_ids = []
        for question_id, grounding_id in dependent.items():
            self.question_frontiers.observe_change(
                actor_id,
                question_id,
                FrontierChange(
                    change_id,
                    "semantic_neighborhood_change",
                    grounding_id,
                ),
            )
            changed_question_ids.append(question_id)
        return tuple(changed_question_ids)

    def _model_answer_responsibility(
        self,
        actor_id: str,
        question_id: str,
    ) -> dict[str, object]:
        question = self.inquiry.act(question_id)
        return answer_responsibility_data(
            question,
            self.question_frontiers.responsibility_data(
                self.inquiry,
                actor_id,
                question_id,
            ),
            role=self.actors[actor_id].spec.role,
            visible_ids=frozenset(
                act.act_id
                for act in self.inquiry.visible_acts_across_inquiries(actor_id)
            ),
        )

    def publish_reference_change(
        self,
        source_actor_id: str,
        reference_id: str,
        *,
        source_local_world_change: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        reference = self.local_worlds.world.get_reference(reference_id)
        observers = self.local_worlds.observers_for_reference(reference)
        source_effect: dict[str, object] = {}
        for actor_id, relations in observers.items():
            if actor_id not in self.actors:
                continue
            self._local_change_sequence += 1
            if actor_id == source_actor_id:
                change_id = f"local-world-change-{self._local_change_sequence}"
                changed_question_ids = self._advance_neighborhood_frontiers(
                    actor_id,
                    relations,
                    change_id,
                )
                source_effect = {
                    "change_id": change_id,
                    "kind": "semantic_neighborhood_change",
                    "reference_id": reference_id,
                    "local_world_change": dict(source_local_world_change or {}),
                    "observation_relations": [
                        neighborhood_observation_data(relation)
                        for relation in relations
                    ],
                    "answer_responsibilities_added": [
                        self._model_answer_responsibility(actor_id, question_id)
                        for question_id in changed_question_ids
                    ],
                }
                self._record_trace(
                    "local_world_change.applied",
                    actor_id=source_actor_id,
                    act_id=change_id,
                    payload=source_effect,
                )
                continue
            delta = LocalWorldDelta(
                f"local-world-delta-{self._local_change_sequence}",
                actor_id,
                reference_id,
                relations,
            )
            self.queue.append(delta)
            self._record_trace(
                "local_world_delta.created",
                actor_id=source_actor_id,
                act_id=delta.delta_id,
                payload=local_world_delta_data(delta, include_recipient=True),
            )
        return source_effect

    def _record_trace(
        self,
        kind: str,
        *,
        actor_id: str = "",
        thread_id: str = "",
        act_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._trace_sequence += 1
        self.traces.append(
            TraceEvent(
                self._trace_sequence,
                kind,
                actor_id,
                thread_id,
                act_id,
                dict(payload or {}),
            )
        )

    def actor_view(self, actor_id: str, thread_id: str) -> dict[str, object]:
        return project_actor_view(self, actor_id, thread_id)

    def snapshot(self) -> dict[str, object]:
        return runtime_snapshot(self)

    def host_costs(self) -> dict[str, object]:
        return host_cost_summary(self)

    def dialogue_for(self, actor_id: str) -> tuple[Mapping[str, Any], ...]:
        return self.turn_driver.dialogue_for(actor_id)

    def turn_host(self, actor: ActorState, trigger: ActorArrival) -> ActorTurnHost:
        return ActorTurnHost(self, actor, trigger, self.tools)

    def reset(self) -> None:
        self.actor_registry.reset()
        self.inquiry.reset()
        self.groundings.reset()
        self.question_frontiers.reset()
        self.queue.clear()
        self.traces.clear()
        self.local_worlds.reset()
        self.turn_driver.reset()
        self._trace_sequence = 0
        self._local_change_sequence = 0

    @staticmethod
    def act_data(act: Act) -> dict[str, object]:
        return project_act(act)

    @staticmethod
    def arrival_data(arrival: ActorArrival) -> dict[str, object]:
        return project_arrival(arrival)

    def actor_data(self, actor: ActorState) -> dict[str, object]:
        return project_actor(actor, self.dialogue_for(actor.spec.actor_id))
