# 动态协作与共享记录工件

本目录新增 18 次历史运行：E41 六次；E29、E30、E31、E32 各三次。所有数据均读取自研究工作区，未重跑模型。

| 条件 | 内容 | 目录 |
| --- | --- | --- |
| E41 | 缺少原件时发起跨组协作，保留未进入修订分支的一次运行 | [dynamic-collaboration](dynamic-collaboration/) |
| E29 | 模型写入修订材料，其他读者随后取得 | [shared-records](shared-records/) 中 `e29-*` |
| E30 | 预置修订版可取得 | 同目录 `e30-*` |
| E31 | 旧副本条件，修订未进入读者局部材料 | 同目录 `e31-*` |
| E32 | 争议答复未形成世界写入，后来者继续取得旧记录 | 同目录 `e32-*` |

每次运行保留 `manifest.json`、`result.json`、`public-acts.json`、`usage.json`、`source-hashes.json` 和 `core-snapshot.json.gz`。快照为原 JSON 字节的无损 gzip 压缩，元数据记录原字节与压缩文件的两种 SHA-256。

[总清单](manifest.json)连接每次运行与对应的[冻结源码包](sources/)。同组源码哈希相同，按版本去重保存为五个 ZIP；归档内的 75 个文件逐项按原 `source-hashes.json` 校验。它们包括当时运行时、提示词、场景、接线及实验脚本，只作为历史出处，不进入当前 Python package。

原始全量 `interface.jsonl` 未复制；语义正确性仍参考保存的公开回答与原模型评审。结构核验可以独立检查对象身份、图上的出向可达性、参与者局部材料、成功工具效果和消息返回关系。

```bash
python scripts/verify_extended.py
python scripts/verify_evidence.py
python scripts/replay.py --case collaboration
python scripts/replay.py --case shared-record
python scripts/replay.py --case stale-record
```

[audit-summary.json](audit-summary.json)是派生核验结果。常规核验不改写文件；只有显式执行 `python scripts/verify_extended.py --write-summary` 才重新生成它。综合核验还会比较该文件与即时计算结果。

两个案例分别说明解释边界：[动态协作](../../docs/cases/dynamic-collaboration.md)、[共享记录](../../docs/cases/shared-records.md)。冻结源码中的绝对或旧相对路径不保证能直接用于本副本，运行时刻的 Git commit 也不由当前源码 HEAD 代替。
