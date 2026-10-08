# WeaveX

**支持动态协作与持续反馈的多智能体运行环境（Agent Harness）**

WeaveX 用 Python 实现模型会话、工具执行、权限检查和消息投递。Agent 可以因执行中的信息缺口发起新的协作，把形成的判断写成其他参与者可取得的材料，并在来源变化后重新评估已有答案。每个参与者保留独立对话与已接触材料，协调者 Broker 根据公开的问题与回答组织协作。

项目围绕三个问题展开：**缺少依据时怎样联系新的来源，形成的认识怎样被后来者使用，以及相关信息变化后怎样继续判断。** 模型选择行动；运行环境维护信息边界、来源依赖与实际执行效果。

[快速运行](#快速运行) · [动态协作](docs/cases/dynamic-collaboration.md) · [共享记录](docs/cases/shared-records.md) · [答案复查](docs/cases/answer-refresh.md) · [架构与代码](docs/ARCHITECTURE.md) · [实现边界](docs/LIMITATIONS.md)

## 核心能力

| 能力 | 实现内容 | 代码入口 |
| --- | --- | --- |
| 按需形成协作 | Agent 可因缺口提出新问题，经过新协调关系取得结果，继续原责任；同一 Agent 可参与多个组 | [communication.py](src/weavex/communication.py)、[coordination.py](src/weavex/coordination.py) |
| 共享材料与持续依据 | Agent 可写入内容和引用，其他参与者经合法路径取得；同一 Agent 的判断依赖可跨问题复用 | [world.py](src/weavex/world.py)、[grounding.py](src/weavex/grounding.py) |
| 回答的持续复查 | 来源变化后定位受影响的问题，模型追加新答案或私下保留旧答案，多个问题各自保持责任 | [question_frontier.py](src/weavex/question_frontier.py)、[runtime.py](src/weavex/runtime.py) |
| 明确的执行边界 | 模型执行后端与状态管理分离；宿主统一检查工具参数、权限、责任及额度 | [execution.py](src/weavex/execution.py)、[turn_host.py](src/weavex/turn_host.py) |
| 增量输入与运行观察 | 对已交付材料去重；保留工具轨迹、token 用量及 provider 缓存统计 | [native_session.py](src/weavex/native_session.py)、[cost_accounting.py](src/weavex/cost_accounting.py) |

三个阅读入口：[动态协作轨迹](docs/cases/dynamic-collaboration.md)说明如何继续取得依据；[共享记录案例](docs/cases/shared-records.md)说明写入与后来者使用之间的联系；[后端边界](docs/BACKEND_CONTRACT.md)与[缓存诊断](docs/cases/cache-continuity.md)说明 Harness 的工程设计。

## 工作方式

![WeaveX 协作架构：用户、Broker、独立 Agent、工具与内容引用](docs/assets/architecture.png)

Broker 本身也是 LLM Agent，理解公开结果并组织协作。业务 Agent 能读取自己取得地址所关联的材料；Broker 无法直接读取这些地址背后的内容。模型提出行动，运行环境检查是否合法并执行。架构支持多种协作方式，图中的关系不规定每次任务必须经历的步骤。

来源出现新增信息后，系统依据已有观察与判断依赖找到受影响的问题，再由模型决定更新答案或保留原结论：

![答案复查机制：已有答案记录信息依赖；来源新增内容后，系统定位受影响的问题，由模型重新判断；需要更新时追加答案并回传，原答案仍成立时私下确认、不重复通知。](docs/assets/answer-refresh-mechanism.png)

具体场景和实际轨迹见[答案复查案例](docs/cases/answer-refresh.md)，内部消息如何传递见[技术时序图](docs/ARCHITECTURE.md#答案复查的消息时序)。模型判断仍可能出错，案例文档中也保留了失败记录。

## 快速运行

需要 Python 3.11+。以下命令在仓库根目录运行，不要求 Conda、GPU 或特定 shell。

**先查看已有真实模型轨迹：无需安装依赖、API key 或网络。**

```bash
python scripts/verify_evidence.py
python scripts/replay.py --case collaboration
python scripts/replay.py --case shared-record
python scripts/replay.py --case stale-record
python scripts/replay.py --case relevant
```

这会检查存档与冻结源码哈希、复算结构事实和缓存用量，并显示保存的模型调用与公开回答。回放不会重新调用模型。原答案复查的 `irrelevant`、`failure` 回放也继续保留，全部选项见 `python scripts/replay.py --help`。

**再运行当前代码中的最小例子：**

```bash
python examples/answer_refresh.py --arm relevant
python examples/answer_refresh.py --arm irrelevant
```

默认使用明确编写的决策脚本，实际执行本仓库的会话循环、工具校验、依赖追踪和消息回传。`relevant` 会先返回 `ALLOW-DEMO`，再追加 `BLOCK-DEMO`；`irrelevant` 只保留一个公开答案，并执行一次私下保留。它验证运行机制，不计入真实模型实验成绩。

安装可选依赖、运行测试：

```bash
python -m pip install -e ".[debug,test]"
python -m pytest -q
```

[开发说明](docs/DEVELOPMENT.md)包含独立虚拟环境、真实模型运行与本地工作台入口。真实运行需要显式 `--live`、模型配置和新的输出目录；本次整理未调用在线模型。

## 已有实验观察

| 案例 | 保存的观察 | 能支持的结论 |
| --- | --- | --- |
| 动态协作，2026-09-18 | E41 六次中五次进入修订，均形成新协作并取得原件；另一次未进入该分支 | 在预置来源与共享线索下，Agent 可自行发起联系，材料沿原问题关系返回 |
| 共享记录，2026-09-18 | E29 三次均有模型写入及另一读者取得；另保留 E30–E32 共九次可得性探针与失败 | 实际写入、可达关系、材料取得及语义采用应分别核查 |
| 答案复查，2026-08-28 | 两种路径 × 两种信息变化 × 三次运行，共 12 次主对照符合预期 | 在小型、相关性明确的场景中，变化投递、更新／保留和上游回传能够共同工作 |
| 缓存诊断，2026-09-09 | 11 次受控 API 请求；另一个三参与者回归共 29 次请求，输入 token 加权命中率 97.70% | 稳定工具定义、持续历史和增量输入可在该回归中同时工作 |

答案复查保留整批 **18 次**记录；动态协作与共享记录另保留 **18 次**，包含未进入分支及传播失败的情况。缓存案例保留初次未闭环运行和一次工具拒绝。各组条件不同，不能合并成总成功率。案例均提供[原始证据](evidence/README.md)、核验方式与限制；结构事实和原模型的语义评审分开报告，也不据此声称跨框架优势。

## 进一步阅读

- [架构与代码导航](docs/ARCHITECTURE.md)：状态由谁拥有，信息如何流动，关键测试在哪里。
- [设计取舍](docs/DESIGN.md)：信息隔离、动态协作、来源依赖、固定 schema 与宿主校验。
- [执行后端与宿主边界](docs/BACKEND_CONTRACT.md)：状态归属、统一工具执行与第二种驱动器的一致性测试。
- [项目表述与证据对应](docs/CLAIMS_AND_EVIDENCE.md)：各项能力可支持的表述，以及实现、历史观察与比较优势的区别。
- [答案复查案例](docs/cases/answer-refresh.md)：条件、结果、代表轨迹和失败。
- [缓存诊断案例](docs/cases/cache-continuity.md)：原因定位、实现对应与统计复算。
- [验证记录](docs/VALIDATION.md)：当前副本实际执行的检查。
- [版本与证据来源](docs/PROVENANCE.md)：当前源码与历史实验的区别、字节哈希及未纳入范围。

当前实现是**单进程、内存态、串行调度的研究原型**。会话持续到 reset，没有跨进程持久恢复；内置世界工具操作文本内容及引用关系。更大规模协作收益和对强基线的优势仍待验证，详见[实现边界](docs/LIMITATIONS.md)。
