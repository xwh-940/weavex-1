# 稳定 schema 与增量对话的三 Actor 真实回归

研究问题：修改后的默认 NativeTurnDriver 在持续多 Actor Question/Answer 中，是否保持每个 Actor 的工具
schema 与实际 provider 消息前缀稳定，并且保留正确的 Answer 责任与材料暴露边界？缓存命中率是外部观测，
不进入 Actor 问题或提示。

初始条件：一个 Broker 和两个 Agent，Broker 初始成员为这两个 Agent。每个 Agent 分别得到一个具有下游
编号的起点 Object，其初始普通 Question 要求取得编号；仅配置 world_references_from，每个新 Question
预算 2。Broker 的初始普通 Question 是报告协调入口就绪。初始 Agent Answer 返回 initialization，不广播。

初始化队列自然处理后，Human 依次提出三个完整 Question：分别返回两个起点的编号；判断两个编号是否相同
并列出依据；返回第一个起点的编号。第一项交付两个不透明地址，之后沿用当前 Broker 的公开历史。
这些普通结果要求不指定 Actor 数量、路由、工具调用或 grounding。没有额外 Human 隐藏事实或运行中世界改动。

运行上限：全部过程最多 20 个 activation、每个 activation 12 次模型调用，单请求 60 秒，无自动重试，
max_tokens=2048，总观察时间 8 分钟；出现 provider/activation 失败立即停止并保存现场。队列自然耗尽时进入
下一项已经授权的 Human Question，不自动回应其他需求。无需新增世界、工具或部署。

观测字段：既有 trace/usage、每个请求的 Actor、activation、固定 schema 哈希、逐消息哈希与前缀一致性。
实际 reasoning 仅按新的 Actor-private provider 契约保留，不输出到工件。无全局状态注入 Actor。

本次是功能和缓存的回归观察，不以固定多 Actor 拓扑或某个缓存百分比作为模型语义成功条件。与此前不同
场景的总体命中率不能作为严格提升幅度。修改前后的受控单变量因果证据见 cache-diagnosis-20260909。
