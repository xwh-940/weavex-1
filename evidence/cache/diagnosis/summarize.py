"""Aggregate previously measured results and inspect repeated visible material."""

import collections
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
rows = [row for file in sorted(OUT.glob("requests-*.json"))[2:]
        for row in json.loads(file.read_text(encoding="utf-8"))]
hit = sum(row["hit"] for row in rows)
prompt = sum(row["hit"] + row["miss"] for row in rows)
result = {"situation_requests": len(rows), "hit": hit, "prompt": prompt,
          "ratio": hit / prompt,
          "changed_schema_input_share": sum(row["hit"] + row["miss"] for row in rows
                                            if row["category"] == "within_activation_changed_tools") / prompt}
data = [json.loads(line) for line in (ROOT / "output/liuwen_20260904_first/main-1/traces.jsonl").read_text(encoding="utf-8").splitlines()]
inp = [t for t in data if t.get("kind") == "model.input" and t.get("actor_id") == "agent-1"][-1]["payload"]
counts, repeats = collections.Counter(), collections.Counter()
seen_obj, seen_ref = set(), set()
unique_char = full_char = receives = lwchars = respchars = groundchars = 0
for message in inp["messages"]:
    counts[message["role"]] += len(json.dumps(message, ensure_ascii=False))
    if message["role"] != "tool":
        continue
    try:
        value = json.loads(message["content"])
    except ValueError:
        continue
    if value.get("source") != "receive_input" or not isinstance(value.get("value"), dict):
        continue
    receives += 1
    world = value["value"].get("local_world", {})
    lwchars += len(json.dumps(world, ensure_ascii=False))
    respchars += len(json.dumps(value["value"].get("answer_responsibilities", []), ensure_ascii=False))
    groundchars += len(json.dumps(world.get("groundings", []), ensure_ascii=False))
    for kind, items, seen in [("object", world.get("objects", []), seen_obj),
                              ("reference", world.get("references", []), seen_ref)]:
        for obj in items:
            key = obj[kind + "_id"]
            n = len(json.dumps(obj, ensure_ascii=False))
            full_char += n
            repeats[kind] += 1
            if key not in seen:
                unique_char += n
                seen.add(key)
result["liuwen_last_agent_history"] = {
    "characters_by_role": counts, "receive_inputs": receives,
    "local_world_chars": lwchars, "responsibility_chars": respchars,
    "grounding_chars": groundchars, "material_appearances": repeats,
    "distinct_objects": len(seen_obj), "distinct_references": len(seen_ref),
    "material_chars": full_char, "unique_material_chars": unique_char,
}
probe = json.loads((OUT / "probe-results.json").read_text(encoding="utf-8"))
result["probe"] = [{"case": row["case"], "status": row["status"],
                    "hit": row.get("usage", {}).get("prompt_cache_hit_tokens"),
                    "miss": row.get("usage", {}).get("prompt_cache_miss_tokens"),
                    "hit_ratio": row.get("hit_ratio")} for row in probe]
result["probe_total_tokens"] = {
    key: sum(row.get("usage", {}).get(key, 0) for row in probe)
    for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}
(OUT / "combined.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
