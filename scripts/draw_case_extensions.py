"""Render two evidence-case figures with the same visual style as the README."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET

import matplotlib.pyplot as plt
from draw_answer_refresh import BLUE, INK, MUTED, ROOT, TEAL, WARM, choose_font
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


class Figure:
    def __init__(self, title, subtitle, font):
        self.regular, self.bold = choose_font(font), choose_font(font, bold=True)
        plt.rcParams.update(
            {"svg.fonttype": "path", "svg.hashsalt": "weavex-evidence-cases-v1"}
        )
        self.fig = plt.figure(figsize=(14.4, 8.2), dpi=100, facecolor="white")
        self.ax = self.fig.add_axes((0, 0, 1, 1))
        self.ax.set(xlim=(0, 1440), ylim=(820, 0))
        self.ax.set_axis_off()
        self.title = title
        self.text(52, 47, "WeaveX", 23, BLUE, True)
        self.text(185, 47, "运行案例", 19, MUTED)
        self.text(52, 103, title, 35, INK, True)
        self.text(52, 147, subtitle, 20, MUTED)

    def text(self, x, y, content, size=20, color=INK, bold=False, align="left"):
        self.ax.text(
            x,
            y,
            content,
            fontsize=size * 0.72,
            color=color,
            fontproperties=self.bold if bold else self.regular,
            ha=align,
            va="center",
            zorder=5,
        )

    def box(self, x, y, w, h, fill="#f0f5fd", edge="#cddff2", radius=20):
        self.ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle=f"round,pad=0,rounding_size={radius}",
                facecolor=fill,
                edgecolor=edge,
                linewidth=1.3,
                zorder=1,
            )
        )

    def arrow(self, x1, y1, x2, y2, color=BLUE):
        self.ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=14,
                linewidth=1.8,
                color=color,
                shrinkA=0,
                shrinkB=0,
                zorder=2,
            )
        )

    def save(self, name, description):
        output = ROOT / "docs/assets"
        svg, png = output / f"{name}.svg", output / f"{name}.png"
        self.fig.savefig(
            svg, format="svg", metadata={"Date": None, "Creator": "WeaveX"}
        )
        self.fig.savefig(png, format="png", dpi=200, facecolor="white")
        plt.close(self.fig)
        ns = "http://www.w3.org/2000/svg"
        ET.register_namespace("", ns)
        tree = ET.parse(svg)
        root = tree.getroot()
        root.set("role", "img")
        root.set("aria-labelledby", "figure-title figure-desc")
        title = ET.Element(f"{{{ns}}}title", id="figure-title")
        title.text = self.title
        desc = ET.Element(f"{{{ns}}}desc", id="figure-desc")
        desc.text = description
        root.insert(0, title)
        root.insert(1, desc)
        tree.write(svg, encoding="utf-8", xml_declaration=True)
        print(png)


def collaboration(font):
    f = Figure(
        "缺少依据时，Agent 可以发起新的协作",
        "E41-003：联系由模型发起；初始角色、原件与共享地址线索由实验提供。",
        font,
    )
    f.box(52, 206, 1336, 56, fill="#f6f8fb", edge="#e5ebf1", radius=12)
    f.text(77, 234, "当前任务", 18, MUTED, True)
    f.text(
        187, 234, "原协调者交付修订要求，原 Agent 需要取得另一位置保存的原件。", 21, INK
    )
    f.box(52, 313, 300, 220)
    f.text(78, 350, "原 Agent", 30, BLUE, True)
    f.text(78, 392, "agent-site · 已有参与者", 18, MUTED)
    f.text(78, 449, "发现原件尚未取得", 22, INK, True)
    f.text(78, 491, "保留自己的问题责任", 20, MUTED)

    f.box(572, 313, 288, 220, fill="#fff8f0", edge="#f0dfca")
    f.text(598, 350, "新 Broker", 30, WARM, True)
    f.text(598, 392, "broker-1 · 运行中创建", 18, MUTED)
    f.text(598, 449, "组织新的问答关系", 22, INK, True)
    f.text(598, 491, "从候选中选择来源", 20, MUTED)

    f.box(1070, 313, 318, 220, fill="#f0f9f6", edge="#cfe6de")
    f.text(1096, 350, "材料持有者", 30, TEAL, True)
    f.text(1096, 392, "agent-facility · 已有参与者", 18, MUTED)
    f.text(1096, 449, "持有预置的原始记录", 22, INK, True)
    f.text(1096, 491, "根据收到的问题回答", 20, MUTED)

    f.arrow(370, 350, 554, 350, WARM)
    f.text(462, 321, "提出新问题", 20, WARM, True, "center")
    f.text(462, 379, "act-7", 16, MUTED, align="center")
    f.arrow(878, 350, 1052, 350, WARM)
    f.text(965, 321, "选择并提问", 20, WARM, True, "center")
    f.text(965, 379, "act-8 → act-9", 16, MUTED, align="center")

    f.arrow(1052, 474, 878, 474, TEAL)
    f.text(965, 443, "返回原件", 20, TEAL, True, "center")
    f.text(965, 505, "act-10", 16, MUTED, align="center")
    f.arrow(554, 474, 370, 474, TEAL)
    f.text(462, 443, "回到原发起者", 20, TEAL, True, "center")
    f.text(462, 505, "act-11", 16, MUTED, align="center")

    f.box(52, 584, 1336, 151, fill="#f6f8fb", edge="#e5ebf1")
    f.text(78, 622, "原问题继续推进", 24, INK, True)
    f.text(78, 665, "原 Agent 据取得的材料修订判断，经原协调者返回用户。", 22, INK)
    f.text(78, 704, "act-12 → act-13；原件对象已进入原 Agent 的局部材料。", 19, MUTED)
    f.text(
        1388,
        775,
        "样本范围：6 次运行，5 次进入修订并完成联系；1 次未进入。",
        17,
        MUTED,
        align="right",
    )
    f.save(
        "dynamic-collaboration",
        "原 Agent 发起新问题，新 Broker 在运行中创建并选择已有材料持有者。原件回答沿原问题返回，原 Agent 继续修订并由原协调者向用户交付。初始角色与材料由实验提供。",
    )


def shared_records(font):
    f = Figure(
        "写下的判断，怎样进入后来者的工作？",
        "分别核查：是否写入、是否接入记录、另一个参与者是否实际取得。",
        font,
    )
    f.text(52, 215, "E29 · 明确要求修订并维护既有记录", 21, TEAL, True)
    for x in (52, 532, 1012):
        f.box(x, 251, 376, 145, fill="#f0f9f6", edge="#cfe6de")
    f.text(78, 290, "Agent 写入修订", 27, TEAL, True)
    f.text(78, 336, "实际新增对象与引用", 21, INK)
    f.text(78, 370, "表示与连接方式由模型选择", 18, MUTED)
    f.text(558, 290, "接入共享案件记录", 27, TEAL, True)
    f.text(558, 336, "修订材料有可达路径", 21, INK)
    f.text(558, 370, "公开答复中的声明可回查", 18, MUTED)
    f.text(1038, 290, "另一 Agent 取得材料", 27, TEAL, True)
    f.text(1038, 336, "同一对象进入读者局部信息", 20, INK)
    f.text(1038, 370, "原评审记录：采用修订版", 18, MUTED)
    f.arrow(445, 321, 514, 321, TEAL)
    f.arrow(925, 321, 994, 321, TEAL)

    f.text(52, 455, "E32 · 处理争议，判断停留在答复中", 21, WARM, True)
    for x in (52, 532, 1012):
        f.box(x, 491, 376, 145, fill="#fff8f0", edge="#f0dfca")
    f.text(78, 530, "答复包含修订判断", 27, WARM, True)
    f.text(78, 576, "没有实际世界写入", 21, INK)
    f.text(78, 610, "不能仅凭答复认定记录已变", 18, MUTED)
    f.text(558, 530, "案件仍连接旧副本", 27, WARM, True)
    f.text(558, 576, "修订材料未进入后续读者输入", 20, INK)
    f.text(558, 610, "新的理解没有成为可取得的材料", 18, MUTED)
    f.text(1038, 530, "后来者继续用旧记录", 27, WARM, True)
    f.text(1038, 576, "旧结论再次出现在回答中", 21, INK)
    f.text(1038, 610, "原评审记录：stale_closure", 18, MUTED)
    f.ax.plot([445, 514], [561, 561], color=WARM, linewidth=1.5, linestyle=(0, (3, 3)))
    f.ax.plot([474, 487], [553, 569], color=WARM, linewidth=2)
    f.ax.plot([474, 487], [569, 553], color=WARM, linewidth=2)
    f.arrow(925, 561, 994, 561, WARM)

    f.box(52, 682, 1336, 67, fill="#f6f8fb", edge="#e5ebf1", radius=14)
    f.text(
        78,
        715,
        "本实验中的后来者仅依据案件记录工作，需要实际可取得的修订材料。",
        22,
        INK,
        True,
    )
    f.text(
        1388,
        789,
        "两组各 3 次；任务与材料不同，不能据此计算写入的纯因果收益。",
        17,
        MUTED,
        align="right",
    )
    f.save(
        "shared-records",
        "E29 中模型实际写入并接入修订材料，另一位读者取得该材料。E32 中答复未形成世界写入，后续读者仍未取得修订版。两组条件不同，图仅展示可核查的路径和断点。",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font")
    args = parser.parse_args()
    collaboration(args.font)
    shared_records(args.font)
