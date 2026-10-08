# 图形资源

- [architecture.png](architecture.png) / [architecture.svg](architecture.svg)：复制自研究工作区的架构概览，来源哈希在根目录清单中。
- [answer-refresh-mechanism.png](answer-refresh-mechanism.png) / [answer-refresh-mechanism.svg](answer-refresh-mechanism.svg)：首页通用机制图，以大字呈现依赖、变化、复查与结果处理，具体案例仅作小字注释。
- [answer-refresh.png](answer-refresh.png) / [answer-refresh.svg](answer-refresh.svg)：答案复查案例文档中的发布审核场景图。
- [dynamic-collaboration.png](dynamic-collaboration.png) / [dynamic-collaboration.svg](dynamic-collaboration.svg)：E41-003 的实际联系与返回关系。
- [shared-records.png](shared-records.png) / [shared-records.svg](shared-records.svg)：E29 模型写入与后来者取得、E32 未写入的断点，分开标注两组条件。

两张答案复查图分别提供[机制图源码](../../scripts/draw_answer_refresh_mechanism.py)和[案例图源码](../../scripts/draw_answer_refresh.py)，保留文案、坐标和颜色。PNG 保证字体和排版一致，SVG 可缩放，文字已转为轮廓。重新生成仅需要额外安装 `matplotlib`，不属于运行环境依赖：

```bash
python scripts/draw_answer_refresh_mechanism.py
python scripts/draw_answer_refresh.py
python scripts/draw_case_extensions.py
```

脚本优先使用本机中文字体；也可用 `--font /path/to/font.ttf` 指定。生成内容位于本目录。

新增两张案例图由 [draw_case_extensions.py](../../scripts/draw_case_extensions.py) 生成，放在案例文档中；首页保留架构和通用机制两张图。
