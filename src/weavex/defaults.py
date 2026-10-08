"""Default in-memory composition of the WeaveX prototype."""

from __future__ import annotations

from .actor import ActorSpec
from .capabilities import DEFAULT_BROKER_CAPABILITIES, build_default_tools
from .execution import TurnDriver
from .initialization import InitialActor, initialize_runtime
from .local_world import LocalWorlds
from .prompts import AGENT_INSTRUCTIONS, BROKER_INSTRUCTIONS
from .provider import ModelPort
from .runner import NativeTurnDriver
from .runtime import PrototypeRuntime
from .world import SemanticWorld


def broker_spec() -> ActorSpec:
    return ActorSpec(
        actor_id="broker",
        role="broker",
        instructions=BROKER_INSTRUCTIONS,
        capabilities=DEFAULT_BROKER_CAPABILITIES,
    )


def build_default_runtime(
    model: ModelPort | None = None,
    *,
    turn_driver: TurnDriver | None = None,
    world: SemanticWorld | None = None,
    initial_actors: tuple[InitialActor, ...] | None = None,
) -> PrototypeRuntime:
    if (model is None) == (turn_driver is None):
        raise ValueError("provide exactly one model or turn_driver")
    tools = build_default_tools()
    resolved_driver = (
        turn_driver if turn_driver is not None else NativeTurnDriver(model, tools)
    )
    runtime = PrototypeRuntime(
        resolved_driver,
        tools,
        local_worlds=LocalWorlds(world),
        agent_base_instructions=AGENT_INSTRUCTIONS,
        broker_id=next(
            (
                item.spec.actor_id
                for item in initial_actors or ()
                if item.spec.role == "broker"
            ),
            "broker",
        ),
    )
    if initial_actors is None:
        runtime.register_actor(broker_spec())
        runtime.freeze_initial_actors()
    else:
        initialize_runtime(runtime, initial_actors)
    return runtime


__all__ = ["broker_spec", "build_default_runtime"]
