"""Read-only live-debug query projections."""

from __future__ import annotations

from weavex.debug_query import (
    pending_human_acts,
    question_rows,
    search_hits,
    summarize_state,
)


def _state() -> dict[str, object]:
    return {
        "actors": [
            {"actor_id": "broker", "role": "broker"},
            {"actor_id": "agent-1", "role": "agent"},
        ],
        "threads": {
            "thread-1": [
                {
                    "act_id": "act-1",
                    "thread_id": "thread-1",
                    "kind": "question",
                    "sender": "human",
                    "recipient": "broker",
                    "payload": {"raw_question": "Find NEEDLE-7"},
                    "reply_to": None,
                    "derived_from": [],
                },
                {
                    "act_id": "act-2",
                    "thread_id": "thread-1",
                    "kind": "incremental",
                    "sender": "broker",
                    "recipient": "human",
                    "payload": {"content": "Provide the code"},
                    "reply_to": "act-1",
                    "derived_from": [],
                },
            ]
        },
        "queue": [],
        "semantic_world": {"objects": [], "references": []},
        "agent_local_worlds": [],
        "coordination_contexts": [{"context_id": "broker"}],
        "traces": [],
    }


def test_debug_query_projects_open_questions_and_pending_human() -> None:
    state = _state()

    assert [item["act_id"] for item in pending_human_acts(state)] == ["act-2"]
    assert question_rows(state)[0]["open"] is True
    assert summarize_state(state) == {
        "actors": 2,
        "brokers": 1,
        "agents": 1,
        "contexts": 1,
        "threads": 1,
        "acts": 2,
        "questions": 1,
        "open_questions": 1,
        "answers": 0,
        "queue": 0,
        "step_in_flight": False,
        "pending_human": 1,
        "failed_traces": 0,
        "world_objects": 0,
        "world_references": 0,
        "provider_usage": {},
        "host_costs": {},
        "latest_act": state["threads"]["thread-1"][-1],
    }


def test_debug_query_search_returns_paths_without_dumping_whole_state() -> None:
    hits = search_hits(_state(), "needle-7")

    assert hits == [
        {
            "path": "threads.thread-1[0].payload.raw_question",
            "value": "Find NEEDLE-7",
        }
    ]
    assert len(search_hits(_state(), "act_id")) == len(
        {item["path"] for item in search_hits(_state(), "act_id")}
    )
