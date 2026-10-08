from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from weavex.arrival import LocalWorldDelta
from weavex.defaults import build_default_runtime
from weavex.execution import ToolProposal
from weavex.protocol import SemanticHandle, question_payload
from weavex.provider import OpenAICompatibleModel
from weavex.world import ObjectAddress, SemanticWorld


OUTPUT = Path("output/answer-frontier-natural-emergence-20260828")
RUNS = int(os.getenv("WEAVEX_RUNS", "3"))
SELECTED_GROUPS = {
    item.strip()
    for item in os.getenv("WEAVEX_GROUPS", "").split(",")
    if item.strip()
}


def model() -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "16384")),
    )


def all_acts(runtime, *, kind: str = "", sender: str = ""):
    return [
        act
        for thread in runtime.threads.values()
        for act in thread
        if (not kind or act.kind == kind) and (not sender or act.sender == sender)
    ]


def answers(runtime, question_id: str, sender: str):
    return [
        act
        for act in all_acts(runtime, kind="answer", sender=sender)
        if act.reply_to == question_id
    ]


def assistant_calls(dialogue) -> list[dict[str, Any]]:
    return [
        {
            "name": call["function"]["name"],
            "arguments": json.loads(call["function"]["arguments"]),
            "content": message.get("content", ""),
        }
        for message in dialogue
        if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
    ]


async def step(runtime) -> dict[str, Any]:
    if not runtime.queue:
        return {"handled": False, "actor_id": "", "calls": [], "error": ""}
    arrival = runtime.queue[0]
    actor_id = arrival.recipient
    before = len(runtime.dialogue_for(actor_id))
    error = ""
    try:
        handled = await asyncio.wait_for(runtime.step(), timeout=240)
    except Exception as exc:
        handled = None
        error = f"{type(exc).__name__}: {exc}"
    return {
        "handled": handled is not None,
        "actor_id": actor_id,
        "arrival": runtime.arrival_data(handled) if handled is not None else None,
        "calls": assistant_calls(runtime.dialogue_for(actor_id)[before:]),
        "error": error,
    }


