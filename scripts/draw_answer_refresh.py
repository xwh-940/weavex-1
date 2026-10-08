"""Render the case-study diagram as SVG and PNG (optional matplotlib dependency)."""

from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1440, 840
INK = "#203746"
MUTED = "#667b89"
BLUE = "#3974b8"
TEAL = "#277f76"
WARM = "#a86435"


def choose_font(explicit: str | None, bold: bool = False):
    if explicit:
        return font_manager.FontProperties(fname=explicit)
    windows_font = Path(
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
    )
    if windows_font.exists():
        return font_manager.FontProperties(fname=windows_font)
    for family in (
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Microsoft YaHei",
        "PingFang SC",
    ):
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
            return font_manager.FontProperties(
                fname=path, weight="bold" if bold else "normal"
            )
        except ValueError:
            continue
    raise RuntimeError("A Chinese font is required; pass --font /path/to/font.ttf")


def render(font: str | None = None):
    regular, bold = choose_font(font), choose_font(font, bold=True)
    plt.rcParams.update(
        {"svg.fonttype": "path", "svg.hashsalt": "weavex-answer-refresh-v1"}
    )
    fig = plt.figure(figsize=(WIDTH / 100, HEIGHT / 100), dpi=100, facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set(xlim=(0, WIDTH), ylim=(HEIGHT, 0))
    ax.set_axis_off()

    def text(x, y, content, size=20, color=INK, weight=False, align="left"):
        return ax.text(
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

    def box(x, y, w, h, fill="white", edge="none", radius=16, linewidth=1.2):
        patch = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=fill,
            edgecolor=edge,
            linewidth=linewidth,
            zorder=1,
        )
        ax.add_patch(patch)

    def line(points, color, width=1.8):
        xs, ys = zip(*points)
        ax.plot(xs, ys, color=color, linewidth=width, solid_capstyle="round", zorder=2)

    def arrow(x1, y1, x2, y2, color, scale=13):
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=scale,
                linewidth=1.8,
                color=color,
                shrinkA=0,
                shrinkB=0,
                zorder=3,
            )
        )

    def check(x, y, color, radius=19):
        ax.add_patch(
            Circle(
                (x, y),
                radius,
                facecolor="white",
                edgecolor=color,
                linewidth=1.4,
                zorder=2,
            )
        )
        line([(x - 8, y), (x - 2, y + 6), (x + 9, y - 7)], color, 2)

    def document(x, y, color):
        box(x, y, 30, 37, fill="white", edge=color, radius=5, linewidth=1.4)
        for offset, length in ((11, 16), (18, 16), (25, 10)):
            line([(x + 7, y + offset), (x + 7 + length, y + offset)], color, 1.2)

    # Opening: a concrete question, followed by a shared initial state.
    text(52, 47, "WeaveX", 23, BLUE, True)
    line([(167, 34), (167, 60)], "#d9e4eb", 1)
    text(185, 47, "持续反馈", 19, MUTED)
    text(52, 102, "新信息到了，旧答案怎么办？", 38, INK, True)
    text(52, 145, "同一个发布审核问题，两种变化，两种处理结果。", 21, MUTED)

    for x, number, label in (
        (52, "01", "已有答案"),
        (397, "02", "来源新增信息"),
        (777, "03", "模型复查"),
        (1097, "04", "用户看到"),
    ):
        text(x, 208, number, 16, BLUE, True)
        text(x + 35, 208, label, 19, MUTED)

    box(52, 269, 256, 303, fill="#f0f5fd", edge="#cddff2", radius=22)
    check(88, 308, BLUE)
    text(121, 308, "发布审核", 20, MUTED)
    text(78, 375, "允许发布", 34, BLUE, True)
    text(78, 417, "当前证据支持这一结论", 19, MUTED)
    line([(78, 449), (282, 449)], "#d2e1f2", 1)
    text(78, 484, "记录判断依据", 20, INK, True)
    text(78, 520, "关注相关信息来源", 19, MUTED)

    # The branches represent separate cases, never two successive events.
    line([(308, 420), (345, 420)], "#9badbc", 1.7)
    line([(345, 338), (345, 524)], "#9badbc", 1.7)
    ax.add_patch(Circle((345, 420), 4, color=BLUE, zorder=3))
    arrow(345, 338, 380, 338, WARM)
    arrow(345, 524, 380, 524, TEAL)

    for y, fill, edge in ((249, "#fff8f0", "#f0dfca"), (437, "#f0f9f6", "#cfe6de")):
        box(382, y, 1006, 176, fill=fill, edge=edge, radius=22)

    # Risk branch.
    document(404, 272, WARM)
    text(449, 290, "发现安全风险", 25, INK, True)
    text(404, 342, "新增未隔离的高危故障", 21, MUTED)
    box(404, 372, 171, 28, fill="#f6e7d4", radius=8)
    text(418, 386, "影响原来的判断", 16, WARM, True)
    arrow(691, 337, 747, 337, WARM)
    box(769, 285, 220, 108, fill="white", edge="#ecd7bd", radius=16)
    text(879, 319, "需要更正", 26, WARM, True, "center")
    text(879, 359, "追加新的答案", 19, MUTED, align="center")
    arrow(1006, 337, 1062, 337, WARM)
    text(1093, 313, "暂停发布", 31, WARM, True)
    text(1093, 354, "向用户返回更正", 22, INK)
    text(1093, 388, "用户收到更新后的结论", 17, MUTED)

    # Administrative-change branch.
    document(404, 460, TEAL)
    text(449, 478, "更新归档编号", 25, INK, True)
    text(404, 530, "行政记录变化，安全情况未变", 19, MUTED)
    box(404, 560, 171, 28, fill="#dfefe8", radius=8)
    text(418, 574, "不影响原来的判断", 16, TEAL, True)
    arrow(691, 525, 747, 525, TEAL)
    box(769, 473, 220, 108, fill="white", edge="#c9e1d7", radius=16)
    text(879, 507, "原答案仍成立", 24, TEAL, True, "center")
    text(879, 547, "私下记录保留判断", 18, MUTED, align="center")
    arrow(1006, 525, 1062, 525, TEAL)
    text(1093, 501, "仍然允许发布", 29, TEAL, True)
    text(1093, 542, "不重复通知", 22, INK)
    text(1093, 576, "用户无需再读一遍相同答案", 17, MUTED)

    # Secondary facts, separated from the causal story.
    box(52, 662, 1336, 119, fill="#f6f8fb", edge="#e5ebf1", radius=18)
    document(77, 701, BLUE)
    text(127, 703, "保留历次答案", 23, INK, True)
    text(127, 742, "更正以追加方式记录，旧答案仍可追溯。", 19, MUTED)
    line([(742, 686), (742, 758)], "#dce5ec", 1)
    text(782, 703, "系统找到待复查的问题", 21, BLUE, True)
    text(782, 742, "模型判断是否需要修改答案", 21, TEAL, True)
    text(
        1388,
        810,
        "案例示意 · 两种变化分别发生；模型判断也可能出错",
        16,
        MUTED,
        align="right",
    )

    output = ROOT / "docs/assets"
    output.mkdir(parents=True, exist_ok=True)
    svg, png = output / "answer-refresh.svg", output / "answer-refresh.png"
    fig.savefig(svg, format="svg", metadata={"Date": None, "Creator": "WeaveX"})
    fig.savefig(png, format="png", dpi=200, facecolor="white")
    plt.close(fig)

    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    tree = ET.parse(svg)
    root = tree.getroot()
    root.set("role", "img")
    root.set("aria-labelledby", "answer-refresh-title answer-refresh-desc")
    title = ET.Element(f"{{{namespace}}}title", id="answer-refresh-title")
    title.text = "WeaveX：新信息到来后的答案复查"
    desc = ET.Element(f"{{{namespace}}}desc", id="answer-refresh-desc")
    desc.text = (
        "已有答案是允许发布。安全风险出现时，模型追加暂停发布的新答案并返回用户；"
        "仅更新归档编号时，模型保留原答案，不重复通知。系统找到待复查问题，"
        "模型判断是否修改答案，旧答案保留可追溯。两种变化分别发生。"
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
