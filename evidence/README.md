# 实验证据目录

| 目录 | 内容 |
| --- | --- |
| [answer-refresh](answer-refresh/) | 18 个逐次运行 JSON、原汇总、原始约定、报告和脚本 |
| [cache/diagnosis](cache/diagnosis/) | 11 次受控请求的夹具和响应，历史请求派生审计表及原分析脚本 |
| [cache/continuity](cache/continuity/) | 初次未闭环运行与后续 8192 输出上限运行，含请求记录和压缩 snapshot |
| [extended](extended/README.md) | E41 六次动态协作与 E29–E32 十二次共享记录运行，含无损快照和冻结源码 |

首批逐字复制工件的 SHA-256 在 [SOURCE_MANIFEST.json](../SOURCE_MANIFEST.json) 中；新增案例使用[独立清单](extended/manifest.json)，记录原字节与压缩工件哈希。运行 `python scripts/verify_evidence.py` 可统一离线校验并复算主要指标。

导读：[动态协作](../docs/cases/dynamic-collaboration.md)、[共享记录](../docs/cases/shared-records.md)、[答案复查](../docs/cases/answer-refresh.md)、[缓存诊断](../docs/cases/cache-continuity.md)。这些导读按公开目录重新组织；原记录保留当时措辞、路径与结论，以区分历史状态和当前实现。

## 历史脚本的使用范围

原始脚本只作为实验出处保留，不能视为已在当前版本适配的运行入口。它们可能引用未纳入的旧 `output/` 路径、旧工具参数，或默认写入自身目录；直接执行还可能发起真实 API 调用。公开入口见[开发说明](../docs/DEVELOPMENT.md)。

`answer-refresh/original/report.md` 中的 `summary.json`、逐 run 文件指上一级目录中的工件。缓存历史审计引用的上游长轨迹没有全部复制；已复制的逐请求表属于派生产物。当前核验能重算受控 probe 和回归统计，不能重新生成那批上游历史请求。

记录中保留历史模型名，不承诺该模型标识现在仍可调用。没有保存实际 API key，也没有把模型生成的隐藏推理作为公开轨迹。
