"""Read-only queries over a live WeaveX debug server."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any


DEFAULT_URL = "http://127.0.0.1:8766"


def _act_sequence(act: Mapping[str, Any]) -> int:
    value = str(act.get("act_id", "")).removeprefix("act-")
    return int(value) if value.isdigit() else -1


def flatten_acts(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = [
        dict(act)
        for thread in state.get("threads", {}).values()
        for act in thread
        if isinstance(act, Mapping)
    ]
    return sorted(values, key=_act_sequence)


def pending_human_acts(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = flatten_acts(state)
    replied = {act.get("reply_to") for act in values}
    return [
        act
        for act in values
        if act.get("recipient") == "human"
        and act.get("kind") == "incremental"
        and act.get("act_id") not in replied
    ]


def question_rows(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = flatten_acts(state)
    answers: dict[str, list[str]] = {}
    for act in values:
        if act.get("kind") == "answer" and act.get("reply_to"):
            answers.setdefault(str(act["reply_to"]), []).append(str(act["act_id"]))
    return [
        {
            **act,
            "direct_answers": answers.get(str(act["act_id"]), []),
            "open": not answers.get(str(act["act_id"])),
        }
        for act in values
        if act.get("kind") == "question"
    ]


def failed_traces(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(trace)
        for trace in state.get("traces", [])
        if isinstance(trace, Mapping)
        and (
            trace.get("kind") == "activation.failed"
            or (
                str(trace.get("kind", "")).endswith("observation")
                and isinstance(trace.get("payload"), Mapping)
                and trace["payload"].get("ok") is False
            )
        )
    ]


def summarize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    values = flatten_acts(state)
    questions = question_rows(state)
    actors = [item for item in state.get("actors", []) if isinstance(item, Mapping)]
    return {
        "actors": len(actors),
        "brokers": sum(item.get("role") == "broker" for item in actors),
        "agents": sum(item.get("role") == "agent" for item in actors),
        "contexts": len(state.get("coordination_contexts", [])),
        "threads": len(state.get("threads", {})),
        "acts": len(values),
        "questions": len(questions),
        "open_questions": sum(item["open"] for item in questions),
        "answers": sum(item.get("kind") == "answer" for item in values),
        "queue": len(state.get("queue", [])),
        "step_in_flight": bool(
            state.get("debug_control", {}).get("step_in_flight", False)
        ),
        "pending_human": len(pending_human_acts(state)),
        "failed_traces": len(failed_traces(state)),
        "world_objects": len(state.get("semantic_world", {}).get("objects", [])),
        "world_references": len(state.get("semantic_world", {}).get("references", [])),
        "provider_usage": dict(state.get("provider_usage", {})),
        "host_costs": dict(state.get("host_costs", {})),
        "latest_act": values[-1] if values else None,
    }


def _preview(value: Any, limit: int = 240) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return text if len(text) <= limit else text[: limit - 1] + "…"


def search_hits(
    value: Any,
    term: str,
    *,
    limit: int = 100,
) -> list[dict[str, str]]:
    needle = term.casefold()
    hits: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    def add(path: str, value: Any) -> None:
        if path in seen_paths or len(hits) >= limit:
            return
        seen_paths.add(path)
        hits.append({"path": path, "value": _preview(value)})

    def walk(current: Any, path: str) -> None:
        if len(hits) >= limit:
            return
        if isinstance(current, Mapping):
            for key, child in current.items():
                child_path = f"{path}.{key}" if path else str(key)
                if needle in str(key).casefold():
                    add(child_path, child)
                    if len(hits) >= limit:
                        return
                walk(child, child_path)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                walk(child, f"{path}[{index}]")
                if len(hits) >= limit:
                    return
        elif needle in str(current).casefold():
            add(path, current)

    walk(value, "")
    return hits


def _contains(value: Any, term: str) -> bool:
    return term.casefold() in json.dumps(value, ensure_ascii=False).casefold()


def _limited(values: list[Any], limit: int) -> list[Any]:
    return values[-limit:] if limit > 0 else values


def _get(base_url: str, path: str, **params: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = base_url.rstrip("/") + path + (f"?{query}" if query else "")
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError("debug API must return a JSON object")
    return value


def _state(base_url: str) -> dict[str, Any]:
    return _get(base_url, "/api/state")


def _scope(state: Mapping[str, Any], name: str) -> Any:
    if name == "acts":
        return flatten_acts(state)
    if name == "questions":
        return question_rows(state)
    if name == "traces":
        return state.get("traces", [])
    if name == "locals":
        return state.get("agent_local_worlds", [])
    if name == "world":
        return state.get("semantic_world", {})
    if name == "contexts":
        return state.get("coordination_contexts", [])
    return state


def _filter_rows(
    values: Iterable[Mapping[str, Any]],
    *,
    actor: str = "",
    thread: str = "",
    kind: str = "",
    act_id: str = "",
    contains: str = "",
) -> list[dict[str, Any]]:
    rows = []
    for value in values:
        if actor and actor not in {
            value.get("actor_id"),
            value.get("sender"),
            value.get("recipient"),
        }:
            continue
        if thread and value.get("thread_id") != thread:
            continue
        if kind and value.get("kind") != kind:
            continue
        if act_id and act_id not in {value.get("act_id"), value.get("reply_to")}:
            continue
        if contains and not _contains(value, contains):
            continue
        rows.append(dict(value))
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("WEAVEX_DEBUG_URL", DEFAULT_URL),
        help="live debug server base URL",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    commands.add_parser("pending-human")

    for name in ("acts", "traces"):
        command = commands.add_parser(name)
        command.add_argument("--actor", default="")
        command.add_argument("--thread", default="")
        command.add_argument("--kind", default="")
        command.add_argument("--act", default="")
        command.add_argument("--contains", default="")
        command.add_argument("--limit", type=int, default=20)

    questions = commands.add_parser("questions")
    questions.add_argument("--thread", default="")
    questions.add_argument("--contains", default="")
    questions.add_argument("--open", action="store_true")
    questions.add_argument("--limit", type=int, default=50)

    actor_view = commands.add_parser("actor-view")
    actor_view.add_argument("--actor", required=True)
    actor_view.add_argument("--thread", required=True)

    search = commands.add_parser("search")
    search.add_argument("term")
    search.add_argument(
        "--scope",
        choices=("state", "acts", "questions", "traces", "locals", "world", "contexts"),
        default="state",
    )
    search.add_argument("--limit", type=int, default=100)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "actor-view":
        result = _get(
            args.url,
            "/api/actor-view",
            actor_id=args.actor,
            thread_id=args.thread,
        )
    else:
        state = _state(args.url)
        if args.command == "summary":
            result = summarize_state(state)
        elif args.command == "pending-human":
            result = pending_human_acts(state)
        elif args.command in {"acts", "traces"}:
            values = (
                flatten_acts(state)
                if args.command == "acts"
                else state.get("traces", [])
            )
            result = _limited(
                _filter_rows(
                    values,
                    actor=args.actor,
                    thread=args.thread,
                    kind=args.kind,
                    act_id=args.act,
                    contains=args.contains,
                ),
                args.limit,
            )
        elif args.command == "questions":
            rows = question_rows(state)
            if args.open:
                rows = [item for item in rows if item["open"]]
            result = _limited(
                _filter_rows(
                    rows,
                    thread=args.thread,
                    contains=args.contains,
                ),
                args.limit,
            )
        else:
            result = search_hits(
                _scope(state, args.scope),
                args.term,
                limit=args.limit,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
