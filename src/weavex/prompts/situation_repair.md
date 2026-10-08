# 初始化卡的有限机械修正

下面的原始 brief、构建初稿和机械校验结果都只属于启动前的适配器。请修正初稿，使它成为一个可运行、仍可继续变化的初始化卡；不要替运行中的 Actor 写候选、结论、反应、工具顺序或工作流。

只修正校验实际指出的结构、身份、初始可见性、Reference 或预算问题。尽量保留原始任务、私有边界、材料原文、已有的局部问题与接触机会；如果其中某项正是错误来源，可以删除或改成更弱的合法接触条件。不得因为修复方便而把私有材料扩大为公开材料，也不得把 Broker ID 写进 `visible_to`。

`visible_to` 只能是 `positions` 中的 Agent `actor_id`。`brokers[].member_ids` 也只能是这些 Agent ID。每项 Reference 的 `source` 必须是已有 Object ID；`target_kind=object` 时 `target` 必须是已有 Object ID，`target_kind=reference` 时才是已有 Reference ID。Actor 和 Broker 身份不是 Semantic Address，不能成为 Reference 端点。初始 Reference 的端点会造成材料暴露；不要用它绕过私有边界。

保留“运行中尚未形成”的状态：修复不是生成候选或环境回应。若无法在不改变原始语义的前提下修复，保留最小合法起点和相应 assumptions，不要编造事实。

只输出 JSON：`{"spec": {...}, "assumptions": [...]}`。
