"""Deterministic Human/Actor communication authority over the Act log."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .actor import ActorState
from .protocol import (
    INITIALIZATION_SOURCE,
    Act,
    SemanticHandle,
    protocol_addresses,
    question_payload,
)

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


def inject_human(
    runtime: "PrototypeRuntime",
    content: str,
    *,
    recipient: str | None = None,
    thread_id: str | None = None,
    interpretation_content: str | None = None,
    semantic_addresses: tuple[SemanticHandle, ...] = (),
    intent: str | None = None,
    new_context: bool = False,
) -> Act:
    text = content.strip()
    if not text:
        raise ValueError("human content cannot be blank")
    if new_context and recipient is not None:
        raise ValueError("a Human Question cannot select and create a context together")
    target = (
        runtime.create_broker().spec.actor_id
        if new_context
        else (recipient or runtime.entry_broker_id).strip()
    )
    if target not in runtime.actors:
        raise KeyError(f"unknown recipient: {target}")
    if runtime.actors[target].spec.role != "broker":
        raise PermissionError("Human Questions must enter through the Broker context")
    resolved_thread = thread_id or runtime.inquiry.new_thread_id()
    value = question_payload(
        text,
        interpretation_content=(
            "" if interpretation_content is None else interpretation_content
        ),
        semantic_addresses=semantic_addresses,
        intent="" if intent is None else intent,
    )
    return emit_act(
        runtime,
        thread_id=resolved_thread,
        kind="question",
        sender="human",
        recipient=target,
        payload=value,
    )


def reply_human(
    runtime: "PrototypeRuntime",
    *,
    thread_id: str,
    reply_to: str,
    content: str,
) -> Act:
    text = content.strip()
    if not text:
        raise ValueError("human reply content cannot be blank")
    try:
        target = next(
            act for act in runtime.threads[thread_id] if act.act_id == reply_to
        )
    except (KeyError, StopIteration) as exc:
        raise ValueError(
            "reply_to must identify an Act in the selected thread"
        ) from exc
    if "human" not in {target.sender, target.recipient}:
        raise ValueError("Human incremental Acts must continue a Human-visible Act")
    counterparts = {target.sender, target.recipient} - {"human"}
    if len(counterparts) != 1:
        raise ValueError("Human incremental Acts require one Broker counterpart")
    recipient = next(iter(counterparts))
    if (
        recipient not in runtime.actors
        or runtime.actors[recipient].spec.role != "broker"
    ):
        raise ValueError("Human incremental Acts may only continue with a Broker")
    return emit_act(
        runtime,
        thread_id=thread_id,
        kind="incremental",
        sender="human",
        recipient=recipient,
        payload={"content": text},
        reply_to=target.act_id,
    )


def send_from_actor(
    runtime: "PrototypeRuntime",
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
) -> Act:
    normalized_kind = kind.casefold()
    if normalized_kind not in {"question", "answer", "incremental"}:
        raise ValueError(f"unknown Actor Act kind: {kind}")
    validate_actor_addresses(runtime, actor, trigger, normalized_kind, payload)
    opens_context = False
    if context_id or new_context:
        if actor.spec.role != "agent" or normalized_kind != "question":
            raise PermissionError(
                "only an Agent external Question can choose a coordination context"
            )
        if context_id and new_context:
            raise ValueError("an external Question must choose one context route")
        if new_context:
            opens_context = True
        else:
            if context_id not in runtime.context_ids_for_agent(actor.spec.actor_id):
                raise PermissionError(
                    "an Agent may only reuse a coordination context it belongs to"
                )
            recipient = context_id
    if normalized_kind == "incremental":
        target_id = reply_to or trigger.act_id
        target = next(
            (
                act
                for act in runtime.visible_acts(actor.spec.actor_id, trigger.thread_id)
                if act.act_id == target_id
            ),
            None,
        )
        if target is None:
            raise ValueError(
                "incremental Acts must reply to an Act visible to the sender"
            )
        counterparts = {target.sender, target.recipient} - {actor.spec.actor_id}
        if len(counterparts) != 1 or recipient != next(iter(counterparts)):
            raise PermissionError(
                "incremental Acts must stay with the visible Act counterpart"
            )
        reply_to = target.act_id
    route = runtime.inquiry.answer_route(trigger)
    targeted_broker_answer = (
        normalized_kind == "answer" and actor.spec.role == "broker" and reply_to
    )
    selected_route = None
    if targeted_broker_answer:
        try:
            selected = runtime.answer_responsibility(
                actor.spec.actor_id,
                reply_to,
            )
            selected_route = (selected.sender, selected.act_id)
        except (KeyError, ValueError):
            selected_route = None
    if targeted_broker_answer and selected_route is None:
        raise ValueError(
            "Broker answer reply_to is not an available Question responsibility"
        )
    if normalized_kind == "answer" and (selected_route or route):
        recipient, reply_to = selected_route or route
    elif actor.spec.role == "broker" and normalized_kind == "question":
        reply_to = route[1] if route else trigger.act_id
    if normalized_kind == "question":
        if actor.spec.role == "agent" and derived_from:
            raise PermissionError("Agents cannot choose Question dependencies")
        if actor.spec.role == "broker" and not derived_from:
            raise ValueError("Broker Questions require explicit non-empty derived_from")
        if actor.spec.role == "agent":
            parent_question = runtime.inquiry.question_for_trigger(trigger)
            derived_from = (runtime.inquiry.nearest_question(trigger).act_id,)
            reply_to = parent_question.act_id
    if opens_context:
        recipient = runtime.create_broker().spec.actor_id
    authorize(
        runtime,
        actor,
        recipient,
        opens_context=opens_context,
        kind=normalized_kind,
        reply_to=reply_to,
    )
    return emit_act(
        runtime,
        thread_id=trigger.thread_id,
        kind=kind,
        sender=actor.spec.actor_id,
        recipient=recipient,
        payload=payload,
        reply_to=reply_to or trigger.act_id,
        derived_from=derived_from,
    )


def reply_from_actor(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    *,
    kind: str,
    payload: Mapping[str, Any],
    reply_to: str = "",
) -> Act:
    normalized_kind = kind.casefold()
    if normalized_kind not in {"answer", "incremental"}:
        raise ValueError(f"unknown Actor reply Act kind: {kind}")
    validate_actor_addresses(runtime, actor, trigger, normalized_kind, payload)
    if normalized_kind == "answer":
        question = (
            runtime.answer_responsibility(actor.spec.actor_id, reply_to)
            if reply_to
            else runtime.inquiry.question_for_trigger(trigger)
        )
        recipient, reply_to = question.sender, question.act_id
        thread_id = question.thread_id
    else:
        recipient, reply_to = trigger.sender, trigger.act_id
        thread_id = trigger.thread_id
    authorize(runtime, actor, recipient, kind=normalized_kind, reply_to=reply_to)
    return emit_act(
        runtime,
        thread_id=thread_id,
        kind=kind,
        sender=actor.spec.actor_id,
        recipient=recipient,
        payload=payload,
        reply_to=reply_to,
    )


def authorize(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    recipient: str,
    *,
    opens_context: bool = False,
    kind: str = "",
    reply_to: str = "",
) -> None:
    if recipient == INITIALIZATION_SOURCE:
        if kind != "answer":
            raise PermissionError(
                "initialization only receives Answers to its Questions"
            )
        question = runtime.answer_responsibility(actor.spec.actor_id, reply_to)
        if question.sender != INITIALIZATION_SOURCE or question.reply_to is not None:
            raise PermissionError(
                "initialization requires its own direct root Question"
            )
        return
    if recipient != "human" and recipient not in runtime.actors:
        raise KeyError(f"unknown recipient: {recipient}")
    target = runtime.actors.get(recipient)
    if actor.spec.role == "agent":
        if target is not None and target.spec.role == "agent":
            raise PermissionError(
                "Agents communicate only through Broker contexts; direct "
                "Agent-to-Agent communication is disabled"
            )
        if target is None or target.spec.role != "broker":
            raise PermissionError("Agents communicate only with a Broker context")
        if not opens_context and actor.spec.actor_id not in runtime.context_members(
            recipient
        ):
            raise PermissionError(
                "an Agent may only communicate with a Broker context it belongs to"
            )
        return
    if actor.spec.role == "broker" and target is not None:
        if target.spec.role == "broker":
            raise PermissionError("direct Broker-to-Broker communication is disabled")
        if target.spec.role == "agent" and recipient not in runtime.context_members(
            actor.spec.actor_id
        ):
            raise PermissionError(
                "a Broker may only communicate with Agents in its fixed group"
            )


def validate_actor_addresses(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    kind: str,
    payload: Mapping[str, Any],
) -> None:
    requested = protocol_addresses(kind, payload)
    if actor.spec.role == "agent":
        runtime.local_worlds.validate_addresses(
            actor.spec.actor_id,
            requested,
        )
        return
    if not requested:
        return
    visible_addresses = {
        address
        for act in runtime.visible_acts(actor.spec.actor_id, trigger.thread_id)
        for address in protocol_addresses(act.kind, act.payload)
    }
    unknown = [address for address in requested if address not in visible_addresses]
    if unknown:
        raise ValueError(
            "Broker can only forward exact Semantic Addresses from visible Acts: "
            + ", ".join(f"{item.kind} address {item.semantic_id}" for item in unknown)
        )


def emit_act(
    runtime: "PrototypeRuntime",
    *,
    thread_id: str,
    kind: str,
    sender: str,
    recipient: str,
    payload: Mapping[str, Any],
    reply_to: str | None = None,
    derived_from: tuple[str, ...] = (),
) -> Act:
    act = runtime.inquiry.emit(
        thread_id=thread_id,
        kind=kind,
        sender=sender,
        recipient=recipient,
        payload=payload,
        reply_to=reply_to,
        derived_from=derived_from,
    )
    if recipient in runtime.actors:
        runtime.queue.append(act)
    runtime._record_trace(
        "act.created",
        actor_id=sender if sender in runtime.actors else "",
        thread_id=thread_id,
        act_id=act.act_id,
        payload=runtime.act_data(act),
    )
    return act


__all__ = ["inject_human", "reply_from_actor", "reply_human", "send_from_actor"]
