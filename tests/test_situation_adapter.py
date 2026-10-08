"""Preparation must expose real affordances without substituting model choices."""

from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from weavex.arrival import LocalWorldDelta
from weavex.execution import ToolProposal
from weavex.protocol import Decision, SemanticHandle
from weavex.provider import ScriptedModel
from weavex.situation import situated_input_data
from weavex.situation_adapter import (
    BrokerPosition,
    ChatTranslator,
    InitialObservation,
    InitialReference,
    SituationCard,
    SituationSpec,
    audit_situation,
    build_situated_runtime,
    prepare_situation,
    adapt_situation,
)


@pytest.mark.asyncio
async def test_raw_construction_rehearses_revised_contexts_before_accepting(spec):
    spec = replace(
        spec,
        brokers=(
            BrokerPosition("measurements", ("research",), "取得边界解释"),
            BrokerPosition("application", ("research", "user"), "取得可用性解释"),
        ),
    )
    calls = []

    async def generate(prompt, value):
        calls.append(value)
        if "received_input" in value:
            if "phase" in value:
                return {
                    "current": {"understanding": "本位置负责当前问题"},
                    "scenarios": [{"next_action": "表达尚未解决的疑问"}],
                }
            return {
                "scenarios": [
                    "一项与当前问题相关的认识已经形成，但其适用性尚待判断。",
                    "收到针对当前认识的新说明。",
                ]
            }
        if "draft" in value:
            number = value["audit_round"]["number"]
            assert len(value["actor_rehearsal"]["actors"]) == 2
            return {
                "decision": "revise" if number == 1 else "accept",
                "spec": asdict(spec),
                "notes": ["两个语境分别有来源问题"],
                "changes": ["去掉草案中过大的统一群"],
                "assumptions": [],
            }
        return {"spec": asdict(replace(spec, brokers=None)), "assumptions": []}

    result = await adapt_situation(
        "给定材料需要边界与实际用途的解释", generate=generate
    )
    assert len(calls) == 15  # Construct, rehearse two endpoints, revise, repeat.
    assert len(result.actor_audit["rounds"]) == 2
    assert result.actor_audit["accepted"] is True
    assert result.actor_audit["stop_reason"] == "accepted"
    assert result.actor_audit["final_card_was_rehearsed"] is True
    runtime = runtime_for(result.card)
    assert runtime.entry_broker_id == "measurements"
    assert runtime.context_members("measurements") == {"research"}
    assert runtime.context_members("application") == {"research", "user"}
    assert result.changes
    assert "broker" not in runtime.actors


@pytest.mark.asyncio
async def test_invalid_construction_receives_one_bounded_mechanical_repair(spec):
    calls = []
    invalid = asdict(spec)
    invalid["materials"][0]["visible_to"] = ["broker"]

    async def generate(prompt, value):
        calls.append(value)
        if "repair_attempt" in value:
            assert value["repair_attempt"] == 1
            assert any(
                item["code"] == "visibility" for item in value["mechanical_findings"]
            )
            return {"spec": asdict(spec), "assumptions": ["保留私有边界"]}
        if "received_input" in value:
            if "phase" in value:
                return {"current": {"understanding": "按局部处境行动"}, "scenarios": []}
            return {"scenarios": ["出现与当前认识有关的新关系。"]}
        if "draft" in value:
            return {
                "decision": "accept",
                "spec": asdict(spec),
                "notes": ["修正后的卡已演练"],
                "changes": [],
            }
        return {"spec": invalid, "assumptions": ["初稿待检查"]}

    result = await adapt_situation(
        "从原始材料构建起点", generate=generate, max_audit_rounds=1
    )

    assert len(result.construction_repairs) == 1
    assert result.construction_repairs[0]["input_findings"][0]["code"] == "visibility"
    assert result.card.spec == spec
    assert result.assumptions == ("保留私有边界",)
    assert calls[0] == {"brief": "从原始材料构建起点"}


@pytest.mark.asyncio
async def test_invalid_construction_can_disable_repair(spec):
    invalid = asdict(spec)
    invalid["materials"][0]["visible_to"] = ["broker"]

    async def generate(_prompt, _value):
        return {"spec": invalid}

    with pytest.raises(ValueError, match="有限修正"):
        await adapt_situation(
            "从原始材料构建起点",
            generate=generate,
            max_construction_repairs=0,
        )


