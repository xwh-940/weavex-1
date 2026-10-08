"""Read-only host resource accounting derived from authoritative runtime facts."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Iterable

from .protocol import TraceEvent
from .provider_usage import provider_usage_summary

if TYPE_CHECKING:
    from .runtime import PrototypeRuntime


HOST_COST_SCHEMA_VERSION = "host-cost-v1"
_TOOL_OBSERVATION_KINDS = frozenset({"tool.observation", "act.observation"})
_WORLD_READ_SOURCES = frozenset({"world_references_from", "world_references_to"})
_WORLD_WRITE_SOURCES = frozenset({"world_add_object", "world_add_reference"})
_WORLD_SOURCES = _WORLD_READ_SOURCES | _WORLD_WRITE_SOURCES


def _outcome_counts(events: Iterable[TraceEvent]) -> dict[str, int]:
    values = tuple(events)
    return {
        "attempted": len(values),
        "succeeded": sum(item.payload.get("ok") is True for item in values),
        "failed": sum(item.payload.get("ok") is not True for item in values),
    }


def _tool_costs(traces: tuple[TraceEvent, ...]) -> dict[str, object]:
    observations = tuple(
        item for item in traces if item.kind in _TOOL_OBSERVATION_KINDS
    )
    sources = sorted(
        {
            str(item.payload.get("source", ""))
            for item in observations
            if item.payload.get("source")
        }
    )
    return {
        **_outcome_counts(observations),
        "by_source": {
            source: _outcome_counts(
                item for item in observations if item.payload.get("source") == source
            )
            for source in sources
        },
    }


def _world_costs(traces: tuple[TraceEvent, ...]) -> dict[str, int]:
    observations = tuple(
        item
        for item in traces
        if item.kind in _TOOL_OBSERVATION_KINDS
        and item.payload.get("source") in _WORLD_SOURCES
    )
    successful_reads = sum(
        item.payload.get("ok") is True
        and item.payload.get("source") in _WORLD_READ_SOURCES
        for item in observations
    )
    successful_writes = sum(
        item.payload.get("ok") is True
        and item.payload.get("source") in _WORLD_WRITE_SOURCES
        for item in observations
    )
    return {
        **_outcome_counts(observations),
        "reads_succeeded": successful_reads,
        "writes_succeeded": successful_writes,
        "objects_created": sum(
            item.payload.get("ok") is True
            and item.payload.get("source") == "world_add_object"
            for item in observations
        ),
        "references_created": sum(
            item.payload.get("ok") is True
            and item.payload.get("source") == "world_add_reference"
            for item in observations
        ),
    }


def _model_costs(traces: tuple[TraceEvent, ...]) -> dict[str, object]:
    usage = provider_usage_summary(
        item.payload for item in traces if item.kind == "provider.usage"
    )
    return {
        "requests": sum(item.kind == "model.input" for item in traces),
        "decisions": sum(item.kind == "model.decision" for item in traces),
        "rejections": sum(item.kind == "model.rejection" for item in traces),
        "usage_reported_requests": usage["requests"],
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "prompt_cache_hit_tokens": usage["prompt_cache_hit_tokens"],
        "prompt_cache_miss_tokens": usage["prompt_cache_miss_tokens"],
        "prompt_cache_hit_ratio": usage["prompt_cache_hit_ratio"],
    }


def _execution_costs(traces: tuple[TraceEvent, ...]) -> dict[str, object]:
    return {
        "activations": {
            "started": sum(
                item.kind in {"act.handling", "local_world_delta.handling"}
                for item in traces
            ),
            "failed": sum(item.kind == "activation.failed" for item in traces),
        },
        "model": _model_costs(traces),
        "host_tools": _tool_costs(traces),
        "semantic_world_operations": _world_costs(traces),
    }


def _dynamic_actor_creations(
    runtime: "PrototypeRuntime", traces: tuple[TraceEvent, ...]
) -> dict[str, int]:
    roles = Counter()
    for item in traces:
        if item.kind != "actor.created":
            continue
        state = runtime.actors.get(item.actor_id)
        role = (
            state.spec.role if state is not None else str(item.payload.get("role", ""))
        )
        roles[role or "unknown"] += 1
    return {
        "total": sum(roles.values()),
        "agents": roles["agent"],
        "brokers": roles["broker"],
        "unknown": roles["unknown"],
    }


def _act_log_growth(runtime: "PrototypeRuntime") -> dict[str, object]:
    acts = tuple(act for values in runtime.threads.values() for act in values)
    kinds = Counter(act.kind for act in acts)
    return {
        "total": len(acts),
        "by_kind": dict(sorted(kinds.items())),
    }


def _current_footprint(runtime: "PrototypeRuntime") -> dict[str, int]:
    world = runtime.local_worlds.world.snapshot()
    local_worlds = runtime.local_worlds.locals_data()
    return {
        "actors": len(runtime.actors),
        "semantic_objects": len(world.objects),
        "semantic_references": len(world.references),
        "act_log_entries": sum(len(values) for values in runtime.threads.values()),
        "queued_arrivals": len(runtime.queue),
        "dialogue_messages": sum(
            len(runtime.dialogue_for(actor_id)) for actor_id in runtime.actors
        ),
        "local_object_memberships": sum(
            len(item["world"].get("objects", [])) for item in local_worlds
        ),
        "local_reference_memberships": sum(
            len(item["world"].get("references", [])) for item in local_worlds
        ),
        "neighborhood_observations": sum(
            len(item["world"].get("observed_neighborhoods", []))
            for item in local_worlds
        ),
        "grounding_nodes": sum(
            len(runtime.groundings.data_for_actor(actor_id))
            for actor_id in runtime.actors
        ),
        "question_frontiers": sum(
            len(runtime.question_frontiers.data_for_actor(actor_id))
            for actor_id in runtime.actors
        ),
    }


def host_cost_summary(runtime: "PrototypeRuntime") -> dict[str, object]:
    """Project an unweighted resource vector without creating mutable cost state."""

    traces = tuple(runtime.traces)
    totals = {
        **_execution_costs(traces),
        "dynamic_actor_creations": _dynamic_actor_creations(runtime, traces),
        "act_log_growth": _act_log_growth(runtime),
        "local_world_deltas": {
            "created": sum(item.kind == "local_world_delta.created" for item in traces),
            "handled": sum(
                item.kind == "local_world_delta.handling" for item in traces
            ),
        },
    }
    return {
        "schema_version": HOST_COST_SCHEMA_VERSION,
        "scope": "runtime_since_reset",
        "cost_model": "unweighted_resource_vector",
        "totals": totals,
        "by_actor": {
            actor_id: _execution_costs(
                tuple(item for item in traces if item.actor_id == actor_id)
            )
            for actor_id in sorted(runtime.actors)
        },
        "current_footprint": _current_footprint(runtime),
    }


__all__ = ["HOST_COST_SCHEMA_VERSION", "host_cost_summary"]
