"""Short, role-local Actor rehearsals for qualitative situation review."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from .execution import ToolProposal
from .prompts import COMMON_INSTRUCTIONS
from .provider import UnavailableModel

if TYPE_CHECKING:
    from .situation_adapter import SituationCard, Translator


async def rehearse_situation(
    card: SituationCard,
    *,
    design: Translator,
    simulate: Translator,
    actor_ids: tuple[str, ...] | None = None,
) -> dict:
    """Project real receive_input, then ask each simulated Actor to handle 1–3 cases.

    Only receive_input runs, in a disposable Runtime. Model decisions are recorded
    as hypothetical actions; they neither modify the card nor execute world tools.
    """
    from .situation_adapter import build_situated_runtime

    runtime = build_situated_runtime(card, UnavailableModel("rehearsal only"))
    selected = tuple(runtime.actors) if actor_ids is None else actor_ids
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("select one or more distinct Actors for rehearsal")
    if set(selected) - runtime.actors.keys():
        raise ValueError("unknown rehearsal Actor")
    roots = {act.recipient: act for act in runtime.queue}
    prompts = files("weavex.prompts")
    scenario_prompt = prompts.joinpath("situation_scenarios.md").read_text(
        encoding="utf-8"
    )
    actor_prompt = prompts.joinpath("situation_actor_probe.md").read_text(
        encoding="utf-8"
    )
    reports = []
    for actor_id in selected:
        actor = runtime.actors[actor_id]
        host = runtime.turn_host(actor, roots[actor_id])
        received = await host.execute(
            ToolProposal("rehearsal-receive", "receive_input")
        )
        if not received.observation.ok:
            raise ValueError(received.observation.error)
        local_input = received.observation.value
        tools = host.tool_schemas()
        view = {
            "actor_id": actor_id,
            "role": actor.spec.role,
            "received_input": local_input,
            "available_tools": tools,
        }
        instructions = (
            COMMON_INSTRUCTIONS + "\n" + actor.spec.instructions + "\n" + actor_prompt
        )
        initial_response = await simulate(instructions, {**view, "phase": "current"})
        # The case designer also sees only this position's input, not the global brief.
        designed = await design(
            scenario_prompt, {**view, "initial_response": initial_response}
        )
        cases = designed.get("scenarios")
        if not isinstance(cases, list) or not 1 <= len(cases) <= 3:
            raise ValueError("case designer must provide one to three scenarios")
        if any(not isinstance(case, str) or not case.strip() for case in cases):
            raise ValueError("scenarios must be nonempty situation descriptions")
        response = await simulate(
            instructions,
            {
                **view,
                "phase": "scenarios",
                "initial_response": initial_response,
                "scenarios": cases,
            },
        )
        reports.append(
            {
                **view,
                "initial_response": initial_response,
                "scenarios": cases,
                "response": response,
            }
        )
    return {
        "tested_card": card.to_dict(),
        "actors": reports,
        "mode": "role_local_hypothetical_actions",
        "world_actions_executed": False,
    }