@pytest.mark.asyncio
async def test_default_audit_is_bounded_and_favors_response_route_endpoints(spec):
    extra = replace(
        spec.positions[0],
        actor_id="extra",
        question="检查额外材料",
        success_condition="形成说明",
    )
    expanded = replace(spec, positions=(*spec.positions, extra))
    rehearsed = []

    async def generate(_prompt, value):
        if "received_input" in value:
            rehearsed.append(value["actor_id"])
            if "phase" in value:
                return {"current": {"understanding": "按当前局部处理"}, "scenarios": []}
            return {"scenarios": ["出现一项与当前责任有关的认识。"]}
        if "draft" in value:
            return {
                "decision": "accept",
                "spec": asdict(expanded),
                "notes": [],
                "changes": [],
            }
        return {"spec": asdict(expanded), "assumptions": []}

    result = await adapt_situation(
        "构建可运行起点", generate=generate, max_audit_rounds=1
    )

    assert result.audited_actor_ids == ("research", "user", "extra")
    assert set(rehearsed) == {"research", "user", "extra"}
    assert "broker" not in rehearsed


@pytest.mark.asyncio
async def test_rehearsal_uses_actual_local_input_without_executing_proposed_actions(
    spec, monkeypatch
):
    import weavex.situation_adapter as adapter

    runtimes = []
    original = adapter.build_situated_runtime

    def capture(*args, **kwargs):
        runtime = original(*args, **kwargs)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(adapter, "build_situated_runtime", capture)
    seen = []

    async def design(prompt, view):
        seen.append(view)
        return {
            "scenarios": [
                "出现一项需要你理解的新材料。",
                "现有依据已覆盖你当前问题的结果条件。",
            ]
        }

    async def simulate(prompt, view):
        seen.append(view)
        assert "不执行工具" in prompt
        if view["phase"] == "current":
            assert "scenarios" not in view
        else:
            assert "initial_response" in view and len(view["scenarios"]) == 2
        assert (
            "available_tools" in view
            and "answer_responsibilities" in view["received_input"]
        )
        return {
            "current": {"next_action": "world_add_object: 演练中的对象"},
            "scenarios": [],
        }

    async def review(prompt, view):
        assert len(view["actor_rehearsal"]["actors"]) == 3
        return {
            "notes": ["这次理解看起来基本一致，但仍保留判断"],
            "changes": [],
            "actor_findings": [],
        }

    checked, report = await adapter.audit_actor_understanding(
        SituationCard(spec),
        brief="演练原始需求",
        design=design,
        simulate=simulate,
        review=review,
    )
    assert checked["notes"] == ["这次理解看起来基本一致，但仍保留判断"]
    assert report["final_card_was_rehearsed"] is True
    assert report["stop_reason"] == "no_revision"
    assert (
        report["accepted"] is False
    )  # No edits alone does not mean understanding passed.
    assert len(report["actors"]) == 3
    for view in seen:
        if view["actor_id"] in {"research", "broker"}:
            assert "本位置用于边界筛选" not in json.dumps(view, ensure_ascii=False)
        assert (
            "brief" not in view
            and "draft" not in view
            and "mechanical_findings" not in view
        )
    assert not report["world_actions_executed"]
    assert len(runtimes[0].local_worlds.world.snapshot().objects) == len(spec.materials)
    assert not runtimes[0].local_worlds.world.snapshot().references


