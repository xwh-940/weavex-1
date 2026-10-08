"""Read-only cache diagnosis over previously captured Runtime traces."""

import collections
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def load(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as handle:
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in handle if line.strip()]
        return json.load(handle)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()[:16]


def aggregate(rows):
    hit = sum(r["hit"] for r in rows)
    miss = sum(r["miss"] for r in rows)
    return {
        "requests": len(rows), "hit": hit, "miss": miss,
        "hit_ratio": round(hit / (hit + miss), 5) if hit + miss else None,
        "zero_hit_requests": sum(r["hit"] == 0 for r in rows),
        "mean_hit": round(hit / len(rows)) if rows else 0,
        "mean_prompt": round((hit + miss) / len(rows)) if rows else 0,
    }


def analyze(path):
    raw = load(path)
    traces = raw if isinstance(raw, list) else raw.get("traces", [])
    traces = sorted(traces, key=lambda t: t.get("seq", 0))
    pending, prev, rows = {}, {}, []
    for t in traces:
        key = (t.get("actor_id"), t.get("act_id"), t.get("payload", {}).get("round"))
        p = t.get("payload", {})
        if t.get("kind") == "model.input":
            pending[key] = t
        if t.get("kind") != "provider.usage" or key not in pending:
            continue
        inp = pending.pop(key)
        actor = key[0]
        messages = inp["payload"]["messages"]
        tools = inp["payload"]["tools"]
        old = prev.get(actor)
        same_activation = bool(old and old["activation"] == key[1])
        same_tools = bool(old and old["tools"] == tools)
        prefix = bool(old and messages[:len(old["messages"])] == old["messages"])
        category = (
            "first_actor_request" if not old else
            "new_activation" if not same_activation else
            "within_activation_same_tools" if same_tools else
            "within_activation_changed_tools"
        )
        prior_tool = ""
        if messages and messages[-1].get("role") == "tool":
            try:
                prior_tool = json.loads(messages[-1]["content"]).get("source", "")
            except (ValueError, TypeError):
                pass
        changed_names = []
        if old:
            old_tools = {v["function"]["name"]: v for v in old["tools"]}
            new_tools = {v["function"]["name"]: v for v in tools}
            changed_names = [n for n in sorted(old_tools.keys() | new_tools.keys())
                             if old_tools.get(n) != new_tools.get(n)]
        row = {
            "seq": t.get("seq"), "actor": actor, "activation": key[1],
            "round": key[2], "model": p.get("model"),
            "hit": p.get("prompt_cache_hit_tokens") or 0,
            "miss": p.get("prompt_cache_miss_tokens") or 0,
            "prompt_tokens": p.get("prompt_tokens") or 0,
            "completion_tokens": p.get("completion_tokens") or 0,
            "category": category, "messages_append_only": prefix,
            "same_tools": same_tools, "prior_tool": prior_tool,
            "tools_hash": digest(tools), "tools_chars": len(encoded(tools)),
            "messages_chars": len(encoded(messages)), "changed_tools": changed_names,
        }
        rows.append(row)
        prev[actor] = {"messages": messages, "tools": tools, "activation": key[1]}
    by_actor = {a: aggregate([r for r in rows if r["actor"] == a])
                for a in sorted({r["actor"] for r in rows})}
    by_category = {c: aggregate([r for r in rows if r["category"] == c])
                   for c in sorted({r["category"] for r in rows})}
    by_prior_tool = {c: aggregate([r for r in rows if r["prior_tool"] == c])
                     for c in sorted({r["prior_tool"] for r in rows})}
    summary = {
        "source": str(path.relative_to(ROOT)), "total": aggregate(rows),
        "actors": len(by_actor), "activations": len({r["activation"] for r in rows}),
        "models": sorted({str(r["model"]) for r in rows}),
        "by_category": by_category, "by_prior_tool": by_prior_tool,
        "non_append_only_visible_messages_after_first": sum(
            not r["messages_append_only"] and r["category"] != "first_actor_request" for r in rows),
        "unique_tool_schemas": len({r["tools_hash"] for r in rows}),
        "usage_mismatch_requests": sum(r["hit"] + r["miss"] != r["prompt_tokens"] for r in rows),
        "top_actors_by_input": sorted(by_actor.items(), key=lambda x: x[1]["hit"] + x[1]["miss"], reverse=True)[:6],
        "common_hit_counts": collections.Counter(r["hit"] for r in rows).most_common(12),
        "tool_change_counts": collections.Counter(n for r in rows for n in r["changed_tools"]),
    }
    return summary, rows


paths = [ROOT / "output/initialization_v0/run-real/snapshot.json",
         ROOT / "output/liuwen_20260904_first/main-1/traces.jsonl"]
paths += sorted((ROOT / "output/situation_v1/real_full").glob("*/snapshot.json.gz"))
paths += sorted((ROOT / "output/situation_v1/real_tail").glob("*/snapshot.json.gz"))
results = []
for i, path in enumerate(paths):
    summary, rows = analyze(path)
    results.append(summary)
    (OUT / f"requests-{i:02}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("source", "total", "actors", "by_category", "non_append_only_visible_messages_after_first", "unique_tool_schemas", "usage_mismatch_requests")}, ensure_ascii=False))
(OUT / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
