"""Opt-in, one-shot situation preparation outside the Actor execution loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from importlib.resources import files
from typing import Awaitable, Callable

from .actor import ActorSpec, validate_question_budget
from .capabilities import DEFAULT_AGENT_CAPABILITIES, DEFAULT_BROKER_CAPABILITIES
from .defaults import build_default_runtime
from .initialization import InitialActor
from .local_world import LocalWorlds, semantic_address
from .prompts import AGENT_INSTRUCTIONS, BROKER_INSTRUCTIONS
from .protocol import SemanticHandle, question_payload
from .world import SemanticWorld


@dataclass(frozen=True)
class Material:
    object_id: str
    content: str
    visible_to: tuple[str, ...]
    status: str = "source"
    basis: str = ""


@dataclass(frozen=True)
class Position:
    actor_id: str
    question: str
    success_condition: str
    world_ops: int = 16
    context: str = ""


@dataclass(frozen=True)
class BrokerPosition:
    actor_id: str
    member_ids: tuple[str, ...]
    question: str
    context: str = ""
    object_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InitialReference:
    reference_id: str
    source: str
    target: str
    visible_to: tuple[str, ...] = ()
    meaning: str = ""
    target_kind: str = "object"


@dataclass(frozen=True)
class InitialObservation:
    actor_id: str
    address: str
    direction: str = "incoming"
    kind: str = "object"


@dataclass(frozen=True)
class ResponseRoute:
    """An authorized opportunity, never a promise of a generated response."""

    publisher: str
    responder: str
    surface: str
    relevance: str


@dataclass(frozen=True)
class SituationSpec:
    public_question: str
    positions: tuple[Position, ...]
    materials: tuple[Material, ...] = ()
    response_routes: tuple[ResponseRoute, ...] = ()
    brokers: tuple[BrokerPosition, ...] | None = None
    references: tuple[InitialReference, ...] = ()
    observations: tuple[InitialObservation, ...] = ()

    @classmethod
    def from_dict(cls, value: dict) -> SituationSpec:
        """Reject unknown keys, including misspelled visibility declarations."""
        return cls(
            **{
                **value,
                "positions": tuple(Position(**item) for item in value["positions"]),
                "materials": tuple(
                    Material(**{**item, "visible_to": tuple(item["visible_to"])})
                    for item in value.get("materials", [])
                ),
                "response_routes": tuple(
                    ResponseRoute(**item) for item in value.get("response_routes", [])
                ),
                "brokers": None
                if value.get("brokers") is None
                else tuple(
                    BrokerPosition(
                        **{
                            **item,
                            "member_ids": tuple(item["member_ids"]),
                            "object_ids": tuple(item.get("object_ids", [])),
                        }
                    )
                    for item in value["brokers"]
                ),
                "references": tuple(
                    InitialReference(
                        **{**item, "visible_to": tuple(item.get("visible_to", []))}
                    )
                    for item in value.get("references", [])
                ),
                "observations": tuple(
                    InitialObservation(**item) for item in value.get("observations", [])
                ),
            }
        )


def _brokers(spec: SituationSpec) -> tuple[BrokerPosition, ...]:
    # Compatibility for the earlier hand-authored card, not a topology constraint.
    if spec.brokers is None:
        return (
            BrokerPosition(
                "broker",
                tuple(p.actor_id for p in spec.positions),
                spec.public_question,
            ),
        )
    return spec.brokers


def _initial_world(spec: SituationSpec) -> SemanticWorld:
    world = SemanticWorld()
    for material in spec.materials:
        content = material.content
        if material.status == "provisional":
            content = "初始化提出的暂定理解，可以修订：\n" + content
        elif material.status == "interface":
            content = "初始化建立的接触位置：\n" + content
        world.add_object(content, object_id=material.object_id)
    pending = list(spec.references)
    while pending:
        progress = []
        for ref in pending:
            if (
                ref.target_kind == "reference"
                and ref.target not in world.snapshot().references
            ):
                continue
            world.add_reference(
                ref.source,
                semantic_address(SemanticHandle(ref.target_kind, ref.target)),
                meaning=ref.meaning,
                reference_id=ref.reference_id,
            )
            progress.append(ref)
        if not progress:
            raise ValueError("初始 Reference 存在循环或找不到目标。")
        pending = [ref for ref in pending if ref not in progress]
    return world


def _initial_addresses(
    spec: SituationSpec, actor_id: str
) -> tuple[SemanticHandle, ...]:
    return tuple(
        [
            SemanticHandle("object", m.object_id)
            for m in spec.materials
            if actor_id in m.visible_to
        ]
        + [
            SemanticHandle("reference", r.reference_id)
            for r in spec.references
            if actor_id in r.visible_to
        ]
    )


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    detail: str


@dataclass(frozen=True)
class Interpretation:
    actor_id: str
    understanding: str
    uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True)
class SituationCard:
    spec: SituationSpec
    interpretations: tuple[Interpretation, ...] = ()

    def to_dict(self) -> dict:
        """Host-only artifact: includes private input and generated interpretations."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> SituationCard:
        return cls(
            **{
                **value,
                "spec": SituationSpec.from_dict(value["spec"]),
                "interpretations": tuple(
                    Interpretation(
                        **{
                            **item,
                            "uncertainties": tuple(item.get("uncertainties", [])),
                        }
                    )
                    for item in value.get("interpretations", [])
                ),
            }
        )


