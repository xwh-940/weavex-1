# 开发与运行

## 环境

Python 3.11+。证据核验、历史回放和默认确定性示例只用标准库与本仓库源码，无需模型服务或 GPU。

建议建立独立虚拟环境。Windows PowerShell：

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[debug,test]"
.venv\Scripts\python -m pytest -q
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[debug,test]"
.venv/bin/python -m pytest -q
```

下文的 `python` 代表你所选环境中的解释器。开发环境依赖来自 [pyproject.toml](../pyproject.toml)；本地实际验证版本见 [VALIDATION.md](VALIDATION.md)。

## 三种不同的运行方式

| 入口 | 执行内容 | 证据性质 |
| --- | --- | --- |
| `scripts/replay.py` | 读取保存的真实模型提案与公开回答 | 历史轨迹展示 |
| `examples/answer_refresh.py` | 固定决策脚本驱动当前运行环境 | 确定性机制示例 |
| `examples/answer_refresh.py --live` | 当前夹具与真实模型的新运行 | 需要单独记录的新实验 |

```bash
python scripts/verify_evidence.py
python scripts/replay.py --case relevant
python examples/answer_refresh.py --arm relevant
python examples/answer_refresh.py --arm irrelevant
```

保存确定性运行到新目录：

```bash
python examples/answer_refresh.py --arm relevant --output output/scripted-relevant-01
```

输出目录必须位于 `output/` 下且尚不存在；不会覆盖历史证据。记录包含模式、源码指纹、实际回答、检查结果和 snapshot。

## 真实模型运行

先安装 `debug` extra。当前 provider 适配包含 thinking 模式传输约定，应选择支持该协议的端点；不能仅凭“OpenAI compatible”认定完全兼容。

在终端环境中设置 `DEEPSEEK_API_KEY` 和明确的 `DEEPSEEK_MODEL`。可选设置 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MAX_TOKENS`，示例的输出上限默认为 8192。[.env.example](../.env.example)只列配置项，程序不会自动加载 `.env`。

```bash
python examples/answer_refresh.py --live --arm relevant --output output/live-relevant-01
python examples/answer_refresh.py --live --arm irrelevant --output output/live-irrelevant-01
```

`--live` 会消耗模型 API 用量。入口要求新的输出目录；每个阶段最多处理 20 个到达事件，每个活动仍使用原生 12 轮上限，单次请求超时 120 秒且关闭 SDK 重试。模型可以不采用预期行动；结果仍会保存，未闭环会返回非零退出码。初始答案未形成时不会注入第二阶段材料。

这不是对历史 12 次运行的逐字复现。角色、变化和检查口径由此示例固定；模型行动与结果必须根据新工件另行解释。本次公开版本整理未执行在线模型实验。

## 调试工作台

安装依赖后：

```bash
python -m weavex.debug_server
```

打开 <http://127.0.0.1:8766/> 查看参与者、问答和材料。`/demo` 保留“失落天文台”的补充演示，主要说明信息接触与持续经历。

未配置 API key 时，可以预置、注入和观察状态；模型执行会明确报错。配置 key 后，工作台的 step/run 会调用真实模型。建议显式设置模型名及输出上限，避免依赖研究代码的默认值。

工作台是开发者观察面，包含全局状态。页面中的人物或故事叙述不独立证明模型实际遵守了信息隔离，应结合各参与者视图与 trace 判断。

## 检查

```bash
python -m pytest -q
python scripts/verify_evidence.py
python -m ruff check scripts examples/answer_refresh.py tests/test_showcase.py
```

`src/weavex/`、原有测试与提示词在本副本中保持来源字节一致；展示脚本和新增测试独立存放。原有机制测试使用 ScriptedModel 或人工 provider 响应，不能当作真实模型的行为成功率。

`evidence/` 下的历史 Python 脚本是出处材料：有旧接口或旧路径，其中部分会写文件或调用模型。请使用本页列出的入口；原脚本的兼容性与输出位置未作为当前开发接口验证。
