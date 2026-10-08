"""Backend-neutral effective-situation and dynamic tool projections."""

from __future__ import annotations


import pytest

from tests.native_model_inputs import (
    agent_input as _input,
    broker_input as _broker_input,
    native_input as _native_input,
)

from weavex.defaults import build_default_runtime
from weavex.protocol import (
    Decision,
    Observation,
    SemanticHandle,
    answer_payload,
    question_payload,
)
from weavex.provider import (
    OpenAICompatibleModel,
    ScriptedModel,
    semantic_input_data,
)
from weavex.prompts import (
    AGENT_INSTRUCTIONS,
    BROKER_INSTRUCTIONS,
)
from weavex.world import SemanticWorld


def test_agent_local_world_persists_across_inquiries() -> None:
    world = SemanticWorld()
    seed = world.add_object("actor lifetime meaning", object_id="object:lifetime")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="retain private semantic experience",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    agent = runtime.actors[str(created["actor_id"])]
    first_root = runtime.inject_human(
        "first root",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
    )
    first_question = runtime.send_from_actor(
        broker,
        first_root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload(
            "learn one identity",
            semantic_addresses=(SemanticHandle("object", seed.object_id),),
        ),
        derived_from=(first_root.act_id,),
    )
    runtime.turn_driver.model_input(runtime.turn_host(agent, first_question), (), ())
    second_root = runtime.inject_human("second root")
    second_question = runtime.send_from_actor(
        broker,
        second_root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload("reuse prior private experience"),
        derived_from=(second_root.act_id,),
    )

    second_input = runtime.turn_driver.model_input(
        runtime.turn_host(agent, second_question), (), ()
    )

    assert second_input.trigger.thread_id != first_question.thread_id
    assert [item["object_id"] for item in second_input.local_world["objects"]] == [
        seed.object_id
    ]


