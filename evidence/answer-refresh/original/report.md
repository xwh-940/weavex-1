# Answer frontier 实现后真实实验报告

正式矩阵共 18 个 `deepseek-v4-flash` thinking run，全部初始功能条件成立。无效 smoke 不计入：Broker 组
最初错误清空了 A0 delivery queue；能力耗尽组最初又把读取方向选择和一次预算混在一起。两项均修正为
单变量夹具后重跑并覆盖对应文件。
最终代码把 Broker capability 投影也统一到当前 Question gap 后，Broker→Human 六次又完整重跑并覆盖，
结果仍为 6/6；因此逐 run 文件与最终行动空间一致。

## 结果

- Agent frontier 相关/无关对照 6/6：相关 3/3 追加同一 Question 的 A1；无关 3/3 调用
  `retain_answer`，没有重复公开 Answer。
- Agent→Broker→Human 6/6：相关 3/3 形成 Agent A1 和 Human A1；无关 3/3 停在 Agent 的私有
  retain，没有唤起 Broker/Human 新公开节点。
- 竞争责任 3/3：delta 到达时旧 Question gap 与新 Question 同时存在，模型每次都在同一 activation
  形成旧 A1 和新 A0；后来新 Question 自身 arrival 只 yield。没有再出现旧 gap 被一次 arrival 遮蔽。
- 能力耗尽 3/3 未伪造隐藏随机标记。1 次形成外部完整 Question，1 次用 incremental 公开缺口且
  frontier 保持 pending，1 次错误调用 `retain_answer`，使 gap 消失。

## 当前解释

frontier 机制解决的是“尚未判断的真实变化是否仍构成有效处境”这一机械问题，不保证 Actor 的语义判断
正确。最后一个反例中，输入明确说明决定性结论仍在不可读邻域，模型却判断 A0 仍充分；Runtime 若阻止
这次 retain，就必须开始裁决开放语义相关性与充分性。因此当前保留反例，不加入 mandatory update、
retain 审计或宿主证据规则。后续更适合用重复真实实验和处境表达消融研究，而不是把一次错误固化为框架
语义。

完整计数见 `summary.json`，逐 run 文件保留 tool calls、Answer Acts、frontier 结果和 provider usage。