def audit_situation(card: SituationCard) -> tuple[Finding, ...]:
    """Check declared mechanics. This cannot prove semantic sufficiency."""
    spec = card.spec
    findings: list[Finding] = []

    def error(code: str, detail: str) -> None:
        findings.append(Finding("error", code, detail))

    def nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    if not nonblank(spec.public_question):
        error("public_question", "入口 Broker 需要调用方提供的公开问题。")
    positions = {p.actor_id: p for p in spec.positions}
    brokers = _brokers(spec)
    broker_ids = {b.actor_id for b in brokers}
    if not brokers or len(broker_ids) != len(brokers):
        error("brokers", "当前初始化接口需要至少一个 Broker，且身份不能重复。")
    for b in brokers:
        if (
            not nonblank(b.actor_id)
            or b.actor_id != b.actor_id.strip()
            or b.actor_id in {"human", "initialization"}
            or b.actor_id in positions
        ):
            error("broker_id", f"不可用的 Broker 身份：{b.actor_id!r}")
        if not nonblank(b.question):
            error("broker_question", f"{b.actor_id} 缺少公开来源问题。")
        if len(set(b.member_ids)) != len(b.member_ids) or any(
            m not in positions for m in b.member_ids
        ):
            error(
                "broker_members", f"{b.actor_id} 的成员必须是已声明的 Agent，不能重复。"
            )
    if len(positions) != len(spec.positions):
        error("positions", "Agent 身份不能重复。")
    for p in spec.positions:
        if (
            not nonblank(p.actor_id)
            or p.actor_id != p.actor_id.strip()
            or p.actor_id in broker_ids | {"human", "initialization"}
        ):
            error("actor_id", f"不可用的 Agent 身份：{p.actor_id!r}")
        if not nonblank(p.question) or not nonblank(p.success_condition):
            error("responsibility", f"{p.actor_id} 缺少自己的问题或结果条件。")
        try:
            validate_question_budget({"world_ops": p.world_ops})
        except ValueError as exc:
            error("budget", f"{p.actor_id}: {exc}")
    materials = {m.object_id: m for m in spec.materials}
    if len(materials) != len(spec.materials):
        error("material_id", "材料身份重复。")
    for m in spec.materials:
        if not nonblank(m.object_id) or not nonblank(m.content):
            error("material", "原材料需要非空身份和原文。")
        if any(a not in positions for a in m.visible_to):
            error("visibility", f"{m.object_id} 的初始可见对象必须是已声明的 Agent。")
        if m.status not in {"source", "provisional", "interface"}:
            error("material_status", f"未知材料状态：{m.status}")
    for b in brokers:
        if any(identity not in materials for identity in b.object_ids):
            error("broker_addresses", f"{b.actor_id} 持有的对象地址不存在。")
    for ref in spec.references:
        if any(a not in positions for a in ref.visible_to):
            error("reference_visibility", f"{ref.reference_id} 的可见位置不存在。")
    observed_ids = {obs.address for obs in spec.observations} | {
        r.surface for r in spec.response_routes
    }
    for m in spec.materials:
        if m.status == "interface" and m.object_id not in observed_ids:
            findings.append(
                Finding(
                    "warning",
                    "unobserved_interface",
                    f"接触位置 {m.object_id} 没有初始观察者；写入相关关系并不自动被其他位置遇到。请判断这是有意留给运行时，还是遗漏了接触条件。",
                )
            )
    try:
        local = LocalWorlds(_initial_world(spec))
        for p in spec.positions:
            local.include_addresses(p.actor_id, _initial_addresses(spec, p.actor_id))
            initial_handles = local.handles_for_actor(p.actor_id)
            for ref in spec.references:
                if (
                    SemanticHandle("object", ref.source) in initial_handles
                    or SemanticHandle(ref.target_kind, ref.target) in initial_handles
                ):
                    local.include_reference_neighborhood(p.actor_id, ref.reference_id)
            allowed_objects = {
                m.object_id for m in spec.materials if p.actor_id in m.visible_to
            }
            actual_objects = {
                h.semantic_id
                for h in local.handles_for_actor(p.actor_id)
                if h.kind == "object"
            }
            if not actual_objects <= allowed_objects:
                error(
                    "reference_exposure",
                    f"初始关系或可读邻域向 {p.actor_id} 暴露了未分配给它的材料：{sorted(actual_objects - allowed_objects)}",
                )
        for obs in spec.observations:
            if (
                obs.actor_id not in positions
                or obs.direction not in {"incoming", "outgoing"}
                or (obs.direction == "outgoing" and obs.kind != "object")
            ):
                error("observation", f"不可用的初始观察：{obs}")
                continue
            local.validate_addresses(
                obs.actor_id, (SemanticHandle(obs.kind, obs.address),)
            )
    except (ValueError, KeyError, PermissionError) as exc:
        error("initial_world", str(exc))
    seen_routes = set()
    for route in spec.response_routes:
        key = (route.publisher, route.responder, route.surface)
        if key in seen_routes:
            error("duplicate_route", f"重复反馈路径：{key}")
        seen_routes.add(key)
        source = positions.get(route.publisher)
        target = positions.get(route.responder)
        surface = materials.get(route.surface)
        if source is None or target is None or source == target:
            error("route_actors", f"{key} 需要两个不同的已声明位置。")
        if surface is None or not {route.publisher, route.responder} <= set(
            surface.visible_to
        ):
            error("route_surface", f"{key} 缺少双方均可见的接触面。")
        if not nonblank(route.relevance):
            error("route_relevance", f"{key} 缺少反馈为何可能影响结果的说明。")
        for position, minimum in ((source, 3), (target, 2)):
            if (
                position
                and type(position.world_ops) is int
                and position.world_ops < minimum
            ):
                error(
                    "route_budget",
                    f"{position.actor_id} 无法承担该路径至少 {minimum} 次 OR 操作。",
                )
    seen_interpretations = set()
    for item in card.interpretations:
        if item.actor_id not in positions or item.actor_id in seen_interpretations:
            error("interpretation_owner", f"解释位置未知或重复：{item.actor_id}")
        seen_interpretations.add(item.actor_id)
        if not isinstance(item.understanding, str) or len(item.understanding) > 1600:
            error("interpretation", f"{item.actor_id} 的解释须为至多 1600 字符的文本。")
        if len(item.uncertainties) > 4 or any(
            not nonblank(text) or len(text) > 300 for text in item.uncertainties
        ):
            error(
                "uncertainties",
                f"{item.actor_id} 最多四项、每项至多 300 字符的未知条件。",
            )
        for text in item.uncertainties:
            findings.append(
                Finding("warning", "model_uncertainty", f"{item.actor_id}: {text}")
            )
    if spec.response_routes or spec.observations:
        findings.append(
            Finding(
                "warning",
                "semantic_review",
                "接触、责任和返回机会可检查；用途差异、反馈价值和模型是否正确理解仍需实验判断。",
            )
        )
    else:
        findings.append(
            Finding(
                "info",
                "no_dynamic_route",
                "未声明初始动态观察；这不表示任务失败，也不要求补建。",
            )
        )
    return tuple(findings)


