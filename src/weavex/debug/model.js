export const actorRoleLabel = { agent: "Agent", broker: "Broker" };

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

export function text(value, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function truncate(value, length = 86) {
  const source = String(value ?? "").replace(/\s+/g, " ").trim();
  return source.length > length ? `${source.slice(0, length - 1)}…` : source;
}

export function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

export function actorMap(state) {
  return new Map((state.actors ?? []).map((actor) => [actor.actor_id, actor]));
}

export function threadIds(state) {
  return Object.keys(state.threads ?? {}).sort((left, right) => {
    const leftNumber = Number(left.split("-").at(-1));
    const rightNumber = Number(right.split("-").at(-1));
    return leftNumber - rightNumber;
  });
}

export function actAddresses(act) {
  if (!act?.payload) return [];
  if (act.kind === "question") {
    return act.payload.interpretation_context?.semantic_addresses ?? [];
  }
  if (act.kind === "answer") return act.payload.semantic_addresses ?? [];
  return [];
}

export function actContent(act) {
  if (!act) return "";
  if (act.kind === "question") return text(act.payload?.raw_question, "Question");
  if (act.kind === "answer") return text(act.payload?.answer, "Answer");
  return text(act.payload?.content, "");
}

export function actLabel(act) {
  const names = {
    question: "Question",
    answer: "Answer",
    incremental: "incremental",
    group_formed: "群形成",
  };
  return names[act?.kind] ?? act?.kind ?? "Act";
}

export function actSummary(act) {
  const content = actContent(act);
  if (content) return truncate(content, 92);
  if (act?.kind === "group_formed") {
    const selected = act.payload?.selected_existing_agents ?? [];
    return selected.length ? `选入 ${selected.join("、")}` : "未选入既有 Agent";
  }
  return "无文本 payload";
}

export function contexts(state) {
  return (state.coordination_contexts ?? []).map((context) => ({
    ...context,
    member_ids: context.member_ids ?? [],
    born_agents: context.born_agents ?? [],
    selected_existing_agents: context.selected_existing_agents ?? [],
  }));
}

export function contextForBroker(state, brokerId) {
  return contexts(state).find((context) => context.broker_id === brokerId);
}

export function contextsForActor(state, actorId) {
  return contexts(state).filter((context) => context.member_ids.includes(actorId));
}

export function actorStatus(state, actorId, activeActorId = "") {
  if (activeActorId === actorId) return "processing";
  if ((state.queue ?? []).some((act) => act.recipient === actorId)) return "queued";
  const failure = [...(state.traces ?? [])]
    .reverse()
    .find((trace) => trace.actor_id === actorId && trace.kind === "activation.failed");
  return failure ? "failed" : "idle";
}

export function humanReplyTargets(state) {
  const answered = new Set();
  const replies = new Set();
  const targets = [];
  const brokerIds = new Set(
    (state.actors ?? [])
      .filter((actor) => actor.role === "broker")
      .map((actor) => actor.actor_id),
  );
  for (const [threadId, acts] of Object.entries(state.threads ?? {})) {
    for (const act of acts) {
      if (act.kind === "answer" && act.reply_to) answered.add(act.reply_to);
      if (act.sender === "human" && act.reply_to) replies.add(act.reply_to);
    }
    for (const act of acts) {
      if (
        act.sender === "human" &&
        brokerIds.has(act.recipient) &&
        act.kind === "question" &&
        !answered.has(act.act_id)
      ) {
        targets.push({ threadId, act });
      }
      if (
        brokerIds.has(act.sender) &&
        act.recipient === "human" &&
        act.kind === "incremental" &&
        !replies.has(act.act_id)
      ) {
        targets.push({ threadId, act });
      }
    }
  }
  return targets;
}

export function deriveInquiry(acts) {
  const byId = new Map(acts.map((act) => [act.act_id, act]));
  const questionDepth = new Map();
  const depthOfQuestion = (act) => {
    if (!act || act.kind !== "question") return 0;
    if (questionDepth.has(act.act_id)) return questionDepth.get(act.act_id);
    const parents = act.derived_from ?? [];
    const value = parents.length
      ? Math.max(...parents.map((id) => depthOfQuestion(byId.get(id)))) + 1
      : 0;
    questionDepth.set(act.act_id, value);
    return value;
  };
  return acts.map((act, index) => ({
    ...act,
    index,
    depth: act.kind === "question"
      ? depthOfQuestion(act)
      : Math.max(0, depthOfQuestion(byId.get(act.reply_to))),
    replySource: act.reply_to ? byId.get(act.reply_to) : undefined,
    derivedSources: (act.derived_from ?? []).map((id) => byId.get(id)).filter(Boolean),
  }));
}

export function worldData(state) {
  const semantic = state.semantic_world ?? { objects: [], references: [] };
  const objects = (semantic.objects ?? []).map((item) => ({
    id: item.object_id,
    content: item.content,
    kind: "object",
  }));
  const references = (semantic.references ?? []).map((item) => ({
    id: item.reference_id,
    sourceId: item.source_object_id,
    meaning: item.meaning ?? "",
    target: item.target,
    kind: "reference",
  }));
  return { objects, references };
}

export function localWorlds(state) {
  return (state.agent_local_worlds ?? []).map((item) => ({
    actorId: item.actor_id,
    objects: item.world?.objects ?? [],
    references: item.world?.references ?? [],
    budgets: item.question_budgets ?? {},
  }));
}

export function localWorldFor(state, actorId) {
  return localWorlds(state).find((world) => world.actorId === actorId);
}

export function localMembership(state, kind, id) {
  return localWorlds(state).filter((world) => {
    const values = kind === "object" ? world.objects : world.references;
    const key = kind === "object" ? "object_id" : "reference_id";
    return values.some((item) => item[key] === id);
  });
}

export function latestTracesForAct(state, actId) {
  return (state.traces ?? []).filter((trace) => trace.act_id === actId);
}

export function newWorldIds(previous, current) {
  const prior = worldData(previous ?? {});
  const next = worldData(current ?? {});
  const priorObjects = new Set(prior.objects.map((item) => item.id));
  const priorReferences = new Set(prior.references.map((item) => item.id));
  return {
    objects: new Set(next.objects.filter((item) => !priorObjects.has(item.id)).map((item) => item.id)),
    references: new Set(next.references.filter((item) => !priorReferences.has(item.id)).map((item) => item.id)),
  };
}

export function addressKey(address) {
  return address ? `${address.kind}:${address.id}` : "";
}
