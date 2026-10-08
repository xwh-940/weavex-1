"""One semantic execution loop shared without special cases by Agent and Broker."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .dialogue import (
    assistant_raw_tool_call_message,
    assistant_tool_call_message,
    decision_tool_proposal,
    tool_message,
    turn_message,
)
from .protocol import ModelToolCall, Observation
from .provider import (
    ModelPort,
    ModelToolRejection,
    NativeModelInput,
    model_situation_data,
)
from .capabilities import ToolRegistry
from .native_session import NativeActorSession
from .execution import ToolProposal
from .situation import SituatedInput

if TYPE_CHECKING:
    from .turn_host import ActorTurnHost


class NativeTurnDriver:
    """Run one dynamic inquiry loop for either cognitive domain.

    For an Agent this is the concrete World Understanding process; for a Broker
    the same loop develops a coordination Local World.
    """

    def __init__(
        self,
        model: ModelPort,
        tools: ToolRegistry,
        *,
        max_rounds: int = 12,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        self.model = model
        self.tools = tools
        self.max_rounds = max_rounds
        self._sessions: dict[str, NativeActorSession] = {}

    def _session_for(self, actor_id: str) -> NativeActorSession:
        if actor_id not in self._sessions:
            self._sessions[actor_id] = NativeActorSession()
        return self._sessions[actor_id]

    def dialogue_for(self, actor_id: str) -> tuple[dict[str, object], ...]:
        session = self._sessions.get(actor_id)
        if session is None:
            return ()
        return tuple(dict(message) for message in session.dialogue)

    def reset(self) -> None:
        reset_actor = getattr(self.model, "reset_actor", None)
        if reset_actor is not None:
            for actor_id in self._sessions:
                reset_actor(actor_id)
        self._sessions.clear()

    def model_input(
        self,
        host: "ActorTurnHost",
        observations: tuple[Observation, ...],
        capability_catalog: tuple[dict[str, object], ...],
        *,
        dialogue_includes_turn: bool = False,
        input_received: bool = True,
    ) -> NativeModelInput:
        session = self._session_for(host.actor_id)
        return NativeModelInput(
            host.situation(input_received=input_received),
            observations,
            capability_catalog,
            dialogue=tuple(dict(message) for message in session.dialogue),
            dialogue_includes_turn=dialogue_includes_turn,
            input_received=input_received,
        )

    @staticmethod
    def _rejection_question_id(
        situation: SituatedInput,
        arguments_json: str,
    ) -> str:
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError:
            return ""
        if not isinstance(arguments, dict):
            return ""
        question_id = arguments.get("question_id")
        return (
            question_id
            if isinstance(question_id, str)
            and question_id in situation.question_budgets
            else ""
        )

    def _trace_provider_usage(
        self,
        host: "ActorTurnHost",
        round_number: int,
    ) -> None:
        take_usage = getattr(self.model, "take_provider_usage", None)
        if take_usage is None:
            return
        usage = take_usage(host.actor_id, host.activation_id)
        if usage:
            host.trace(
                "provider.usage",
                {"round": round_number, **usage},
            )

    async def activate(
        self,
        host: "ActorTurnHost",
    ) -> None:
        try:
            await self._activate(host)
        finally:
            end_activation = getattr(self.model, "end_activation", None)
            if end_activation is not None:
                end_activation(host.actor_id, host.activation_id)

    async def _activate(
        self,
        host: "ActorTurnHost",
    ) -> None:
        observations: list[Observation] = []
        session = self._session_for(host.actor_id)
        session.dialogue.append(turn_message())
        for round_number in range(1, self.max_rounds + 1):
            capability_catalog = host.capability_catalog()
            model_input = self.model_input(
                host,
                tuple(observations),
                capability_catalog,
                dialogue_includes_turn=True,
                input_received=host.input_received,
            )
            host.trace(
                "model.input",
                {
                    "round": round_number,
                    **model_situation_data(model_input),
                },
            )
            fallback_question_id = host.default_question_id()
            try:
                decision = await self.model.decide(model_input)
            except ModelToolRejection as rejection:
                rejection_question_id = self._rejection_question_id(
                    host.situation(),
                    rejection.tool_call.arguments_json,
                )
                assistant, call_id = assistant_raw_tool_call_message(
                    rejection.tool_call,
                    fallback_call_id=session.new_call_id(host.actor_id),
                    content=rejection.assistant_content,
                )
                session.dialogue.append(assistant)
                observation = host.rejected_observation(
                    rejection.tool_call.name,
                    str(rejection),
                    question_id=(rejection_question_id if host.input_received else ""),
                )
                observations.append(observation)
                session.dialogue.append(tool_message(call_id, observation))
                host.trace(
                    "model.rejection",
                    {
                        "round": round_number,
                        "tool_name": rejection.tool_call.name,
                        "arguments_json": rejection.tool_call.arguments_json,
                        "category": rejection.category,
                        "reason": rejection.reason,
                        "assistant_content": rejection.assistant_content,
                    },
                )
                continue
            finally:
                self._trace_provider_usage(host, round_number)
            fallback_call_id = session.new_call_id(host.actor_id)
            proposal = (
                ToolProposal(
                    decision.call_id or fallback_call_id,
                    decision.name,
                    decision.arguments,
                    decision.assistant_content,
                )
                if isinstance(decision, ToolProposal)
                else decision_tool_proposal(
                    decision,
                    actor_role=host.actor_spec.role,
                    fallback_call_id=fallback_call_id,
                    fallback_reply_to=host.activation_id,
                    fallback_question_id=fallback_question_id,
                )
            )
            assistant, call_id = assistant_tool_call_message(
                ModelToolCall(
                    proposal.call_id,
                    proposal.name,
                    proposal.arguments,
                ),
                fallback_call_id=proposal.call_id,
                content=proposal.assistant_content,
            )
            session.dialogue.append(assistant)
            host.trace(
                "model.decision",
                {
                    "round": round_number,
                    "tool_name": proposal.name,
                    "arguments": dict(proposal.arguments),
                    "assistant_content": proposal.assistant_content,
                },
            )
            input_was_received = host.input_received
            execution = await host.execute(proposal)
            dialogue_observation = session.project_observation(execution.observation)
            session.dialogue.append(tool_message(call_id, dialogue_observation))
            if input_was_received and proposal.name not in {
                "receive_input",
                "yield_turn",
            }:
                observations.append(execution.observation)
            if execution.turn_ended:
                return
        raise RuntimeError(
            f"Actor {host.actor_id} exceeded {self.max_rounds} semantic rounds"
        )