def _require_valid(card: SituationCard) -> None:
    errors = [f.detail for f in audit_situation(card) if f.level == "error"]
    if errors:
        raise ValueError("\n".join(errors))


def _local_facts(spec: SituationSpec, actor_id: str) -> dict:
    p = next(p for p in spec.positions if p.actor_id == actor_id)
    return {
        "position": asdict(p),
        "materials": [
            {
                "object_id": m.object_id,
                "content": m.content,
                "status": m.status,
                "basis": m.basis,
            }
            for m in spec.materials
            if actor_id in m.visible_to
        ],
        "environment": _environment_text(spec, actor_id),
    }


def _environment_text(spec: SituationSpec, actor_id: str) -> str:
    contexts = [b.actor_id for b in _brokers(spec) if actor_id in b.member_ids]
    lines = [
        f"已有外部问题语境：{', '.join(contexts) if contexts else '无'}。"
        "可向已有语境表达当前问题缺口，也可以建立新的外部语境。"
        "这些语境只有公开协调信息，不能读取各位置的私有材料，也不保证已有所需答案。"
    ]
    for obs in spec.observations:
        # Initial observation topology is public here; private material contents are not.
        if any(
            h.semantic_id == obs.address and h.kind == obs.kind
            for h in _initial_addresses(spec, actor_id)
        ):
            lines.append(
                f"已存在的接触条件：{obs.actor_id} 持续观察 {obs.kind} {obs.address} 的 {obs.direction} 邻域；直接改变该邻域的新增 Reference 会给它带来局部变化。"
            )
    lines.append(
        "某项认识 U 与接触面 S 建立 U→S，可被 S 的入向观察者遇到；针对 U 的反应 V 若表达为 V→U，可被 U 的入向观察者遇到。观察只针对直接邻域，地址本身不代表已观察。"
    )
    for r in spec.response_routes:
        if actor_id not in {r.publisher, r.responder}:
            continue
        lines.append(
            f"接触条件：{r.responder} 初始持续观察 {r.surface} 的入向邻域，并有自己的问题责任。"
            f"{r.publisher} 的某项认识 U 若以 U→{r.surface} 建立 Reference，"
            f"它会进入该观察者的局部世界。该接触面上的其他同向关联同样可见。"
            f"与结果的关系：{r.relevance}"
        )
        lines.append(
            f"返回条件：若 {r.responder} 形成针对 U 的反应 V，V→U 属于 U 的入向邻域；"
            f"{r.publisher} 读取 U 的入向邻域后才建立对后续 V→U 的持续观察。"
            f"这只影响 {r.publisher} 接收反馈，{r.responder} 接收 U→{r.surface} 的观察已预置，"
            f"不依赖 {r.publisher} 是否观察 U。U→V 不会命中 U 的入向观察。"
            f"U 尚未存在，适配器没有替 {r.publisher} 建立对 U 的观察。"
            "读取可以取得已经存在的反应；持续观察接收以后新增的直接关联。"
        )
    lines.append(
        "邻域到达只提供接触与处理机会，不保证回应或认可。"
        "新变化能否重开已回答的问题，取决于原回答的 grounding 是否依赖相应观察；"
        "尚未回答的问题仍保留责任。当前结果已经充分时可以直接回答。"
    )
    return "\n".join(lines)


