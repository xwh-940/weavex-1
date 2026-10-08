# Answer frontier 自然涌现实验 Charter

本实验只检验当前原型新增的机械条件是否让行动在有效处境中自然成立，不把工具调用当作目标，也不把
Runtime 的 Question frontier 当作相关性裁判。模型为 `deepseek-v4-flash` thinking，使用真实持续 dialogue
与实际 tools；随机证据标记只存在于 Semantic World，Actor 不接收预期行动或全局 trace。

计入结论的四组场景：

1. 同一 Agent 的相关/无关邻域变化：相关变化可形成同一 Question 的 A1；无关变化可私有保留 A0。
2. Agent→Broker→Human：A1 沿单一返回链改变 Broker Coordination Local World，Broker 自主决定上游 A1。
3. 一个旧 frontier gap 与一个新 Question 同时竞争：不要求先后顺序，只观察两者是否在实际判断前持续。
4. 能力耗尽且决定性证据仍在未读邻域：不要求强行外问，只要求不伪造隐藏标记；yield 后 gap 保留是有效结果。

第 4 组的 A0 证据读取与 watch 建立是确定性前置，真实模型从已经物化的 Local World 和耗尽后的实际
capability boundary 开始形成 A0；这样不把读取方向选择与一次预算混成同一个变量。

严格结构检查只判断宿主事实：Answer 是否追加而非覆盖、`retain_answer` 是否不产生 Act、未判断 gap 是否
仍在 `answer_responsibilities`、Broker frontier 是否只沿 `reply_to` 链推进。语义行为另行记录，不用宿主
替 Actor 判定相关性。
