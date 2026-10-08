"""Provider boundary and deterministic model-input rendering for the prototype."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .arrival import arrival_id
from .execution import ToolProposal
from .prompts import COMMON_INSTRUCTIONS
from .protocol import Decision, ModelToolCallEnvelope, Observation
from .provider_usage import response_usage_data
from .situation import SituatedInput, model_data, situated_input_data
from .tool_schema import actor_tool_schemas


@dataclass(frozen=True)
class NativeModelInput:
    """Native model input composed around a backend-neutral situation."""

    situation: SituatedInput
    observations: tuple[Observation, ...]
    capability_catalog: tuple[Mapping[str, Any], ...]
    dialogue: tuple[Mapping[str, Any], ...] = ()
    dialogue_includes_turn: bool = False
    input_received: bool = True

    @property
    def actor(self):
        return self.situation.actor

    @property
    def trigger(self):
        return self.situation.trigger

    @property
    def visible_acts(self):
        return self.situation.visible_acts

    @property
    def local_world(self):
        return self.situation.local_world

    @property
    def external_contexts(self):
        return self.situation.external_contexts

    @property
    def answer_responsibilities(self):
        return self.situation.answer_responsibilities

    @property
    def question_budgets(self):
        return self.situation.question_budgets

    @property
    def activation_id(self) -> str:
        return arrival_id(self.situation.trigger)


class ModelPort(Protocol):
    """The only boundary allowed to perform semantic inference."""

    async def decide(self, value: NativeModelInput) -> Decision | ToolProposal: ...


Script = Callable[
    [NativeModelInput],
    Decision | ToolProposal | Awaitable[Decision | ToolProposal],
]


class ScriptedModel:
    """Deterministic semantic fake used by tests and executable scenarios."""

    def __init__(self, script: Script, *, auto_yield: bool = True) -> None:
        self.script = script
        self.auto_yield = auto_yield
        self.calls: list[NativeModelInput] = []

    async def decide(self, value: NativeModelInput) -> Decision | ToolProposal:
        if not value.input_received:
            return Decision("call", name="receive_input")
        if self.auto_yield and value.dialogue:
            last_message = value.dialogue[-1]
            if last_message.get("role") == "tool":
                last_result = json.loads(str(last_message.get("content", "{}")))
                if last_result.get("ok") is True and last_result.get("source") in {
                    "send_act",
                    "reply_act",
                }:
                    return Decision("call", name="yield_turn")
        self.calls.append(value)
        result = self.script(value)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, (Decision, ToolProposal)):
            raise TypeError("scripted model must return Decision or ToolProposal")
        return result


class UnavailableModel:
    """Explicit debug-server behavior when no real provider is configured."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def decide(self, value: NativeModelInput) -> Decision | ToolProposal:
        del value
        raise RuntimeError(self.reason)


class ModelToolRejection(ValueError):
    """A replayable model tool proposal the Actor can correct in-place."""

    def __init__(
        self,
        tool_call: ModelToolCallEnvelope,
        *,
        category: str,
        reason: str,
        assistant_content: str = "",
    ) -> None:
        self.tool_call = tool_call
        self.category = category.strip()
        self.reason = reason.strip()
        self.assistant_content = assistant_content
        super().__init__(f"{self.category}: {self.reason}")


class ModelNoToolCall(ValueError):
    """A provider response that contains text but no executable decision."""

    def __init__(self, assistant_content: str = "") -> None:
        super().__init__("provider must return at least one decision tool call")
        self.assistant_content = assistant_content


def semantic_input_data(value: NativeModelInput) -> dict[str, object]:
    return situated_input_data(value.situation)