def test_default_broker_input_is_self_contained_and_has_role_boundaries() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="未使用"))
    )
    root = runtime.inject_human("检查默认模型输入")
    broker = runtime.actors["broker"]
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, root),
        (),
        runtime.tools.catalog_for(broker.spec.capabilities),
    )

    messages = OpenAICompatibleModel.render_messages(value)
    protocol_tools = OpenAICompatibleModel.protocol_tools(value)
    current_input = semantic_input_data(value)
    common_instructions = " ".join(messages[0]["content"].split())
    broker_instructions = " ".join(BROKER_INSTRUCTIONS.split())
    agent_instructions = " ".join(AGENT_INSTRUCTIONS.split())
    topology_capability = next(
        item
        for item in value.capability_catalog
        if item["name"] == "inspect_inquiry_topology"
    )
    create_actor_capability = next(
        item for item in value.capability_catalog if item["name"] == "create_actor"
    )
    form_group_capability = next(
        item for item in value.capability_catalog if item["name"] == "form_group"
    )
    create_actor_tool = next(
        item for item in protocol_tools if item["function"]["name"] == "create_actor"
    )

    def has_chinese(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    required_contracts = {
        common_instructions: {
            "`arrival`",
            "`answer_responsibilities`",
            "`visible_acts`",
            "`question_id`",
            "`interpretation_context`",
            "`intent`",
            "Observation",
            "通过 Question/Answer 契约相互耦合",
            "历史快照",
            "实际 tools",
            "Runtime reset",
            "assistant tool call",
            "可能已经在先前取得的责任",
            "(assistant action → tool Observation)*",
        },
        broker_instructions: {
            "Coordination Local World",
            "`local_world.candidates`",
            "`local_world.open_question_responsibilities`",
            "`available_capabilities`",
            "`shared_context_ids` 不只是拓扑邻近",
            "可以针对它形成 Answer",
            "持续、自治且私有历史不可见的有限认知位置",
            "相对于当前 Question",
            "认知位置数量",
            "每个完整 Question 都保留自己的 Answer 责任",
            "沿单一返回关系实际到达的新 Act",
            "Actor-private frontier gap",
            "较宽 Question 已经可回答也不会取消",
            "不继承其他位置私有内容",
            "不是帮助来源位置",
            "私有 purpose 标签",
            "不会进入该位置或其他 context 的处境",
            "普通 assistant content 留下新的最小区别",
            "你不为它编写跨 Question 持续的内部 workflow",
            "认知位置不是固定专家类型",
            "复用旧位置是在选择这段演化",
            "不要把自己的认知边界改写成语义来源的负面事实",
            "还没有满足该 Answer 责任",
        },
        agent_instructions: {
            "SemanticObject",
            "外部 Answer context",
            "该 context 自主形成 Answer",
            "不把你变成失忆状态",
            "后续独立 Question 不会清空",
            "target content 不复制进 `meaning`",
            "实际 tools",
            "都是当前处境中的真实差距",
            "以后实际 Local World change",
            "`retain_answer`",
            "不保证首次可见",
            "不同 Question 之间复用或组合",
        },
    }
    for instructions, required in required_contracts.items():
        assert all(term in instructions for term in required)

    for instructions in required_contracts:
        assert "World Understanding" not in instructions
    for instructions in (common_instructions, broker_instructions, agent_instructions):
        assert all(
            architecture_name not in instructions
            for architecture_name in ("Agent", "Broker", "OSW", "Semantic World")
        )
    assert "GroundingGraph" not in agent_instructions
    assert "适合依赖" not in agent_instructions
    assert "`derived_from`" not in common_instructions
    assert "`focus_question`" not in common_instructions
    assert all(
        term not in agent_instructions
        for term in (
            "`form_group`",
            "`create_actor`",
            "`world_references_from`",
            "`ask_external_question`",
            "`ask_in_existing_context`",
            "`derived_from`",
            "`source_act_ids`",
            "candidates",
        )
    )
    assert all(
        term not in broker_instructions
        for term in (
            "`form_group`",
            "`create_actor`",
            "`inspect_inquiry_topology`",
            "`send_question`",
            "`send_answer`",
        )
    )
    assert "不能读取、搜索、解释或修改其指向的" in broker_instructions
    assert (
        "candidate_source_questions_without_direct_answers"
        in topology_capability["description"]
    )
    assert "当前 turn 让出以前" in topology_capability["description"]
    assert "不传递 Question、地址或知识" in create_actor_capability["description"]
    assert "新认知位置" in create_actor_capability["description"]
    assert "相对于以后收到的 Question" in create_actor_capability["description"]
    assert "限定这个位置以后实际可请求的行动" in create_actor_capability["description"]
    assert "只随你自己的原生对话保留" in create_actor_capability["description"]
    assert "不写入新位置的处境" in create_actor_capability["description"]
    assert "普通 assistant content" in create_actor_capability["description"]
    assert "会随以后实际经历继续变化" in create_actor_capability["description"]
    assert "不表示其中无证据" in create_actor_capability["description"]
    assert (
        "当前完整 Question 另由 send_question" in create_actor_capability["description"]
    )
    assert (
        "每项都需要由当前公开协调需要说明用途"
        not in create_actor_capability["description"]
    )
    assert "capabilities" in create_actor_capability["description"]
    create_properties = create_actor_tool["function"]["parameters"]["properties"]
    assert "创建者私有暂时标签" in create_properties["purpose"]["description"]
    assert "不成为 Actor 配置" in create_properties["purpose"]["description"]
    assert "instructions" not in create_properties
    assert (
        "创建者不为它写入持久自然语言 workflow"
        in create_actor_capability["description"]
    )
    assert "永久" in form_group_capability["description"]
    assert create_actor_tool["function"]["parameters"]["required"] == [
        "budget",
        "capabilities",
    ]
    assert "external_contexts" not in current_input
    assert "budget" not in current_input
    assert "role" not in current_input["actor"]
    assert (
        create_actor_tool["function"]["parameters"]["properties"]["budget"][
            "properties"
        ]["world_ops"]["minimum"]
        == 0
    )
    assert all(has_chinese(message["content"]) for message in messages[:2])
    assert all(has_chinese(item["function"]["description"]) for item in protocol_tools)
    assert all(has_chinese(item["description"]) for item in value.capability_catalog)
    yield_tool = next(
        item for item in protocol_tools if item["function"]["name"] == "yield_turn"
    )
    assert "若以后有新的 Act 实际到达" in yield_tool["function"]["description"]


def test_send_question_is_the_current_question_delivery_boundary() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="未使用"))
    )
    broker = runtime.actors["broker"]
    runtime.create_agent(
        broker,
        purpose="形成跨 Question 仍可区分的语义来源",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    root = runtime.inject_human("检查当前 Question 的交付边界")
    value = runtime.turn_driver.model_input(
        runtime.turn_host(broker, root),
        (),
        runtime.tools.catalog_for(broker.spec.capabilities),
    )

    send_tool = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "send_question"
    )
    description = send_tool["function"]["description"]

    assert "这才使当前结果责任进入 recipient 的处境" in description
    assert "私有 purpose 标签不会交付" in description
    assert "创建者也不会为 recipient 写入持久自然语言 workflow" in description
    assert "自主判断行动" in description


