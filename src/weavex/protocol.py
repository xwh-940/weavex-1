"""Small values shared by the prototype's semantic and mechanical halves."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


DecisionKind = Literal["call", "send", "reply", "stop"]
SemanticHandleKind = Literal["object", "reference"]
INITIALIZATION_SOURCE = "initialization"


def _text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


def _semantic_string(value: Any, label: str, *, allow_blank: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not allow_blank and not value.strip():
        raise ValueError(f"{label} cannot be blank")
    return value


@dataclass(frozen=True)
class SemanticHandle:
    kind: SemanticHandleKind
    semantic_id: str

    def __post_init__(self) -> None:
        if self.kind not in {"object", "reference"}:
            raise ValueError(f"unknown semantic handle kind: {self.kind}")
        object.__setattr__(self, "semantic_id", _text(self.semantic_id, "semantic_id"))


def handle_data(value: SemanticHandle) -> dict[str, str]:
    return {"kind": value.kind, "id": value.semantic_id}


def question_payload(
    raw_question: str,
    *,
    interpretation_content: str = "",
    semantic_addresses: tuple[SemanticHandle, ...] = (),
    intent: str = "",
) -> dict[str, Any]:
    return {
        "raw_question": _semantic_string(
            raw_question, "raw_question", allow_blank=False
        ),
        "interpretation_context": {
            "content": _semantic_string(
                interpretation_content,
                "interpretation_context.content",
                allow_blank=True,
            ),
            "semantic_addresses": [handle_data(value) for value in semantic_addresses],
        },
        "intent": _semantic_string(intent, "intent", allow_blank=True),
    }


def answer_payload(
    answer: str,
    *,
    semantic_addresses: tuple[SemanticHandle, ...] = (),
) -> dict[str, Any]:
    return {
        "answer": _semantic_string(answer, "answer", allow_blank=False),
        "semantic_addresses": [handle_data(value) for value in semantic_addresses],
    }


def parse_handles(raw_addresses: Any) -> tuple[SemanticHandle, ...]:
    if not isinstance(raw_addresses, list):
        raise ValueError("semantic_addresses must be an array")
    values = []
    for item in raw_addresses:
        if not isinstance(item, Mapping) or set(item) != {"kind", "id"}:
            raise ValueError("each semantic address requires only kind and id")
        values.append(SemanticHandle(str(item["kind"]), str(item["id"])))
    return tuple(values)


def protocol_addresses(
    kind: str, payload: Mapping[str, Any]
) -> tuple[SemanticHandle, ...]:
    if kind == "question":
        if set(payload) != {"raw_question", "interpretation_context", "intent"}:
            raise ValueError(
                "question requires raw_question, interpretation_context, and intent"
            )
        _semantic_string(payload["raw_question"], "raw_question", allow_blank=False)
        context = payload["interpretation_context"]
        if not isinstance(context, Mapping) or set(context) != {
            "content",
            "semantic_addresses",
        }:
            raise ValueError(
                "interpretation_context requires content and semantic_addresses"
            )
        _semantic_string(
            context["content"],
            "interpretation_context.content",
            allow_blank=True,
        )
        _semantic_string(payload["intent"], "intent", allow_blank=True)
        raw_addresses = context["semantic_addresses"]
    elif kind == "answer":
        if set(payload) != {"answer", "semantic_addresses"}:
            raise ValueError("answer requires answer and semantic_addresses")
        _semantic_string(payload["answer"], "answer", allow_blank=False)
        raw_addresses = payload["semantic_addresses"]
    else:
        return ()
    return parse_handles(raw_addresses)


@dataclass(frozen=True)
class Act:
    """One immutable expression from one principal to one recipient."""

    act_id: str
    thread_id: str
    kind: str
    sender: str
    recipient: str
    payload: Mapping[str, Any]
    reply_to: str | None = None
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label in ("act_id", "thread_id", "kind", "sender", "recipient"):
            object.__setattr__(self, label, _text(getattr(self, label), label))
        object.__setattr__(self, "kind", self.kind.casefold())
        object.__setattr__(self, "payload", dict(self.payload))
        try:
            json.dumps(self.payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Act payload must be JSON-serializable") from exc
        protocol_addresses(self.kind, self.payload)
        if self.reply_to is not None:
            object.__setattr__(self, "reply_to", _text(self.reply_to, "reply_to"))
            if self.reply_to == self.act_id:
                raise ValueError("an Act cannot reply to itself")
        sources = tuple(_text(value, "derived_from") for value in self.derived_from)
        if len(sources) != len(set(sources)):
            raise ValueError("derived_from cannot contain duplicates")
        if self.act_id in sources:
            raise ValueError("an Act cannot derive from itself")
        if sources and self.kind != "question":
            raise ValueError("only Questions can have derived_from sources")
        object.__setattr__(self, "derived_from", sources)


@dataclass(frozen=True)
class ModelToolCallEnvelope:
    """One replayable provider tool call before argument interpretation."""

    call_id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", self.call_id.strip())
        object.__setattr__(self, "name", _text(self.name, "model tool name"))
        if not isinstance(self.arguments_json, str):
            raise ValueError("model tool arguments_json must be a string")


@dataclass(frozen=True)
class ModelToolCall:
    """The exact provider tool call that produced one host Decision."""

    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", self.call_id.strip())
        object.__setattr__(self, "name", _text(self.name, "model tool name"))
        object.__setattr__(self, "arguments", dict(self.arguments))
        try:
            json.dumps(self.arguments, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("model tool arguments must be JSON-serializable") from exc


@dataclass(frozen=True)
class Decision:
    """One semantic proposal. The host, not the model, performs every effect."""

    kind: DecisionKind
    name: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    recipient: str = ""
    act_kind: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    reply_to: str = ""
    derived_from: tuple[str, ...] = ()
    context_id: str = ""
    new_context: bool = False
    reason: str = ""
    assistant_content: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"call", "send", "reply", "stop"}:
            raise ValueError(f"unknown decision kind: {self.kind}")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "recipient", self.recipient.strip())
        object.__setattr__(self, "act_kind", self.act_kind.strip())
        object.__setattr__(self, "act_kind", self.act_kind.casefold())
        object.__setattr__(self, "reply_to", self.reply_to.strip())
        object.__setattr__(self, "context_id", self.context_id.strip())
        if not isinstance(self.new_context, bool):
            raise ValueError("new_context must be a boolean")
        if not isinstance(self.assistant_content, str):
            raise ValueError("assistant_content must be a string")
        routes = sum(
            bool(value) for value in (self.recipient, self.context_id, self.new_context)
        )
        sources = tuple(_text(value, "derived_from") for value in self.derived_from)
        if len(sources) != len(set(sources)):
            raise ValueError("derived_from cannot contain duplicates")
        if sources and (self.kind != "send" or self.act_kind != "question"):
            raise ValueError("only a sent Question can choose derived_from sources")
        object.__setattr__(self, "derived_from", sources)
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "payload", dict(self.payload))
        if self.kind == "call" and not self.name:
            raise ValueError("call requires a capability name")
        if self.kind == "send" and (routes != 1 or not self.act_kind):
            raise ValueError(
                "send requires act_kind and exactly one recipient or context route"
            )
        if (self.context_id or self.new_context) and (
            self.kind != "send" or self.act_kind != "question"
        ):
            raise ValueError("context routes are only valid for sent Questions")
        if self.kind == "reply" and not self.act_kind:
            raise ValueError("reply requires act_kind")


@dataclass(frozen=True)
class Observation:
    source: str
    ok: bool
    value: Any = None
    error: str = ""
    capability_boundary_change: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceEvent:
    seq: int
    kind: str
    actor_id: str = ""
    thread_id: str = ""
    act_id: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