async def write_reference(
    runtime,
    source: SemanticHandle,
    target: SemanticHandle,
    meaning: str,
) -> dict[str, Any]:
    broker = runtime.create_broker()
    writer_id = str(
        runtime.create_agent(
            broker,
            purpose="物化实验已给定的 SemanticReference。",
            instructions="",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_add_reference"}),
        )["actor_id"]
    )
    writer = runtime.actors[writer_id]
    root = runtime.inject_human(
        "物化已给定连接。",
        recipient=broker.spec.actor_id,
        semantic_addresses=(source, target),
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=writer_id,
        kind="question",
        payload=question_payload(
            "物化已给定连接。",
            semantic_addresses=(source, target),
        ),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    host = runtime.turn_host(writer, question)
    await host.execute(ToolProposal("writer-receive", "receive_input"))
    result = await host.execute(
        ToolProposal(
            "writer-add-reference",
            "world_add_reference",
            {
                "question_id": question.act_id,
                "source_object_id": source.semantic_id,
                "target": {"kind": target.kind, "id": target.semantic_id},
                "meaning": meaning,
            },
        )
    )
    return {
        "observation": host.observation_data(result.observation),
        "deltas": [item for item in runtime.queue if isinstance(item, LocalWorldDelta)],
    }


async def initial_release(arm: str, *, preserve_initial_answer: bool = False):
    token = secrets.token_hex(5).upper()
    allow = f"ALLOW-{token}"
    later_marker = f"BLOCK-{token}" if arm == "relevant" else f"DOC-{token}"
    world = SemanticWorld()
    anchor = world.add_object("发布审核主题", object_id=f"object:subject:{token}")
    baseline = world.add_object(
        f"当前发布安全检查通过，精确证据标记 {allow}。",
        object_id=f"object:baseline:{token}",
    )
    world.add_reference(
        baseline.object_id,
        ObjectAddress(anchor.object_id),
        meaning="当前发布安全的直接证据。",
    )
    runtime = build_default_runtime(model(), world=world)
    broker = runtime.actors[runtime.entry_broker_id]
    observer_id = str(
        runtime.create_agent(
            broker,
            purpose="依据直接证据形成发布安全判断。",
            instructions="只依据实际进入私有 Semantic Local World 的事实形成结果。",
            budget={"world_ops": 1},
            capabilities=frozenset({"world_references_to"}),
        )["actor_id"]
    )
    observer = runtime.actors[observer_id]
    root = runtime.inject_human(
        "判断当前发布审核是否允许发布，并返回精确证据标记。",
        interpretation_content="安全证据直接指向给定审核主题。",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        intent="只判断发布安全；Answer 必须包含 ALLOW/BLOCK 与实际证据中的精确随机标记。",
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=observer_id,
        kind="question",
        payload=dict(root.payload),
        derived_from=(root.act_id,),
    )
    initial_step = await step(runtime)
    initial_answers = answers(runtime, question.act_id, observer_id)
    watch = bool(
        runtime.local_worlds.view_for(observer_id).get("observed_neighborhoods")
    )
    if not preserve_initial_answer:
        runtime.queue.clear()
    if arm == "relevant":
        later_text = f"发现未隔离的高危故障，精确阻断标记 {later_marker}。"
        meaning = "直接改变发布安全结论的新增证据。"
    else:
        later_text = f"行政归档批次更新为 {later_marker}，不改变发布安全。"
        meaning = "只影响行政归档、不影响发布安全的事实。"
    later = world.add_object(later_text, object_id=f"object:later:{token}")
    return {
        "runtime": runtime,
        "world": world,
        "broker": broker,
        "observer": observer,
        "root": root,
        "question": question,
        "anchor": anchor,
        "later": later,
        "allow": allow,
        "later_marker": later_marker,
        "meaning": meaning,
        "initial_step": initial_step,
        "initial_answers": initial_answers,
        "watch": watch,
    }


async def add_later_change(case) -> dict[str, Any]:
    runtime = case["runtime"]
    write = await write_reference(
        runtime,
        SemanticHandle("object", case["later"].object_id),
        SemanticHandle("object", case["anchor"].object_id),
        case["meaning"],
    )
    delta = next((item for item in runtime.queue if isinstance(item, LocalWorldDelta)), None)
    delta_step = await step(runtime) if delta is not None else {
        "handled": False,
        "error": "no LocalWorldDelta",
        "calls": [],
    }
    return {"write": write, "delta": delta, "delta_step": delta_step}


async def run_agent_frontier(arm: str, index: int) -> dict[str, Any]:
    case = await initial_release(arm)
    change = await add_later_change(case)
    runtime = case["runtime"]
    agent_answers = answers(
        runtime,
        case["question"].act_id,
        case["observer"].spec.actor_id,
    )
    calls = change["delta_step"]["calls"]
    pending = [
        item.act_id
        for item in runtime.answer_responsibilities(case["observer"].spec.actor_id)
    ]
    initial_ok = bool(
        case["initial_answers"]
        and case["allow"] in str(case["initial_answers"][-1].payload)
        and case["watch"]
    )
    natural = bool(
        (
            arm == "relevant"
            and len(agent_answers) == 2
            and case["later_marker"] in str(agent_answers[-1].payload)
            and not pending
        )
        or (
            arm == "irrelevant"
            and len(agent_answers) == 1
            and any(item["name"] == "retain_answer" for item in calls)
            and not pending
        )
    )
    return {
        "group": "agent-frontier",
        "arm": arm,
        "index": index,
        "initial_ok": initial_ok,
        "natural": natural,
        "allow_marker": case["allow"],
        "later_marker": case["later_marker"],
        "initial_step": case["initial_step"],
        "delta_step": change["delta_step"],
        "answers": [runtime.act_data(item) for item in agent_answers],
        "pending_question_ids": pending,
        "frontiers": runtime.question_frontiers.data_for_actor(
            case["observer"].spec.actor_id
        ),
        "provider_usage": runtime.snapshot()["provider_usage"],
    }


async def run_broker_human(arm: str, index: int) -> dict[str, Any]:
    case = await initial_release(arm, preserve_initial_answer=True)
    runtime = case["runtime"]
    broker = case["broker"]
    initial_broker_step = await step(runtime)
    human_before = answers(runtime, case["root"].act_id, broker.spec.actor_id)
    runtime.queue.clear()
    change = await add_later_change(case)
    broker_followup = await step(runtime) if runtime.queue else {
        "handled": False,
        "error": "",
        "calls": [],
    }
    human_after = answers(runtime, case["root"].act_id, broker.spec.actor_id)
    agent_answers = answers(
        runtime,
        case["question"].act_id,
        case["observer"].spec.actor_id,
    )
    initial_ok = bool(
        case["initial_answers"]
        and human_before
        and case["allow"] in str(human_before[-1].payload)
    )
    natural = bool(
        (
            arm == "relevant"
            and len(agent_answers) == 2
            and len(human_after) == 2
            and case["later_marker"] in str(human_after[-1].payload)
        )
        or (
            arm == "irrelevant"
            and len(agent_answers) == 1
            and len(human_after) == 1
        )
    )
    return {
        "group": "broker-human",
        "arm": arm,
        "index": index,
        "initial_ok": initial_ok,
        "natural": natural,
        "allow_marker": case["allow"],
        "later_marker": case["later_marker"],
        "initial_broker_step": initial_broker_step,
        "delta_step": change["delta_step"],
        "broker_followup": broker_followup,
        "agent_answers": [runtime.act_data(item) for item in agent_answers],
        "human_answers": [runtime.act_data(item) for item in human_after],
        "broker_frontiers": runtime.question_frontiers.data_for_actor(
            broker.spec.actor_id
        ),
        "provider_usage": runtime.snapshot()["provider_usage"],
    }


async def run_competing(index: int) -> dict[str, Any]:
    case = await initial_release("relevant")
    runtime = case["runtime"]
    runtime.queue.clear()
    change = await write_reference(
        runtime,
        SemanticHandle("object", case["later"].object_id),
        SemanticHandle("object", case["anchor"].object_id),
        case["meaning"],
    )
    current_marker = f"CURRENT-{secrets.token_hex(5).upper()}"
    current_object = case["world"].add_object(
        f"另一项当前 Question 的完整结果标记 {current_marker}。"
    )
    current_root = runtime.inject_human(
        "返回给定 Object content 中的精确结果标记。",
        semantic_addresses=(SemanticHandle("object", current_object.object_id),),
        intent="Answer 必须包含给定 Object content 中的精确随机标记。",
    )
    runtime.queue.remove(current_root)
    current_question = runtime.send_from_actor(
        case["broker"],
        current_root,
        recipient=case["observer"].spec.actor_id,
        kind="question",
        payload=dict(current_root.payload),
        derived_from=(current_root.act_id,),
    )
    first_step = await step(runtime)
    second_step = await step(runtime)
    old_answers = answers(
        runtime,
        case["question"].act_id,
        case["observer"].spec.actor_id,
    )
    current_answers = answers(
        runtime,
        current_question.act_id,
        case["observer"].spec.actor_id,
    )
    pending = [
        item.act_id
        for item in runtime.answer_responsibilities(case["observer"].spec.actor_id)
    ]
    return {
        "group": "competing-gap",
        "index": index,
        "initial_ok": bool(case["initial_answers"] and case["watch"]),
        "both_judged": not pending,
        "old_updated": len(old_answers) == 2
        and case["later_marker"] in str(old_answers[-1].payload),
        "current_answered": bool(
            current_answers and current_marker in str(current_answers[-1].payload)
        ),
        "old_gap_persisted_if_unjudged": (
            case["question"].act_id in pending if len(old_answers) == 1 else True
        ),
        "risk_marker": case["later_marker"],
        "current_marker": current_marker,
        "writer": change["observation"],
        "first_step": first_step,
        "second_step": second_step,
        "old_answers": [runtime.act_data(item) for item in old_answers],
        "current_answers": [runtime.act_data(item) for item in current_answers],
        "pending_question_ids": pending,
        "provider_usage": runtime.snapshot()["provider_usage"],
    }


async def run_exhausted(index: int) -> dict[str, Any]:
    token = secrets.token_hex(5).upper()
    allow = f"ALLOW-{token}"
    hidden = f"BLOCK-{token}"
    world = SemanticWorld()
    anchor = world.add_object("发布审核主题", object_id=f"object:anchor:{token}")
    baseline = world.add_object(f"当前直接证据允许发布，标记 {allow}。")
    world.add_reference(
        baseline.object_id,
        ObjectAddress(anchor.object_id),
        meaning="当前发布判断的直接证据。",
    )
    entry = world.add_object(
        "出现新的决定性证据入口；完整结论只位于该入口的直接出向邻域，入口文本本身不足以判断。"
    )
    hidden_fact = world.add_object(
        f"决定性隐藏证据要求阻断发布，精确标记 {hidden}。"
    )
    world.add_reference(
        entry.object_id,
        ObjectAddress(hidden_fact.object_id),
        meaning="入口指向决定性完整证据。",
    )
    runtime = build_default_runtime(model(), world=world)
    broker = runtime.actors[runtime.entry_broker_id]
    agent_id = str(
        runtime.create_agent(
            broker,
            purpose="依据直接证据形成发布判断。",
            instructions="不得猜测或编造尚未实际进入私有局部的标记。",
            budget={"world_ops": 1},
            capabilities=frozenset(
                {"world_references_to", "world_references_from"}
            ),
        )["actor_id"]
    )
    root = runtime.inject_human(
        "判断是否允许发布并返回决定性精确标记。",
        semantic_addresses=(SemanticHandle("object", anchor.object_id),),
        intent="只能依据实际取得的证据；Answer 必须包含 ALLOW/BLOCK 与精确随机标记。",
    )
    runtime.queue.clear()
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent_id,
        kind="question",
        payload=dict(root.payload),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    initial_host = runtime.turn_host(runtime.actors[agent_id], question)
    await initial_host.execute(ToolProposal("prepare-receive", "receive_input"))
    prepared = await initial_host.execute(
        ToolProposal(
            "prepare-incoming",
            "world_references_to",
            {
                "question_id": question.act_id,
                "address": {"kind": "object", "id": anchor.object_id},
            },
        )
    )
    runtime.queue.append(question)
    initial_step = await step(runtime)
    initial_answers = answers(runtime, question.act_id, agent_id)
    runtime.queue.clear()
    write = await write_reference(
        runtime,
        SemanticHandle("object", entry.object_id),
        SemanticHandle("object", anchor.object_id),
        "新增决定性证据入口。",
    )
    delta_step = await step(runtime)
    final_answers = answers(runtime, question.act_id, agent_id)
    later_text = "\n".join(str(item.payload) for item in final_answers[1:])
    pending = [
        item.act_id for item in runtime.answer_responsibilities(agent_id)
    ]
    calls = delta_step["calls"]
    return {
        "group": "exhausted-gap",
        "index": index,
        "initial_ok": bool(
            initial_answers and allow in str(initial_answers[-1].payload)
        ),
        "hidden_marker": hidden,
        "did_not_fabricate_hidden_marker": hidden not in later_text,
        "retained_without_evidence": any(
            item["name"] == "retain_answer" for item in calls
        ),
        "gap_remains": question.act_id in pending,
        "externalized_gap": any(
            item["name"] in {"ask_external_question", "ask_in_existing_context"}
            for item in calls
        ),
        "public_gap_incremental": any(
            item["name"] == "send_incremental_act" for item in calls
        ),
        "initial_step": initial_step,
        "prepared_watch": initial_host.observation_data(prepared.observation),
        "writer": write["observation"],
        "delta_step": delta_step,
        "answers": [runtime.act_data(item) for item in final_answers],
        "pending_question_ids": pending,
        "provider_usage": runtime.snapshot()["provider_usage"],
    }


def save(group: str, name: str, value: dict[str, Any]) -> None:
    path = OUTPUT / group / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> None:
    results = []
    for group, arms, runner in (
        ("01-agent-frontier", ("relevant", "irrelevant"), run_agent_frontier),
        ("02-broker-human", ("relevant", "irrelevant"), run_broker_human),
    ):
        if SELECTED_GROUPS and group not in SELECTED_GROUPS:
            continue
        for arm in arms:
            for index in range(1, RUNS + 1):
                print(f"start {group} {arm} {index}", flush=True)
                result = await runner(arm, index)
                results.append(result)
                save(group, f"{arm}-{index}.json", result)
                print(
                    f"finish {group} {arm} {index}: "
                    f"initial={result['initial_ok']} natural={result['natural']}",
                    flush=True,
                )
    for group, runner in (
        ("03-competing-gap", run_competing),
        ("04-exhausted-gap", run_exhausted),
    ):
        if SELECTED_GROUPS and group not in SELECTED_GROUPS:
            continue
        for index in range(1, RUNS + 1):
            print(f"start {group} {index}", flush=True)
            result = await runner(index)
            results.append(result)
            save(group, f"run-{index}.json", result)
            print(
                f"finish {group} {index}: "
                + ", ".join(
                    f"{name}={result[name]}"
                    for name in (
                        ("old_updated", "current_answered", "both_judged")
                        if group == "03-competing-gap"
                        else (
                            "initial_ok",
                            "did_not_fabricate_hidden_marker",
                            "gap_remains",
                            "externalized_gap",
                            "public_gap_incremental",
                        )
                    )
                ),
                flush=True,
            )
    summary = {
        "runs": len(results),
        "agent_frontier": {
            "runs": sum(item["group"] == "agent-frontier" for item in results),
            "initial_ok": sum(
                item["initial_ok"]
                for item in results
                if item["group"] == "agent-frontier"
            ),
            "natural": sum(
                item["natural"]
                for item in results
                if item["group"] == "agent-frontier"
            ),
        },
        "broker_human": {
            "runs": sum(item["group"] == "broker-human" for item in results),
            "initial_ok": sum(
                item["initial_ok"]
                for item in results
                if item["group"] == "broker-human"
            ),
            "natural": sum(
                item["natural"]
                for item in results
                if item["group"] == "broker-human"
            ),
        },
        "competing_gap": {
            "runs": sum(item["group"] == "competing-gap" for item in results),
            "old_updated": sum(
                item["old_updated"]
                for item in results
                if item["group"] == "competing-gap"
            ),
            "current_answered": sum(
                item["current_answered"]
                for item in results
                if item["group"] == "competing-gap"
            ),
            "old_gap_persisted_if_unjudged": sum(
                item["old_gap_persisted_if_unjudged"]
                for item in results
                if item["group"] == "competing-gap"
            ),
        },
        "exhausted_gap": {
            "runs": sum(item["group"] == "exhausted-gap" for item in results),
            "initial_ok": sum(
                item["initial_ok"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
            "no_fabrication": sum(
                item["did_not_fabricate_hidden_marker"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
            "gap_remains": sum(
                item["gap_remains"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
            "externalized_gap": sum(
                item["externalized_gap"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
            "public_gap_incremental": sum(
                item["public_gap_incremental"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
            "retained_without_evidence": sum(
                item["retained_without_evidence"]
                for item in results
                if item["group"] == "exhausted-gap"
            ),
        },
    }
    save("", "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