Translator = Callable[[str, dict], Awaitable[dict]]


@dataclass(frozen=True)
class Adaptation:
    brief: str
    draft: dict
    card: SituationCard
    review_notes: tuple[str, ...]
    changes: tuple[str, ...]
    assumptions: tuple[str, ...]
    actor_audit: dict | None = None
    construction_repairs: tuple[dict, ...] = ()
    audited_actor_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


async def adapt_situation(
    brief: str,
    *,
    generate: Translator,
    review: Translator | None = None,
    simulate: Translator | None = None,
    max_audit_rounds: int = 3,
    max_construction_repairs: int = 1,
    max_audit_actors: int = 3,
) -> Adaptation:
    """Propose a situation, rehearse local Actors, then review their understanding."""
    if not brief.strip():
        raise ValueError("需要原始任务和材料。")
    if type(max_audit_rounds) is not int or max_audit_rounds < 1:
        raise ValueError("max_audit_rounds must be a positive integer")
    if type(max_construction_repairs) is not int or max_construction_repairs < 0:
        raise ValueError("max_construction_repairs must be a non-negative integer")
    if type(max_audit_actors) is not int or max_audit_actors < 1:
        raise ValueError("max_audit_actors must be a positive integer")
    contract = (
        files("weavex.prompts")
        .joinpath("situation_construction.md")
        .read_text(encoding="utf-8")
    )
    draft = await generate(contract, {"brief": brief})
    candidate, repairs = await _repair_construction(
        brief,
        draft,
        generate=generate,
        max_repairs=max_construction_repairs,
    )
    audited_actor_ids = _select_audit_actors(candidate.spec, max_audit_actors)
    checked, actor_audit = await audit_actor_understanding(
        candidate,
        brief=brief,
        design=generate,
        simulate=simulate or generate,
        review=review or generate,
        actor_ids=audited_actor_ids,
        max_rounds=max_audit_rounds,
    )
    card = SituationCard(SituationSpec.from_dict(checked["spec"]))
    _require_valid(card)
    return Adaptation(
        brief,
        draft,
        card,
        tuple(checked.get("notes", [])),
        tuple(checked.get("changes", [])),
        tuple(
            checked.get(
                "assumptions",
                repairs[-1].get("assumptions", draft.get("assumptions", []))
                if repairs
                else draft.get("assumptions", []),
            )
        ),
        actor_audit,
        tuple(repairs),
        audited_actor_ids,
    )


