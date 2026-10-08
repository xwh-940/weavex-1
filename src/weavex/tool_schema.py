"""Stable Actor action grammar and host-side structural validation."""

from __future__ import annotations

from typing import Any, Mapping

from .actor import ActorSpec


def _object(properties: Mapping[str, object], required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _id(description: str) -> dict:
    return {"type": "string", "minLength": 1, "description": description}


def _tool(name: str, description: str, properties: dict, required: tuple) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _object(properties, required),
        },
    }


def _grounding_inputs_schema(role: str) -> dict:
    descriptions = {
        "act": "当前可见的不可变公开 Act。",
        "grounding": "当前认知位置的 prior grounding；传递继承其 observation 敏感关系。",
    }
    if role == "agent":
        descriptions.update(
            {
                "observation": "当前已建立的邻域 observation；以后精确新增会重新形成 Question gap。",
                "object": "当前 Local World 中内容不可变的 Object。",
                "reference": "当前 Local World 中结构与 meaning 不可变的 Reference。",
            }
        )
    return {
        "type": "array",
        "uniqueItems": True,
        "items": {
            "oneOf": [
                {
                    **_object(
                        {
                            "kind": {"type": "string", "enum": [kind]},
                            "id": _id(description),
                        },
                        ("kind", "id"),
                    ),
                    "description": description,
                }
                for kind, description in descriptions.items()
            ]
        },
        "description": (
            "本次判断实际采用的私有来源，形成该 Question 新的唯一最新 grounding root。"
            "先前 root 及其 observation 敏感关系不会自动继承，须显式选择 prior grounding。"
            "宿主只维护结构并验证来源可用，不判断真假或充分性。允许空数组；此字段不随 Answer 公开。"
        ),
    }