@pytest.mark.asyncio
@pytest.mark.parametrize("max_rounds", [1, 3])
async def test_audit_limit_returns_last_tested_card_and_keeps_unapplied_proposal(
    spec, max_rounds
):
    from copy import deepcopy
    from weavex.situation_adapter import audit_actor_understanding

    spec = replace(
        spec,
        positions=(replace(spec.positions[0], context="round-0"), spec.positions[1]),
    )
    beginnings = []
    reviews = []

    async def design(prompt, view):
        assert "previous_reviews" not in view and "audit_round" not in view
        return {"scenarios": ["材料仍不足以回答原问题。", "收到一项新的公开判断。"]}

    async def simulate(prompt, view):
        assert "previous_reviews" not in view and "audit_round" not in view
        if view["phase"] == "current":
            assert "initial_response" not in view
            text = view["received_input"]["arrival"]["payload"][
                "interpretation_context"
            ]["content"]
            beginnings.append(text.splitlines()[0])
        return {"current": {"understanding": "按本轮处境处理"}, "scenarios": []}

    async def review(prompt, view):
        number = view["audit_round"]["number"]
        reviews.append(number)
        assert len(view["audit_round"]["previous_reviews"]) == number - 1
        revised = deepcopy(view["draft"]["spec"])
        revised["positions"][0]["context"] = f"round-{number}"
        return {
            "decision": "revise",
            "spec": revised,
            "changes": [f"修改 {number}"],
            "remaining_issues": ["处境意义仍需再看一次"],
        }

    checked, report = await audit_actor_understanding(
        SituationCard(spec),
        brief="当前问题",
        design=design,
        simulate=simulate,
        review=review,
        actor_ids=("research",),
        max_rounds=max_rounds,
    )
    assert reviews == list(range(1, max_rounds + 1))
    assert beginnings == [f"round-{i}" for i in range(max_rounds)]
    assert report["accepted"] is False
    assert report["stop_reason"] == "round_limit"
    assert report["final_card_was_rehearsed"] is True
    assert checked["spec"]["positions"][0]["context"] == f"round-{max_rounds - 1}"
    assert (
        report["pending_revision"]["spec"]["positions"][0]["context"]
        == f"round-{max_rounds}"
    )
    assert checked["changes"] == [f"修改 {i}" for i in range(1, max_rounds)]
    assert report["rounds"][-1]["revision_applied"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accept", "uncertain"])
async def test_audit_can_stop_after_one_subjective_review_without_a_revision(
    spec, decision
):
    from weavex.situation_adapter import audit_actor_understanding

    calls = []

    async def design(prompt, view):
        return {"scenarios": ["现有材料已经充分。"]}

    async def simulate(prompt, view):
        return {"current": {"next_action": "直接回答"}, "scenarios": []}

    async def review(prompt, view):
        calls.append(view)
        return {"decision": decision, "notes": ["按本轮表现判断"], "changes": []}

    _, report = await audit_actor_understanding(
        SituationCard(spec),
        brief="当前问题",
        design=design,
        simulate=simulate,
        review=review,
        actor_ids=("research",),
    )
    assert len(calls) == 1
    assert report["accepted"] is (decision == "accept")
    assert report["stop_reason"] == (
        "accepted" if decision == "accept" else "no_revision"
    )


def test_initial_relations_observations_and_broker_only_start(spec):
    spec = replace(
        spec,
        response_routes=(),
        references=(
            InitialReference(
                "reference:context",
                "object:measurements",
                "object:use",
                ("research",),
                "两者的已有背景联系",
            ),
        ),
        observations=(
            InitialObservation("research", "object:measurements", "outgoing"),
        ),
    )
    with pytest.raises(ValueError, match="可读邻域"):
        runtime_for(SituationCard(spec))
    spec = replace(
        spec,
        materials=(
            replace(spec.materials[0], visible_to=("research", "user")),
            *spec.materials[1:],
        ),
    )
    runtime = runtime_for(SituationCard(spec))
    assert runtime.local_worlds.world.get_reference("reference:context")
    assert runtime.local_worlds.observations_for("research")[0].direction == "outgoing"
    seed = SituationSpec(
        "理解这份材料",
        (),
        (replace(spec.materials[0], visible_to=()),),
        brokers=(
            BrokerPosition(
                "entry", (), "理解所给地址中的材料", object_ids=("object:measurements",)
            ),
        ),
    )
    runtime = runtime_for(SituationCard(seed))
    assert set(runtime.actors) == {"entry"}
    assert runtime.queue[0].payload["interpretation_context"]["semantic_addresses"]


@pytest.fixture
def spec():
    path = Path(__file__).parents[1] / "examples" / "situation_adapter.json"
    return SituationSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))


def runtime_for(card):
    return build_situated_runtime(card, ScriptedModel(lambda _: Decision("stop")))


async def execute(host, name, arguments=None):
    result = await host.execute(ToolProposal(name, name, arguments or {}))
    assert result.observation.ok, result.observation.error
    return result.observation.value


