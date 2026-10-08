"""Dynamic Actor tool targets and deterministic WeaveX host effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .actor import ActorState
from .arrival import ActorArrival
from .capabilities import ToolRegistry
from .dialogue import act_result_data
from .execution import ToolProposal
from .grounding import GroundingInput
from .protocol import Act
from .situation import SituatedInput, broker_source_acts, incremental_targets

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


def questions_from_source_acts(
    situation: SituatedInput,
    source_act_ids: Any,
) -> tuple[str, ...]:
    if (
        not isinstance(source_act_ids, list)
        or not source_act_ids
        or not all(isinstance(item, str) and item.strip() for item in source_act_ids)
        or len(source_act_ids) != len(set(source_act_ids))
    ):
        raise ValueError("source_act_ids must be a non-empty unique array of Act ids")
    candidates = {act.act_id: act for act in broker_source_acts(situation)}
    questions = []
    for source_id in source_act_ids:
        source = candidates.get(source_id)
        if source is None:
            raise ValueError(
                "source_act_ids must identify visible Question/Answer Acts"
            )
        if source.kind == "question":
            question_id = source.act_id
        else:
            answered = candidates.get(source.reply_to or "")
            if answered is None or answered.kind != "question":
                raise ValueError(
                    "a selected Answer must directly reply to a visible Question"
                )
            question_id = answered.act_id
        if question_id not in questions:
            questions.append(question_id)
    threads = {candidates[question_id].thread_id for question_id in questions}
    if len(threads) != 1:
        raise ValueError("source_act_ids must belong to one Inquiry")
    return tuple(questions)


def grounding_inputs(value: Any) -> tuple[GroundingInput, ...]:
    if not isinstance(value, list):
        raise ValueError("grounding_inputs must be an array")
    inputs = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"kind", "id"}:
            raise ValueError("each grounding input requires only kind and id")
        inputs.append(GroundingInput(str(item["kind"]), str(item["id"])))
    if len(inputs) != len(set(inputs)):
        raise ValueError("grounding_inputs cannot contain duplicates")
    return tuple(inputs)


@dataclass(frozen=True)
class HostToolEffect:
    source: str
    value: object


class HostToolDispatcher:
    """Apply one tool proposal without giving the backend semantic authority."""

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    @staticmethod
    def observation_source(trigger: ActorArrival, proposal: ToolProposal) -> str:
        if proposal.name in {"ask_external_question", "ask_in_existing_context"}:
            return "send_act"
        if proposal.name == "send_question":
            return "send_act"
        if proposal.name == "answer_question":
            return "reply_act"
        if proposal.name == "retain_answer":
            return "retain_answer"
        if proposal.name == "send_incremental_act":
            return (
                "reply_act"
                if isinstance(trigger, Act)
                and proposal.arguments.get("reply_to") == trigger.act_id
                else "send_act"
            )
        return proposal.name

    @staticmethod
    def question_id_for(
        runtime: "PrototypeRuntime",
        actor: ActorState,
        proposal: ToolProposal,
    ) -> str:
        selected = proposal.arguments.get("question_id")
        if isinstance(selected, str) and selected:
            try:
                runtime.answer_responsibility(actor.spec.actor_id, selected)
            except (KeyError, ValueError):
                return ""
            return selected
        if proposal.name in actor.spec.capabilities:
            return ""
        reply_to = proposal.arguments.get("reply_to")
        if isinstance(reply_to, str) and reply_to:
            try:
                return runtime.inquiry.question_for_trigger(
                    runtime.inquiry.act(reply_to)
                ).act_id
            except (KeyError, ValueError):
                return ""
        return ""

    @staticmethod
    def _action_trigger(
        runtime: "PrototypeRuntime",
        actor: ActorState,
        trigger: ActorArrival,
        proposal: ToolProposal,
    ) -> Act:
        selected_id = proposal.arguments.get("question_id")
        if proposal.name == "send_incremental_act":
            selected_id = proposal.arguments.get("reply_to")
        if not isinstance(selected_id, str) or not selected_id:
            if not isinstance(trigger, Act):
                raise ValueError("this tool requires an Act target")
            return trigger
        try:
            selected = runtime.inquiry.act(selected_id)
        except KeyError as exc:
            raise ValueError("selected Act does not exist") from exc
        if actor.spec.actor_id not in {selected.sender, selected.recipient}:
            raise PermissionError("selected Act is not visible to this Actor")
        if proposal.name in {
            "answer_question",
            "retain_answer",
            "ask_external_question",
            "ask_in_existing_context",
            "send_question",
            "form_group",
        } or (proposal.name in actor.spec.capabilities and actor.spec.role == "agent"):
            runtime.answer_responsibility(actor.spec.actor_id, selected_id)
        if proposal.name == "inspect_inquiry_topology" and selected.kind != "question":
            raise ValueError("question_id must identify a visible Question")
        return selected

    @staticmethod
    def _keys(arguments: Mapping[str, Any], expected: set[str]) -> None:
        if set(arguments) != expected:
            missing = sorted(expected - set(arguments))
            extra = sorted(set(arguments) - expected)
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if extra:
                details.append("extra: " + ", ".join(extra))
            raise ValueError("invalid tool arguments (" + "; ".join(details) + ")")

    async def execute(
        self,
        runtime: "PrototypeRuntime",
        actor: ActorState,
        activation_trigger: ActorArrival,
        situation: SituatedInput,
        proposal: ToolProposal,
    ) -> HostToolEffect:
        action_trigger = self._action_trigger(
            runtime,
            actor,
            activation_trigger,
            proposal,
        )
        arguments = dict(proposal.arguments)
        if proposal.name in actor.spec.capabilities:
            if actor.spec.role == "broker" and not runtime.answer_responsibilities(
                actor.spec.actor_id
            ):
                raise ValueError(
                    "this capability requires a current direct Question gap"
                )
            value = await self.tools.execute(
                runtime,
                actor,
                action_trigger,
                proposal.name,
                arguments,
            )
            return HostToolEffect(proposal.name, value)
        question_keys = {
            "raw_question",
            "interpretation_context",
            "intent",
        }
        if proposal.name == "answer_question":
            self._keys(
                arguments,
                {
                    "question_id",
                    "answer",
                    "semantic_addresses",
                    "grounding_inputs",
                },
            )
            inputs = grounding_inputs(arguments["grounding_inputs"])
            emitted = runtime.reply_from_actor(
                actor,
                action_trigger,
                kind="answer",
                payload={
                    "answer": arguments["answer"],
                    "semantic_addresses": arguments["semantic_addresses"],
                },
                reply_to=str(arguments["question_id"]),
                grounding_inputs=inputs,
            )
            return HostToolEffect(
                "reply_act",
                {
                    **act_result_data(emitted),
                    "grounding_id": runtime.latest_grounding_id(
                        actor.spec.actor_id,
                        str(arguments["question_id"]),
                    ),
                },
            )
        if proposal.name == "retain_answer":
            self._keys(arguments, {"question_id", "grounding_inputs"})
            return HostToolEffect(
                "retain_answer",
                runtime.retain_answer(
                    actor.spec.actor_id,
                    str(arguments["question_id"]),
                    grounding_inputs(arguments["grounding_inputs"]),
                ),
            )
        if proposal.name in {"ask_external_question", "ask_in_existing_context"}:
            expected = {"question_id", *question_keys}
            if proposal.name == "ask_in_existing_context":
                expected.add("context_id")
            self._keys(arguments, expected)
            emitted = runtime.send_from_actor(
                actor,
                action_trigger,
                recipient="",
                kind="question",
                payload={name: arguments[name] for name in question_keys},
                reply_to=str(arguments["question_id"]),
                context_id=(
                    str(arguments["context_id"])
                    if proposal.name == "ask_in_existing_context"
                    else ""
                ),
                new_context=proposal.name == "ask_external_question",
            )
            return HostToolEffect("send_act", act_result_data(emitted))
        if proposal.name == "send_question":
            self._keys(
                arguments,
                {"question_id", "recipient", "source_act_ids", *question_keys},
            )
            sources = questions_from_source_acts(
                situation,
                arguments["source_act_ids"],
            )
            if any(
                runtime.inquiry.act(source).thread_id != action_trigger.thread_id
                for source in sources
            ):
                raise ValueError(
                    "question_id and source_act_ids must belong to the same Inquiry"
                )
            if arguments["recipient"] in {
                item.sender for item in situation.answer_responsibilities
            }:
                raise PermissionError(
                    "recipient cannot be a current direct Question source"
                )
            emitted = runtime.send_from_actor(
                actor,
                action_trigger,
                recipient=str(arguments["recipient"]),
                kind="question",
                payload={name: arguments[name] for name in question_keys},
                reply_to=str(arguments["question_id"]),
                derived_from=sources,
            )
            return HostToolEffect("send_act", act_result_data(emitted))
        if proposal.name == "send_incremental_act":
            self._keys(arguments, {"reply_to", "content"})
            reply_to = str(arguments["reply_to"])
            payload = {"content": arguments["content"]}
            if (
                isinstance(activation_trigger, Act)
                and reply_to == activation_trigger.act_id
            ):
                emitted = runtime.reply_from_actor(
                    actor,
                    action_trigger,
                    kind="incremental",
                    payload=payload,
                )
                return HostToolEffect("reply_act", act_result_data(emitted))
            targets = incremental_targets(situation)
            if reply_to not in targets:
                raise ValueError("reply_to must identify a visible incremental target")
            emitted = runtime.send_from_actor(
                actor,
                action_trigger,
                recipient=targets[reply_to],
                kind="incremental",
                payload=payload,
                reply_to=reply_to,
            )
            return HostToolEffect("send_act", act_result_data(emitted))
        raise KeyError(f"unknown Actor tool: {proposal.name}")


__all__ = [
    "HostToolDispatcher",
    "HostToolEffect",
    "grounding_inputs",
    "questions_from_source_acts",
]