def test_broker_projects_open_question_responsibilities_from_the_act_log() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="form one release confirmation",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    position = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("Return a release confirmation")
    runtime.queue.clear()
    delegated = runtime.send_from_actor(
        broker,
        root,
        recipient=position.spec.actor_id,
        kind="question",
        payload=question_payload("Return the confirmation after receiving one code"),
        derived_from=(root.act_id,),
    )
    runtime.queue.clear()
    request = runtime.send_from_actor(
        position,
        delegated,
        recipient=broker.spec.actor_id,
        kind="incremental",
        payload={"content": "Provide the missing code."},
        reply_to=delegated.act_id,
    )
    runtime.queue.clear()
    human_request = runtime.send_from_actor(
        broker,
        request,
        recipient="human",
        kind="incremental",
        payload={"content": "Provide the code."},
        reply_to=root.act_id,
    )
    human_reply = runtime.reply_human(
        thread_id=root.thread_id,
        reply_to=human_request.act_id,
        content="The code is CODE-9.",
    )

    before_answer = runtime.turn_driver.model_input(
        runtime.turn_host(broker, human_reply), (), ()
    )

    assert before_answer.local_world["open_question_responsibilities"] == [
        {
            "question_id": root.act_id,
            "answer_expected_from": broker.spec.actor_id,
            "answerable_by_actor": True,
        },
        {
            "question_id": delegated.act_id,
            "answer_expected_from": position.spec.actor_id,
            "answerable_by_actor": False,
        },
    ]
    assert (
        semantic_input_data(before_answer)["local_world"][
            "open_question_responsibilities"
        ]
        == before_answer.local_world["open_question_responsibilities"]
    )

    position_answer = runtime.reply_from_actor(
        position,
        delegated,
        kind="answer",
        payload=answer_payload("Release confirmed with CODE-9."),
    )
    after_answer = runtime.turn_driver.model_input(
        runtime.turn_host(broker, position_answer), (), ()
    )

    assert after_answer.local_world["open_question_responsibilities"] == [
        {
            "question_id": root.act_id,
            "answer_expected_from": broker.spec.actor_id,
            "answerable_by_actor": True,
        }
    ]
    assert (
        "open_question_responsibilities"
        not in runtime.turn_driver.model_input(
            runtime.turn_host(position, delegated),
            (),
            (),
        ).local_world
    )


