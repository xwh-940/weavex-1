# 本地验证记录

验证日期：2026-10-08。平台：Windows，Python 3.12.3（Conda 环境）。所有执行均在独立副本中完成，没有运行真实模型调用。

## 已完成

| 检查 | 结果 |
| --- | --- |
| 原有测试 | 202 项通过 |
| 加上新示例与工件完整性测试 | 205 项通过 |
| 复制哈希 | 143 个复制文件与清单一致 |
| 答案复查证据 | 保留 18 次记录；12 次主对照的工具与答案记录符合保存的结果；失败反例保留 |
| 缓存统计 | 11 次 probe；29 次回归请求复算为 325,248 / 332,914 = 97.70% |
| 增量交付核验 | 回归 snapshot 中所核查的材料 ID 无重复投递；保留 1 次工具拒绝和初次未闭环运行 |
| 当前源码示例 | relevant、irrelevant 两分支通过，默认均为脚本决策 |
| 新增代码 lint | `ruff check` 通过 |
| wheel 构建 | `weavex_prototype-0.0.0-py3-none-any.whl` 构建成功 |
| wheel 独立导入 | 在 `python -I -S` 下从 wheel 解包目录加载核心包、提示词并创建 Runtime |
| 无第三方包入口 | `python -I -S` 下的确定性示例与证据核验通过 |
| 文档链接 | 90 个本地链接目标存在 |
| 图形检查 | 首页通用机制图与完整案例图的 PNG / SVG 已生成并目视检查；案例图位于案例文档，技术时序图位于架构文档 |

常用检查命令：

```bash
python -m pytest -q -p no:cacheprovider
python scripts/verify_evidence.py
python examples/answer_refresh.py --arm relevant
python examples/answer_refresh.py --arm irrelevant
python -m ruff check scripts examples/answer_refresh.py tests/test_showcase.py
python -m pip wheel --no-deps --no-build-isolation . -w dist
```

原源码、提示词和既有测试按字节保留；只对新增脚本和新增测试执行了格式化。

## 本地依赖版本

```text
pytest==8.4.2
pytest-asyncio==0.21.1
fastapi==0.127.0
httpx==0.28.1
openai==2.11.0
uvicorn==0.35.0
ruff==0.15.0
setuptools==75.1.0
```

以上是本地验证环境记录，不是包含全部传递依赖的锁文件。

## 未验证范围

未执行当前源码的真实模型重跑；历史 12 次结果与当前示例分别报告。未声称在 Linux 或 Python 3.11 上已经实测。

仓库包含面向 Ubuntu/Windows、Python 3.11/3.12 的 [GitHub Actions 配置](../.github/workflows/checks.yml)，本地整理时未上传，也未触发云端 CI。其结果应以上传后的实际工作流为准。
