"""Backend-neutral Actor activation and dialogue inspection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from .protocol import Observation

if TYPE_CHECKING:
    from .turn_host import ActorTurnHost


@dataclass(frozen=True)
class ToolProposal:
    """One backend-neutral model proposal awaiting deterministic host effects."""

    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    assistant_content: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", self.call_id.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))
        if not self.name:
            raise ValueError("tool proposal name cannot be blank")


@dataclass(frozen=True)
class ToolExecution:
    observation: Observation
    turn_ended: bool = False


class TurnDriver(Protocol):
    """Drive one Actor activation while WeaveX retains semantic authority."""

    async def activate(
        self,
        host: "ActorTurnHost",
    ) -> None: ...

    def dialogue_for(self, actor_id: str) -> tuple[Mapping[str, Any], ...]: ...

    def reset(self) -> None: ...


__all__ = ["ToolExecution", "ToolProposal", "TurnDriver"]
