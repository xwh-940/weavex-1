"""Deterministic WeaveX capabilities available to every TurnDriver."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .actor import ActorState
from .local_world import local_world_change, reference_data
from .protocol import Act, SemanticHandle, handle_data

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


CapabilityHandler = Callable[
    ["PrototypeRuntime", ActorState, Act, dict[str, Any]],
    object | Awaitable[object],
]


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    parameters: Mapping[str, object]
    handler: CapabilityHandler
    required: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        if capability.name in self._capabilities:
            raise ValueError(f"duplicate capability: {capability.name}")
        self._capabilities[capability.name] = capability

    def catalog_for(self, names: frozenset[str]) -> tuple[dict[str, object], ...]:
        unknown = names.difference(self._capabilities)
        if unknown:
            raise ValueError("unknown capabilities: " + ", ".join(sorted(unknown)))
        return tuple(
            {
                "name": item.name,
                "description": item.description,
                "parameters": dict(item.parameters),
                "required": list(item.required),
            }
            for name in sorted(names)
            for item in (self._capabilities[name],)
        )

    async def execute(
        self,
        runtime: "PrototypeRuntime",
        actor: ActorState,
        trigger: Act,
        name: str,
        arguments: Mapping[str, Any],
    ) -> object:
        if name not in actor.spec.capabilities:
            raise PermissionError(f"Actor {actor.spec.actor_id} cannot call {name}")
        try:
            capability = self._capabilities[name]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {name}") from exc
        result = capability.handler(runtime, actor, trigger, dict(arguments))
        return await result if inspect.isawaitable(result) else result


DEFAULT_AGENT_CAPABILITIES = frozenset(
    {
        "world_references_from",
        "world_references_to",
        "world_add_object",
        "world_add_reference",
    }
)
DEFAULT_BROKER_CAPABILITIES = frozenset(
    {"create_actor", "form_group", "inspect_inquiry_topology"}
)


def _string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip()


def _bounded_int(
    arguments: Mapping[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(name, default)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _handle(arguments: Mapping[str, Any], name: str) -> SemanticHandle:
    raw = arguments.get(name)
    if not isinstance(raw, dict) or set(raw) != {"kind", "id"}:
        raise ValueError(f"{name} requires only kind and id")
    return SemanticHandle(str(raw["kind"]), str(raw["id"]))


def _budget(arguments: Mapping[str, Any]) -> dict[str, int]:
    raw = arguments.get("budget")
    if not isinstance(raw, dict):
        raise ValueError("budget must be an object")
    if set(raw) - {"world_ops"}:
        raise ValueError("the prototype currently supports only world_ops budget")
    if not all(
        isinstance(name, str)
        and name.strip()
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount >= 0
        for name, amount in raw.items()
    ):
        raise ValueError("budget requires non-negative integer amounts")
    return dict(raw)


def _capabilities(arguments: Mapping[str, Any]) -> frozenset[str]:
    raw = arguments.get("capabilities")
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise ValueError("capabilities must be a string array")
    values = frozenset(raw)
    if not values <= DEFAULT_AGENT_CAPABILITIES:
        raise PermissionError(
            "new cognitive positions may only receive semantic-local capabilities"
        )
    return values


def _member_ids(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    raw = arguments.get("member_ids")
    if not isinstance(raw, list) or not all(
        isinstance(value, str) and value.strip() for value in raw
    ):
        raise ValueError("member_ids must be an array of non-empty Actor ids")
    values = tuple(value.strip() for value in raw)
    if len(values) != len(set(values)):
        raise ValueError("member_ids cannot contain duplicates")
    return values


def _create_actor(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    del trigger
    if set(arguments) - {"purpose", "budget", "capabilities"}:
        raise ValueError("create_actor accepts only purpose, budget, and capabilities")
    return runtime.create_agent(
        actor,
        purpose=_optional_string(arguments, "purpose"),
        instructions="",
        budget=_budget(arguments),
        capabilities=_capabilities(arguments),
    )


def _form_group(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    return runtime.form_group(actor, trigger, _member_ids(arguments))


def _inspect_inquiry_topology(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    if set(arguments) not in (set(), {"question_id"}):
        raise ValueError("inspect_inquiry_topology accepts only question_id")
    if arguments and _string(arguments, "question_id") != trigger.act_id:
        raise ValueError("question_id must identify the selected Question")
    local_world = runtime.coordination_local_world(actor, trigger)
    topology = dict(runtime.inquiry_topology(trigger.thread_id))
    topology.pop("thread_id", None)
    return {
        **topology,
        "context": local_world["context"],
        "actors": [
            {
                "actor_id": actor.spec.actor_id,
                "role": actor.spec.role,
                "capabilities": sorted(actor.spec.capabilities),
            },
            *local_world["actors"],
        ],
    }


def _require_world_op(
    runtime: "PrototypeRuntime", actor: ActorState, trigger: Act
) -> str:
    question_id = runtime.inquiry.question_for_trigger(trigger).act_id
    if actor.budget_for(question_id).get("world_ops", 0) < 1:
        raise RuntimeError("world_ops budget exhausted")
    return question_id


def _world_references_from(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    object_id = _string(arguments, "object_id")
    offset = _bounded_int(
        arguments,
        "offset",
        default=0,
        minimum=0,
        maximum=1_000_000,
    )
    limit = _bounded_int(arguments, "limit", default=8, minimum=1, maximum=20)
    question_id = _require_world_op(runtime, actor, trigger)
    before = runtime.local_worlds.view_for(actor.spec.actor_id)
    values = runtime.local_worlds.references_from(
        actor.spec.actor_id,
        object_id,
        offset,
        limit,
    )
    actor.consume(question_id, "world_ops")
    observation = runtime.local_worlds.observe_neighborhood(
        actor.spec.actor_id,
        question_id=question_id,
        address=SemanticHandle("object", object_id),
        direction="outgoing",
    )
    after = runtime.local_worlds.view_for(actor.spec.actor_id)
    change = local_world_change(before, after)
    acquisition_grounding_id = runtime.record_acquisition_grounding(
        actor,
        question_id,
        observation.observation_id,
        change,
    )
    return {
        "references": [reference_data(value) for value in values],
        "next_offset": offset + len(values) if len(values) == limit else None,
        "observation": {
            "observation_id": observation.observation_id,
            "address": handle_data(observation.address),
            "direction": observation.direction,
            "established_from_question_ids": list(
                observation.established_from_question_ids
            ),
        },
        "acquisition_grounding_id": acquisition_grounding_id,
        "local_world_change": change,
    }


def _world_references_to(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    address = _handle(arguments, "address")
    offset = _bounded_int(
        arguments,
        "offset",
        default=0,
        minimum=0,
        maximum=1_000_000,
    )
    limit = _bounded_int(arguments, "limit", default=8, minimum=1, maximum=20)
    question_id = _require_world_op(runtime, actor, trigger)
    before = runtime.local_worlds.view_for(actor.spec.actor_id)
    values = runtime.local_worlds.references_to(
        actor.spec.actor_id,
        address,
        offset,
        limit,
    )
    actor.consume(question_id, "world_ops")
    observation = runtime.local_worlds.observe_neighborhood(
        actor.spec.actor_id,
        question_id=question_id,
        address=address,
        direction="incoming",
    )
    after = runtime.local_worlds.view_for(actor.spec.actor_id)
    change = local_world_change(before, after)
    acquisition_grounding_id = runtime.record_acquisition_grounding(
        actor,
        question_id,
        observation.observation_id,
        change,
    )
    return {
        "references": [reference_data(value) for value in values],
        "next_offset": offset + len(values) if len(values) == limit else None,
        "observation": {
            "observation_id": observation.observation_id,
            "address": handle_data(observation.address),
            "direction": observation.direction,
            "established_from_question_ids": list(
                observation.established_from_question_ids
            ),
        },
        "acquisition_grounding_id": acquisition_grounding_id,
        "local_world_change": change,
    }


def _world_add_object(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    content = _string(arguments, "content")
    question_id = _require_world_op(runtime, actor, trigger)
    value = runtime.local_worlds.add_object(
        actor.spec.actor_id,
        content,
    )
    actor.consume(question_id, "world_ops")
    return {
        **value,
        "semantic_address": handle_data(SemanticHandle("object", value["object_id"])),
    }


def _world_add_reference(
    runtime: "PrototypeRuntime",
    actor: ActorState,
    trigger: Act,
    arguments: dict[str, Any],
) -> object:
    source_object_id = _string(arguments, "source_object_id")
    target = _handle(arguments, "target")
    meaning = _optional_string(arguments, "meaning")
    question_id = _require_world_op(runtime, actor, trigger)
    before = runtime.local_worlds.view_for(actor.spec.actor_id)
    value = runtime.local_worlds.add_reference(
        actor.spec.actor_id,
        source_object_id,
        target,
        meaning,
    )
    actor.consume(question_id, "world_ops")
    change = local_world_change(
        before,
        runtime.local_worlds.view_for(actor.spec.actor_id),
    )
    source_effect = runtime.publish_reference_change(
        actor.spec.actor_id,
        str(value["reference_id"]),
        source_local_world_change=change,
    )
    if source_effect:
        return {**value, "observed_neighborhood_change": source_effect}
    return value


def build_default_tools() -> ToolRegistry:
    registry = ToolRegistry()
    for item in (
        Capability(
            "form_group",
            (
                "从当前 candidates 中一次选择零个或多个已有认知位置，建立这个 context 的永久已有"
                "成员边界。选入的位置持续可联系，相对于以后进入本 context 的 Question 可以形成可区分"
                "的公开 Answer 来源，并与本群以后创建的位置"
                "形成直接邻近；成功后不能再选入其他已有位置。member_ids 使用 [] 表示不选择候选。"
            ),
            {
                "member_ids": {
                    "type": "array",
                    "description": "一次性选入本群的当前候选认知位置 actor_id",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                }
            },
            _form_group,
            ("member_ids",),
        ),
        Capability(
            "create_actor",
            (
                "在本 context 中创建一个不继承其他位置私有内容的新认知位置，并返回 actor_id 与"
                "成功状态。新位置持续、自治且拥有新的私有历史起点，相对于以后收到"
                "的 Question 可以形成可区分的公开 Answer 来源；创建本身不传递 Question、地址或知识。"
                "成功后它进入 local_world.actors，并可在同一 turn 的后续 Decision 中接收 Question。"
                "完整 Question 后来携带的 Semantic Address 会在该位置处理前成为其私有语义起点；你当前"
                "不能读取地址或尚无既有位置，不表示其中无证据，也不取消新位置形成结果的可能。"
                "群尚未形成时直接创建表示不选择已有 candidates。列出的 capabilities 会"
                "成为持久配置并限定这个位置以后实际可请求的行动；它们与每个新 Question 的有限"
                "操作能力共同构成该位置的行动边界。该位置会随以后实际经历继续变化。可选 purpose 只是"
                "你对这个位置的私有创建时标签："
                "它只随你自己的原生对话保留，不写入新位置的处境，也不进入其他 context 的 Actor 描述。"
                "以后可在普通 assistant content 中依据实际公开 Answer 追加或修正这份理解；旧理解作为"
                "历史保留，宿主不另建标签状态。新位置从共同认知边界开始，并在实际 Question、私有局部"
                "与自身持续对话中形成以后认识；创建者不为它写入持久自然语言 workflow。"
                "当前完整 Question 另由 send_question 交付。"
            ),
            {
                "purpose": {
                    "type": "string",
                    "description": (
                        "可选的创建者私有暂时标签，帮助你以后结合公开历史回想为何建立这个会继续变化的"
                        "位置。它只存在于"
                        "你发出的本次 tool call 及持续原生对话中，不成为 Actor 配置、公开事实或新位置"
                        "的自我描述。无需标签时省略；它不替代当前 Question，也不证明该位置具有某种知识"
                    ),
                },
                "budget": {
                    "type": "object",
                    "description": (
                        "新位置每个独立 Question 开始时实际拥有的语义读取或持久改变能力余量；"
                        "使用 {} 表示新 Question 不具有 world 行动余量"
                    ),
                    "properties": {
                        "world_ops": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "每个新 Question 可成功执行的 world capability 次数",
                        }
                    },
                    "additionalProperties": False,
                },
                "capabilities": {
                    "type": "array",
                    "description": (
                        "显式提供给新位置的 capability 名称；[] 表示不提供 world 行动。"
                        "world_references_from "
                        "读取已知 Object 的出向 Reference；world_references_to 读取已有 Object/Reference "
                        "的入向 Reference；world_add_object 物化具有稳定"
                        "身份且可成为 Reference source 的 Object；world_add_reference 在已有"
                        "source 与 target 之间物化带可选开放 meaning 的引用行为。"
                    ),
                    "items": {
                        "type": "string",
                        "enum": sorted(DEFAULT_AGENT_CAPABILITIES),
                    },
                    "uniqueItems": True,
                },
            },
            _create_actor,
            ("budget", "capabilities"),
        ),
        Capability(
            "inspect_inquiry_topology",
            (
                "查看公开认知位置、Question 派生关系、Answer 连接、结构上的叶 Question、"
                "缺少直接 Answer 的多源 Question，以及已有候选子 Answer 的来源 Question。"
                "candidate_source_questions_without_direct_answers 列出多源派生 Question "
                "已有 Answer、但自身尚无直接 Answer 的直接来源 Question。成功 Observation"
                "是调用时的只读拓扑；在当前 turn 让出以前，不会有其他 Actor 的 Act 改变其中的 "
                "Question/Answer 连接。"
            ),
            {},
            _inspect_inquiry_topology,
        ),
        Capability(
            "world_references_from",
            (
                "读取一个当前已知 Object 的一页直接出向 SemanticReference，返回结构、meaning、"
                "完整地址和 next_offset。每条直接 target 同时进入 local_world：Object target 带"
                "content；Reference target 带该 Reference 与其 source Object，但不继续展开它的 target。"
                "更深 target 仍只是地址；继续扩展时，当前可作为 object_id 的是已经进入 local_world 的"
                " source Object。成功读取同时建立一个属于当前认知位置、持续到 Runtime reset 的直接出向"
                "邻域 observation，并返回稳定 observation_id；宿主还自动形成 acquisition grounding，把本次"
                "材料与 observation 来源连接后返回 acquisition_grounding_id。前者可作为 observation 来源，"
                "后者可作为 prior grounding 复用；重复读取只追加建立来源。以后新增出向 Reference 会作为"
                "Local World change 到达；只有某 Question 的最新判断 grounding 直接或传递依赖该 observation，"
                "才会机械重新形成对应 gap，是否行动仍由当前认知位置判断。"
                "local_world_change 给出本次新增内容。成功执行使用 question_id 指定责任的"
                " 1 次语义操作能力，Observation 显示能力余量变化。"
            ),
            {
                "object_id": {
                    "type": "string",
                    "description": "当前 local_world 中已有 Object 的 object_id",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页起点；可选，默认 0",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "本页数量；可选，默认 8",
                },
            },
            _world_references_from,
            ("object_id",),
        ),
        Capability(
            "world_references_to",
            (
                "读取一个当前已有 Object/Reference 的一页直接入向 SemanticReference，返回结构、meaning、"
                "完整地址和 next_offset；每条 Reference 及其 source Object 同时进入 local_world，不递归"
                "展开其他连接。成功读取同时建立一个属于当前认知位置、持续到 Runtime reset 的直接入向邻域"
                " observation，并返回稳定 observation_id；宿主还自动形成 acquisition grounding，把本次材料"
                "与 observation 来源连接后返回 acquisition_grounding_id。前者可作为 observation 来源，后者可作为"
                "prior grounding 复用；重复读取只追加建立来源。以后新增的入向 Reference 会连同必要浅层语义"
                "作为 Local World change 到达；只有某 Question 的最新判断 grounding 直接或传递依赖该"
                " observation，才会机械重新形成对应 gap，是否影响旧结果仍由当前认知位置判断。空结果读取"
                "同样建立关系，分页不拆分 relation。成功执行"
                "使用 question_id 指定责任的 1 次语义操作能力，Observation 显示能力余量变化。"
            ),
            {
                "address": {
                    "type": "object",
                    "description": "当前 local_world 中已有 Object/Reference 的 Semantic Address",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["object", "reference"],
                        },
                        "id": {"type": "string"},
                    },
                    "required": ["kind", "id"],
                    "additionalProperties": False,
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页起点；可选，默认 0",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "本页数量；可选，默认 8",
                },
            },
            _world_references_to,
            ("address",),
        ),
        Capability(
            "world_add_object",
            (
                "把 content 物化为新的 SemanticObject，返回 object_id、content 和完整地址。每次成功"
                "调用创建新的身份，不自动接入已有 Object，也不发送地址或完成 Question。"
                "成功执行使用 question_id 指定责任的 1 次语义操作能力，Observation 显示能力"
                "余量变化。"
            ),
            {
                "content": {
                    "type": "string",
                    "description": "要作为一个可相对独立使用的 SemanticObject 物化的完整语义内容",
                }
            },
            _world_add_object,
            ("content",),
        ),
        Capability(
            "world_add_reference",
            (
                "在已有 source Object 与已有 Object/Reference target 之间物化一项具有身份的"
                "SemanticReference，返回 reference_id、source、target、meaning 和完整地址。该行动不"
                "修改两端、不创建 Object，也不发送 Question/Answer Act 或完成 Question；以后从 source 读取"
                "出向 Reference 的认知位置可以取得同一 Reference。新增 source→target 还会精确进入已经持续"
                "观察 source 出向或 target 入向的其他认知位置，连同必要浅层语义形成 Local World change；"
                "例如要让反应 V 进入对 U 的入向观察，必须创建 V→U，U→V 只改变 U 的出向邻域。内容到达不"
                "自动产生对方的 Question 责任、判断或回应。若该新增精确改变当前认知位置自己"
                "持续观察的邻域，成功 Observation 还返回 observed_neighborhood_change，并沿最新"
                "grounding 对匹配 observation 的直接或传递依赖机械形成 Question gap；其中"
                "answer_responsibilities_added 给出因此真实增加的完整责任与 response frontier。它不会排入"
                "重复的自我 delta，是否形成新 Answer 或 retain 仍由变化后的处境判断。成功执行使用 question_id"
                "指定责任的 1 次语义操作能力，Observation 显示能力余量变化。"
            ),
            {
                "source_object_id": {
                    "type": "string",
                    "description": "已有 source Object 的 object_id",
                },
                "target": {
                    "type": "object",
                    "description": "已有目标的 Semantic Address",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["object", "reference"],
                        },
                        "id": {"type": "string"},
                    },
                    "required": ["kind", "id"],
                    "additionalProperties": False,
                },
                "meaning": {
                    "type": "string",
                    "description": (
                        "可选；target 在这次 source→target 引用中的 source-relative 开放含义；"
                        "省略或空字符串只断言 source 引用 target；任何非空完整文本都会成为"
                        "这条 Reference 额外承担的持续世界语义"
                    ),
                },
            },
            _world_add_reference,
            ("source_object_id", "target"),
        ),
    ):
        registry.register(item)
    return registry