def _select_audit_actors(spec: SituationSpec, limit: int) -> tuple[str, ...]:
    """Keep initialization rehearsal bounded while favoring declared contact endpoints."""

    ordered: list[str] = []
    for route in spec.response_routes:
        for actor_id in (route.publisher, route.responder):
            if actor_id not in ordered:
                ordered.append(actor_id)
    for position in spec.positions:
        if position.actor_id not in ordered:
            ordered.append(position.actor_id)
    return tuple(ordered[:limit])


def _construction_card(value: dict) -> tuple[SituationCard | None, tuple[Finding, ...]]:
    """Turn one generated draft into a card while retaining machine-readable faults."""

    try:
        card = SituationCard(SituationSpec.from_dict(value["spec"]))
    except (KeyError, TypeError, ValueError) as exc:
        return None, (Finding("error", "construction_schema", str(exc)),)
    return card, tuple(item for item in audit_situation(card) if item.level == "error")


async def _repair_construction(
    brief: str,
    draft: dict,
    *,
    generate: Translator,
    max_repairs: int,
) -> tuple[SituationCard, list[dict]]:
    """Allow a bounded construction-only repair before any Actor rehearsal exists."""

    current = draft
    repairs: list[dict] = []
    prompt = (
        files("weavex.prompts")
        .joinpath("situation_repair.md")
        .read_text(encoding="utf-8")
    )
    for attempt in range(max_repairs + 1):
        card, errors = _construction_card(current)
        if card is not None and not errors:
            return card, repairs
        if attempt == max_repairs:
            detail = "\n".join(item.detail for item in errors)
            raise ValueError(f"初始化卡在有限修正后仍无效：{detail}")
        repaired = await generate(
            prompt,
            {
                "brief": brief,
                "draft": current,
                "mechanical_findings": [asdict(item) for item in errors],
                "repair_attempt": attempt + 1,
                "max_repairs": max_repairs,
            },
        )
        repairs.append(
            {
                "attempt": attempt + 1,
                "input_findings": [asdict(item) for item in errors],
                "output": repaired,
                "assumptions": repaired.get("assumptions", []),
            }
        )
        current = repaired
    raise AssertionError("bounded construction repair did not terminate")


