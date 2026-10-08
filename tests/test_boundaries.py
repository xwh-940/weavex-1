"""Static gates that keep the semantic prototype independent."""

from __future__ import annotations

import ast
from pathlib import Path

from weavex.prompts import (
    AGENT_INSTRUCTIONS,
    BROKER_INSTRUCTIONS,
    COMMON_INSTRUCTIONS,
)


ROOT = Path(__file__).resolve().parents[1] / "src" / "weavex"


def _logic_paths() -> tuple[Path, ...]:
    return tuple(path for path in ROOT.glob("*.py") if path.name != "__init__.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            result.add("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
    return result


def test_prompt_markdown_files_are_the_runtime_instruction_sources() -> None:
    prompt_root = ROOT / "prompts"
    expected = {
        "common.md": COMMON_INSTRUCTIONS,
        "broker.md": BROKER_INSTRUCTIONS,
        "agent.md": AGENT_INSTRUCTIONS,
    }
    # Adapter prompts are construction-time inputs, not Actor runtime instructions.
    adapter_prompts = {
        "situation_actor_probe.md",
        "situation_adapter.md",
        "situation_construction.md",
        "situation_repair.md",
        "situation_review.md",
        "situation_scenarios.md",
    }

    assert {
        path.name for path in prompt_root.glob("*.md")
    } == expected.keys() | adapter_prompts
    for name, instructions in expected.items():
        assert (prompt_root / name).read_text(encoding="utf-8").strip() == instructions


def test_weavex_does_not_import_the_legacy_intelligence_runtime() -> None:
    offenders = []
    for path in ROOT.glob("*.py"):
        for module in _imports(path):
            if module == "intelligence" or module.startswith("intelligence."):
                offenders.append((path.name, module))
    assert offenders == []


def test_weavex_does_not_import_retired_project_packages() -> None:
    offenders = []
    for path in ROOT.glob("*.py"):
        for module in _imports(path):
            if module == "harness" or module.startswith("harness."):
                offenders.append((path.name, module))
            if module == "open_semantic_world_proto" or module.startswith(
                "open_semantic_world_proto."
            ):
                offenders.append((path.name, module))
    assert offenders == []


def test_mechanical_modules_do_not_import_a_provider_or_openai() -> None:
    offenders = []
    for name in (
        "protocol.py",
        "actor.py",
        "dialogue.py",
        "execution.py",
        "situation.py",
        "local_world.py",
        "coordination.py",
        "capabilities.py",
        "tool_schema.py",
        "tool_surface.py",
        "turn_host.py",
    ):
        path = ROOT / name
        for module in _imports(path):
            if module in {"openai", "weavex.provider", ".provider"}:
                offenders.append((name, module))
    assert offenders == []


def test_provider_does_not_own_weavex_host_effects() -> None:
    imports = _imports(ROOT / "provider.py")

    assert ".runtime" not in imports
    assert ".communication" not in imports
    assert ".capabilities" not in imports


def test_situation_projections_do_not_depend_on_native_or_effect_layers() -> None:
    assert ".dialogue" not in _imports(ROOT / "situation.py")
    assert ".tool_surface" not in _imports(ROOT / "tool_schema.py")


def test_no_production_runtime_vocabulary_enters_logic_modules() -> None:
    forbidden = {
        "sqlite3",
        "checkpoint",
        "migration",
        "outbox",
        "inbox",
        "deliveryreceipt",
    }
    offenders = []
    for path in _logic_paths():
        normalized = path.read_text(encoding="utf-8").casefold()
        offenders.extend(
            (path.name, token) for token in forbidden if token in normalized
        )
    assert offenders == []
