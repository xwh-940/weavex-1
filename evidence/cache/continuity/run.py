"""Bounded real-runtime validation; only hashes of private transport are logged."""

import asyncio
import argparse
import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from openai import AsyncOpenAI

from weavex.actor import ActorSpec
from weavex.capabilities import DEFAULT_BROKER_CAPABILITIES
from weavex.defaults import build_default_runtime
from weavex.initialization import InitialActor
from weavex.prompts import AGENT_INSTRUCTIONS, BROKER_INSTRUCTIONS
from weavex.protocol import SemanticHandle, question_payload
from weavex.provider import OpenAICompatibleModel
from weavex.world import SemanticWorld, ObjectAddress

OUT = Path(__file__).resolve().parent


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ResponseMetadata:
    def __init__(self, completions):
        self.completions = completions
        self.latest = {}

    async def create(self, **kwargs):
        self.latest = {}
        response = await self.completions.create(**kwargs)
        self.latest = {"finish_reason": response.choices[0].finish_reason,
                       "tool_calls": len(response.choices[0].message.tool_calls or [])}
        return response


class MeasuredModel(OpenAICompatibleModel):
    def __init__(self, client, max_tokens):
        self.response_metadata = ResponseMetadata(client.chat.completions)
        wrapped = SimpleNamespace(chat=SimpleNamespace(completions=self.response_metadata))
        super().__init__(wrapped, model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), max_tokens=max_tokens)
        self.previous = {}
        self.rows = []

    async def decide(self, value):
        messages = self._provider_messages(value)
        hashes = [fingerprint(item) for item in messages]
        old = self.previous.get(value.actor.actor_id)
        row = {"actor_id": value.actor.actor_id, "activation_id": value.activation_id,
               "message_count": len(hashes), "tools_hash": fingerprint(self.protocol_tools(value)),
               "previous_messages_unchanged": None if old is None else hashes[:len(old)] == old}
        self.previous[value.actor.actor_id] = hashes
        del messages
        started = time.monotonic()
        try:
            result = await super().decide(value)
            row["tool"] = result.name
            row["ok"] = True
            return result
        except Exception as exc:
            row.update(ok=False, error_type=type(exc).__name__)
            raise
        finally:
            row["seconds"] = time.monotonic() - started
            row["usage"] = dict(self._provider_usage.get((value.actor.actor_id, value.activation_id), {}))
            row["response_metadata"] = dict(self.response_metadata.latest)
            self.rows.append(row)
            with (OUT / "requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")


async def main(args):
    global OUT
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "requests.jsonl").exists():
        raise RuntimeError("this run already has observations")
    world = SemanticWorld()
    for index, code in enumerate(("ALPHA-417", "BETA-926"), start=1):
        world.add_object(f"起点 {index}，登记编号在直接引用的记录中。", object_id=f"entry-{index}")
        world.add_object(f"登记编号：{code}", object_id=f"record-{index}")
        world.add_reference(f"entry-{index}", ObjectAddress(f"record-{index}"), meaning="登记编号")
    declarations = [InitialActor(ActorSpec("broker", "broker", BROKER_INSTRUCTIONS,
                        DEFAULT_BROKER_CAPABILITIES, initial_member_ids=("agent-1", "agent-2")),
                        question_payload("报告当前协调入口已经就绪。"))]
    for index in (1, 2):
        declarations.append(InitialActor(ActorSpec(f"agent-{index}", "agent", AGENT_INSTRUCTIONS,
                    frozenset({"world_references_from"})),
                    question_payload("给出这个起点登记的完整编号。", semantic_addresses=(SemanticHandle("object", f"entry-{index}"),)),
                    {"world_ops": 2}))
    client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), timeout=60, max_retries=0)
    model = MeasuredModel(client, args.max_tokens)
    (OUT / "config.json").write_text(json.dumps({"model": model.model, "max_tokens": model.max_tokens,
        "request_timeout": 60, "max_activations": 20, "max_seconds": 480}), encoding="utf-8")
    runtime = build_default_runtime(model, world=world, initial_actors=tuple(declarations))
    handled, error, roots = 0, "", []
    started = time.monotonic()
    phases = [None, ("分别给出两个起点登记的完整编号，按起点列出。", True),
              ("刚才两个起点的编号是否相同？列出两个编号作为依据。", False),
              ("给出刚才第一个起点的完整编号。", False)]
    try:
        for phase in phases:
            if phase is not None:
                root = runtime.inject_human(phase[0], semantic_addresses=tuple(
                    SemanticHandle("object", f"entry-{i}") for i in (1, 2)) if phase[1] else ())
                roots.append(root)
            while runtime.queue:
                if handled >= 20 or time.monotonic() - started > 480:
                    raise RuntimeError("external observation limit")
                await runtime.step()
                handled += 1
                print(json.dumps({"handled": handled, "queue": len(runtime.queue), "actors": len(runtime.actors)}), flush=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.close()
    snapshot = runtime.snapshot()
    with gzip.open(OUT / "snapshot.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False)
    summary = {"error": error, "handled": handled, "actors": len(runtime.actors),
               "requests": len(model.rows), "provider_usage": snapshot["provider_usage"],
               "schema_variants_by_actor": {a: len({r["tools_hash"] for r in model.rows if r["actor_id"] == a}) for a in runtime.actors},
               "changed_history_prefixes": sum(r["previous_messages_unchanged"] is False for r in model.rows),
               "human_answers": [[dict(a.payload) for a in runtime.inquiry.direct_answers(q)] for q in roots],
               "open_responsibilities": {a: len(runtime.answer_responsibilities(a)) for a in runtime.actors},
               "tool_failures": sum(t.kind in {"act.observation", "tool.observation"} and t.payload.get("ok") is False for t in runtime.traces)}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--max-tokens", type=int, default=2048)
    asyncio.run(main(parser.parse_args()))