def test_default_agent_input_is_self_contained_without_design_theory() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="未使用"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="判断当前问题",
        instructions="",
        budget={"world_ops": 2},
    )
    agent = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("判断事件结果")
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=agent.spec.actor_id,
        kind="question",
        payload=question_payload(
            "他接下来会怎么做？",
            interpretation_content="他：小明；阶段：事件 ABC 之后",
            intent="信息范围：只依据小明知道的信息",
        ),
        derived_from=(root.act_id,),
    )
    value = runtime.turn_driver.model_input(
        runtime.turn_host(agent, question),
        (),
        runtime.tools.catalog_for(agent.spec.capabilities),
    )

    messages = OpenAICompatibleModel.render_messages(value)
    tools = OpenAICompatibleModel.protocol_tools(value)
    current_input = semantic_input_data(value)
    assert "budget" not in current_input
    assert current_input["capability_boundaries"] == {
        question.act_id: {"semantic_world_operations": {"remaining_capacity": 2}}
    }
    assert "external_contexts" in current_input
    assert "role" not in current_input["actor"]
    assert current_input["answer_responsibilities"][0]["act_id"] == question.act_id
    assert current_input["visible_acts"] == []
    assert current_input["local_world"]["domain"] == "semantic"
    tool_names = {item["function"]["name"] for item in tools}
    assert {
        "answer_question",
        "ask_external_question",
        "ask_in_existing_context",
        "world_references_from",
        "world_add_object",
        "world_add_reference",
    } <= tool_names
    for name in (
        "world_references_from",
        "world_add_object",
        "world_add_reference",
    ):
        description = next(
            item["function"]["description"]
            for item in tools
            if item["function"]["name"] == name
        )
        assert "成功执行" in description
        assert "1 次语义操作能力" in " ".join(description.split())
        assert "能力余量变化" in description
    read_reference = next(
        item for item in tools if item["function"]["name"] == "world_references_from"
    )
    assert "不继续展开它的 target" in read_reference["function"]["description"]
    assert (
        "已经进入 local_world 的 source Object"
        in (read_reference["function"]["description"])
    )
    assert "更深 target 仍只是地址" in read_reference["function"]["description"]
    assert "Agent-level" not in read_reference["function"]["description"]
    assert "属于当前认知位置" in read_reference["function"]["description"]
    assert "acquisition_grounding_id" in read_reference["function"]["description"]
    assert "直接或传递依赖" in read_reference["function"]["description"]
    add_object = next(
        item for item in tools if item["function"]["name"] == "world_add_object"
    )
    add_reference = next(
        item for item in tools if item["function"]["name"] == "world_add_reference"
    )
    reference_parameters = add_reference["function"]["parameters"]
    assert reference_parameters["properties"]["meaning"]["type"] == "string"
    assert "meaning" not in reference_parameters["required"]
    assert (
        "source-relative"
        in reference_parameters["properties"]["meaning"]["description"]
    )
    assert (
        "只断言 source 引用 target"
        in reference_parameters["properties"]["meaning"]["description"]
    )
    assert (
        "持续世界语义" in reference_parameters["properties"]["meaning"]["description"]
    )
    assert "新的身份" in add_object["function"]["description"]
    assert "不自动接入" in add_object["function"]["description"]
    assert "不修改两端" in add_reference["function"]["description"]
    assert "不创建 Object" in add_reference["function"]["description"]
    assert "当前 Decision 的私有说明" in messages[1]["content"]
    assert "target content 不复制进 `meaning`" in messages[1]["content"]
    assert "未知不表示对方一定没有" in messages[0]["content"]
    assert "不能被假定为已经拥有" in messages[0]["content"]
    assert "示例、否定、排除条件" in messages[0]["content"]
    assert "guaranteed_shared_context" in messages[0]["content"]
    assert "World Understanding" not in "\n".join(
        message["content"] for message in messages[:2]
    )
    assert all(
        any("\u4e00" <= char <= "\u9fff" for char in item["function"]["description"])
        for item in tools
    )


