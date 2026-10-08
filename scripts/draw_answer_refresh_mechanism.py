"""Render the general answer-refresh mechanism; examples remain small annotations."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET

from draw_answer_refresh import BLUE, INK, MUTED, ROOT, TEAL, WARM, choose_font
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch


def render(font: str | None = None):
    regular, bold = choose_font(font), choose_font(font, bold=True)
    width, height = 1440, 840
    plt.rcParams.update(
        {"svg.fonttype": "path", "svg.hashsalt": "weavex-answer-refresh-mechanism-v1"}
    )
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set(xlim=(0, width), ylim=(height, 0))
    ax.set_axis_off()

    def text(x, y, content, size=20, color=INK, weight=False, align="left"):
        ax.text(
            x,
            y,
            content,
            fontsize=size * 0.72,
            color=color,
            fontproperties=bold if weight else regular,
            ha=align,
            va="center",
            zorder=5,
        )

    def box(x, y, w, h, fill="white", edge="none", radius=18):
        ax.add_patch(
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

    def line(points, color="#b2c3d1", width=1.8):
        xs, ys = zip(*points)
        ax.plot(xs, ys, color=color, linewidth=width, solid_capstyle="round", zorder=2)

    def arrow(x1, y1, x2, y2, color=BLUE):
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=13,
                linewidth=1.8,
                color=color,
                shrinkA=0,
                shrinkB=0,
                zorder=3,
            )
        )

    def document(x, y, color=BLUE):
        box(x, y, 29, 36, edge=color, radius=5)
        for offset, length in ((11, 15), (18, 15), (25, 9)):
            line([(x + 7, y + offset), (x + 7 + length, y + offset)], color, 1.2)

    text(52, 47, "WeaveX", 23, BLUE, True)
    line([(167, 34), (167, 60)], "#d9e4eb", 1)
    text(185, 47, "持续反馈机制", 19, MUTED)
    text(52, 102, "信息变化，如何触发答案复查？", 38, INK, True)
    text(
        52, 145, "来源持续更新，问题重新进入判断；结果可以更新，也可以保留。", 21, MUTED
    )

    for x, number, label in (
        (52, "01", "答案与依据"),
        (365, "02", "来源出现变化"),
        (715, "03", "模型重新判断"),
        (1060, "04", "更新或保留"),
    ):
        text(x, 212, number, 16, BLUE, True)
        text(x + 35, 212, label, 19, MUTED)

    # Shared causal path; branching occurs only after the model's judgment.
    box(52, 321, 250, 243, fill="#f0f5fd", edge="#cddff2", radius=22)
    document(78, 347)
    text(121, 365, "已有答案", 28, BLUE, True)
    text(78, 415, "记录判断依赖", 22, INK, True)
    line([(78, 445), (276, 445)], "#d2e1f2", 1)
    text(78, 482, "已接触的材料", 20, MUTED)
    text(78, 523, "持续关注的信息来源", 19, MUTED)
    arrow(313, 442, 352, 442)

    box(365, 321, 296, 243, fill="#f6f9fd", edge="#d6e3ee", radius=22)
    text(391, 366, "来源新增内容", 27, INK, True)
    text(391, 413, "匹配已建立的观察关系", 20, MUTED)
    line([(391, 445), (635, 445)], "#dce5ee", 1)
    box(388, 471, 250, 58, fill="#e8f0fb", radius=12)
    text(513, 500, "系统找到受影响的问题", 20, BLUE, True, "center")
    arrow(673, 442, 702, 442)

    box(715, 321, 252, 243, fill="white", edge="#b8d2ed", radius=22)
    text(841, 368, "重新判断", 31, BLUE, True, "center")
    text(841, 416, "结合新信息与已有依据", 19, MUTED, align="center")
    line([(739, 445), (943, 445)], "#dce5ee", 1)
    text(841, 498, "原答案是否仍然成立？", 20, INK, True, "center")

    line([(967, 442), (1010, 442)])
    line([(1010, 339), (1010, 543)])
    ax.add_patch(Circle((1010, 442), 4, color=BLUE, zorder=3))
    arrow(1010, 339, 1048, 339, WARM)
    arrow(1010, 543, 1048, 543, TEAL)

    box(1060, 257, 328, 165, fill="#fff8f0", edge="#f0dfca", radius=22)
    text(1085, 284, "需要更新", 18, WARM)
    text(1085, 325, "追加新答案", 29, WARM, True)
    text(1085, 362, "沿原问题关系回传", 21, INK)
    text(1085, 396, "例：发现安全风险 → 暂停发布", 16, MUTED)

    box(1060, 461, 328, 165, fill="#f0f9f6", edge="#cfe6de", radius=22)
    text(1085, 488, "原答案仍成立", 18, TEAL)
    text(1085, 529, "保留原答案", 29, TEAL, True)
    text(1085, 566, "私下确认，不重复通知", 21, INK)
    text(1085, 600, "例：仅更新归档编号", 16, MUTED)

    # Cross-cutting properties are secondary to the flow.
    box(52, 672, 1336, 109, fill="#f6f8fb", edge="#e5ebf1", radius=18)
    document(77, 710)
    text(128, 711, "历次答案可追溯", 23, INK, True)
    text(128, 748, "新答案以追加方式记录，旧答案仍保留。", 19, MUTED)
    line([(742, 695), (742, 760)], "#dce5ec", 1)
    text(782, 711, "系统定位待复查的问题", 21, BLUE, True)
    text(782, 748, "模型决定更新还是保留", 21, TEAL, True)
    text(
        1388,
        810,
        "变化投递取决于观察与依赖关系；模型判断也可能出错。",
        16,
        MUTED,
        align="right",
    )

    output = ROOT / "docs/assets"
    output.mkdir(parents=True, exist_ok=True)
    svg = output / "answer-refresh-mechanism.svg"
    png = output / "answer-refresh-mechanism.png"
    fig.savefig(svg, format="svg", metadata={"Date": None, "Creator": "WeaveX"})
    fig.savefig(png, format="png", dpi=200, facecolor="white")
    plt.close(fig)

    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    tree = ET.parse(svg)
    root = tree.getroot()
    root.set("role", "img")
    root.set("aria-labelledby", "mechanism-title mechanism-desc")
    title = ET.Element(f"{{{namespace}}}title", id="mechanism-title")
    title.text = "WeaveX：信息变化如何触发答案复查"
    desc = ET.Element(f"{{{namespace}}}desc", id="mechanism-desc")
    desc.text = (
        "已有答案记录判断依据及关注来源。来源新增内容匹配观察关系时，系统找到受影响的问题，"
        "由模型结合新信息与已有依据重新判断。需要更新时追加答案并沿原问题关系回传；"
        "原答案仍成立时私下确认，不重复通知。历次答案可追溯。发布审核仅作为小字例子。"
    )
    root.insert(0, title)
    root.insert(1, desc)
    tree.write(svg, encoding="utf-8", xml_declaration=True)
    print(svg)
    print(png)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", help="Optional Chinese font file for rendering")
    render(parser.parse_args().font)
