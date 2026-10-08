"""Actor-private Question gaps across sparse public Answer nodes."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .inquiry import InquiryLog
from .protocol import Act


@dataclass(frozen=True, slots=True)
class FrontierChange:
    """One mechanical Local World addition related to a direct Question."""

    change_id: str
    kind: str
    through_grounding_id: str = ""


@dataclass(frozen=True, slots=True)
class QuestionFrontierState:
    """Mechanical frontier facts without a host-side relevance judgment."""

    question_id: str
    latest_local_change_id: str = ""
    latest_judged_change_id: str = ""
    latest_answer_id: str = ""
    latest_grounding_id: str = ""
    pending_changes: tuple[FrontierChange, ...] = ()

    @property
    def has_pending_change(self) -> bool:
        return bool(self.pending_changes)


class QuestionFrontiers:
    """Keep Local World change gaps private to the responsible Actor."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], QuestionFrontierState] = {}

    def state(self, actor_id: str, question_id: str) -> QuestionFrontierState:
        return self._states.get(
            (actor_id, question_id),
            QuestionFrontierState(question_id),
        )

    def observe_act(self, actor_id: str, question_id: str, value: Act) -> None:
        state = self.state(actor_id, question_id)
        self.observe_change(
            actor_id,
            question_id,
            FrontierChange(
                value.act_id,
                "coordination_act",
                state.latest_grounding_id,
            ),
        )

    def observe_change(
        self,
        actor_id: str,
        question_id: str,
        change: FrontierChange,
    ) -> None:
        key = (actor_id, question_id)
        current = self.state(*key)
        if (
            change.change_id in {item.change_id for item in current.pending_changes}
            or change.change_id == current.latest_judged_change_id
        ):
            return
        self._states[key] = replace(
            current,
            latest_local_change_id=change.change_id,
            pending_changes=(*current.pending_changes, change),
        )

    def record_answer(
        self,
        actor_id: str,
        question_id: str,
        answer_id: str,
        grounding_id: str,
    ) -> None:
        key = (actor_id, question_id)
        current = self.state(*key)
        self._states[key] = replace(
            current,
            latest_judged_change_id=current.latest_local_change_id,
            latest_answer_id=answer_id,
            latest_grounding_id=grounding_id,
            pending_changes=(),
        )

    def validate_retain(
        self,
        inquiry: InquiryLog,
        actor_id: str,
        question_id: str,
    ) -> str:
        question = inquiry.act(question_id)
        if question.recipient != actor_id or question.kind != "question":
            raise ValueError("question_id must identify this Actor's direct Question")
        current = self.state(actor_id, question_id)
        answers = inquiry.direct_answers(question)
        if not answers or not current.has_pending_change:
            raise ValueError(
                "retain_answer requires an existing Answer and unjudged Local World changes"
            )
        return answers[-1].act_id

    def retain_answer(
        self,
        inquiry: InquiryLog,
        actor_id: str,
        question_id: str,
        grounding_id: str,
    ) -> QuestionFrontierState:
        answer_id = self.validate_retain(inquiry, actor_id, question_id)
        current = self.state(actor_id, question_id)
        retained = replace(
            current,
            latest_judged_change_id=current.latest_local_change_id,
            latest_answer_id=answer_id,
            latest_grounding_id=grounding_id,
            pending_changes=(),
        )
        self._states[(actor_id, question_id)] = retained
        return retained

    def answer_responsibilities(
        self,
        inquiry: InquiryLog,
        actor_id: str,
    ) -> tuple[Act, ...]:
        return tuple(
            question
            for question in inquiry.direct_questions(actor_id)
            if not inquiry.direct_answers(question)
            or self.state(actor_id, question.act_id).has_pending_change
        )

    def answer_responsibility(
        self,
        inquiry: InquiryLog,
        actor_id: str,
        question_id: str,
    ) -> Act:
        try:
            question = inquiry.act(question_id)
        except KeyError as exc:
            raise ValueError("question_id must identify a current Answer gap") from exc
        if question not in self.answer_responsibilities(inquiry, actor_id):
            raise ValueError("question_id must identify a current Answer gap")
        return question

    def responsibility_data(
        self,
        inquiry: InquiryLog,
        actor_id: str,
        question_id: str,
    ) -> dict[str, object]:
        question = inquiry.act(question_id)
        answers = inquiry.direct_answers(question)
        state = self.state(actor_id, question_id)
        return {
            "gap": (
                "local_world_changes_after_judged_frontier"
                if answers
                else "no_public_answer"
            ),
            "latest_answer_id": answers[-1].act_id if answers else "",
            "latest_grounding_id": state.latest_grounding_id,
            "latest_local_change_id": state.latest_local_change_id,
            "latest_judged_change_id": state.latest_judged_change_id,
            "pending_changes": [
                {
                    "change_id": item.change_id,
                    "kind": item.kind,
                    "through_grounding_id": item.through_grounding_id,
                }
                for item in state.pending_changes
            ],
        }

    def grounding_roots(self, actor_id: str) -> dict[str, str]:
        return {
            question_id: state.latest_grounding_id
            for (owner_id, question_id), state in self._states.items()
            if owner_id == actor_id and state.latest_grounding_id
        }

    def data_for_actor(self, actor_id: str) -> list[dict[str, object]]:
        return [
            {
                "question_id": question_id,
                "latest_local_change_id": state.latest_local_change_id,
                "latest_judged_change_id": state.latest_judged_change_id,
                "latest_answer_id": state.latest_answer_id,
                "latest_grounding_id": state.latest_grounding_id,
                "pending_changes": [
                    {
                        "change_id": item.change_id,
                        "kind": item.kind,
                        "through_grounding_id": item.through_grounding_id,
                    }
                    for item in state.pending_changes
                ],
            }
            for (owner_id, question_id), state in self._states.items()
            if owner_id == actor_id
        ]

    def reset(self) -> None:
        self._states.clear()


__all__ = ["FrontierChange", "QuestionFrontierState", "QuestionFrontiers"]