def test_agent_model_view_projects_all_responsibilities_without_broker_topology() -> (
    None
):
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    first = runtime.create_agent(
        broker,
        purpose="parent resolver",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    second = runtime.create_agent(
        broker,
        purpose="gap resolver",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    parent_actor = runtime.actors[str(first["actor_id"])]
    child_actor = runtime.actors[str(second["actor_id"])]
    root = runtime.inject_human("Resolve one parent Question")
    parent = runtime.send_from_actor(
        broker,
        root,
        recipient=parent_actor.spec.actor_id,
        kind="question",
        payload=question_payload("Resolve the parent"),
        derived_from=(root.act_id,),
    )
    runtime.turn_driver.model_input(runtime.turn_host(parent_actor, parent), (), ())
    gap = runtime.send_from_actor(
        parent_actor,
        parent,
        recipient="broker",
        kind="question",
        payload=question_payload("Resolve the external gap"),
    )
    delegated = runtime.send_from_actor(
        broker,
        gap,
        recipient=child_actor.spec.actor_id,
        kind="question",
        payload=dict(gap.payload),
        derived_from=(gap.act_id,),
    )
    child_answer = runtime.reply_from_actor(
        child_actor,
        delegated,
        kind="answer",
        payload=answer_payload("gap result"),
    )
    returned = runtime.reply_from_actor(
        broker,
        child_answer,
        kind="answer",
        payload=dict(child_answer.payload),
    )

    value = runtime.turn_driver.model_input(
        runtime.turn_host(parent_actor, returned), (), ()
    )
    current_input = semantic_input_data(value)

    assert value.answer_responsibilities == (parent,)
    assert current_input["answer_responsibilities"][0]["act_id"] == parent.act_id
    assert current_input["arrival"]["reply_to"] == gap.act_id
    assert "derived_from" not in current_input["arrival"]
    assert all(
        act["act_id"] != returned.act_id for act in current_input["visible_acts"]
    )
    assert any(act["act_id"] == gap.act_id for act in current_input["visible_acts"])
    assert all(act["act_id"] != parent.act_id for act in current_input["visible_acts"])
    assert all("derived_from" not in act for act in current_input["visible_acts"])
    assert "capabilities" not in current_input["actor"]


def test_question_addresses_form_initial_local_world_without_world_capability() -> None:
    world = SemanticWorld()
    seed = world.add_object("initial addressed meaning", object_id="object:initial")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="use initial context",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    actor = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human(
        "Use the initial meaning",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
    )
    question = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=dict(root.payload),
        derived_from=(root.act_id,),
    )

    value = runtime.turn_driver.model_input(runtime.turn_host(actor, question), (), ())

    assert value.capability_catalog == ()
    assert value.question_budgets == {question.act_id: {}}
    assert value.local_world["objects"] == [
        {
            "object_id": seed.object_id,
            "content": seed.content,
            "semantic_address": {"kind": "object", "id": seed.object_id},
        }
    ]


def test_broker_can_only_forward_exact_visible_semantic_addresses() -> None:
    world = SemanticWorld()
    seed = world.add_object("opaque meaning", object_id="object:opaque")
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused")),
        world=world,
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="receive opaque address",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    root = runtime.inject_human(
        "Forward one address",
        semantic_addresses=(SemanticHandle("object", seed.object_id),),
    )

    forwarded = runtime.send_from_actor(
        broker,
        root,
        recipient=str(created["actor_id"]),
        kind="question",
        payload=dict(root.payload),
        derived_from=(root.act_id,),
    )
    assert forwarded.payload["interpretation_context"]["semantic_addresses"] == [
        {"kind": "object", "id": seed.object_id}
    ]

    with pytest.raises(ValueError, match="only forward exact Semantic Addresses"):
        runtime.send_from_actor(
            broker,
            root,
            recipient=str(created["actor_id"]),
            kind="question",
            payload=question_payload(
                "Do not rewrite the address",
                semantic_addresses=(SemanticHandle("object", "opaque"),),
            ),
            derived_from=(root.act_id,),
        )


def test_broker_model_view_keeps_sources_for_answer_responsibilities() -> None:
    runtime = build_default_runtime(
        ScriptedModel(lambda _value: Decision("stop", reason="unused"))
    )
    broker = runtime.actors["broker"]
    created = runtime.create_agent(
        broker,
        purpose="one answer",
        instructions="",
        budget={},
        capabilities=frozenset(),
    )
    actor = runtime.actors[str(created["actor_id"])]
    root = runtime.inject_human("Resolve through one Agent")
    delegated = runtime.send_from_actor(
        broker,
        root,
        recipient=actor.spec.actor_id,
        kind="question",
        payload=question_payload("Return one result"),
        derived_from=(root.act_id,),
    )
    answer = runtime.reply_from_actor(
        actor,
        delegated,
        kind="answer",
        payload=answer_payload("result"),
    )

    value = runtime.turn_driver.model_input(runtime.turn_host(broker, answer), (), ())
    current_input = semantic_input_data(value)

    assert value.answer_responsibilities == (root,)
    assert current_input["answer_responsibilities"][0]["act_id"] == root.act_id
    assert all(act["act_id"] != answer.act_id for act in current_input["visible_acts"])
    assert all(act["act_id"] != root.act_id for act in current_input["visible_acts"])
    delegated_view = next(
        act
        for act in current_input["visible_acts"]
        if act["act_id"] == delegated.act_id
    )
    assert delegated_view["derived_from"] == [root.act_id]
    assert "broker_id" not in current_input["local_world"]["context"]
    assert "member_ids" not in current_input["local_world"]["context"]
    assert "history" not in current_input["local_world"]
    assert all("role" not in actor for actor in current_input["local_world"]["actors"])


def test_agent_question_and_answer_tools_use_strong_semantic_schemas() -> None:
    base = _input("schema")
    tools = OpenAICompatibleModel.protocol_tools(base)
    ask = next(
        item for item in tools if item["function"]["name"] == "ask_external_question"
    )
    answer = next(
        item for item in tools if item["function"]["name"] == "answer_question"
    )
    ask_properties = ask["function"]["parameters"]["properties"]
    address = ask_properties["interpretation_context"]["properties"][
        "semantic_addresses"
    ]["items"]

    assert address["required"] == ["kind", "id"]
    assert address["additionalProperties"] is False
    assert ask_properties["raw_question"]["type"] == "string"
    assert (
        ask_properties["interpretation_context"]["properties"]["content"]["type"]
        == "string"
    )
    assert ask_properties["intent"]["type"] == "string"
    assert answer["function"]["parameters"]["properties"]["answer"]["type"] == (
        "string"
    )
    assert "enum" not in answer["function"]["parameters"]["properties"]["question_id"]
    assert (
        "理解 raw_question" in ask_properties["interpretation_context"]["description"]
    )
    assert (
        "事件阶段"
        in ask_properties["interpretation_context"]["properties"]["content"][
            "description"
        ]
    )
    address_description = ask_properties["interpretation_context"]["properties"][
        "semantic_addresses"
    ]["description"]
    assert "同一身份会在处理前物化" in address_description
    assert "Local World" in address_description
    assert "不请求 world capability" in address_description
    assert "复制普通文本不会传递语义身份" in address_description
    assert "Reference 地址" in address_description
    assert "target 保持地址而不递归展开" in address_description
    assert "合格 Answer" in ask_properties["intent"]["description"]
    assert "投影规则" in ask_properties["intent"]["description"]
    assert "不描述认知位置配置" in ask_properties["intent"]["description"]
    assert ask["function"]["parameters"]["required"] == [
        "question_id",
        "raw_question",
        "interpretation_context",
        "intent",
    ]
    assert answer["function"]["parameters"]["required"] == [
        "question_id",
        "answer",
        "semantic_addresses",
        "grounding_inputs",
    ]
    grounding = answer["function"]["parameters"]["properties"]["grounding_inputs"]
    assert grounding["type"] == "array"
    assert grounding["uniqueItems"] is True
    assert "宿主只维护结构" in grounding["description"]
    assert "新的唯一最新 grounding root" in grounding["description"]
    assert "不会自动继承" in grounding["description"]
    assert any(
        variant["properties"]["kind"]["enum"] == ["act"]
        for variant in grounding["items"]["oneOf"]
    )
    grounded = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        {**base.local_world, "groundings": [{"grounding_id": "grounding:test"}]},
        base.observations,
        base.capability_catalog,
        answer_responsibilities=base.answer_responsibilities,
        question_budgets=base.question_budgets,
    )
    grounded_answer = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(grounded)
        if item["function"]["name"] == "answer_question"
    )
    grounding_variant = next(
        variant
        for variant in grounded_answer["function"]["parameters"]["properties"][
            "grounding_inputs"
        ]["items"]["oneOf"]
        if variant["properties"]["kind"]["enum"] == ["grounding"]
    )
    assert "继承" in grounding_variant["description"]
    assert "observation" in grounding_variant["description"]
    assert "question_id" in ask["function"]["description"]
    assert "Answer" in ask["function"]["description"]
    assert "新的外部 Answer context" in ask["function"]["description"]
    assert "不继承已有 context" in ask["function"]["description"]
    assert "另一个认知域" not in ask["function"]["description"]
    assert "未来生长路线" not in ask["function"]["description"]
    answer_address_description = answer["function"]["parameters"]["properties"][
        "semantic_addresses"
    ]["description"]
    assert "地址到达私有 Semantic 局部时" in answer_address_description
    assert "Coordination 局部中只被持有和转交" in answer_address_description
    assert "未填写的私有处境不会自动交付" in answer["function"]["description"]
    assert "Human" not in ask["function"]["description"]
    assert "后一情况应" not in ask["function"]["description"]
    assert "question_id" in answer["function"]["description"]
    assert "本次激活" not in ask["function"]["description"]
    assert all(item["function"]["name"] != "stop_activation" for item in tools)
    assert all(item["function"]["name"] != "call_capability" for item in tools)

    assert "recipient" not in ask_properties
    assert "derived_from" not in ask_properties
    assert any(item["function"]["name"] == "yield_turn" for item in tools)