def actor_tool_schemas(
    actor: ActorSpec, capability_catalog: tuple[Mapping[str, Any], ...]
) -> list[dict[str, object]]:
    """Depend only on immutable Actor configuration, never on situated facts."""
    address = _object(
        {
            "kind": {"type": "string", "enum": ["object", "reference"]},
            "id": _id("已有语义身份的完整 ID。"),
        },
        ("kind", "id"),
    )
    addresses = {
        "type": "array",
        "items": address,
        "description": (
            "随 Act 交付的已有 Semantic Address。同一身份会在处理前物化进私有 Local World，"
            "不请求 world capability；Object 地址带 content，Reference 地址带结构、meaning 与 source Object，"
            "target 保持地址而不递归展开。地址到达私有 Semantic 局部时取得同一身份；"
            "Coordination 局部中只被持有和转交；复制普通文本不会传递语义身份。"
            "转交地址时，私有 Semantic 局部要求该身份已在 Local World；Coordination 局部要求地址由"
            "本次 question_id 所在 Inquiry 中自己参与的 Act 明确携带。"
        ),
    }
    question = {
        "raw_question": _id("需要理解与回答的原始问题文本。"),
        "interpretation_context": {
            **_object(
                {
                    "content": {
                        "type": "string",
                        "description": "消除指代、讨论对象、时空或事件阶段、局部术语歧义的文本；可为空。",
                    },
                    "semantic_addresses": addresses,
                },
                ("content", "semantic_addresses"),
            ),
            "description": "理解 raw_question 所必需的上下文。",
        },
        "intent": {
            "type": "string",
            "description": (
                "合格 Answer 必须满足的开放约束，例如视角、信息边界、投影规则、范围、深度或假设；"
                "不描述认知位置配置或通讯拓扑。无额外约束时使用空字符串。"
            ),
        },
    }
    question_id = _id("当前 answer_responsibilities 中本次实际推进的直接 Question ID。")
    tools = [
        _tool(
            "receive_input",
            (
                "取得本轮 arrival、当前全部直接 Question gap、能力边界及新增局域事实。"
                "每个 turn 必须先成功取得输入，且只能成功调用一次；此前其他行动不满足执行前置条件。"
                "arrival 不保证首次可见或具有注意优先级。旧 Act 与局域材料已在持续对话中时只给关系指针或增量。"
                "调用不发送 Act、不改变公开关系。"
            ),
            {},
            (),
        ),
        _tool(
            "yield_turn",
            (
                "在本轮已经取得输入后让出当前 turn。不发送 Act、不推进或取消 Question gap、不恢复能力。"
                "历史对话与 provider reasoning 继续保留到 Runtime reset。若以后有新的 Act 实际到达，"
                "将在新的内部 turn 取得输入。"
            ),
            {},
            (),
        ),
        _tool(
            "answer_question",
            (
                "为 question_id 指定的当前直接 gap 返回满足 raw_question 和 intent 的完整公开 Answer。"
                "旧 Answer 不变，这次判断推进该 Question 的 frontier，不推进其他责任。接收者获得填写的文本与地址，"
                "未填写的私有处境不会自动交付。成功后本轮仍可继续。initialization 只是初始条件结果接收端，"
                "向它返回根 Question 的 Answer 不激活处理者或等待回应。"
            ),
            {
                "question_id": question_id,
                "answer": _id("完整结果文本。"),
                "semantic_addresses": addresses,
                "grounding_inputs": _grounding_inputs_schema(actor.role),
            },
            ("question_id", "answer", "semantic_addresses", "grounding_inputs"),
        ),
        _tool(
            "retain_answer",
            (
                "判断最近 Answer 仍代表 question_id 的当前 frontier，私下推进判断，不发送 Act 或结束 turn。"
                "要求本 Actor 直接承担该 Question、已有 Answer 且存在尚未判断的变化。新的 grounding 不自动继承旧 root。"
            ),
            {
                "question_id": question_id,
                "grounding_inputs": _grounding_inputs_schema(actor.role),
            },
            ("question_id", "grounding_inputs"),
        ),
        _tool(
            "send_incremental_act",
            (
                "沿 reply_to 指定的既有可见 Act 向其另一方交付 content，补入或请求原 Question 的参数、指代、修正或条件。"
                "不建立独立 Answer 责任、不推进判断、不切换 context，不携带 Semantic Address。"
                "initialization 不接受 incremental；本轮仍可继续。"
            ),
            {
                "reply_to": _id(
                    "本 Actor 参与且另一方可接收 incremental 的已有 Act ID。"
                ),
                "content": {"description": "相对于既有通讯语境的增量内容。"},
            },
            ("reply_to", "content"),
        ),
    ]
    if actor.role == "agent":
        for name, existing in (
            ("ask_external_question", False),
            ("ask_in_existing_context", True),
        ):
            properties = {"question_id": question_id}
            if existing:
                properties["context_id"] = _id(
                    "external_contexts 中本认知位置已属于的 context ID。"
                )
            properties.update(question)
            tools.append(
                _tool(
                    name,
                    (
                        "在自己已经属于的外部 Answer context 中提交完整 Question，沿用 card 明示的 guaranteed_shared_context。"
                        if existing
                        else "在新的外部 Answer context 中提交完整 Question，不继承已有 context 的共享文本。"
                    )
                    + "要求 question_id 是当前直接 gap；外问保留该责任，返回的 Answer 以后到达。仅交付填写的文本和地址，本轮仍可继续。",
                    properties,
                    tuple(properties),
                )
            )
    else:
        tools.append(
            _tool(
                "send_question",
                (
                    "把独立完整 Question 交给本群中可联系的认知位置，这才使当前结果责任进入 recipient 的处境。"
                    "私有 purpose 标签不会交付，创建者也不会为 recipient 写入持久自然语言 workflow；recipient 自主判断行动，"
                    "保留它此前的对话和 Local World，其他协调事实不自动交付。question_id 须是当前直接责任，"
                    "recipient 不能是当前直接责任的来源位置；source_act_ids 须是同一 Inquiry 中的可见 Question/Answer。"
                ),
                {
                    "question_id": question_id,
                    "recipient": _id("本群中已有的可联系认知位置 actor_id。"),
                    **question,
                    "source_act_ids": {
                        "type": "array",
                        "items": _id("直接暴露缺口的可见 Question/Answer Act ID。"),
                        "minItems": 1,
                        "uniqueItems": True,
                        "description": (
                            "直接来源事实，不是 Answer 的返回目标；Question 映射到自身，Answer 沿 reply_to 映射到它回答的 Question。"
                            "不用共同根节点或祖先替代直接来源。"
                        ),
                    },
                },
                (
                    "question_id",
                    "recipient",
                    "raw_question",
                    "interpretation_context",
                    "intent",
                    "source_act_ids",
                ),
            )
        )
    for item in sorted(capability_catalog, key=lambda value: str(value["name"])):
        if item["name"] not in actor.capabilities:
            continue
        properties = dict(item["parameters"])
        required = tuple(item.get("required", ()))
        if actor.role == "agent" or item["name"] == "form_group":
            properties = {"question_id": question_id, **properties}
            required = ("question_id", *required)
        elif item["name"] == "inspect_inquiry_topology":
            properties = {
                "question_id": _id("选择要查看其所在 Inquiry 的当前可见 Question。")
            }
            required = ("question_id",)
        precondition = (
            "前置条件：question_id 是当前直接 gap，所需地址已进入 Local World，且该责任尚有语义操作能力。"
            if actor.role == "agent"
            else "前置条件：当前仍有直接 Question gap；建群只能在本 context 尚未形成时进行。"
        )
        tools.append(
            _tool(
                str(item["name"]),
                str(item["description"]) + precondition,
                properties,
                required,
            )
        )
    return tools


def validate_arguments(
    value: Any, schema: Mapping[str, Any], path: str = "arguments"
) -> None:
    """Validate the structural vocabulary actually used by our fixed schemas."""
    if "oneOf" in schema:
        matched = 0
        for variant in schema["oneOf"]:
            try:
                validate_arguments(value, variant, path)
            except ValueError:
                continue
            matched += 1
        if matched != 1:
            raise ValueError(f"{path} must match one permitted source type")
        return
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if kind in types and (
        not isinstance(value, types[kind])
        or (kind == "integer" and isinstance(value, bool))
    ):
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} has an invalid value")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", ())) - value.keys():
            raise ValueError(f"{path} is missing required fields")
        if (
            schema.get("additionalProperties") is False
            and value.keys() - properties.keys()
        ):
            raise ValueError(f"{path} contains extra fields")
        for name, item in value.items():
            validate_arguments(item, properties.get(name, {}), f"{path}.{name}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path} has too few items")
        if schema.get("uniqueItems") and any(
            item in value[:index] for index, item in enumerate(value)
        ):
            raise ValueError(f"{path} cannot contain duplicates")
        for item in value:
            validate_arguments(item, schema.get("items", {}), path)
    elif kind == "string" and len(value.strip()) < schema.get("minLength", 0):
        raise ValueError(f"{path} cannot be blank")
    elif kind == "integer" and not schema.get("minimum", value) <= value <= schema.get(
        "maximum", value
    ):
        raise ValueError(f"{path} is outside the permitted range")


__all__ = ["actor_tool_schemas", "validate_arguments"]