@pytest.mark.asyncio
async def test_local_translation_preserves_originals_and_private_boundaries(spec):
    seen = []

    async def translate(prompt, facts):
        seen.append(json.dumps(facts, ensure_ascii=False))
        return {
            "understanding": "这是待验证的解释",
            "uncertainties": ["实际用途的反馈尚未形成"],
        }

    card = await prepare_situation(spec, translate=translate)
    assert len(seen) == 2
    assert "object:private-purpose" not in seen[0]
    assert "本位置用于边界筛选" not in seen[0]
    assert "本位置用于边界筛选" in seen[1]
    assert SituationCard.from_dict(json.loads(json.dumps(card.to_dict()))) == card
    runtime = runtime_for(card)
    roots = {act.recipient: act for act in runtime.queue}
    assert roots["research"].payload["raw_question"] == spec.positions[0].question
    assert roots["research"].payload["intent"] == spec.positions[0].success_condition
    assert "可能有误" in roots["research"].payload["interpretation_context"]["content"]
    assert (
        runtime.local_worlds.world.get_object("object:measurements").content
        == spec.materials[0].content
    )
    for actor_id in ("research", "broker"):
        host = runtime.turn_host(runtime.actors[actor_id], roots[actor_id])
        value = await execute(host, "receive_input")
        assert "本位置用于边界筛选" not in json.dumps(value, ensure_ascii=False)
    assert not runtime.local_worlds.world.snapshot().references
    assert len(runtime.local_worlds.world.snapshot().objects) == len(spec.materials)
    assert {
        o.address.semantic_id for o in runtime.local_worlds.observations_for("user")
    } == {"object:use"}
    assert not runtime.local_worlds.observations_for("research")


@pytest.mark.asyncio
async def test_static_card_needs_no_or_activity_or_dynamic_path(spec):
    spec = replace(
        spec,
        response_routes=(),
        positions=(spec.positions[0],),
        materials=(spec.materials[0],),
    )
    card = await prepare_situation(spec)
    assert not card.interpretations
    assert [f.code for f in audit_situation(card)] == ["no_dynamic_route"]
    runtime = runtime_for(card)
    root = next(a for a in runtime.queue if a.recipient == "research")
    host = runtime.turn_host(runtime.actors["research"], root)
    await execute(host, "receive_input")
    await execute(
        host,
        "answer_question",
        {
            "question_id": root.act_id,
            "answer": "记录说明基准为10，用途条件未知。",
            "semantic_addresses": [],
            "grounding_inputs": [],
        },
    )
    assert not runtime.answer_responsibilities("research")
    assert runtime.actors["research"].budget_for(root.act_id)["world_ops"] == 12
    assert not runtime.local_worlds.observations_for("research")


@pytest.mark.parametrize(
    "fault,code",
    [
        ("surface", "route_surface"),
        ("visibility", "route_surface"),
        ("responder", "route_actors"),
        ("question", "responsibility"),
        ("budget", "route_budget"),
        ("relevance", "route_relevance"),
    ],
)
def test_disabled_paths_rejected_before_initialization(spec, fault, code):
    route = spec.response_routes[0]
    if fault == "surface":
        spec = replace(spec, response_routes=(replace(route, surface="absent"),))
    elif fault == "visibility":
        spec = replace(
            spec,
            materials=(
                spec.materials[0],
                replace(spec.materials[1], visible_to=("research",)),
                spec.materials[2],
            ),
        )
    elif fault == "responder":
        spec = replace(spec, response_routes=(replace(route, responder="absent"),))
    elif fault == "question":
        spec = replace(
            spec, positions=(spec.positions[0], replace(spec.positions[1], question=""))
        )
    elif fault == "budget":
        spec = replace(
            spec, positions=(replace(spec.positions[0], world_ops=2), spec.positions[1])
        )
    else:
        spec = replace(spec, response_routes=(replace(route, relevance=""),))
    card = SituationCard(spec)
    assert code in {f.code for f in audit_situation(card) if f.level == "error"}
    with pytest.raises(ValueError):
        runtime_for(card)


