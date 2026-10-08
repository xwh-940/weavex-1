"""Append-only model dialogue records owned by one Actor lifetime."""

from __future__ import annotations

import json
from typing import Any

from .execution import ToolProposal
from .protocol import (
    Act,
    Decision,
    ModelToolCall,
    ModelToolCallEnvelope,
    Observation,
)
from .situation import model_data


DialogueMessage = dict[str, Any]


def act_result_data(act: Act) -> dict[str, str]:
    """Return the model-relevant effect of one successfully created Act."""

    return {"act_id": act.act_id, "kind": act.kind}


def turn_message() -> DialogueMessage:
    return {
        "role": "user",
        "content": '{"kind":"actor_turn"}',
    }


def _fallback_tool_call(
    decision: Decision,
    *,
    fallback_reply_to: str,
    fallback_question_id: str,
) -> tuple[str, dict[str, Any]]:
    if decision.kind == "call":
        arguments = dict(decision.arguments)
        if (
            decision.name
            in {
                "world_references_from",
                "world_add_object",
                "world_add_reference",
                "form_group",
                "inspect_inquiry_topology",
            }
            and "question_id" not in arguments
        ):
            arguments["question_id"] = fallback_question_id
        return decision.name, arguments
    if decision.kind == "reply":
        if decision.act_kind == "answer":
            return "answer_question", {
                "question_id": decision.reply_to or fallback_question_id,
                "grounding_inputs": [],
                **dict(decision.payload),
            }
        return "send_incremental_act", {
            "reply_to": fallback_reply_to,
            "content": decision.payload.get("content", ""),
        }
    if decision.act_kind == "question":
        arguments = dict(decision.payload)
        if decision.new_context:
            return "ask_external_question", {
                "question_id": decision.reply_to or fallback_question_id,
                **arguments,
            }
        if decision.context_id:
            return "ask_in_existing_context", {
                "question_id": decision.reply_to or fallback_question_id,
                "context_id": decision.context_id,
                **arguments,
            }
        return "send_question", {
            "question_id": decision.reply_to or fallback_question_id,
            "recipient": decision.recipient,
            **arguments,
            "source_act_ids": list(decision.derived_from),
        }
    if decision.act_kind == "answer":
        return "answer_question", {
            "question_id": decision.reply_to or fallback_question_id,
            "grounding_inputs": [],
            **dict(decision.payload),
        }
    return "send_incremental_act", {
        "reply_to": decision.reply_to or fallback_reply_to,
        "content": decision.payload.get("content", ""),
    }


def decision_tool_proposal(
    decision: Decision,
    *,
    actor_role: str,
    fallback_call_id: str,
    fallback_reply_to: str,
    fallback_question_id: str,
) -> ToolProposal:
    """Normalize the native Decision transport to the host tool-call contract."""

    if decision.kind == "stop":
        return ToolProposal(
            fallback_call_id,
            "yield_turn",
            {},
            decision.assistant_content or decision.reason,
        )
    if (
        actor_role == "agent"
        and decision.kind == "send"
        and decision.act_kind == "question"
        and decision.recipient
        and not decision.context_id
        and not decision.new_context
    ):
        return ToolProposal(
            fallback_call_id,
            "ask_in_existing_context",
            {
                "question_id": decision.reply_to or fallback_question_id,
                "context_id": decision.recipient,
                **dict(decision.payload),
            },
            decision.assistant_content,
        )
    name, arguments = _fallback_tool_call(
        decision,
        fallback_reply_to=fallback_reply_to,
        fallback_question_id=fallback_question_id,
    )
    return ToolProposal(
        fallback_call_id,
        name,
        arguments,
        decision.assistant_content,
    )


def assistant_tool_call_message(
    tool_call: ModelToolCall,
    *,
    fallback_call_id: str,
    content: str = "",
) -> tuple[DialogueMessage, str]:
    return assistant_raw_tool_call_message(
        ModelToolCallEnvelope(
            tool_call.call_id,
            tool_call.name,
            json.dumps(
                model_data(tool_call.arguments),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
        fallback_call_id=fallback_call_id,
        content=content,
    )


def assistant_raw_tool_call_message(
    tool_call: ModelToolCallEnvelope,
    *,
    fallback_call_id: str,
    content: str = "",
) -> tuple[DialogueMessage, str]:
    call_id = tool_call.call_id or fallback_call_id
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments_json,
                },
            }
        ],
    }, call_id


def tool_message(call_id: str, observation: Observation) -> DialogueMessage:
    if not call_id:
        raise ValueError("a tool Observation requires a model tool call id")
    content = {
        "source": observation.source,
        "ok": observation.ok,
        "value": model_data(observation.value),
        "error": observation.error,
    }
    if observation.capability_boundary_change:
        content["capability_boundary_change"] = model_data(
            observation.capability_boundary_change
        )
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def dialogue_data(messages: list[DialogueMessage]) -> list[DialogueMessage]:
    return [dict(message) for message in messages]


__all__ = [
    "DialogueMessage",
    "act_result_data",
    "decision_tool_proposal",
    "dialogue_data",
    "tool_message",
    "turn_message",
]
