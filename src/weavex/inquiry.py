"""The single Act log and its deterministic Inquiry projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .protocol import Act


class InquiryLog:
    """Own Act identity and derive topology without a second mutable graph."""

    def __init__(self) -> None:
        self.threads: dict[str, list[Act]] = {}
        self._act_sequence = 0
        self._thread_sequence = 0

    def new_thread_id(self) -> str:
        self._thread_sequence += 1
        return f"thread-{self._thread_sequence}"

    def emit(
        self,
        *,
        thread_id: str,
        kind: str,
        sender: str,
        recipient: str,
        payload: Mapping[str, Any],
        reply_to: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> Act:
        thread = self.threads.setdefault(thread_id, [])
        acts = {item.act_id: item for item in thread}
        if reply_to is not None and reply_to not in acts:
            raise ValueError("reply_to must reference an Act in the same thread")
        if len(derived_from) != len(set(derived_from)):
            raise ValueError("derived_from cannot contain duplicates")
        if any(acts.get(source) is None for source in derived_from):
            raise ValueError("derived_from must reference Acts in the same thread")
        if any(acts[source].kind != "question" for source in derived_from):
            raise ValueError("derived_from must reference Questions")
        self._act_sequence += 1
        act = Act(
            act_id=f"act-{self._act_sequence}",
            thread_id=thread_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            payload=payload,
            reply_to=reply_to,
            derived_from=derived_from,
        )
        thread.append(act)
        return act

    def visible_acts(self, actor_id: str, thread_id: str) -> tuple[Act, ...]:
        return tuple(
            act
            for act in self.threads.get(thread_id, ())
            if actor_id in {act.sender, act.recipient}
        )

    def visible_acts_across_inquiries(self, actor_id: str) -> tuple[Act, ...]:
        """Project every Act on a relation in which this Actor participates."""

        return tuple(
            act
            for thread in self.threads.values()
            for act in thread
            if actor_id in {act.sender, act.recipient}
        )

    def act(self, act_id: str) -> Act:
        try:
            return next(
                act
                for thread in self.threads.values()
                for act in thread
                if act.act_id == act_id
            )
        except StopIteration as exc:
            raise KeyError(f"unknown Act: {act_id}") from exc

    def direct_questions(self, actor_id: str) -> tuple[Act, ...]:
        """Project every immutable direct Question assigned to one Actor."""

        return tuple(
            act
            for thread in self.threads.values()
            for act in thread
            if act.kind == "question" and act.recipient == actor_id
        )

    def direct_answers(self, question: Act) -> tuple[Act, ...]:
        """Project all sparse public Answer nodes for one Question."""

        if question.kind != "question":
            raise ValueError("direct Answers require a Question")
        return tuple(
            act
            for act in self.threads.get(question.thread_id, ())
            if act.kind == "answer" and act.reply_to == question.act_id
        )

    def answer_route(self, trigger: Act) -> tuple[str, str] | None:
        if trigger.kind != "answer":
            return None
        acts = {value.act_id: value for value in self.threads[trigger.thread_id]}
        answered = acts.get(trigger.reply_to or "")
        parent = acts.get(answered.reply_to or "") if answered else None
        return (parent.sender, parent.act_id) if parent else None

    def open_question_responsibilities(
        self,
        actor_id: str,
    ) -> tuple[dict[str, object], ...]:
        """Project all visible Questions that still lack a direct Answer."""

        answered = {
            act.reply_to
            for thread in self.threads.values()
            for act in thread
            if act.kind == "answer" and act.reply_to is not None
        }
        return tuple(
            {
                "question_id": act.act_id,
                "answer_expected_from": act.recipient,
                "answerable_by_actor": act.recipient == actor_id,
            }
            for act in self.visible_acts_across_inquiries(actor_id)
            if act.kind == "question" and act.act_id not in answered
        )

    def nearest_question(self, trigger: Act) -> Act:
        acts = {value.act_id: value for value in self.threads[trigger.thread_id]}
        current = trigger
        while current.kind != "question" and current.reply_to:
            current = acts[current.reply_to]
        if current.kind != "question":
            raise ValueError("a derived Question requires a prior Question")
        return current

    def question_for_trigger(self, trigger: Act) -> Act:
        """Project the Question mechanically related to one arrival Act."""

        if trigger.kind == "question":
            return trigger
        acts = {value.act_id: value for value in self.threads[trigger.thread_id]}
        if trigger.kind == "answer":
            route = self.answer_route(trigger)
            if route is not None:
                return acts[route[1]]
        return self.nearest_question(trigger)

    def direct_question_on_reply_chain(
        self,
        actor_id: str,
        arrival: Act,
    ) -> Act | None:
        """Find one Actor responsibility through the public single return chain."""

        acts = {value.act_id: value for value in self.threads[arrival.thread_id]}
        current = arrival
        seen = set()
        while current.act_id not in seen:
            seen.add(current.act_id)
            if current.kind == "question" and current.recipient == actor_id:
                return current
            if not current.reply_to or current.reply_to not in acts:
                return None
            current = acts[current.reply_to]
        return None

    def question_has_direct_answer(self, question: Act) -> bool:
        return bool(self.direct_answers(question))

    def topology(self, thread_id: str) -> dict[str, object]:
        acts = self.threads.get(thread_id, ())
        questions = [act for act in acts if act.kind == "question"]
        children = {act.act_id: [] for act in questions}
        answers = {act.act_id: [] for act in questions}
        multi_sources: set[str] = set()
        for act in questions:
            for source in act.derived_from:
                children[source].append(act.act_id)
            if len(act.derived_from) > 1:
                multi_sources.update(act.derived_from)
        for act in acts:
            if act.kind == "answer" and act.reply_to in answers:
                answers[act.reply_to].append(act.act_id)
        candidate_sources = {
            source
            for act in questions
            if len(act.derived_from) > 1 and answers[act.act_id]
            for source in act.derived_from
        }
        return {
            "thread_id": thread_id,
            "leaf_questions_without_direct_answers": [
                act.act_id
                for act in questions
                if not children[act.act_id] and not answers[act.act_id]
            ],
            "multi_source_questions_without_direct_answers": [
                act.act_id
                for act in questions
                if act.act_id in multi_sources and not answers[act.act_id]
            ],
            "candidate_source_questions_without_direct_answers": [
                act.act_id
                for act in questions
                if act.act_id in candidate_sources and not answers[act.act_id]
            ],
            "questions": [
                {
                    "question_id": act.act_id,
                    "sender": act.sender,
                    "recipient": act.recipient,
                    "reply_to": act.reply_to,
                    "derived_from": list(act.derived_from),
                    "derived_children": children[act.act_id],
                    "answers": answers[act.act_id],
                }
                for act in questions
            ],
        }

    def reset(self) -> None:
        self.threads.clear()
        self._act_sequence = 0
        self._thread_sequence = 0


__all__ = ["InquiryLog"]