def test_broker_question_schema_keeps_typed_sources_without_dynamic_ids() -> None:
    broker_base = _broker_input("schema")
    assert any(
        item["function"]["name"] == "send_question"
        for item in OpenAICompatibleModel.protocol_tools(broker_base)
    )
    assert any(
        item["function"]["name"] == "answer_question"
        for item in OpenAICompatibleModel.protocol_tools(broker_base)
    )
    broker_with_agent = _native_input(
        broker_base.actor,
        broker_base.trigger,
        broker_base.visible_acts,
        {
            "domain": "coordination",
            "actors": [{"actor_id": "agent-existing", "role": "agent"}],
        },
        (
            Observation(
                "inspect_inquiry_topology",
                True,
                value={
                    "actors": [
                        {"actor_id": "agent-existing", "role": "agent"},
                    ]
                },
            ),
        ),
        broker_base.capability_catalog,
        answer_responsibilities=broker_base.answer_responsibilities,
    )
    broker_send = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(broker_with_agent)
        if item["function"]["name"] == "send_question"
    )
    broker_properties = broker_send["function"]["parameters"]["properties"]
    assert "enum" not in broker_properties["question_id"]
    sources = broker_properties["source_act_ids"]
    assert sources["uniqueItems"] is True
    assert sources["minItems"] == 1
    assert "enum" not in sources["items"]
    assert "共同根节点" in sources["description"]
    assert "不是 Answer 的返回目标" in sources["description"]
    assert "reply_to" in sources["description"]
    assert "derived_from" not in broker_properties
    assert "结束本次处理" not in broker_send["function"]["description"]
    assert "保留它此前的对话和 Local World" in broker_send["function"]["description"]
    assert "其他协调事实不自动交付" in broker_send["function"]["description"]

    assert all(
        item["function"]["name"] != "stop_activation"
        for item in OpenAICompatibleModel.protocol_tools(broker_base)
    )


