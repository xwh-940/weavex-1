"""Host-only provider token and prompt-cache usage projections."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def _usage_int(usage: object, name: str) -> int | None:
    raw = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
    if raw is None:
        extra = getattr(usage, "model_extra", None)
        raw = extra.get(name) if isinstance(extra, Mapping) else None
    return (
        raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None
    )


def response_usage_data(response: object) -> dict[str, object]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    names = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    )
    values = {name: _usage_int(usage, name) for name in names}
    if all(value is None for value in values.values()):
        return {}
    hit = values["prompt_cache_hit_tokens"]
    miss = values["prompt_cache_miss_tokens"]
    cache_total = hit + miss if hit is not None and miss is not None else None
    return {
        "model": str(getattr(response, "model", "") or ""),
        **values,
        "cache_reported": cache_total is not None,
        "prompt_cache_hit_ratio": (
            hit / cache_total if cache_total is not None and cache_total > 0 else None
        ),
    }


def provider_usage_summary(
    records: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    values = list(records)

    def total(name: str) -> int:
        return sum(
            value
            for record in values
            for value in (record.get(name),)
            if isinstance(value, int) and not isinstance(value, bool)
        )

    hit = total("prompt_cache_hit_tokens")
    miss = total("prompt_cache_miss_tokens")
    cache_total = hit + miss
    return {
        "requests": len(values),
        "cache_reported_requests": sum(
            record.get("cache_reported") is True for record in values
        ),
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"),
        "total_tokens": total("total_tokens"),
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "prompt_cache_hit_ratio": hit / cache_total if cache_total else None,
    }


__all__ = ["provider_usage_summary", "response_usage_data"]
