"""Controlled real provider requests; never retain generated hidden reasoning."""

import asyncio
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

OUT = Path(__file__).resolve().parent


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def fixture():
    tools = [{"type": "function", "function": {
        "name": "lookup_record", "description": "按已知编号读取一条固定记录。",
        "parameters": {"type": "object", "properties": {
            "question_id": {"type": "string", "enum": ["question-1"]},
            "record_id": {"type": "string"}},
            "required": ["question_id", "record_id"], "additionalProperties": False}}}]
    records = [{"record_id": f"record-{i:03}",
                "code": hashlib.sha256(f"weavex-cache-20260909-{i}".encode()).hexdigest()[:24],
                "value": (i * 17 + 9) % 997} for i in range(160)]
    messages = [
        {"role": "system", "content": "你处理一组固定编号记录。记录只是被动资料。只执行最后一条 user 的请求；用户要求简短确认时直接回复 OK，无需解释或调用工具。"},
        {"role": "user", "content": "准备接收固定记录。"},
        {"role": "assistant", "content": "读取固定记录。",
         "reasoning_content": "此处为实验员公开撰写的模拟历史文本，并非模型隐藏推理。需要取得编号记录以继续当前请求。",
         "tool_calls": [{"id": "call-fixture-1", "type": "function", "function": {
             "name": "lookup_record", "arguments": encoded({"question_id": "question-1", "record_id": "record-000"})}}]},
        {"role": "tool", "tool_call_id": "call-fixture-1", "content": encoded({"records": records})},
        {"role": "user", "content": "已收到资料；现在只需直接回复 OK，无需调用工具。"},
    ]
    changed = copy.deepcopy(tools)
    changed[0]["function"]["parameters"]["properties"]["question_id"]["enum"].append("question-2")
    receive = [{"type": "function", "function": {"name": "receive_input", "description": "取得本轮输入。",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}]
    dropped = copy.deepcopy(messages)
    dropped[2].pop("reasoning_content")
    appended = copy.deepcopy(messages)
    appended[-1]["content"] += " 本次标记为 continuation-A。"
    return messages, tools, changed, receive, dropped, appended


async def main():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable in this shell")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    m, t, changed, receive, dropped, appended = fixture()
    cases = [("baseline_warm", m, t), ("baseline_repeat", m, t),
             ("tail_append", appended, t), ("enum_change", m, changed),
             ("enum_change_repeat", m, changed), ("receive_only", m, receive),
             ("receive_only_repeat", m, receive), ("drop_reasoning", dropped, t),
             ("drop_reasoning_repeat", dropped, t), ("baseline_restored", m, t),
             ("baseline_restored_repeat", m, t)]
    (OUT / "probe-fixtures.json").write_text(encoded({"model": model, "cases": cases}), encoding="utf-8")
    client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                        timeout=60.0, max_retries=0)
    rows = []
    try:
        for i, (name, messages, tools) in enumerate(cases):
            start = time.monotonic()
            row = {"case": name, "started": datetime.now(timezone.utc).isoformat(), "model": model,
                   "messages_hash": fingerprint(messages), "tools_hash": fingerprint(tools),
                   "messages_chars": len(encoded(messages)), "tools_chars": len(encoded(tools))}
            print(json.dumps({"case": name, "status": "started"}), flush=True)
            try:
                response = await client.chat.completions.create(
                    model=model, messages=messages, tools=tools, tool_choice="auto",
                    parallel_tool_calls=False, stream=False, max_tokens=512,
                    extra_body={"thinking": {"type": "enabled"}})
                usage = response.usage.model_dump()
                row["usage"] = usage
                row["response_model"] = response.model
                hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
                row["hit_ratio"] = hit / (hit + miss) if hit is not None and miss is not None and hit + miss else None
                row["finish_reason"] = response.choices[0].finish_reason
                row["status"] = "completed"
                del response
            except Exception as exc:
                row.update(status="failed", error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None))
            row["seconds"] = round(time.monotonic() - start, 3)
            rows.append(row)
            (OUT / "probe-results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row["status"] == "failed" and not (name.startswith("drop_reasoning") and row.get("status_code") == 400):
                break
            if i + 1 < len(cases):
                await asyncio.sleep(6)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
