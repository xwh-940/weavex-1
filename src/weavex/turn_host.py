"""Activation-scoped semantic host offered to interchangeable TurnDrivers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .actor import (
    ActorSpec,
    ActorState,
    capability_boundary_change,
    capability_boundary_data,
)
from .arrival import ActorArrival, arrival_id
from .capabilities import ToolRegistry
from .execution import ToolExecution, ToolProposal
from .protocol import Act, Observation
from .situation import SituatedInput, situated_input_data
from .tool_schema import actor_tool_schemas, validate_arguments
from .tool_surface import HostToolDispatcher

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


class ActorTurnHost:
    """Expose situated input and host effects without transferring authority."""

    def __init__(
        self,
        runtime: "PrototypeRuntime",
        actor: ActorState,
        trigger: ActorArrival,
        tools: ToolRegistry,
    ) -> None:
        self._runtime = runtime
        self._actor = actor
        self._trigger = trigger
        self._tools = tools
        self._dispatcher = HostToolDispatcher(tools)
        self.input_received = False
        self.turn_ended = False

    @property
    def actor_id(self) -> str:
        return self._actor.spec.actor_id

    @property
    def actor_spec(self) -> ActorSpec:
        return self._actor.spec

    @property
    def activation_id(self) -> str:
        return arrival_id(self._trigger)

    @property
    def activation_trigger(self) -> ActorArrival:
        return self._trigger

    def situation(self, *, input_received: bool | None = None) -> SituatedInput:
        received = self.input_received if input_received is None else input_received
        return self._runtime.build_situated_input(
            self._actor,
            self._trigger,
            input_received=received,
        )

    def capability_catalog(self) -> tuple[dict[str, object], ...]:
        return self._tools.catalog_for(self.actor_spec.capabilities)

    def tool_schemas(self) -> list[dict[str, object]]:
        return actor_tool_schemas(
            self.actor_spec,
            self.capability_catalog(),
        )

    def default_question_id(self) -> str:
        if not isinstance(self._trigger, Act):
            return ""
        return self._runtime.inquiry.question_for_trigger(self._trigger).act_id

    def question_id_for(self, proposal: ToolProposal) -> str:
        return self._dispatcher.question_id_for(
            self._runtime,
            self._actor,
            proposal,
        )

    def _boundary(self, question_id: str) -> dict[str, object]:
        if (
            self._actor.spec.role != "agent"
            or not question_id
            or question_id not in self._actor.question_budgets
        ):
            return {}
        return capability_boundary_data(self._actor.question_budgets[question_id])

    def _observation(
        self,
        source: str,
        ok: bool,
        *,
        question_id: str,
        before: Mapping[str, object],
        value: object = None,
        error: str = "",
    ) -> Observation:
        return Observation(
            source,
            ok,
            value=value,
            error=error,
            capability_boundary_change=capability_boundary_change(
                before,
                self._boundary(question_id),
                question_id=question_id,
            ),
        )

    @staticmethod
    def observation_data(observation: Observation) -> dict[str, object]:
        payload: dict[str, object] = {
            "source": observation.source,
            "ok": observation.ok,
            "value": observation.value,
            "error": observation.error,
        }
        if observation.capability_boundary_change:
            payload["capability_boundary_change"] = dict(
                observation.capability_boundary_change
            )
        return payload

    def trace(self, kind: str, payload: Mapping[str, Any]) -> None:
        self._runtime.trace(
            kind,
            actor=self._actor,
            arrival=self._trigger,
            payload=payload,
        )

    def rejected_observation(
        self,
        source: str,
        error: str,
        *,
        question_id: str = "",
    ) -> Observation:
        observation = self._observation(
            source,
            False,
            question_id=question_id,
            before=self._boundary(question_id),
            error=error,
        )
        self.trace("tool.observation", self.observation_data(observation))
        return observation

    async def execute(self, proposal: ToolProposal) -> ToolExecution:
        try:
            if self.turn_ended:
                raise ValueError("this Actor turn has already ended")
            offered = {
                item["function"]["name"]: item["function"]
                for item in self.tool_schemas()
            }
            if proposal.name not in offered:
                raise PermissionError("tool is not configured for this Actor")
            validate_arguments(
                dict(proposal.arguments), offered[proposal.name]["parameters"]
            )
        except (ValueError, PermissionError) as exc:
            return ToolExecution(
                self.rejected_observation(
                    proposal.name,
                    f"{type(exc).__name__}: {exc}",
                    question_id=self.question_id_for(proposal)
                    if self.input_received
                    else "",
                )
            )
        return await self._execute(proposal)

    async def _execute(self, proposal: ToolProposal) -> ToolExecution:
        if not self.input_received and proposal.name != "receive_input":
            observation = Observation(
                proposal.name,
                False,
                error=(
                    "this Actor turn has not received its situated input; "
                    "only receive_input is currently available"
                ),
            )
            self.trace("tool.observation", self.observation_data(observation))
            return ToolExecution(observation)
        if proposal.name == "receive_input":
            if self.input_received:
                observation = Observation(
                    proposal.name,
                    False,
                    error=(
                        "receive_input is only available before this turn's input "
                        "is received"
                    ),
                )
            else:
                self.input_received = True
                observation = Observation(
                    proposal.name,
                    True,
                    value=situated_input_data(self.situation()),
                )
            self.trace("tool.observation", self.observation_data(observation))
            return ToolExecution(observation)
        if proposal.name == "yield_turn":
            self.turn_ended = True
            observation = Observation(
                proposal.name,
                True,
                value={"turn_ended": True},
            )
            self.trace("tool.observation", self.observation_data(observation))
            return ToolExecution(observation, turn_ended=True)
        question_id = self.question_id_for(proposal)
        before = self._boundary(question_id)
        source = self._dispatcher.observation_source(self._trigger, proposal)
        try:
            effect = await self._dispatcher.execute(
                self._runtime,
                self._actor,
                self._trigger,
                self.situation(),
                proposal,
            )
            observation = self._observation(
                effect.source,
                True,
                question_id=question_id,
                before=before,
                value=effect.value,
            )
        except Exception as exc:
            observation = self._observation(
                source,
                False,
                question_id=question_id,
                before=before,
                error=f"{type(exc).__name__}: {exc}",
            )
        self.trace(
            (
                "act.observation"
                if observation.source in {"send_act", "reply_act"}
                else "tool.observation"
            ),
            self.observation_data(observation),
        )
        return ToolExecution(observation)


__all__ = ["ActorTurnHost"]