@pytest.mark.asyncio
async def test_translation_cannot_install_topology_and_failure_does_not_retry(spec):
    calls = []

    async def bad_translation(prompt, facts):
        calls.append(facts)
        return {"understanding": "test", "uncertainties": [], "response_routes": []}

    with pytest.raises(ValueError, match="only understanding"):
        await prepare_situation(spec, translate=bad_translation)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_dynamic_round_trip_and_answer_reopening_use_actual_host_tools(spec):
    runtime = runtime_for(SituationCard(spec))
    roots = {act.recipient: act for act in runtime.queue}
    research = runtime.turn_host(runtime.actors["research"], roots["research"])
    user = runtime.turn_host(runtime.actors["user"], roots["user"])
    await execute(research, "receive_input")
    await execute(user, "receive_input")
    runtime.queue.clear()  # Test driver controls activations; no model choice is claimed.
    q = roots["research"].act_id
    candidate = await execute(
        research,
        "world_add_object",
        {"question_id": q, "content": "候选：只计入大于基准的值。"},
    )
    u = candidate["object_id"]
    observed = await execute(
        research,
        "world_references_to",
        {"question_id": q, "address": {"kind": "object", "id": u}},
    )
    await execute(
        research,
        "world_add_reference",
        {
            "question_id": q,
            "source_object_id": u,
            "target": {"kind": "object", "id": "object:use"},
            "meaning": "实际使用的候选理解",
        },
    )
    assert len(runtime.queue) == 1
    delta = runtime.queue[0]
    assert isinstance(delta, LocalWorldDelta) and delta.recipient == "user"
    await runtime.step()
    assert SemanticHandle("object", u) in runtime.local_worlds.handles_for_actor("user")
    user = runtime.turn_host(runtime.actors["user"], delta)
    await execute(user, "receive_input")
    await execute(
        research,
        "answer_question",
        {
            "question_id": q,
            "answer": "当前暂定候选，适用性仍有限。",
            "semantic_addresses": [],
            "grounding_inputs": [
                {"kind": "observation", "id": observed["observation"]["observation_id"]}
            ],
        },
    )
    assert not runtime.answer_responsibilities("research")
    v = await execute(
        user,
        "world_add_object",
        {
            "question_id": roots["user"].act_id,
            "content": "实际用途需要包含等于基准的值，候选需要修正。",
        },
    )
    runtime.queue.clear()
    await execute(
        user,
        "world_add_reference",
        {
            "question_id": roots["user"].act_id,
            "source_object_id": u,
            "target": {"kind": "object", "id": v["object_id"]},
            "meaning": "反向关联不会命中 U 的入向观察",
        },
    )
    assert not runtime.queue
    await execute(
        user,
        "world_add_reference",
        {
            "question_id": roots["user"].act_id,
            "source_object_id": v["object_id"],
            "target": {"kind": "object", "id": u},
            "meaning": "使用反应",
        },
    )
    assert len(runtime.queue) == 1 and runtime.queue[0].recipient == "research"
    returned = runtime.queue[0]
    await runtime.step()
    assert roots["research"] in runtime.answer_responsibilities("research")
    value = situated_input_data(
        runtime.build_situated_input(runtime.actors["research"], returned)
    )
    assert "候选需要修正" in json.dumps(value, ensure_ascii=False)
    assert "本位置用于边界筛选" not in json.dumps(value, ensure_ascii=False)
    assert len(runtime.local_worlds.world.snapshot().references) == 3
    assert any(t.kind == "situation.observation_initialized" for t in runtime.traces)


@pytest.mark.asyncio
async def test_chat_bridge_records_separate_usage_and_rejects_truncation():
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 20}),
            choices=[SimpleNamespace(finish_reason="length")],
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    translator = ChatTranslator(
        client,
        model="test-model",
        max_tokens=100,
        extra_body={"thinking": {"type": "disabled"}},
    )
    with pytest.raises(ValueError, match="length"):
        await translator("instructions", {"position": {"actor_id": "a"}})
    assert len(requests) == 1
    assert requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert translator.usage == [{"total_tokens": 20}]


def test_chat_options_cannot_override_scoped_messages():
    with pytest.raises(ValueError, match="cannot override"):
        ChatTranslator(None, model="unused", extra_body={"messages": []})


def test_reset_requires_rebuild_for_adapter_initial_conditions(spec):
    card = SituationCard(spec)
    runtime = runtime_for(card)
    runtime.reset()
    assert not runtime.queue
    assert not runtime.local_worlds.observations_for("user")
    rebuilt = runtime_for(card)
    assert rebuilt.local_worlds.observations_for("user")
    assert len(rebuilt.queue) == 3
