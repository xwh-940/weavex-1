# 案例：持续会话中的缓存诊断与增量输入

## 问题与诊断

参与者已经保留连续历史，后续请求的缓存复用却不稳定。2026-09-09 的诊断从请求结构入手，复核历史 usage 与工具变化，并使用人工构造输入进行 11 次受控 API 调用。

| 受控请求变化 | 命中率 |
| --- | ---: |
| 原请求重复 | 98.21% |
| 仅在最后一条用户消息末尾追加文本 | 98.07% |
| 仅给工具参数枚举增加一个 ID | 0.00% |
| 重复修改后的枚举请求 | 98.11% |
| 仅删除历史 reasoning 字段 | 5.06% |

每个变体重复后重新获得高命中，支持“请求前缀变化破坏复用”的解释。reasoning 对照使用实验员编写的占位内容；工件不包含模型生成的隐藏推理。

历史请求审计涵盖 1,527 次请求。此副本保留逐请求派生表和原审计脚本，没有纳入所有上游长轨迹；这部分属于历史审计材料，其完整抽取过程不能仅靠本副本重跑。11 次受控请求的夹具与响应 usage 则完整保留。

[原始诊断报告](../../evidence/cache/diagnosis/REPORT.md) · [受控夹具](../../evidence/cache/diagnosis/probe-fixtures.json) · [响应记录](../../evidence/cache/diagnosis/probe-results.json)

## 与当前实现的对应

- [tool_schema.py](../../src/weavex/tool_schema.py)：工具定义依赖参与者配置，不动态枚举每次出现的身份。
- [turn_host.py](../../src/weavex/turn_host.py)：动态权限、参数与额度仍由执行层验证。
- [provider.py](../../src/weavex/provider.py)：维持 provider 所需的连续传输历史，私有传输字段不写入可见对话或共享材料。
- [native_session.py](../../src/weavex/native_session.py)：对已经交付的材料去重，继续表达当前责任等可变状态。

## 真实回归记录

最初 `max_tokens=2048` 的运行未闭环，保存了 13 次请求。一条响应没有工具提案，输出 token 恰好达到上限；原运行没有记录 `finish_reason`，所以不能完全证明停止原因。

随后保持世界、初始角色、问题和额度相同，将上限改为 8192，重新建立 Runtime。保存的结果如下：

| 指标 | 数值 |
| --- | ---: |
| 参与者 | 1 Broker + 2 Agent |
| 模型请求 | 29 |
| 输入 tokens | 332,914 |
| 输入缓存 hit tokens | 325,248 |
| 输入缓存 miss tokens | 7,666 |
| 加权缓存命中率 | 97.70% |
| 每个参与者的工具 schema 版本数 | 1 |
| 请求元数据中的历史前缀改写 | 0 |
| 结束时未完成的直接责任 | 0 |

计算方式为 `sum(hit_tokens) / sum(prompt_tokens)`，不对每次请求的百分比简单平均。

Broker 曾转交当前问题不允许携带的地址，被宿主拒绝；随后删除非法地址字段、保留结果文本并成功作答。共 1 次失败工具观察，0 次活动失败。对最终对话的核查未发现 `receive_input` 重复交付相同 Object、Reference、grounding 或成员 ID。

```bash
python scripts/verify_evidence.py --json
```

核验工具逐行汇总保存的请求 usage、与原汇总核对，并从压缩 snapshot 重新检查材料 ID 和工具失败记录。前缀未改写依据原请求哈希元数据；由于私有传输文本未存档，不能从工件重建全部线上字节流。

[原始回归报告](../../evidence/cache/continuity/REPORT.md) · [请求记录](../../evidence/cache/continuity/retry-8192/requests.jsonl) · [原汇总](../../evidence/cache/continuity/retry-8192/summary.json)

## 结论范围

这次回归说明几项机制在该小型场景中能够共同工作。两次相邻运行可能共享暖缓存；不能把 97.70% 与先前不同任务的 17% 或 35% 当成同条件提升，更不能直接换算成本或延迟下降。

回归之后原项目补充过工具地址前置条件的说明，未针对那次补充重跑真实实验。当前公开源码又有后续修改，因此以上数值仅属于保存的历史运行。
