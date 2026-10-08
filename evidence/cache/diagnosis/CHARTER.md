# 缓存前缀受控真实传输实验

研究问题：保持长对话不变时，仅改变 tools 的目标枚举、工具集合，或移除历史 reasoning 字段，会怎样改变 DeepSeek 实际报告的缓存命中？

本实验位于 Runtime 外部，是 provider 输入缓存实验，不检验 Actor 的语义判断或涌现。使用人工构造的无敏感信息记录、公开撰写的模拟 assistant/tool 历史，以及公开撰写的 reasoning_content 占位文本。它不是模型产生的隐藏推理。实际响应 reasoning 仅在本次调用内存在，不写入文件，不复用到下一调用。

保持 model、thinking、tool_choice、输出上限、基础 system 和长记录固定。每组两个或更多请求，使服务端有机会落盘。先测试完全相同请求与尾部追加；之后分别只改变一个 schema enum、切换为 receive_input 工具集合、移除人工 reasoning 字段，再恢复基线。缓存是尽力而为；重复调用用于检验暖缓存上界，不能把一次 miss 当作精确因果证明。

仅发送 11 个请求，串行执行，间隔 6 秒；单次请求超时 60 秒，无自动重试；每次 max_tokens=512。非预期传输错误终止；移除 reasoning 的条件允许记录 400 作为该对照的接口结果。总运行上限 15 分钟。任何实际返回内容都不进入 WeaveX Actor、OSW、Human Act 或历史实验状态。

观测：逐请求 hit/miss、prompt/completion tokens、请求耗时、schema 与 messages 哈希、输入组成长度、HTTP 状态。保存人工 fixture、实验脚本和统计，不保存 key、响应隐藏推理或服务端完整错误响应。

授权来自本次用户要求重新设计并运行真实实验。完成条件为受控对照结束，或 provider 故障使继续运行无意义。核心源码与模型侧提示不修改。
