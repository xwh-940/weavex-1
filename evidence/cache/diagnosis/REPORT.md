# 持续多 Actor 实验的缓存命中诊断

诊断日期：2026-09-09。结论：主要损失来自持续变化的 tools schema 对请求前缀的破坏；activation 结束时移除 reasoning 是第二个实际前缀变化来源；完整 Local World 的重复追加放大了前缀失效时的输入重算量。当前采样未发现命中率汇总错误，可见 messages 本身保持追加。

## 证据范围

- 复核 `initialization_v0/run-real/snapshot.json`：62 个真实请求。
- 复核 `liuwen_20260904_first/main-1/traces.jsonl`：140 个真实请求。
- 复核 `situation_v1/real_full` 与 `real_tail` 共 16 份 snapshot：1325 个真实请求。
- 新执行 11 次真实 provider 对照，模型 `deepseek-v4-flash`，总输入 55,800 tokens、输出 206 tokens；全部正常返回，无重试或失败。
- 没有修改 src、Actor 提示、Runtime 权限或既有实验状态。新实验只测 provider 前缀，不作为多 Actor 自然行为的证据。

历史数据按 `(actor_id, act_id, round)` 关联 `model.input` 和 `provider.usage`，比较同一 Actor 前后请求的 messages 和 tools。所有命中率都是 `sum(hit) / sum(hit + miss)`，不是请求百分比的简单平均。1527 个历史请求均满足 `hit + miss == prompt_tokens`；同一 Actor 的相邻可见 messages 未出现历史改写。历史 trace 不含隐藏 reasoning，因此无法直接恢复历史完整 provider token 流。

## 受控真实实验

人工构造约 5,000 input tokens 的无敏感记录与模拟 assistant/tool 历史。reasoning_content 是实验员公开撰写的占位文本，不是模型隐藏推理。固定其他请求参数，串行调用并间隔 6 秒。实际生成的隐藏 reasoning 不保存或跨请求复用。

| 条件 | hit tokens | miss tokens | 命中率 |
| --- | ---: | ---: | ---: |
| 首次基线 | 0 | 5,083 | 0.00% |
| 原请求重复 | 4,992 | 91 | 98.21% |
| 仅在末尾 user 文本追加标记 | 4,992 | 98 | 98.07% |
| 仅给 question_id enum 增加一个 ID | 0 | 5,088 | 0.00% |
| 重复新 enum 请求 | 4,992 | 96 | 98.11% |
| 仅把工具集合换成 receive_input | 256 | 4,787 | 5.08% |
| 重复 receive_input 集合请求 | 4,992 | 51 | 98.99% |
| 原 tools 不变，仅删除历史 reasoning 字段 | 256 | 4,802 | 5.06% |
| 重复删除 reasoning 后的请求 | 4,992 | 66 | 98.70% |
| 恢复完整基线 | 4,992 | 91 | 98.21% |
| 再次重复完整基线 | 4,992 | 91 | 98.21% |

这组结果区分了“服务没有缓存”与“请求变成新的前缀分支”：每个变体重复后都能高命中，恢复旧请求也能直接命中旧分支。它不能给出每个真实运行中各因素独立贡献的精确比例，但证实很小的 schema/历史字段变化足以使后方长记录失去复用。

## 首要原因：tools 本身处于缓存前缀中且频繁变化

