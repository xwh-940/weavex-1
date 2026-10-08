"""Current-runtime example: scripted by default, opt-in live model execution.

The fixture declares the participants and injects evidence. In scripted mode the
decision policy is deliberately explicit; only the host effects are being shown.
Live mode uses the same fixture with the unchanged WeaveX runtime prompts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weavex.actor import ActorSpec  # noqa: E402
from weavex.capabilities import DEFAULT_BROKER_CAPABILITIES  # noqa: E402
from weavex.defaults import build_default_runtime  # noqa: E402
from weavex.execution import ToolProposal  # noqa: E402
from weavex.initialization import InitialActor  # noqa: E402
from weavex.prompts import AGENT_INSTRUCTIONS, BROKER_INSTRUCTIONS  # noqa: E402
from weavex.protocol import SemanticHandle  # noqa: E402
from weavex.provider import ScriptedModel  # noqa: E402
from weavex.world import ObjectAddress, SemanticWorld  # noqa: E402


class DemoDecisions:
    """A deterministic test double, not an LLM or an autonomous planner."""

    def __init__(self):
        self.sent = set()

    def __call__(self, value):
        def proposal(name, **arguments):
            return ToolProposal("", name, arguments)

        if not value.answer_responsibilities:
            return proposal("yield_turn")
        question = value.answer_responsibilities[0]
        qid = question.act_id
        if value.actor.role == "broker":
            acts = [*value.visible_acts]
            if hasattr(value.trigger, "kind") and value.trigger.kind == "answer":
                acts.append(value.trigger)
            answers = [
                act for act in acts if act.kind == "answer" and act.sender == "reviewer"
            ]
            if answers:
                latest = answers[-1]
                return proposal(
                    "answer_question",
                    question_id=qid,
                    answer=latest.payload["answer"],
                    semantic_addresses=[],
                    grounding_inputs=[{"kind": "act", "id": latest.act_id}],
                )
            if qid in self.sent:
                return proposal("yield_turn")
            self.sent.add(qid)
            return proposal(
                "send_question",
                question_id=qid,
                recipient="reviewer",
                source_act_ids=[qid],
                **dict(question.payload),
            )

        observations = value.local_world.get("observed_neighborhoods", [])
        if not observations:
            return proposal(
                "world_references_to",
                question_id=qid,
                address={"kind": "object", "id": "subject"},
            )
        inputs = [
            {"kind": "observation", "id": item["observation_id"]}
            for item in observations
        ]
        objects = value.local_world.get("objects", [])
        inputs.extend({"kind": "object", "id": item["object_id"]} for item in objects)
        frontier = value.situation.responsibility_frontiers[qid]
        blocked = any("BLOCK-DEMO" in item["content"] for item in objects)
        if frontier.get("latest_answer_id") and not blocked:
            return proposal("retain_answer", question_id=qid, grounding_inputs=inputs)
        answer = (
            "BLOCK-DEMO: new safety evidence blocks release."
            if blocked
            else "ALLOW-DEMO: available safety evidence permits release."
        )
        return proposal(
            "answer_question",
            question_id=qid,
            answer=answer,
            semantic_addresses=[],
            grounding_inputs=inputs,
        )


async def run_case(arm: str, model):
    world = SemanticWorld()
    world.add_object(
        "Release review: evidence points to this subject.", object_id="subject"
    )
    world.add_object(
        "Safety checks passed. Evidence marker ALLOW-DEMO.", object_id="baseline"
    )
    world.add_reference(
        "baseline", ObjectAddress("subject"), meaning="Current safety evidence."
    )
    declarations = (
        InitialActor(
            ActorSpec(
                "broker",
                "broker",
                BROKER_INSTRUCTIONS,
                DEFAULT_BROKER_CAPABILITIES,
                initial_member_ids=("reviewer",),
            )
        ),
        InitialActor(
            ActorSpec(
                "reviewer",
                "agent",
                AGENT_INSTRUCTIONS,
                frozenset({"world_references_to"}),
            ),
            budget={"world_ops": 2},
        ),
    )
    runtime = build_default_runtime(model, world=world, initial_actors=declarations)
    root = runtime.inject_human(
        "Determine whether this release is safe and include the exact evidence marker.",
        interpretation_content="Safety evidence points to the supplied subject. The reviewer can read it.",
        semantic_addresses=(SemanticHandle("object", "subject"),),
        intent="Judge release safety using available evidence. Report ALLOW or BLOCK with the evidence marker.",
    )
    error = ""
    initial_answers = []
    try:
        await runtime.run_until_idle(max_steps=20)
        initial_answers = list(runtime.inquiry.direct_answers(root))
        if not initial_answers:
            raise RuntimeError(
                "Initial user question has no answer; later evidence was not injected"
            )
        later_text = (
            "New unisolated critical failure. Evidence marker BLOCK-DEMO."
            if arm == "relevant"
            else "Administrative archive batch changed to DOC-DEMO; release safety is unchanged."
        )
        world.add_object(later_text, object_id="later")
        reference = world.add_reference(
            "later",
            ObjectAddress("subject"),
            meaning="New evidence for the release review.",
        )
        # This is an external fixture action, not a claimed model-created relation.
        runtime.publish_reference_change("external-fixture", reference.reference_id)
        await runtime.run_until_idle(max_steps=20)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    answers = list(runtime.inquiry.direct_answers(root))
    retained = [
        t
        for t in runtime.traces
        if t.kind == "model.decision" and t.payload.get("tool_name") == "retain_answer"
    ]
    pending = {
        actor: len(runtime.answer_responsibilities(actor)) for actor in runtime.actors
    }
    initial_valid = (
        len(initial_answers) == 1
        and "ALLOW-DEMO" in initial_answers[0].payload["answer"]
    )
    initial_preserved = (
        bool(initial_answers) and answers[: len(initial_answers)] == initial_answers
    )
    expected = (
        len(answers) == 2 and "BLOCK-DEMO" in answers[-1].payload["answer"]
        if arm == "relevant"
        else len(answers) == 1 and bool(retained)
    )
    return {
        "arm": arm,
        "error": error,
        "checks": {
            "initial_answer_valid": initial_valid,
            "original_answer_preserved": initial_preserved,
            "expected_update_or_retain": expected,
            "no_pending_responsibilities": not any(pending.values()),
        },
        "passed": not error
        and initial_valid
        and initial_preserved
        and expected
        and not any(pending.values()),
        "human_answers": [runtime.act_data(answer) for answer in answers],
        "private_retain_proposals": len(retained),
        "pending_responsibilities": pending,
        "snapshot": runtime.snapshot(),
    }


def source_fingerprint():
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/weavex").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".md"}
    }
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


async def main(args):
    client = None
    config = {
        "mode": "live" if args.live else "scripted",
        "arm": args.arm,
        "source_fingerprint": source_fingerprint(),
        "fixture": "declared broker/reviewer; external evidence injection; v1",
    }
    output = None
    if args.output:
        output = (ROOT / args.output).resolve()
        if not output.is_relative_to(ROOT / "output") or output == ROOT / "output":
            raise ValueError("--output must name a new subdirectory under output/")
        output.mkdir(parents=True, exist_ok=False)
    try:
        if args.live:
            from openai import AsyncOpenAI
            from weavex.provider import OpenAICompatibleModel

            api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
            model_name = os.environ.get("DEEPSEEK_MODEL", "").strip()
            if not api_key or not model_name:
                raise ValueError(
                    "Live mode requires DEEPSEEK_API_KEY and DEEPSEEK_MODEL"
                )
            limit = int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192"))
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                timeout=120,
                max_retries=0,
            )
            model = OpenAICompatibleModel(client, model=model_name, max_tokens=limit)
            config.update(
                model=model_name,
                max_tokens=limit,
                max_arrivals_per_phase=20,
                max_tool_rounds_per_arrival=12,
            )
        else:
            model = ScriptedModel(DemoDecisions())
        result = await run_case(args.arm, model)
        result["config"] = config
        if output:
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"{config['mode'].upper()} | {args.arm} | passed={result['passed']}")
        if not args.live:
            print(
                "Decisions are scripted; real Runtime, tool checks, dependency tracking and message delivery execute."
            )
        for answer in result["human_answers"]:
            print(
                f"{answer['act_id']} -> {answer['reply_to']}: {answer['payload']['answer']}"
            )
        print(f"Private retain proposals: {result['private_retain_proposals']}")
        print(json.dumps(result["checks"], indent=2))
        if result["error"]:
            print(result["error"], file=sys.stderr)
        return 0 if result["passed"] else 1
    finally:
        if client is not None:
            await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("relevant", "irrelevant"), default="relevant")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured model API; incurs provider usage",
    )
    parser.add_argument(
        "--output",
        help="Save a new run under output/; existing directories are never overwritten",
    )
    args = parser.parse_args()
    if args.live and not args.output:
        parser.error(
            "--live requires --output output/<new-run-name> to preserve results"
        )
    try:
        raise SystemExit(asyncio.run(main(args)))
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, f"{exc}\n")
