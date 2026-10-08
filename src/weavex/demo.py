"""External demo Charter for the neutral Lost Observatory walkthrough."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .protocol import Act, SemanticHandle
from .world import ObjectAddress

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


@dataclass
class ObservatoryDemo:
    """Prepare Human Questions without prescribing any Actor decisions."""

    phase: int = 0
    question_ids: list[str] = field(default_factory=list)
    signature: str = ""
    public_code: str = ""
    fresh_text: str = ""

    def clear(self) -> None:
        self.phase = 0
        self.question_ids.clear()
        self.signature = ""
        self.public_code = ""
        self.fresh_text = ""

    def load(self, runtime: "PrototypeRuntime") -> Act:
        runtime.reset()
        self.clear()
        nonce = secrets.token_hex(4).upper()
        self.public_code = f"ASTRA-{secrets.token_hex(3).upper()}"
        self.signature = f"CAL-{secrets.token_hex(4).upper()}"
        self.fresh_text = f"NEBULA-{secrets.token_hex(5).upper()}-42"
        gate = runtime.local_worlds.world.add_object(
            "失落天文台的密封观测入口。公开口令与校准签名只存在于直接连接的日志中。",
            object_id=f"object:observatory-gate-{nonce}",
        )
        log = runtime.local_worlds.world.add_object(
            (
                f"第七码盘记录：本次可公开的穹顶口令是 {self.public_code}。"
                f"读取者同时会接触不得在第一幕公开的校准签名 {self.signature}。"
            ),
            object_id=f"object:observatory-log-{nonce}",
        )
        runtime.local_worlds.world.add_reference(
            gate.object_id,
            ObjectAddress(log.object_id),
            meaning="这份日志给出入口当前有效的穹顶口令与读取者私有校准签名。",
            reference_id=f"reference:observatory-log-{nonce}",
        )
        act = runtime.inject_human(
            "读取天文台入口直接连接的日志，找出当前穹顶口令，并用一句话说明它来自哪类记录。",
            interpretation_content=(
                "入口地址是唯一初始材料；日志内容必须由实际具有读取能力的认知位置取得。"
            ),
            semantic_addresses=(SemanticHandle("object", gate.object_id),),
            intent=(
                "Answer 必须包含完整穹顶口令和简短依据，但不得公开校准签名或日志 target 地址。"
            ),
        )
        self.phase = 1
        self.question_ids.append(act.act_id)
        return act

    def advance(self, runtime: "PrototypeRuntime") -> Act | None:
        if not self.phase:
            raise ValueError("demo is not active")
        if runtime.queue:
            raise ValueError("finish the current demo chapter before advancing")
        if not self._current_answered(runtime):
            raise ValueError("the current demo Question has no direct Answer")
        if self.phase == 1:
            act = runtime.inject_human(
                "让上一幕实际读取日志的同一持续认知位置，返回它已经接触但尚未公开的完整校准签名。",
                interpretation_content=(
                    "本轮不再提供 Semantic Address；结果必须来自上一幕真实读取者的持续私有接触。"
                ),
                intent=(
                    "Answer 只返回完整校准签名，并说明这是既有私有接触而不是重新取得材料。"
                ),
            )
            self.phase = 2
        elif self.phase == 2:
            act = runtime.inject_human(
                f"判断公开字符串“{self.fresh_text}”是否包含数字字符，并在结论后原样附上完整字符串。",
                interpretation_content="判断只需要当前公开文本，不需要任何天文台材料。",
                intent=(
                    "合格 Answer 必须来自不继承此前天文台入口、日志或校准签名的新认知起点；"
                    "选择性忽略旧材料不能满足该来源边界。Answer 应给出判断，并保持原字符串不变。"
                ),
            )
            self.phase = 3
        elif self.phase == 3:
            self.phase = 4
            return None
        else:
            raise ValueError("demo is already complete")
        self.question_ids.append(act.act_id)
        return act

    def public_data(self, runtime: "PrototypeRuntime") -> dict[str, object]:
        chapters = {
            1: (
                "第一幕 · 密封入口",
                "先留在 Actor 场：协调位置看不见日志内容，只能让合适的认知位置取得 Answer。",
            ),
            2: (
                "第二幕 · 旧记忆",
                "这次没有再次提供地址。观察系统是否找到真正读过日志的持续认知位置。",
            ),
            3: (
                "第三幕 · 新视角",
                "公开问题要求不继承旧秘密。观察是否出现一个新的认知起点。",
            ),
            4: (
                "演示完成",
                "切换到问题协作流查看三轮 Answer，再点开不同 Actor 比较各自 Local World。",
            ),
        }
        title, cue = chapters.get(self.phase, ("", ""))
        return {
            "active": bool(self.phase),
            "title": "失落天文台",
            "phase": self.phase,
            "total_phases": 3,
            "chapter": title,
            "cue": cue,
            "question_id": self.question_ids[-1] if self.question_ids else "",
            "answered": self._current_answered(runtime),
            "complete": self.phase == 4,
        }

    def _current_answered(self, runtime: "PrototypeRuntime") -> bool:
        if not self.question_ids:
            return False
        question_id = self.question_ids[-1]
        return any(
            act.kind == "answer" and act.reply_to == question_id
            for acts in runtime.threads.values()
            for act in acts
        )


__all__ = ["ObservatoryDemo"]
