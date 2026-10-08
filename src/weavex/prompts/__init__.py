"""Load the model instructions maintained as Markdown resources."""

from __future__ import annotations

from importlib.resources import files


def _load(name: str) -> str:
    return files(__package__).joinpath(name).read_text(encoding="utf-8").strip()


COMMON_INSTRUCTIONS = _load("common.md")
BROKER_INSTRUCTIONS = _load("broker.md")
AGENT_INSTRUCTIONS = _load("agent.md")


__all__ = ["AGENT_INSTRUCTIONS", "BROKER_INSTRUCTIONS", "COMMON_INSTRUCTIONS"]