async def audit_actor_understanding(
    card: SituationCard,
    *,
    brief: str,
    design: Translator,
    simulate: Translator,
    review: Translator,
    actor_ids: tuple[str, ...] | None = None,
    max_rounds: int = 3,
) -> tuple[dict, dict]:
    """Revise and rehearse within a fixed bound; return a card that was rehearsed."""
    if type(max_rounds) is not int or max_rounds < 1:
        raise ValueError("max_rounds must be a positive integer")
    current = card
    rounds = []
    applied_changes = []
    selected_assumptions = None
    pending_revision = None
    accepted = False
    for number in range(1, max_rounds + 1):
        checked, rehearsal = await _audit_actor_round(
            current,
            brief=brief,
            design=design,
            simulate=simulate,
            review=review,
            actor_ids=actor_ids,
            round_context={
                "number": number,
                "max_rounds": max_rounds,
                "previous_reviews": [
                    {"round": r["round"], **r["judgement"]} for r in rounds
                ],
            },
        )
        record = {
            **rehearsal,
            "round": number,
            "review": checked,
            "revision_applied": False,
        }
        rounds.append(record)
        errors = [
            f
            for f in rehearsal["revision_configuration_findings"]
            if f["level"] == "error"
        ]
        if not errors and actor_ids is not None:
            proposed = SituationSpec.from_dict(checked["spec"])
            identities = {p.actor_id for p in proposed.positions} | {
                b.actor_id for b in _brokers(proposed)
            }
            if set(actor_ids) - identities:
                errors.append(
                    {
                        "level": "error",
                        "detail": "修改移除了指定的受测 Actor，无法继续同一范围的演练。",
                    }
                )
                record["revision_configuration_findings"].extend(errors)
        if errors:
            stop_reason = "invalid_revision"
            pending_revision = checked
            break
        if rehearsal["final_card_was_rehearsed"]:
            accepted = checked.get("decision") == "accept"
            stop_reason = "accepted" if accepted else "no_revision"
            if "assumptions" in checked:
                selected_assumptions = checked["assumptions"]
            break
        if number == max_rounds:
            stop_reason = "round_limit"
            pending_revision = checked
            break
        current = SituationCard(SituationSpec.from_dict(checked["spec"]))
        record["revision_applied"] = True
        applied_changes.extend(checked.get("changes") or [f"第 {number} 轮更新处境"])
        if "assumptions" in checked:
            selected_assumptions = checked["assumptions"]
    # Keep last-round proposals separately: an untested rewrite is not the final card.
    result = {
        **checked,
        "spec": asdict(current.spec),
        "changes": applied_changes,
        "decision": "accept" if accepted else "uncertain",
    }
    if selected_assumptions is None:
        result.pop("assumptions", None)
    else:
        result["assumptions"] = selected_assumptions
    return result, {
        **rehearsal,
        "rounds": rounds,
        "max_rounds": max_rounds,
        "accepted": accepted,
        "stop_reason": stop_reason,
        "pending_revision": pending_revision,
        "final_card_was_rehearsed": True,
    }