def test_world_capability_tool_requires_question_target() -> None:
    tools = OpenAICompatibleModel.protocol_tools(_input("schema"))
    direct = next(
        item for item in tools if item["function"]["name"] == "world_references_from"
    )
    assert direct["function"]["parameters"]["required"] == [
        "question_id",
        "object_id",
    ]
    assert "enum" not in direct["function"]["parameters"]["properties"]["question_id"]
    assert direct["function"]["parameters"]["properties"]["object_id"] == {
        "type": "string"
    }


def test_inspection_observation_does_not_change_fixed_broker_tools() -> None:
    base = _broker_input("reuse")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        base.local_world,
        (
            Observation(
                "inspect_inquiry_topology",
                True,
                value={
                    "actors": [
                        {"actor_id": "agent-existing", "role": "agent"},
                    ]
                },
            ),
        ),
        base.capability_catalog,
    )

    assert any(
        item["function"]["name"] == "send_question"
        for item in OpenAICompatibleModel.protocol_tools(value)
    )


def test_broker_recipient_schema_is_independent_of_projected_members() -> None:
    base = _broker_input("persistent-actors")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        {
            "domain": "coordination",
            "actors": [
                {
                    "actor_id": "agent-existing",
                    "role": "agent",
                    "capabilities": [],
                }
            ],
        },
        base.observations,
        base.capability_catalog,
        answer_responsibilities=base.answer_responsibilities,
    )

    send = next(
        item
        for item in OpenAICompatibleModel.protocol_tools(value)
        if item["function"]["name"] == "send_question"
    )

    assert "enum" not in send["function"]["parameters"]["properties"]["recipient"]


def test_broker_schema_is_stable_without_answer_responsibility() -> None:
    base = _broker_input("already answered")
    value = _native_input(
        base.actor,
        base.trigger,
        base.visible_acts,
        {
            "domain": "coordination",
            "actors": [{"actor_id": "agent-existing", "role": "agent"}],
        },
        base.observations,
        base.capability_catalog,
        answer_responsibilities=(),
    )

    assert "send_question" in {
        item["function"]["name"] for item in OpenAICompatibleModel.protocol_tools(value)
    }