DeepSeek 的缓存复用已落盘的完整前缀单元，并非在全文中寻找重复片段。官方 V4 编码参考把 tools 渲染进 system 内容；线上 API 的具体编码位置不完全公开，但本次控制实验直接确认 schema 改动会破坏后面的对话缓存。[官方缓存规则](https://api-docs.deepseek.com/guides/kv_cache/)、[官方 V4 编码参考](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/blob/main/encoding/encoding_dsv4.py)

当前 `tool_schema.py` 的变动来源包括：

- 每次 activation 第一次只提供 `receive_input`，之后切换为实际行动集合；这是每轮都会出现的一次集合切换。
- `question_id`、`source_act_ids`、incremental 的 `reply_to`、recipient 和 context 均枚举当前合法 ID。
- `grounding_inputs` 枚举当前 observation、Object、Reference、prior grounding 和可见 Act。读取邻域、写入世界或新增 Act 会让其中的列表变化。
- Answer/retain 推进 frontier 后，责任和合法工具随之消失或改变；预算耗尽也会移除 world tools。

只改变一个枚举也足以影响后方整个长历史，而不是只重算那几个新增字符。动态工具往往还未被重复使用就再次变化，因此每个新分支很难获得长缓存的摊销。

历史运行与此一致：

| 样本 | 请求数 | 合计命中率 | 同一 activation 内 schema 变化 | 同一 activation 内 schema 不变 |
| --- | ---: | ---: | --- | --- |
| initialization_v0 | 62 | 31.93% | 46 次，25.14% | 3 次，79.86% |
| liuwen main-1 | 140 | 17.41% | 105 次，2.47% | 1 次，99.43% |
| situation_v1 共 16 份 | 1325 | 35.15% | 960 次，27.93% | 26 次，90.62% |

这些是观察分组，不是随机消融，尤其 schema 不变组样本较少。situation_v1 中 schema 变化组占输入 token 的 83.88%，因此主导总比例。

一个具体例子：liuwen `agent-1 / act-44` 第 1 轮、只有 receive_input 时命中 601,728 / 680,026 tokens（88.49%）；第 2 轮切换到动态行动 tools 后仅命中 3,840 / 779,599（0.49%）。同一个 Actor 的历史仍完整存在，前缀复用却被工具变化截断。第 1 轮可以重用过去相同 receive_input schema 的旧分支；这也解释了为何每轮第一步有时高命中，而紧接着骤降。

## 第二个来源：跨 activation 删除 reasoning 改变实际历史

`provider.py:_provider_messages` 只回传当前 activation 的 reasoning，`end_activation` 清除此缓存。`runner.py` 在 finally 中调用清除。于是上一 activation 实际请求中出现过的 reasoning，在下一 activation 的历史中消失。

这不是可见 dialogue 改写，因此 `model.input.messages` 的追加检查看不出来。控制实验在其他内容完全相同情况下，仅删除该历史字段，将命中从 98.21% 降为 5.06%。这证明该因素确实可以破坏缓存；历史 trace 没有此字段，无法从既有记录算出它的独立损失。

截至本次查询，DeepSeek 文档要求携带 tools 时在后续轮次继续回传历史 reasoning_content；官方样例也保留 Turn 1 reasoning 到 Turn 2。这与仓库当前 activation-local 约束存在冲突。本次删除人工历史 reasoning 的对照仍被 API 接受，未返回文档描述的 400；因此不能声称历史运行必然因此报错。[当前思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)

不能为了缓存直接把隐藏 reasoning 写入 Actor dialogue 或跨 activation 留存：那会改变项目当前明确的认知连续性边界。应先决定支持的 provider transport 契约与该语义边界如何兼容。

## 放大因素：完整 Local World 重复进入长期历史

`runtime.py:build_situated_input` 每次投影完整 Local World 和所有 grounding。`runner.py:project_receive_observation` 主要去重已投影的 Act，不对完整 Local World 做增量投影。

liuwen 最后一个 agent-1 请求的历史中，11 次 receive_input 累计包含：

- 187 个不同 Object，出现 1629 次；184 个不同 Reference，出现 1597 次。
- Object/Reference 的投影合计 1,006,941 个 JSON 字符，按稳定身份只计一次为 117,538 字符，约 8.57 倍。
- 完整 Local World 投影合计 1,336,525 字符，其中 grounding 为 286,679 字符。

这里的字符统计不是 provider token 统计，也没有把语义摘要或未知内容当作可删除数据。这种重复追加本身不会破坏已有前缀，但会增加首次输入量，并在 tools 改变时扩大必须重算的长后缀。长期对话越长，固定几千 token 的命中量占比越低。

## 已排除或暂不支持的解释

- 汇总分母错误：样本中未发现；统计没有混入 completion tokens。
- 所有 Actor 每次新开空会话：可见 messages 检查不支持；每个 Actor 都保留自己的历史。
- 多 Actor 数量本身是主因：不能这样解释。situation_v1 的 Actor 首次请求合计命中 98.05%，初始固定前缀可以跨请求共享；liuwen 仅两个 Actor，整体反而更低。
- 没开缓存：控制请求重复后约 98% 命中，缓存有效。
- 服务端落盘、过期、路由或竞争：官方明确缓存尽力而为，落盘需要时间、闲置会清理。这些可能影响个别请求，但无法替代本次单变量变化的直接证据。

## 后续处理顺序

1. 首先评估“每步精确动态 schema”与 provider 前缀缓存的兼容方式。单纯缩短 prompt、增加等待或调整调度不能消除持续变化的工具前缀。
2. 独立评估 receive_input 与实际行动集合的每轮切换；固定 receive_input 分支可以高命中，但不能把这个局部数字误当作后续行动也会高命中。
3. 核对 reasoning transport 契约。任何跨 activation 保存策略都需要正面处理当前项目边界，不能作为无语义影响的缓存优化偷偷加入。
4. 再考虑保留历史语义的 Local World 增量投影，以减少新增输入与前缀失效的代价。

不建议未经语义设计就冻结全工具集合、删除所有合法 ID enum 或把 action catalog 转写到旁路文本；这些会改变当前模型实际行动空间及宿主与模型的契约。此次完成的是原因判断，没有实施这些改变。

## 工件

- `CHARTER.md`、`probe.py`：受控真实实验约定及脚本。
- `probe-fixtures.json`：全部人工输入及单变量变体，不包含 secret 或生成的隐藏 reasoning。
- `probe-results.json`：11 次实际响应 usage 与请求哈希。
- `analyze.py`、`summary.json`、`requests-00.json` 至 `requests-17.json`：历史逐请求审计。
- `summarize.py`、`combined.json`：汇总和重复材料统计。