async def _audit_actor_round(
    card: SituationCard,
    *,
    brief: str,
    design: Translator,
    simulate: Translator,
    review: Translator,
    actor_ids: tuple[str, ...] | None,
    round_context: dict,
) -> tuple[dict, dict]:
    """One fresh rehearsal and subjective review, without an inner retry loop."""
    from .situation_rehearsal import rehearse_situation

    _require_valid(card)
    rehearsal = await rehearse_situation(
        card, design=design, simulate=simulate, actor_ids=actor_ids
    )
    contract = (
        files("weavex.prompts")
        .joinpath("situation_construction.md")
        .read_text(encoding="utf-8")
    )
    findings = [asdict(f) for f in audit_situation(card)]
    checked = await review(
        contract
        + "\n"
        + files("weavex.prompts")
        .joinpath("situation_review.md")
        .read_text(encoding="utf-8"),
        {
            "brief": brief,
            "draft": {"spec": asdict(card.spec)},
            "mechanical_findings": findings,
            "actor_rehearsal": rehearsal,
            "audit_round": round_context,
        },
    )
    checked.setdefault("spec", asdict(card.spec))
    rehearsal["judgement"] = {
        "notes": checked.get("notes", []),
        "actor_findings": checked.get("actor_findings", []),
        "changes": checked.get("changes", []),
        "decision": checked.get("decision", "uncertain"),
        "remaining_issues": checked.get("remaining_issues", []),
    }
    try:
        spec = SituationSpec.from_dict(checked["spec"])
        revised = SituationCard(spec, card.interpretations if spec == card.spec else ())
        rehearsal["revision_configuration_findings"] = [
            asdict(f) for f in audit_situation(revised)
        ]
        rehearsal["final_card_was_rehearsed"] = (
            revised.to_dict() == rehearsal["tested_card"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        rehearsal["revision_configuration_findings"] = [
            {"level": "error", "detail": str(exc)}
        ]
        rehearsal["final_card_was_rehearsed"] = False
    return checked, rehearsal


async def prepare_situation(
    spec: SituationSpec, *, translate: Translator | None = None
) -> SituationCard:
    """At most one isolated translation per Agent; no retries or repair loop."""
    card = SituationCard(spec)
    _require_valid(card)
    if translate is None:
        return card
    prompt = (
        files("weavex.prompts")
        .joinpath("situation_adapter.md")
        .read_text(encoding="utf-8")
    )
    interpretations = []
    for p in spec.positions:
        raw = await translate(prompt, _local_facts(spec, p.actor_id))
        if not isinstance(raw, dict) or set(raw) != {"understanding", "uncertainties"}:
            raise ValueError(
                "translation requires only understanding and uncertainties"
            )
        if not isinstance(raw["uncertainties"], list):
            raise ValueError("uncertainties must be an array")
        interpretations.append(
            Interpretation(
                p.actor_id, raw["understanding"], tuple(raw["uncertainties"])
            )
        )
        _require_valid(SituationCard(spec, tuple(interpretations)))
    return SituationCard(spec, tuple(interpretations))


class ChatTranslator:
    """Optional existing chat client bridge; adapter usage is separate from Runtime."""

    def __init__(
        self,
        client,
        *,
        model: str,
        max_tokens: int = 2048,
        extra_body: dict | None = None,
    ):
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        if extra_body and set(extra_body) & {
            "messages",
            "model",
            "max_tokens",
            "response_format",
            "tools",
        }:
            raise ValueError("extra_body cannot override adapter request fields")
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.extra_body = dict(extra_body or {})
        self.usage: list[dict] = []

    async def __call__(self, instructions: str, facts: dict) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_tokens=self.max_tokens,
            **({"extra_body": self.extra_body} if self.extra_body else {}),
        )
        usage = getattr(response, "usage", None)
        self.usage.append(usage.model_dump() if usage is not None else {})
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(f"translation did not finish: {choice.finish_reason}")
        return json.loads(choice.message.content)


def build_situated_runtime(card: SituationCard, model=None, *, turn_driver=None):
    """Build a fresh Runtime, then install explicitly declared initial observations."""
    _require_valid(card)
    spec = card.spec
    interpretations = {i.actor_id: i for i in card.interpretations}
    world = _initial_world(spec)
    declarations = [
        InitialActor(
            ActorSpec(
                b.actor_id,
                "broker",
                BROKER_INSTRUCTIONS,
                DEFAULT_BROKER_CAPABILITIES,
                initial_member_ids=b.member_ids,
            ),
            question_payload(
                b.question,
                interpretation_content=b.context,
                semantic_addresses=tuple(
                    SemanticHandle("object", identity) for identity in b.object_ids
                ),
            ),
        )
        for b in _brokers(spec)
    ]
    for p in spec.positions:
        context = p.context + "\n" + _environment_text(spec, p.actor_id)
        interpretation = interpretations.get(p.actor_id)
        if interpretation:
            context += (
                "\n适配器的初步解释（可能有误，以原问题、材料和实际工具效果为准）：\n"
                + interpretation.understanding
            )
            if interpretation.uncertainties:
                context += "\n尚未确认的条件：\n" + "\n".join(
                    interpretation.uncertainties
                )
        declarations.append(
            InitialActor(
                ActorSpec(
                    p.actor_id, "agent", AGENT_INSTRUCTIONS, DEFAULT_AGENT_CAPABILITIES
                ),
                question_payload(
                    p.question,
                    intent=p.success_condition,
                    interpretation_content=context,
                    semantic_addresses=_initial_addresses(spec, p.actor_id),
                ),
                {"world_ops": p.world_ops},
            )
        )
    runtime = build_default_runtime(
        model, turn_driver=turn_driver, world=world, initial_actors=tuple(declarations)
    )
    roots = {act.recipient: act for act in runtime.queue}
    observations = set()
    initial_observations = (
        *spec.observations,
        *(InitialObservation(r.responder, r.surface) for r in spec.response_routes),
    )
    for obs in initial_observations:
        key = (obs.actor_id, obs.kind, obs.address, obs.direction)
        if key in observations:
            continue
        observations.add(key)
        address = SemanticHandle(obs.kind, obs.address)
        runtime.local_worlds.include_addresses(obs.actor_id, (address,))
        observation = runtime.local_worlds.observe_neighborhood(
            obs.actor_id,
            question_id=roots[obs.actor_id].act_id,
            address=address,
            direction=obs.direction,
        )
        runtime.record_acquisition_grounding(
            runtime.actors[obs.actor_id],
            roots[obs.actor_id].act_id,
            observation.observation_id,
            {},
        )
        runtime._record_trace(
            "situation.observation_initialized",
            actor_id=obs.actor_id,
            payload={
                "address": obs.address,
                "kind": obs.kind,
                "direction": obs.direction,
                "question_id": roots[obs.actor_id].act_id,
                "observation_id": observation.observation_id,
                "origin": "situation_adapter",
            },
        )
    return runtime


def main() -> None:
    """Offline card preparation/audit. LLM access is an explicit Python opt-in."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="SituationSpec or SituationCard JSON")
    parser.add_argument(
        "--output", type=Path, help="write a host-only card and audit report"
    )
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.input.resolve():
        parser.error("--output must differ from the input file")
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    card = (
        SituationCard.from_dict(raw)
        if "spec" in raw
        else SituationCard(SituationSpec.from_dict(raw))
    )
    findings = audit_situation(card)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(card.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        args.output.with_suffix(".audit.json").write_text(
            json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    if any(f.level == "error" for f in findings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