class OpenAICompatibleModel:
    """Small protocol-tool adapter; no persistence or application cache."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = "deepseek-flash",
        context_window: int = 1_000_000,
        max_tokens: int = 393_216,
    ) -> None:
        if context_window < 1:
            raise ValueError("context_window must be positive")
        if max_tokens < 1 or max_tokens > context_window:
            raise ValueError("max_tokens must be within context_window")
        self.client = client
        self.model = model
        self.context_window = context_window
        self.max_tokens = max_tokens
        self._actor_transport: dict[str, dict[str, dict[str, str]]] = {}
        self._provider_usage: dict[tuple[str, str], dict[str, object]] = {}

    def _provider_messages(self, value: NativeModelInput) -> list[dict[str, Any]]:
        messages = self.render_messages(value)
        actor_id = value.actor.actor_id
        transport = self._actor_transport.get(actor_id, {})
        for message in messages:
            if message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls", []):
                record = transport.get(str(call.get("id", "")))
                if record is not None:
                    call["function"]["arguments"] = record["arguments_json"]
                    if "reasoning_content" in record:
                        message["reasoning_content"] = record["reasoning_content"]
        return messages

    def end_activation(self, actor_id: str, trigger_id: str) -> None:
        self._provider_usage.pop((actor_id, trigger_id), None)

    def reset_actor(self, actor_id: str) -> None:
        self._actor_transport.pop(actor_id, None)
        for key in tuple(self._provider_usage):
            if key[0] == actor_id:
                self._provider_usage.pop(key)

    def take_provider_usage(
        self, actor_id: str, trigger_id: str
    ) -> dict[str, object] | None:
        return self._provider_usage.pop((actor_id, trigger_id), None)

    @staticmethod
    def render_messages(value: NativeModelInput) -> list[dict[str, Any]]:
        """Render one Actor's append-only dialogue after its stable system prefix."""

        dialogue = deepcopy(list(value.dialogue))
        if not value.dialogue_includes_turn:
            dialogue.append({"role": "user", "content": '{"kind":"actor_turn"}'})
        return [
            {"role": "system", "content": COMMON_INSTRUCTIONS},
            {"role": "system", "content": value.actor.instructions},
            *dialogue,
        ]

    @staticmethod
    def protocol_tools(value: NativeModelInput) -> list[dict[str, object]]:
        return actor_tool_schemas(
            value.actor,
            value.capability_catalog,
        )

    async def decide(self, value: NativeModelInput) -> ToolProposal:
        tools = self.protocol_tools(value)
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._provider_messages(value),
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            stream=False,
            max_tokens=self.max_tokens,
            extra_body={"thinking": {"type": "enabled"}},
        )
        usage = response_usage_data(response)
        if usage:
            self._provider_usage[(value.actor.actor_id, value.activation_id)] = usage
        try:
            response_message = response.choices[0].message
            response_content = getattr(response_message, "content", None)
            if response_content is None:
                assistant_content = ""
            elif isinstance(response_content, str):
                assistant_content = response_content
            else:
                raise TypeError("assistant content must be a string or null")
            calls = response_message.tool_calls or []
            if not calls:
                raise ModelNoToolCall(assistant_content)
            call = calls[0]
            if not isinstance(call.function.name, str) or not isinstance(
                call.function.arguments,
                str,
            ):
                raise TypeError("decision tool name and arguments must be strings")
            tool_call = ModelToolCallEnvelope(
                str(getattr(call, "id", "")),
                call.function.name,
                call.function.arguments,
            )

            def reject(category: str, reason: str) -> None:
                raise ModelToolRejection(
                    tool_call,
                    category=category,
                    reason=reason,
                    assistant_content=assistant_content,
                )

            reasoning = getattr(response_message, "reasoning_content", None)
            if tool_call.call_id:
                record = {"arguments_json": tool_call.arguments_json}
                if isinstance(reasoning, str):
                    record["reasoning_content"] = reasoning
                self._actor_transport.setdefault(value.actor.actor_id, {})[
                    tool_call.call_id
                ] = record
            try:
                raw = json.loads(tool_call.arguments_json)
            except json.JSONDecodeError as exc:
                reject(
                    "invalid_json",
                    (
                        "tool arguments 不是合法 JSON"
                        f"（第 {exc.lineno} 行，第 {exc.colno} 列）。"
                    ),
                )
            if not isinstance(raw, dict):
                reject(
                    "argument_shape",
                    "tool arguments 必须是 JSON object。",
                )
            return ToolProposal(
                tool_call.call_id,
                tool_call.name,
                raw,
                assistant_content,
            )
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(
                f"provider returned an invalid structured decision: "
                f"{type(exc).__name__}: {exc}"
            ) from exc


def model_situation_data(value: NativeModelInput) -> dict[str, object]:
    """Project actual messages/tools plus read-only state derived for debugging."""

    return {
        "common_instructions": COMMON_INSTRUCTIONS,
        "actor_instructions": value.actor.instructions,
        "derived_current_state": semantic_input_data(value),
        "activation_observations": [
            {
                "source": item.source,
                "ok": item.ok,
                "value": model_data(item.value),
                "error": item.error,
                **(
                    {
                        "capability_boundary_change": model_data(
                            item.capability_boundary_change
                        )
                    }
                    if item.capability_boundary_change
                    else {}
                ),
            }
            for item in value.observations
        ],
        "messages": OpenAICompatibleModel.render_messages(value),
        "tools": OpenAICompatibleModel.protocol_tools(value),
    }
