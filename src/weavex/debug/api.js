const jsonHeaders = { "Content-Type": "application/json" };

export async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: jsonHeaders,
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || JSON.stringify(payload));
  }
  return payload;
}

export function readState() {
  return request("/api/state");
}

export function inspectActor(actorId, threadId) {
  const query = new URLSearchParams({ actor_id: actorId, thread_id: threadId });
  return request(`/api/actor-view?${query.toString()}`);
}

export function injectQuestion(command) {
  return request("/api/inject", {
    method: "POST",
    body: JSON.stringify(command),
  });
}

export function replyHuman(command) {
  return request("/api/human-reply", {
    method: "POST",
    body: JSON.stringify(command),
  });
}

export function stepRuntime() {
  return request("/api/step", { method: "POST" });
}

export function resetRuntime() {
  return request("/api/reset", { method: "POST" });
}

export function loadDemo() {
  return request("/api/demo/load", { method: "POST" });
}

export function advanceDemo() {
  return request("/api/demo/next", { method: "POST" });
}

export function addObject(content) {
  return request("/api/world-object", {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function addReference(sourceObjectId, target, meaning = "") {
  return request("/api/world-reference", {
    method: "POST",
    body: JSON.stringify({ source_object_id: sourceObjectId, target, meaning }),
  });
}
