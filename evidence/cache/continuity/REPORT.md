# 三 Actor 缓存连续性真实回归

完成于 2026-09-09。核心源码已实现稳定 schema、宿主统一动态校验、跨 turn reasoning 私有回传，以及
Native Local World 增量投影。确定性回归共 190 项通过。

## 初始运行与失败保留

父目录初始运行采用 max_tokens=2048。三个初始化 Question 已处理；后续 Human Question 中，一次响应
没有返回工具调用，completion_tokens 恰为 2048。该运行保留 13 次请求，总缓存命中率 82.84%，
Human 根 Question 未闭环，没有把它当成功样本。

该次未保存 finish_reason，输出 token 达上限只是支持检查截断的证据，并非对原始停止原因的完全证明。
snapshot.json.gz、requests.jsonl 和 summary.json 保留现场。

## 相邻有界运行

保持同样的世界、初始 Actor、预算、Question 与提示，只将 max_tokens 调整为 8192，重新构造 Runtime。
该运行位于 retry-8192/，新增记录 finish_reason 和工具调用数量，不保存生成的隐藏 reasoning。

| 指标 | 实际结果 |
| --- | ---: |
| Actor | 3：一个 Broker、两个 Agent |
| activation | 8 |
| 模型请求 | 29 |
| 输入 tokens | 332,914 |
| 输出 tokens | 10,475 |
| 缓存 hit tokens | 325,248 |
| 缓存 miss tokens | 7,666 |
| token 加权缓存命中率 | 97.70% |
| 每 Actor 的 schema 版本数 | 均为 1 |
| 相邻实际请求改写旧消息前缀 | 0 |
| 未完成的直接责任 | 0 |
| activation / provider 失败 | 0 |

模型自主让一个已有 Agent 完成两个起点编号的公开结果，Broker 再依据公开历史回答比较与单一编号问题。
三个 Human Question 分别得到两条编号、两者不同的判断和第一个编号，全部正确。两个初始 Agent 的
结果仍返回 initialization，没有自动广播；后来的协调结果来自实际派生 Question。

## 一次合法拒绝及模型恢复

Broker 在新的 Inquiry 中尝试转交上一 Inquiry Answer 携带的 record-1/record-2 地址。宿主按既有
当前 Inquiry 地址权限拒绝，未产生额外 Act。该 Question 只要求比较编号文本，模型在同一 activation
保留结果文本、移除不合法的地址字段后成功回答。共一次失败 tool Observation，零 activation failure。

回归后补充了稳定地址 schema 的中文前置条件，明确区分 Agent Local World 地址权限与 Broker 当前
Question 所在 Inquiry 的可见 Act 地址权限；未放宽宿主校验。这项说明补充和最终格式整理没有重新跑
真实实验，报告中的请求哈希和百分比对应 retry-8192/ 内保存的实际版本。

## 增量交付核验

对最终持续 dialogue 的 receive_input 进行审计：

- Broker 共 5 次输入，公开成员描述只交付一次，grounding 节点没有重复。
- agent-1 共 2 次输入，第二次只交付新增起点；此前工具结果取得的内容没有再次完整投影。
- agent-2 共 1 次输入。
- 所有 Actor 的 receive_input 中，Object/Reference、grounding 和成员描述均没有重复材料 ID。

完整统计见 retry-8192/projection-audit.json。成功读取自身的结果仍正常返回，这里的去重只针对随后
Local World 处境投影，不把成功重复调用的真实 Observation 抹除。

## 结论边界

本次真实场景确认了固定 schema、持续 provider 前缀、增量材料及责任闭环能够共同工作。它是小型回归，
不能将 97.70% 与先前不同场景的 17%/35% 直接解释为同条件提升幅度。两次相邻运行可能共享服务端暖
前缀；更大规模、长时间闲置和服务端尽力缓存仍会影响总体比例。

原始工件均在本目录，Runtime、Actor 提示和实际请求的语义内容仍由 snapshot 与 trace 对照；任何
reasoning 只保留在运行时的 provider 私有内存中，工件只记录请求哈希及非推理元数据。
