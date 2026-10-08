import {
  addObject,
  addReference,
  advanceDemo,
  injectQuestion,
  inspectActor,
  readState,
  replyHuman,
  resetRuntime,
  stepRuntime,
} from "/debug/api.js";
import {
  actAddresses,
  actContent,
  actLabel,
  actSummary,
  actorMap,
  actorRoleLabel,
  actorStatus,
  contextForBroker,
  contexts,
  contextsForActor,
  deriveInquiry,
  escapeHtml,
  humanReplyTargets,
  latestTracesForAct,
  localMembership,
  localWorldFor,
  localWorlds,
  newWorldIds,
  pretty,
  text,
  threadIds,
  truncate,
  worldData,
} from "/debug/model.js";
import { ForceLayout, routedPath } from "/debug/layout.js";

const app = document.querySelector("#app");
const actorLayout = new ForceLayout({ nodeRadius: 48, spacing: 190 });
const worldLayout = new ForceLayout({ nodeRadius: 54, spacing: 202 });

const ui = {
  state: null,
  previousState: null,
  view: "actors",
  selectedThread: "",
  selection: { type: "actor", id: "broker" },
  actorView: null,
  actorViewKey: "",
  activeActorId: "",
  activeActId: "",
  lastActId: "",
  autoRunning: false,
  busy: false,
  pollTimer: 0,
  canvasSize: { width: 980, height: 620 },
  worldScope: { mode: "global", actorId: "" },
  worldSearch: "",
  inspectorTab: "details",
  inspectorOpen: true,
  railOpen: false,
  railCollapsed: false,
  topbarCollapsed: false,
  dockMode: "question",
  dockCollapsed: true,
  demoHidden: false,
  theme: window.localStorage.getItem("weavex-debug-theme") || "dark",
  speed: 440,
  toast: { kind: "", text: "" },
  drag: null,
  suppressNodeClick: false,
  canvasGesture: null,
  viewports: {
    actors: { x: 0, y: 0, zoom: 1 },
    inquiry: { x: 0, y: 0, zoom: 1 },
    world: { x: 0, y: 0, zoom: 1 },
  },
  drafts: {
    rawQuestion: "",
    interpretation: "",
    intent: "",
    newContext: false,
    recipient: "broker",
    advancedQuestion: "",
    replyTarget: "",
    replyContent: "",
    objectContent: "",
    referenceSource: "",
    referenceTarget: "",
    referenceMeaning: "",
  },
};

function currentThreadActs() {
  return ui.state?.threads?.[ui.selectedThread] ?? [];
}

function scheduleRender() {
  window.requestAnimationFrame(render);
}

function viewportTransform(view) {
  const viewport = ui.viewports[view];
  return `transform:translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`;
}

function viewportControls(view, extra = "") {
  const zoom = Math.round(ui.viewports[view].zoom * 100);
  return `<div class="stage-actions"><span data-zoom-label="${view}">${zoom}%</span><button data-action="reset-view" data-view="${view}">复位视图</button>${extra}</div>`;
}

function applyViewport(view) {
  const layer = app.querySelector(`[data-pan-layer="${view}"]`);
  if (layer) layer.style.transform = `translate(${ui.viewports[view].x}px, ${ui.viewports[view].y}px) scale(${ui.viewports[view].zoom})`;
  const label = app.querySelector(`[data-zoom-label="${view}"]`);
  if (label) label.textContent = `${Math.round(ui.viewports[view].zoom * 100)}%`;
}

function resetViewport(view) {
  ui.viewports[view] = { x: 0, y: 0, zoom: 1 };
  scheduleRender();
}

function setToast(kind, message) {
  ui.toast = { kind, text: message };
  scheduleRender();
  if (message) {
    window.setTimeout(() => {
      if (ui.toast.text === message) {
        ui.toast = { kind: "", text: "" };
        scheduleRender();
      }
    }, 4200);
  }
}

function installState(nextState) {
  ui.previousState = ui.state;
  ui.state = nextState;
  const ids = threadIds(nextState);
  if (!ids.includes(ui.selectedThread)) {
    ui.selectedThread = ids.at(-1) ?? "";
  }
  const actors = actorMap(nextState);
  if (ui.selection.type === "actor" && !actors.has(ui.selection.id)) {
    ui.selection = { type: "actor", id: nextState.actors?.[0]?.actor_id ?? "" };
    ui.actorView = null;
    ui.actorViewKey = "";
  }
  if (!ui.worldScope.actorId) {
    ui.worldScope.actorId = (nextState.actors ?? []).find((actor) => actor.role === "agent")?.actor_id ?? "";
  }
  scheduleRender();
}

async function refresh({ silent = false } = {}) {
  try {
    installState(await readState());
  } catch (error) {
    if (!silent) setToast("error", `无法读取 Runtime：${error.message}`);
  }
}

function selectedActor() {
  return ui.selection.type === "actor"
    ? actorMap(ui.state).get(ui.selection.id)
    : undefined;
}

async function loadActorView(actorId = ui.selection.id, threadId = ui.selectedThread) {
  if (!actorId || !threadId) return;
  const key = `${actorId}:${threadId}`;
  try {
    const value = await inspectActor(actorId, threadId);
    if (ui.selection.type === "actor" && ui.selection.id === actorId && ui.selectedThread === threadId) {
      ui.actorView = value;
      ui.actorViewKey = key;
      scheduleRender();
    }
  } catch (error) {
    setToast("error", `无法读取 Actor 视图：${error.message}`);
  }
}

function chooseActor(actorId) {
  ui.selection = { type: "actor", id: actorId };
  ui.inspectorTab = "details";
  ui.inspectorOpen = true;
  ui.actorView = null;
  ui.actorViewKey = "";
  loadActorView(actorId, ui.selectedThread);
  scheduleRender();
}

function chooseAct(actId) {
  ui.selection = { type: "act", id: actId };
  ui.inspectorTab = "details";
  ui.inspectorOpen = true;
  scheduleRender();
}

function chooseWorld(kind, id) {
  ui.selection = { type: "world", id, kind };
  ui.inspectorTab = "details";
  ui.inspectorOpen = true;
  scheduleRender();
}

function chooseThread(threadId) {
  ui.selectedThread = threadId;
  ui.actorView = null;
  ui.actorViewKey = "";
  if (ui.selection.type === "actor") loadActorView(ui.selection.id, threadId);
  scheduleRender();
}

function activeQueueHead() {
  return ui.state?.queue?.[0] ?? null;
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function pollWhileBusy() {
  if (!ui.busy) return;
  await refresh({ silent: true });
  if (ui.busy) ui.pollTimer = window.setTimeout(pollWhileBusy, 360);
}

async function performStep() {
  if (ui.busy || !ui.state?.queue?.length) return false;
  const pending = activeQueueHead();
  ui.busy = true;
  ui.activeActorId = pending?.recipient ?? "";
  ui.activeActId = pending?.act_id ?? "";
  scheduleRender();
  ui.pollTimer = window.setTimeout(pollWhileBusy, 360);
  try {
    const result = await stepRuntime();
    installState(result.state);
    ui.lastActId = result.handled?.act_id ?? "";
    return Boolean(result.handled);
  } catch (error) {
    ui.autoRunning = false;
    setToast("error", `Step 失败：${error.message}`);
    await refresh({ silent: true });
    return false;
  } finally {
    ui.busy = false;
    ui.activeActorId = "";
    ui.activeActId = "";
    window.clearTimeout(ui.pollTimer);
    ui.pollTimer = 0;
    scheduleRender();
  }
}

async function toggleAutoRun() {
  if (ui.autoRunning) {
    ui.autoRunning = false;
    scheduleRender();
    return;
  }
  ui.autoRunning = true;
  scheduleRender();
  while (ui.autoRunning && ui.state?.queue?.length) {
    const handled = await performStep();
    if (!handled || !ui.autoRunning) break;
    await delay(ui.speed);
  }
  ui.autoRunning = false;
  scheduleRender();
}

async function resetAll() {
  if (ui.busy || !window.confirm("确定重置整个内存 Runtime 吗？")) return;
  try {
    const state = await resetRuntime();
    ui.selection = { type: "actor", id: state.actors?.[0]?.actor_id ?? "broker" };
    ui.actorView = null;
    ui.actorViewKey = "";
    ui.lastActId = "";
    installState(state);
    setToast("success", "Runtime 已重置");
  } catch (error) {
    setToast("error", `重置失败：${error.message}`);
  }
}

async function nextDemoChapter() {
  if (ui.busy || ui.state?.queue?.length) return;
  ui.busy = true;
  scheduleRender();
  try {
    const result = await advanceDemo();
    installState(result.state);
    if (result.act) {
      ui.view = "actors";
      ui.selectedThread = result.act.thread_id;
      ui.selection = { type: "act", id: result.act.act_id };
      ui.inspectorOpen = false;
      setToast("success", `${result.state.demo.chapter} 已进入队列`);
    } else {
      ui.view = "inquiry";
      ui.demoHidden = false;
      setToast("success", "三幕演示完成，可以查看协作流与不同 Local World");
    }
  } catch (error) {
    setToast("error", `无法进入下一幕：${error.message}`);
  } finally {
    ui.busy = false;
    scheduleRender();
  }
}

function questionCommand() {
  const advanced = ui.drafts.advancedQuestion.trim();
  if (advanced) {
    const parsed = JSON.parse(advanced);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("高级 Question 必须是 JSON object");
    }
    return parsed;
  }
  const rawQuestion = ui.drafts.rawQuestion.trim();
  if (!rawQuestion) throw new Error("请填写 raw_question");
  return {
    raw_question: rawQuestion,
    interpretation_context: {
      content: ui.drafts.interpretation.trim() || null,
      semantic_addresses: selectedAddressDrafts(),
    },
    intent: ui.drafts.intent.trim() || null,
    ...(ui.drafts.newContext ? { new_context: true } : { recipient: ui.drafts.recipient || "broker" }),
  };
}

function selectedAddressDrafts() {
  return [...app.querySelectorAll("[data-address-choice]:checked")].map((input) => {
    const [kind, ...id] = input.value.split(":");
    return { kind, id: id.join(":") };
  });
}

async function submitQuestion() {
  try {
    const result = await injectQuestion(questionCommand());
    ui.drafts.rawQuestion = "";
    ui.drafts.interpretation = "";
    ui.drafts.intent = "";
    ui.drafts.advancedQuestion = "";
    ui.dockCollapsed = true;
    installState(result.state);
    ui.selectedThread = result.act.thread_id;
    chooseAct(result.act.act_id);
    setToast("success", `Question 已注入：${result.act.act_id}`);
  } catch (error) {
    setToast("error", `Question 未注入：${error.message}`);
  }
}

async function submitReply() {
  const target = humanReplyTargets(ui.state).find((item) => item.act.act_id === ui.drafts.replyTarget);
  const content = ui.drafts.replyContent.trim();
  if (!target || !content) {
    setToast("error", "请选择要延续的 Human/Broker Act 并填写增量内容");
    return;
  }
  try {
    const result = await replyHuman({
      thread_id: target.threadId,
      reply_to: target.act.act_id,
      content,
    });
    ui.drafts.replyContent = "";
    ui.drafts.replyTarget = "";
    ui.dockCollapsed = true;
    installState(result.state);
    chooseThread(result.act.thread_id);
    chooseAct(result.act.act_id);
    setToast("success", `Human ${result.act.kind} 已加入队列`);
  } catch (error) {
    setToast("error", `增量未发送：${error.message}`);
  }
}

async function submitObject() {
  const content = ui.drafts.objectContent.trim();
  if (!content) return setToast("error", "SemanticObject 内容不能为空");
  try {
    const result = await addObject(content);
    ui.drafts.objectContent = "";
    installState(result.state);
    chooseWorld("object", result.semantic_address.id);
    setToast("success", `已注入 ${result.semantic_address.id}`);
  } catch (error) {
    setToast("error", `Object 注入失败：${error.message}`);
  }
}

async function submitReference() {
  const [kind, id] = ui.drafts.referenceTarget.split("|");
  if (!ui.drafts.referenceSource || !kind || !id) {
    return setToast("error", "请选择 source Object 和 target Semantic Address");
  }
  try {
    const result = await addReference(
      ui.drafts.referenceSource,
      { kind, id },
      ui.drafts.referenceMeaning.trim(),
    );
    ui.drafts.referenceMeaning = "";
    installState(result.state);
    chooseWorld("reference", result.semantic_address.id);
    setToast("success", `已注入 ${result.semantic_address.id}`);
  } catch (error) {
    setToast("error", `Reference 注入失败：${error.message}`);
  }
}

function renderHeader() {
  const queueCount = ui.state?.queue?.length ?? 0;
  const usage = ui.state?.provider_usage ?? {};
  const cacheRatio = typeof usage.prompt_cache_hit_ratio === "number" ? `${Math.round(usage.prompt_cache_hit_ratio * 100)}%` : "—";
  const processing = ui.busy || ui.state?.debug_control?.step_in_flight;
  const current = processing ? "处理中" : ui.autoRunning ? "自动运行" : "已暂停";
  return `
    <header class="topbar ${ui.topbarCollapsed ? "collapsed" : ""}">
      <div class="brand"><span class="brand-mark">⌘</span><div><small>WEAVEX / DEBUG</small><strong>Semantic Evolution</strong></div></div>
      ${ui.topbarCollapsed ? "" : `
      <nav class="view-tabs" aria-label="调试视图">
        ${tabButton("actors", "Actor 场")}
        ${tabButton("inquiry", "问题协作流")}
        ${tabButton("world", "语义世界")}
      </nav>
      <div class="runtime-controls">
        <button class="demo-launch" data-action="${ui.state?.demo?.active ? "demo-toggle" : "demo-present"}">${ui.state?.demo?.active ? "演示导览" : "观众模式"}</button>
        <span class="queue-chip"><i></i>待处理 ${queueCount}</span>
        <span class="queue-chip" title="DeepSeek prompt cache：${usage.prompt_cache_hit_tokens ?? 0} hit / ${usage.prompt_cache_miss_tokens ?? 0} miss tokens">缓存命中 ${cacheRatio}</span>
        <span class="runtime-state ${processing ? "active" : ""}">${current}</span>
        <button data-action="step" ${processing || !queueCount ? "disabled" : ""}>单步</button>
        <button class="primary" data-action="autorun" ${!queueCount && !ui.autoRunning ? "disabled" : ""}>${ui.autoRunning ? "暂停" : "运行"}</button>
        <button data-action="reset" ${processing ? "disabled" : ""}>重置</button>
        <select data-field="speed" aria-label="自动运行速度">
          ${[[720, "慢"], [440, "正常"], [180, "快"]].map(([value, label]) => `<option value="${value}" ${ui.speed === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
        <button class="icon-button" data-action="theme" aria-label="切换主题">${ui.theme === "dark" ? "☼" : "◐"}</button>
      </div>`}
      <button class="topbar-collapse" data-action="topbar-collapse" aria-expanded="${!ui.topbarCollapsed}">${ui.topbarCollapsed ? "展开顶部栏" : "收起顶部栏"}</button>
    </header>`;
}

function renderDemoStory() {
  const demo = ui.state?.demo;
  if (!demo?.active || ui.demoHidden) return "";
  const queueCount = ui.state?.queue?.length ?? 0;
  const processing = ui.busy || ui.state?.debug_control?.step_in_flight;
  const activePhase = Math.min(demo.phase, demo.total_phases);
  const progress = Array.from({ length: demo.total_phases }, (_, index) => {
    const phase = index + 1;
    const state = phase < activePhase || demo.complete ? "done" : phase === activePhase ? "active" : "";
    return `<i class="${state}"></i>`;
  }).join("");
  let action = `<button class="demo-primary" data-action="demo-step">推进一个 Act</button>`;
  let status = `还有 ${queueCount} 个 Act 等待处理；每次推进都由当前 Actor 自己选择行动。`;
  if (processing) {
    action = `<button class="demo-primary" disabled>正在形成 Decision…</button>`;
    status = "当前 Act 已进入处理；queue 暂时为空不代表 Question 已经停止。";
  } else if (!queueCount && demo.complete) {
    action = `<button class="demo-primary" data-action="demo-review">查看完整协作流</button>`;
    status = "三轮 Question 已闭合。现在比较关系结构与不同认知位置实际接触的内容。";
  } else if (!queueCount && demo.answered) {
    action = `<button class="demo-primary" data-action="demo-next">${demo.phase === 3 ? "完成演示" : "进入下一幕"}</button>`;
    status = "本幕已经获得直接 Answer；下一幕会改变信息连续性的成立条件。";
  } else if (!queueCount) {
    action = `<button class="demo-primary" disabled>本幕没有形成 Answer</button>`;
    status = "队列已经停止，但当前 Question 尚未闭合；请查看失败 Observation 或 trace。";
  }
  return `<aside class="demo-story">
    <header><div><span>INTERACTIVE DEMO</span><strong>${escapeHtml(demo.title)}</strong></div><button data-action="demo-hide" aria-label="收起演示导览">×</button></header>
    <div class="demo-progress">${progress}<small>${escapeHtml(demo.chapter)}</small></div>
    <p>${escapeHtml(demo.cue)}</p>
    <footer><small>${escapeHtml(status)}</small>${action}</footer>
  </aside>`;
}

function tabButton(id, label) {
  return `<button class="${ui.view === id ? "active" : ""}" data-action="view" data-view="${id}" aria-selected="${ui.view === id}">${label}</button>`;
}

function renderRail() {
  const ids = threadIds(ui.state);
  const replies = humanReplyTargets(ui.state);
  const queue = ui.state?.queue ?? [];
  return `
    <aside class="inquiry-rail ${ui.railOpen ? "open" : ""} ${ui.railCollapsed ? "collapsed" : ""}">
      <button class="rail-collapse" data-action="rail-collapse" aria-expanded="${!ui.railCollapsed}">${ui.railCollapsed ? "展开问题协作" : "收起问题协作"}</button>
      <div class="rail-content">
      <section class="rail-section rail-intro">
        <span class="eyebrow">问题协作</span>
        <h1>多 Actor 问题协作</h1>
        <p>这里显示当前问题、关联协作线与待处理 Act。</p>
        <button class="new-question" data-action="open-question">＋ 提出问题</button>
      </section>
      <section class="rail-section">
        <header><span>协作线</span><b>${ids.length}</b></header>
        <div class="thread-list">${ids.length ? ids.map(renderThreadCard).join("") : empty("尚未开始问题协作", "提出问题后，这里会出现对应的 thread。")} </div>
      </section>
      <section class="rail-section queue-section">
        <header><span>待处理</span><b>${queue.length}</b></header>
        <div class="queue-list">${queue.length ? queue.map((act, index) => `
          <button class="queue-item ${index === 0 ? "next" : ""}" data-action="select-act" data-act-id="${escapeHtml(act.act_id)}">
            <span>${index === 0 ? "NEXT" : `#${index + 1}`}</span><strong>${escapeHtml(act.recipient)}</strong><p>${escapeHtml(actSummary(act))}</p>
          </button>`).join("") : empty("队列为空", "当前没有待处理 Act。")} </div>
      </section>
      ${replies.length ? `<section class="rail-section reply-alert"><header><span>可补入增量</span><b>${replies.length}</b></header>${replies.map((item) => `<button data-action="reply-target" data-reply-id="${escapeHtml(item.act.act_id)}" data-act-id="${escapeHtml(item.act.act_id)}"><strong>${escapeHtml(item.threadId)}</strong><p>${escapeHtml(actSummary(item.act))}</p></button>`).join("")}</section>` : ""}
      </div>
    </aside>`;
}

function renderThreadCard(threadId) {
  const acts = ui.state.threads?.[threadId] ?? [];
  const roots = acts.filter((act) => act.kind === "question" && !(act.derived_from ?? []).length);
  const latest = acts.at(-1);
  return `<button class="thread-card ${threadId === ui.selectedThread ? "selected" : ""}" data-action="select-thread" data-thread-id="${escapeHtml(threadId)}" data-act-id="${escapeHtml(latest?.act_id ?? "")}">
    <span>${escapeHtml(threadId)}</span><strong>${escapeHtml(truncate(actContent(roots[0]), 48) || "等待 Question")}</strong>
    <small>${acts.length} 个 Act · ${latest ? actLabel(latest) : "-"}</small>
  </button>`;
}

function empty(title, detail) {
  return `<div class="empty"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function renderStage() {
  if (ui.view === "inquiry") return renderInquiryStage();
  if (ui.view === "world") return renderWorldStage();
  return renderActorStage();
}

function stageToolbar(title, detail, actions = "") {
  return `<div class="stage-title"><div><span class="eyebrow">${title}</span><h2>${detail}</h2></div>${actions}</div>`;
}

function renderActorStage() {
  const actors = ui.state?.actors ?? [];
  const byId = actorMap(ui.state);
  const groupContexts = contexts(ui.state);
  const ids = actors.map((actor) => actor.actor_id);
  const relationMap = new Map();
  for (const context of groupContexts) {
    for (const memberId of context.member_ids) {
      if (!byId.has(memberId)) continue;
      const key = `${context.broker_id}:${memberId}`;
      relationMap.set(key, { source: context.broker_id, target: memberId, weight: 1, context });
    }
  }
  const relations = [...relationMap.values()];
  const positions = actorLayout.simulate(ids, relations, ui.canvasSize.width, ui.canvasSize.height, ui.selection.type === "actor" ? ui.selection.id : "");
  const focus = actorFocusSet(ui.state, ui.selection);
  const latestAct = (ui.state?.traces ?? []).findLast?.((trace) => trace.kind === "act.created")?.payload;
  const recent = ui.activeActId
    ? currentThreadActs().find((act) => act.act_id === ui.activeActId)
    : latestAct?.act_id ? currentThreadActs().find((act) => act.act_id === latestAct.act_id) : null;
  const svgEdges = relations.map((relation, index) => actorEdge(relation, positions, index)).join("");
  const actEdge = recent ? actorActEdge(recent, positions) : "";
  const nodes = actors.map((actor) => actorNode(actor, positions.get(actor.actor_id), focus)).join("");
  const human = currentThreadActs().length ? humanNode() : "";
  return `<section class="stage actor-stage">
    ${stageToolbar("协作场", "Broker / Agent 协作", viewportControls("actors", `<button data-action="rearrange">重排</button>`))}
    <div class="legend"><span><i class="broker-dot"></i>Broker 协调群</span><span><i class="agent-dot"></i>Agent</span><span><i class="act-line"></i>最近 Act</span></div>
    <div class="stage-canvas actor-canvas" data-canvas="actors">
      <div class="canvas-layer" data-pan-layer="actors" style="${viewportTransform("actors")}">
        <svg class="edge-layer" viewBox="0 0 ${ui.canvasSize.width} ${ui.canvasSize.height}" preserveAspectRatio="none" aria-hidden="true">
          <defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
          ${svgEdges}${actEdge}
        </svg>
        ${human}${nodes}
        ${actors.length ? "" : empty("尚无 Actor", "Runtime 初始化时会注册默认 Human 入口 Broker。")}
      </div>
    </div>
  </section>`;
}

function actorFocusSet(state, selection) {
  if (selection.type !== "actor") return new Set();
  const selected = selection.id;
  const actor = actorMap(state).get(selected);
  const result = new Set([selected]);
  if (actor?.role === "broker") {
    const context = contextForBroker(state, selected);
    for (const member of context?.member_ids ?? []) result.add(member);
  } else {
    for (const context of contextsForActor(state, selected)) {
      result.add(context.broker_id);
      for (const member of context.member_ids) result.add(member);
    }
  }
  return result;
}

function actorEdge(relation, positions, index) {
  const source = positions.get(relation.source);
  const target = positions.get(relation.target);
  if (!source || !target) return "";
  const born = relation.context.born_agents.includes(relation.target);
  const selected = relation.context.selected_existing_agents.includes(relation.target);
  const sourceActor = relation.context.source_actor_id === relation.target;
  const style = born ? "born" : selected ? "selected" : sourceActor ? "source" : "member";
  return `<path class="actor-edge ${style}" d="${routedPath(source, target, (index % 2 ? 1 : -1) * 19)}" />`;
}

function actorActEdge(act, positions) {
  const source = positions.get(act.sender);
  const target = positions.get(act.recipient);
  if (!source || !target || act.sender === act.recipient) return "";
  return `<path class="act-edge" d="${routedPath(source, target, -38)}" marker-end="url(#arrow)" />`;
}

function actorNode(actor, position, focus) {
  if (!position) return "";
  const context = actor.role === "broker" ? contextForBroker(ui.state, actor.actor_id) : null;
  const status = actorStatus(ui.state, actor.actor_id, ui.activeActorId);
  const dimmed = focus.size && !focus.has(actor.actor_id);
  const selected = ui.selection.type === "actor" && ui.selection.id === actor.actor_id;
  const memberships = actor.role === "agent" ? contextsForActor(ui.state, actor.actor_id).length : context?.member_ids?.length ?? 0;
  return `<button class="actor-node role-${actor.role} state-${status} ${dimmed ? "dimmed" : ""} ${selected ? "selected" : ""}" style="--x:${position.x}px;--y:${position.y}px" data-action="select-actor" data-actor-id="${escapeHtml(actor.actor_id)}" aria-pressed="${selected}">
    <span class="actor-core"><i></i></span>
    <span class="actor-role">${actorRoleLabel[actor.role] ?? actor.role}</span>
    <strong>${escapeHtml(actor.actor_id)}</strong>
    <small>${escapeHtml(actor.role === "broker" ? "协调群" : "持续演化的认知位置")}</small>
    <em>${actor.role === "broker" ? `${context?.formed ? "群" : "待形成"} · ${memberships}` : `${memberships} 个协调群`}</em>
  </button>`;
}

function humanNode() {
  return `<button class="human-node" data-action="open-question"><span>HUMAN</span><strong>外部入口</strong><small>Question / Answer</small></button>`;
}

function renderInquiryStage() {
  const acts = deriveInquiry(currentThreadActs());
  const width = Math.max(860, 180 + (Math.max(0, ...acts.map((act) => act.depth)) + 1) * 245);
  const height = Math.max(520, 120 + acts.length * 132);
  const positions = new Map(acts.map((act) => [act.act_id, { x: 132 + act.depth * 245, y: 90 + act.index * 126 }]));
  const replyEdges = acts.map((act) => {
    const source = act.replySource && positions.get(act.replySource.act_id);
    const target = positions.get(act.act_id);
    return source && target ? `<path class="inquiry-edge reply" d="${routedPath(source, target, 42)}" />` : "";
  }).join("");
  const derivedEdges = acts.flatMap((act) => act.derivedSources.map((sourceAct, index) => {
    const source = positions.get(sourceAct.act_id);
    const target = positions.get(act.act_id);
    return source && target ? `<path class="inquiry-edge derived" d="${routedPath(source, target, (index + 1) * -26)}" />` : "";
  })).join("");
  const nodes = acts.map((act) => inquiryNode(act, positions.get(act.act_id))).join("");
  const title = ui.selectedThread || "选择一个协作线";
  return `<section class="stage inquiry-stage">
    ${stageToolbar("问题协作流", title, viewportControls("inquiry", `<span class="stage-stat">${acts.length} 个 Act</span>`))}
    <div class="inquiry-key"><span><i class="reply-line"></i>reply_to</span><span><i class="derived-line"></i>derived_from</span></div>
    <div class="stage-canvas inquiry-viewport" data-canvas="inquiry"><div class="inquiry-pan-layer" data-pan-layer="inquiry" style="width:${width}px;height:${height}px;${viewportTransform("inquiry")}"><div class="inquiry-canvas" style="width:${width}px;height:${height}px">
      <svg class="edge-layer" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">${replyEdges}${derivedEdges}</svg>
      ${acts.length ? nodes : empty("尚未选择协作线", "从左侧选择一个 thread。")}
    </div></div></div>
  </section>`;
}

function inquiryNode(act, position) {
  const selected = ui.selection.type === "act" && ui.selection.id === act.act_id;
  const addresses = actAddresses(act);
  return `<button class="act-node kind-${escapeHtml(act.kind)} ${selected ? "selected" : ""}" style="--x:${position.x}px;--y:${position.y}px" data-action="select-act" data-act-id="${escapeHtml(act.act_id)}" aria-pressed="${selected}">
    <header><span>${escapeHtml(actLabel(act))}</span><small>${escapeHtml(act.act_id)}</small></header>
    <p>${escapeHtml(actSummary(act))}</p>
    <footer><b>${escapeHtml(act.sender)}</b><i>→</i><b>${escapeHtml(act.recipient)}</b>${addresses.length ? `<em>${addresses.length} 地址</em>` : ""}</footer>
  </button>`;
}

function renderWorldStage() {
  const { objects, references } = worldData(ui.state);
  const scope = ui.worldScope;
  const local = scope.mode === "local" ? localWorldFor(ui.state, scope.actorId) : null;
  const allowed = local ? new Set([
    ...local.objects.map((item) => `object:${item.object_id}`),
    ...local.references.map((item) => `reference:${item.reference_id}`),
  ]) : null;
  const query = ui.worldSearch.trim().toLowerCase();
  const nodes = [
    ...objects.map((item) => ({ ...item, key: `object:${item.id}` })),
    ...references.map((item) => ({ ...item, key: `reference:${item.id}` })),
  ].filter((item) => !query || `${item.id} ${item.content ?? ""} ${item.meaning ?? ""}`.toLowerCase().includes(query));
  const ids = nodes.map((item) => item.key);
  const visible = new Set(ids);
  const edges = references.flatMap((reference) => {
    const refKey = `reference:${reference.id}`;
    const sourceKey = `object:${reference.sourceId}`;
    const targetKey = reference.target ? `${reference.target.kind}:${reference.target.id}` : "";
    return [
      visible.has(sourceKey) && visible.has(refKey) ? { source: sourceKey, target: refKey, weight: 1 } : null,
      visible.has(refKey) && visible.has(targetKey) ? { source: refKey, target: targetKey, weight: 1 } : null,
    ].filter(Boolean);
  });
  const positions = worldLayout.simulate(ids, edges, ui.canvasSize.width, ui.canvasSize.height, ui.selection.type === "world" ? `${ui.selection.kind}:${ui.selection.id}` : "");
  const newIds = newWorldIds(ui.previousState, ui.state);
  const edgePaths = edges.map((edge, index) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    return source && target ? `<path class="world-edge" d="${routedPath(source, target, (index % 3 - 1) * 14)}" marker-end="url(#world-arrow)" />` : "";
  }).join("");
  const cards = nodes.map((item) => worldNode(item, positions.get(item.key), allowed, newIds)).join("");
  return `<section class="stage world-stage">
    ${stageToolbar("语义世界", "Object / Reference 身份", `${renderWorldControls(objects, references)}${viewportControls("world")}`)}
    <div class="world-legend"><span><i class="object-dot"></i>语义 Object</span><span><i class="reference-dot"></i>语义 Reference</span>${local ? `<b>仅高亮 ${escapeHtml(scope.actorId)} 的 Actor 生命周期 Local World</b>` : ""}</div>
    <div class="stage-canvas world-canvas" data-canvas="world">
      <div class="canvas-layer" data-pan-layer="world" style="${viewportTransform("world")}">
        <svg class="edge-layer" viewBox="0 0 ${ui.canvasSize.width} ${ui.canvasSize.height}" preserveAspectRatio="none" aria-hidden="true"><defs><marker id="world-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>${edgePaths}</svg>
        ${cards || empty("语义世界为空", "通过“语义世界预置”或 Agent capability 产生 Object/Reference。")}
      </div>
    </div>
  </section>`;
}

function renderWorldControls(objects, references) {
  const agents = (ui.state?.actors ?? []).filter((actor) => actor.role === "agent");
  return `<div class="world-controls">
    <input data-field="world-search" value="${escapeHtml(ui.worldSearch)}" placeholder="搜索 ID、Object 内容或 Reference meaning" aria-label="搜索语义世界" />
    <select data-field="world-mode"><option value="global" ${ui.worldScope.mode === "global" ? "selected" : ""}>全局语义世界</option><option value="local" ${ui.worldScope.mode === "local" ? "selected" : ""}>Agent 局部世界</option></select>
    ${ui.worldScope.mode === "local" ? `<select data-field="world-actor">${agents.map((actor) => `<option value="${escapeHtml(actor.actor_id)}" ${ui.worldScope.actorId === actor.actor_id ? "selected" : ""}>${escapeHtml(actor.actor_id)}</option>`).join("")}</select>` : ""}
    <span>${objects.length} O · ${references.length} R</span>
  </div>`;
}

function worldNode(item, position, allowed, newIds) {
  if (!position) return "";
  const selected = ui.selection.type === "world" && ui.selection.kind === item.kind && ui.selection.id === item.id;
  const dimmed = allowed && !allowed.has(item.key);
  const appeared = item.kind === "object" ? newIds.objects.has(item.id) : newIds.references.has(item.id);
  const content = item.kind === "object" ? truncate(item.content, 74) : truncate(item.meaning || `${item.sourceId} → ${item.target?.kind} · ${item.target?.id}`, 74);
  const kindLabel = item.kind === "object" ? "Object" : "Reference";
  const label = `${kindLabel} · ${item.id}`;
  const dragging = ui.drag?.layout === "world" && ui.drag.id === item.key;
  return `<button class="world-node kind-${item.kind} ${selected ? "selected" : ""} ${dimmed ? "dimmed" : ""} ${appeared ? "appeared" : ""} ${dragging ? "is-dragging" : ""}" style="--x:${position.x}px;--y:${position.y}px" data-action="select-world" data-world-kind="${item.kind}" data-world-id="${escapeHtml(item.id)}" aria-label="查看 ${escapeHtml(label)}" aria-pressed="${selected}" title="${escapeHtml(`${label}\n${content}`)}"></button>`;
}

function renderInspector() {
  const title = ui.selection.type === "actor" ? "Actor" : ui.selection.type === "act" ? "Act" : "语义内容";
  return `<aside class="inspector">
    <header class="inspector-header"><div><span class="eyebrow">${title} 详情</span><h2>${escapeHtml(inspectorHeading())}</h2></div><button class="icon-button" data-action="close-inspector" aria-label="收起检视器">×</button></header>
    <nav class="inspector-tabs" aria-label="检视器标签">
      ${inspectorTab("details", "详情")}${inspectorTab("trace", "运行轨迹")}${inspectorTab("raw", "原始数据")}${inspectorTab("lab", "预置")}
    </nav>
    <div class="inspector-body">${renderInspectorBody()}</div>
  </aside>`;
}

function inspectorTab(id, label) {
  return `<button class="${ui.inspectorTab === id ? "active" : ""}" data-action="inspector-tab" data-tab="${id}">${label}</button>`;
}

function inspectorHeading() {
  if (ui.selection.type === "actor") return ui.selection.id || "选择 Actor";
  if (ui.selection.type === "act") return ui.selection.id || "选择 Act";
  return ui.selection.id || "选择 Object/Reference";
}

function renderInspectorBody() {
  if (ui.inspectorTab === "lab") return renderLab();
  if (ui.inspectorTab === "raw") return `<pre class="raw-view">${escapeHtml(pretty(inspectorRaw()))}</pre>`;
  if (ui.inspectorTab === "trace") return renderTrace();
  if (ui.selection.type === "actor") return renderActorInspector();
  if (ui.selection.type === "act") return renderActInspector();
  return renderWorldInspector();
}

function renderActorInspector() {
  const actor = selectedActor();
  if (!actor) return empty("未选择 Actor", "在 Actor 场中选择一个 Broker 或 Agent。");
  const context = actor.role === "broker" ? contextForBroker(ui.state, actor.actor_id) : null;
  const locals = localWorlds(ui.state).filter((world) => world.actorId === actor.actor_id);
  const key = `${actor.actor_id}:${ui.selectedThread}`;
  const view = ui.actorViewKey === key ? ui.actorView : null;
  const extra = actor.role === "broker" ? renderBrokerDetails(context, view) : renderAgentDetails(actor, locals, view);
  return `<div class="details-stack">
    <section class="detail-card"><span class="detail-label">${escapeHtml(actorRoleLabel[actor.role] ?? actor.role)}</span><h3>${escapeHtml(actor.actor_id)}</h3><p>${escapeHtml(actor.role === "broker" ? "协调群" : "持续演化；具体理解以公开历史为准")}</p>
      <dl><div><dt>创建者</dt><dd>${escapeHtml(actor.created_by || "—")}</dd></div><div><dt>对话消息</dt><dd>${actor.dialogue?.length ?? 0}（持续到 reset）</dd></div><div><dt>能力</dt><dd>${actor.capabilities?.length ? actor.capabilities.map((item) => `<code>${escapeHtml(item)}</code>`).join(" ") : "—"}</dd></div></dl>
    </section>${extra}</div>`;
}

function renderBrokerDetails(context, view) {
  if (!context) return empty("Broker 协调群尚未形成", "默认 Broker 仍可作为 Human 入口处理 Question。");
  const candidates = view?.local_world?.candidates ?? [];
  const history = view?.local_world?.history ?? [];
  return `<section class="detail-card"><h3>协调 context</h3><dl>
    <div><dt>群已形成</dt><dd>${context.formed ? "是" : "否"}</dd></div><div><dt>来源 Actor</dt><dd>${escapeHtml(context.source_actor_id || "Human / 未记录")}</dd></div><div><dt>来源 Question</dt><dd>${escapeHtml(context.source_question_id || "—")}</dd></div>
  </dl><h4>成员（${context.member_ids.length}）</h4><div class="chip-list">${context.member_ids.length ? context.member_ids.map((id) => `<button data-action="select-actor" data-actor-id="${escapeHtml(id)}">${escapeHtml(id)}</button>`).join("") : "—"}</div>
  ${!context.formed ? `<h4>当前可选 Agent（${candidates.length}）</h4><div class="chip-list">${candidates.length ? candidates.map((item) => `<button data-action="select-actor" data-actor-id="${escapeHtml(item.actor_id)}">${escapeHtml(item.actor_id)}</button>`).join("") : "无"}</div>` : ""}
  <p class="boundary-note">Broker 仅机械持有和转交 Semantic Address；此处不展示 Object/Reference 内容。</p>
  ${history.length ? `<h4>相关记录</h4><div class="mini-list">${history.slice(-5).map((act) => `<button data-action="select-act" data-act-id="${escapeHtml(act.act_id)}"><span>${escapeHtml(act.kind)}</span>${escapeHtml(actSummary(act))}</button>`).join("")}</div>` : ""}</section>`;
}

function renderAgentDetails(actor, locals, view) {
  const selectedLocal = localWorldFor(ui.state, actor.actor_id);
  const budgets = selectedLocal?.budgets ?? view?.budget ?? {};
  return `<section class="detail-card"><h3>Agent 局部世界</h3><p>${locals.length ? "随 Actor 持续到 Runtime reset" : "尚未物化 Local World"}</p>
    <dl><div><dt>Object 数量</dt><dd>${selectedLocal?.objects.length ?? 0}</dd></div><div><dt>Reference 数量</dt><dd>${selectedLocal?.references.length ?? 0}</dd></div></dl>
    <h4>当前 Question 预算</h4><div class="chip-list">${Object.entries(budgets).length ? Object.entries(budgets).map(([name, remaining]) => `<code>${escapeHtml(name)}: ${escapeHtml(remaining)}</code>`).join("") : "—"}</div>
    <button class="secondary" data-action="show-local-world" data-actor-id="${escapeHtml(actor.actor_id)}">查看当前 Agent 局部世界</button>
  </section>`;
}

function renderActInspector() {
  const act = currentThreadActs().find((item) => item.act_id === ui.selection.id);
  if (!act) return empty("Act 不在当前 thread", "从问题协作流或待处理列表中选择一个 Act。");
  const addresses = actAddresses(act);
  return `<div class="details-stack"><section class="detail-card"><span class="detail-label">${escapeHtml(actLabel(act))}</span><h3>${escapeHtml(act.act_id)}</h3><p>${escapeHtml(actContent(act) || actSummary(act))}</p>
    <dl><div><dt>sender</dt><dd><button data-action="select-actor" data-actor-id="${escapeHtml(act.sender)}">${escapeHtml(act.sender)}</button></dd></div><div><dt>recipient</dt><dd><button data-action="select-actor" data-actor-id="${escapeHtml(act.recipient)}">${escapeHtml(act.recipient)}</button></dd></div><div><dt>reply_to</dt><dd>${act.reply_to ? `<button data-action="select-act" data-act-id="${escapeHtml(act.reply_to)}">${escapeHtml(act.reply_to)}</button>` : "—"}</dd></div><div><dt>derived_from</dt><dd>${act.derived_from?.length ? act.derived_from.map((id) => `<button data-action="select-act" data-act-id="${escapeHtml(id)}">${escapeHtml(id)}</button>`).join(" ") : "—"}</dd></div></dl>
    <h4>语义地址</h4>${addresses.length ? `<div class="address-list">${addresses.map((address) => `<button data-action="select-world" data-world-kind="${escapeHtml(address.kind)}" data-world-id="${escapeHtml(address.id)}"><span>${escapeHtml(address.kind)}</span>${escapeHtml(address.id)}</button>`).join("")}</div>` : "无"}
  </section></div>`;
}

function renderWorldInspector() {
  const { objects, references } = worldData(ui.state);
  const kind = ui.selection.kind;
  const item = kind === "object" ? objects.find((object) => object.id === ui.selection.id) : references.find((reference) => reference.id === ui.selection.id);
  if (!item) return empty("未选择语义身份", "在语义世界中选择 Object 或 Reference。");
  const memberships = localMembership(ui.state, kind, item.id);
  const details = kind === "object" ? `<p class="object-content">${escapeHtml(item.content)}</p>` : `${item.meaning ? `<p class="object-content">${escapeHtml(item.meaning)}</p>` : ""}<dl><div><dt>source Object</dt><dd><button data-action="select-world" data-world-kind="object" data-world-id="${escapeHtml(item.sourceId)}">${escapeHtml(item.sourceId)}</button></dd></div><div><dt>target</dt><dd><button data-action="select-world" data-world-kind="${escapeHtml(item.target?.kind)}" data-world-id="${escapeHtml(item.target?.id)}">${escapeHtml(item.target?.kind)} · ${escapeHtml(item.target?.id)}</button></dd></div></dl>`;
  return `<div class="details-stack"><section class="detail-card"><span class="detail-label">Semantic${kind === "object" ? "Object" : "Reference"}</span><h3>${escapeHtml(item.id)}</h3>${details}
    <h4>Local World 分布</h4><div class="chip-list">${memberships.length ? memberships.map((world) => `<button data-action="show-local-world" data-actor-id="${escapeHtml(world.actorId)}">${escapeHtml(world.actorId)}</button>`).join("") : "尚未进入任何 Agent Local World"}</div>
  </section></div>`;
}

function renderTrace() {
  let traces = [];
  if (ui.selection.type === "act") traces = latestTracesForAct(ui.state, ui.selection.id);
  if (ui.selection.type === "actor") traces = (ui.state?.traces ?? []).filter((trace) => trace.actor_id === ui.selection.id).slice(-18);
  if (ui.selection.type === "world") traces = (ui.state?.traces ?? []).filter((trace) => JSON.stringify(trace.payload).includes(ui.selection.id)).slice(-18);
  return traces.length ? `<div class="trace-list">${traces.map((trace) => `<details><summary><span>#${trace.seq}</span><strong>${escapeHtml(trace.kind)}</strong><small>${escapeHtml(trace.actor_id || "runtime")}</small></summary><pre>${escapeHtml(pretty(trace.payload))}</pre></details>`).join("")}</div>` : empty("暂无相关 Trace", "运行一个 Step 后，此处会显示 model.input、Decision 和 Observation。");
}

function inspectorRaw() {
  if (ui.selection.type === "act") return currentThreadActs().find((act) => act.act_id === ui.selection.id);
  if (ui.selection.type === "actor") return selectedActor();
  const { objects, references } = worldData(ui.state);
  return ui.selection.kind === "object" ? objects.find((item) => item.id === ui.selection.id) : references.find((item) => item.id === ui.selection.id);
}

function renderLab() {
  const { objects, references } = worldData(ui.state);
  const targets = [
    ...objects.map((item) => ({ value: `object|${item.id}`, label: `Object · ${item.id}` })),
    ...references.map((item) => ({ value: `reference|${item.id}`, label: `Reference · ${item.id}` })),
  ];
  return `<div class="details-stack"><section class="detail-card lab-card"><span class="detail-label">调试预置</span><h3>Semantic World 预置（仅调试）</h3><p>直接准备实验初始 Semantic World；不产生 Act，不代表任何 Human、Broker 或 Agent 行为。</p>
    <label>SemanticObject 内容<textarea data-field="object-content" placeholder="添加一个初始 SemanticObject">${escapeHtml(ui.drafts.objectContent)}</textarea></label><button class="primary" data-action="add-object">预置 SemanticObject</button>
    <hr><label>source Object<select data-field="reference-source"><option value="">选择 Object</option>${objects.map((item) => `<option value="${escapeHtml(item.id)}" ${ui.drafts.referenceSource === item.id ? "selected" : ""}>${escapeHtml(item.id)}</option>`).join("")}</select></label>
    <label>target Semantic Address<select data-field="reference-target"><option value="">选择 target</option>${targets.map((item) => `<option value="${escapeHtml(item.value)}" ${ui.drafts.referenceTarget === item.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select></label>
    <label>Reference 开放 meaning<textarea data-field="reference-meaning" placeholder="可选；target 在 source 意义结构中的作用">${escapeHtml(ui.drafts.referenceMeaning)}</textarea></label>
    <button class="secondary" data-action="add-reference">预置 SemanticReference</button>
  </section></div>`;
}

function renderDock() {
  const replies = humanReplyTargets(ui.state);
  const { objects, references } = worldData(ui.state);
  const addresses = [...objects.map((item) => ({ kind: "object", id: item.id })), ...references.map((item) => ({ kind: "reference", id: item.id }))];
  const brokers = (ui.state?.actors ?? []).filter((actor) => actor.role === "broker");
  const isQuestion = ui.dockMode === "question";
  const summary = isQuestion
    ? ui.drafts.rawQuestion.trim() || "准备一个新问题"
    : ui.drafts.replyTarget
      ? `准备延续 ${ui.drafts.replyTarget}`
      : replies.length
        ? `${replies.length} 条 Human/Broker 关系可以补入`
        : "当前没有可补入的 Human/Broker 关系";
  const form = isQuestion ? `<div class="question-form">
      <label>原始问题 raw_question<textarea data-field="raw-question" placeholder="这轮需要理解和回答什么？">${escapeHtml(ui.drafts.rawQuestion)}</textarea></label>
      <label>解释上下文 C_interpret<input data-field="interpretation" value="${escapeHtml(ui.drafts.interpretation)}" placeholder="可选解释上下文" /></label>
      <label>Answer 要求 Intent<input data-field="intent" value="${escapeHtml(ui.drafts.intent)}" placeholder="可选 Answer 成立条件" /></label>
      <div class="question-routing"><label><input type="checkbox" data-field="new-context" ${ui.drafts.newContext ? "checked" : ""} />开启新的 Broker context</label>${!ui.drafts.newContext ? `<select data-field="recipient">${brokers.map((broker) => `<option value="${escapeHtml(broker.actor_id)}" ${ui.drafts.recipient === broker.actor_id ? "selected" : ""}>发送至 ${escapeHtml(broker.actor_id)}</option>`).join("")}</select>` : ""}</div>
      <details><summary>Semantic Addresses（可选）</summary><div class="address-picker">${addresses.length ? addresses.map((address) => `<label><input type="checkbox" data-address-choice value="${address.kind}:${escapeHtml(address.id)}" /><span>${address.kind}</span>${escapeHtml(address.id)}</label>`).join("") : "Semantic World 暂无可选地址"}</div></details>
      <details><summary>高级 JSON</summary><textarea id="questionTriple" data-field="advanced-question" placeholder='{"raw_question":"...","interpretation_context":{"content":null,"semantic_addresses":[]},"intent":null}'>${escapeHtml(ui.drafts.advancedQuestion)}</textarea></details>
      <button class="primary submit-question" data-action="submit-question">提交问题</button>
    </div>` : `<div class="reply-form"><label>要延续的 Human/Broker Act<select data-field="reply-target"><option value="">选择可见 Act</option>${replies.map((item) => `<option value="${escapeHtml(item.act.act_id)}" ${ui.drafts.replyTarget === item.act.act_id ? "selected" : ""}>${escapeHtml(item.threadId)} · ${escapeHtml(item.act.act_id)} · ${escapeHtml(actSummary(item.act))}</option>`).join("")}</select></label><label>增量内容<textarea data-field="reply-content" placeholder="补入参数、修正或成立条件">${escapeHtml(ui.drafts.replyContent)}</textarea></label><button class="primary" data-action="submit-reply">发送 incremental</button></div>`;
  return `<section class="command-dock ${ui.dockMode} ${ui.dockCollapsed ? "collapsed" : "expanded"}">
    <nav class="dock-tabs"><button class="${isQuestion ? "active" : ""}" data-action="dock-mode" data-mode="question">提出问题</button><button class="${!isQuestion ? "active" : ""}" data-action="dock-mode" data-mode="reply" ${replies.length ? "" : "disabled"}>补入增量${replies.length ? ` · ${replies.length}` : ""}</button></nav>
    <div class="dock-surface">
      <div class="dock-summary"><span>${escapeHtml(summary)}</span><button data-action="dock-toggle" aria-expanded="${!ui.dockCollapsed}">${ui.dockCollapsed ? "展开" : "收起"}${isQuestion ? "问题" : "增量"}</button></div>
      ${ui.dockCollapsed ? "" : form}
    </div>
  </section>`;
}

function render() {
  if (!ui.state) {
    app.innerHTML = `<div class="boot-screen"><span>WEAVEX</span><strong>正在读取 Runtime…</strong></div>`;
    return;
  }
  app.dataset.theme = ui.theme;
  app.classList.toggle("topbar-collapsed", ui.topbarCollapsed);
  app.innerHTML = `${renderHeader()}${renderDemoStory()}<div class="workbench ${ui.inspectorOpen ? "" : "inspector-closed"} ${ui.railCollapsed ? "rail-collapsed" : ""}"><button class="rail-toggle" data-action="rail-toggle">☰</button>${renderRail()}<main class="main-pane">${renderStage()}</main>${ui.inspectorOpen ? renderInspector() : ""}</div>${renderDock()}${ui.toast.text ? `<div class="toast ${ui.toast.kind}" role="status">${escapeHtml(ui.toast.text)}</div>` : ""}`;
  window.requestAnimationFrame(syncStageSize);
}

function syncStageSize() {
  const canvas = app.querySelector(".stage-canvas");
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const next = { width: Math.round(rect.width), height: Math.round(rect.height) };
  if (next.width < 120 || next.height < 120) return;
  if (Math.abs(next.width - ui.canvasSize.width) > 8 || Math.abs(next.height - ui.canvasSize.height) > 8) {
    ui.canvasSize = next;
    scheduleRender();
  }
}

function updateDraft(field, value, checked) {
  const mappings = {
    "raw-question": "rawQuestion",
    interpretation: "interpretation",
    intent: "intent",
    "new-context": "newContext",
    recipient: "recipient",
    "advanced-question": "advancedQuestion",
    "reply-target": "replyTarget",
    "reply-content": "replyContent",
    "object-content": "objectContent",
    "reference-source": "referenceSource",
    "reference-target": "referenceTarget",
    "reference-meaning": "referenceMeaning",
  };
  if (field === "speed") {
    ui.speed = Number(value);
    return;
  }
  if (field === "world-search") {
    ui.worldSearch = value;
    scheduleRender();
    return;
  }
  if (field === "world-mode") {
    ui.worldScope.mode = value;
    scheduleRender();
    return;
  }
  if (field === "world-actor") {
    ui.worldScope.actorId = value;
    scheduleRender();
    return;
  }
  const property = mappings[field];
  if (property) ui.drafts[property] = field === "new-context" ? checked : value;
}

app.addEventListener("input", (event) => {
  const field = event.target.dataset.field;
  if (field) updateDraft(field, event.target.value, event.target.checked);
});

app.addEventListener("change", (event) => {
  const field = event.target.dataset.field;
  if (field) {
    updateDraft(field, event.target.value, event.target.checked);
    if (["new-context", "recipient", "world-mode", "world-actor"].includes(field)) scheduleRender();
  }
});

app.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (ui.suppressNodeClick) {
    event.preventDefault();
    return;
  }
  const action = target.dataset.action;
  if (action === "demo-present") window.location.href = "/demo";
  if (action === "demo-toggle") { ui.demoHidden = !ui.demoHidden; scheduleRender(); }
  if (action === "demo-hide") { ui.demoHidden = true; scheduleRender(); }
  if (action === "demo-step") void performStep();
  if (action === "demo-next") void nextDemoChapter();
  if (action === "demo-review") { ui.view = "inquiry"; ui.demoHidden = true; scheduleRender(); }
  if (action === "view") { ui.view = target.dataset.view; scheduleRender(); }
  if (action === "step") void performStep();
  if (action === "autorun") void toggleAutoRun();
  if (action === "reset") void resetAll();
  if (action === "theme") { ui.theme = ui.theme === "dark" ? "light" : "dark"; window.localStorage.setItem("weavex-debug-theme", ui.theme); scheduleRender(); }
  if (action === "select-thread") {
    chooseThread(target.dataset.threadId);
    if (target.dataset.actId) chooseAct(target.dataset.actId);
  }
  if (action === "select-actor") chooseActor(target.dataset.actorId);
  if (action === "select-act") chooseAct(target.dataset.actId);
  if (action === "select-world") chooseWorld(target.dataset.worldKind, target.dataset.worldId);
  if (action === "open-question") { ui.dockMode = "question"; ui.dockCollapsed = false; scheduleRender(); window.requestAnimationFrame(() => app.querySelector("[data-field='raw-question']")?.focus()); }
  if (action === "dock-mode") { ui.dockMode = target.dataset.mode; ui.dockCollapsed = false; scheduleRender(); }
  if (action === "dock-toggle") { ui.dockCollapsed = !ui.dockCollapsed; scheduleRender(); }
  if (action === "reply-target") { chooseAct(target.dataset.actId); ui.dockMode = "reply"; ui.dockCollapsed = false; ui.drafts.replyTarget = target.dataset.replyId; scheduleRender(); }
  if (action === "submit-question") void submitQuestion();
  if (action === "submit-reply") void submitReply();
  if (action === "add-object") void submitObject();
  if (action === "add-reference") void submitReference();
  if (action === "inspector-tab") { ui.inspectorTab = target.dataset.tab; scheduleRender(); }
  if (action === "show-local-world") { ui.view = "world"; ui.worldScope = { mode: "local", actorId: target.dataset.actorId }; scheduleRender(); }
  if (action === "rearrange") { actorLayout.resetPins(); worldLayout.resetPins(); scheduleRender(); }
  if (action === "reset-view") resetViewport(target.dataset.view);
  if (action === "rail-toggle") { ui.railOpen = !ui.railOpen; scheduleRender(); }
  if (action === "rail-collapse") { ui.railCollapsed = !ui.railCollapsed; scheduleRender(); }
  if (action === "topbar-collapse") { ui.topbarCollapsed = !ui.topbarCollapsed; scheduleRender(); }
  if (action === "close-inspector") { ui.inspectorOpen = false; scheduleRender(); }
});

app.addEventListener("pointerdown", (event) => {
  const actorNode = event.target.closest(".actor-node");
  const actorCanvas = event.target.closest(".actor-canvas");
  if (actorNode && actorCanvas && event.button === 0) {
    const rect = actorCanvas.getBoundingClientRect();
    ui.drag = { layout: "actor", id: actorNode.dataset.actorId, node: actorNode, canvas: actorCanvas, rect, startX: event.clientX, startY: event.clientY, moved: false };
    actorNode.setPointerCapture(event.pointerId);
    return;
  }
  const worldNode = event.target.closest(".world-node");
  const worldCanvas = event.target.closest(".world-canvas");
  if (worldNode && worldCanvas && event.button === 0) {
    const rect = worldCanvas.getBoundingClientRect();
    ui.drag = {
      layout: "world",
      id: `${worldNode.dataset.worldKind}:${worldNode.dataset.worldId}`,
      node: worldNode,
      canvas: worldCanvas,
      rect,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    worldNode.classList.add("is-dragging");
    worldNode.setPointerCapture(event.pointerId);
    return;
  }
  const panCanvas = event.target.closest("[data-canvas]");
  if (
    !panCanvas ||
    event.button !== 0 ||
    event.target.closest("button, input, select, textarea, summary, label")
  ) return;
  const view = panCanvas.dataset.canvas;
  ui.canvasGesture = {
    view,
    canvas: panCanvas,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    originX: ui.viewports[view].x,
    originY: ui.viewports[view].y,
  };
  event.preventDefault();
  panCanvas.classList.add("is-panning");
  panCanvas.setPointerCapture(event.pointerId);
});

app.addEventListener("pointermove", (event) => {
  if (ui.drag) {
    const dx = event.clientX - ui.drag.startX;
    const dy = event.clientY - ui.drag.startY;
    if (Math.abs(dx) + Math.abs(dy) < 7 && !ui.drag.moved) return;
    ui.drag.moved = true;
    const view = ui.drag.layout === "world" ? "world" : "actors";
    const viewport = ui.viewports[view];
    const x = (event.clientX - ui.drag.rect.left - viewport.x) / (ui.drag.rect.width * viewport.zoom) * ui.canvasSize.width;
    const y = (event.clientY - ui.drag.rect.top - viewport.y) / (ui.drag.rect.height * viewport.zoom) * ui.canvasSize.height;
    if (ui.drag.layout === "world") worldLayout.pin(ui.drag.id, x, y);
    else actorLayout.pin(ui.drag.id, x, y);
    scheduleRender();
    return;
  }
  if (!ui.canvasGesture || event.pointerId !== ui.canvasGesture.pointerId) return;
  const gesture = ui.canvasGesture;
  ui.viewports[gesture.view].x = gesture.originX + event.clientX - gesture.startX;
  ui.viewports[gesture.view].y = gesture.originY + event.clientY - gesture.startY;
  applyViewport(gesture.view);
});

function finishPointerGesture(event) {
  if (ui.drag) {
    ui.drag.node?.classList.remove("is-dragging");
    const moved = ui.drag.moved;
    ui.drag = null;
    if (moved) {
      ui.suppressNodeClick = true;
      window.setTimeout(() => { ui.suppressNodeClick = false; }, 0);
      scheduleRender();
    }
  }
  if (ui.canvasGesture && event.pointerId === ui.canvasGesture.pointerId) {
    ui.canvasGesture.canvas.classList.remove("is-panning");
    ui.canvasGesture = null;
  }
}

app.addEventListener("pointerup", finishPointerGesture);
app.addEventListener("pointercancel", finishPointerGesture);

app.addEventListener("wheel", (event) => {
  const canvas = event.target.closest("[data-canvas]");
  if (!canvas || event.target.closest("input, select, textarea")) return;
  event.preventDefault();
  const view = canvas.dataset.canvas;
  const viewport = ui.viewports[view];
  const rect = canvas.getBoundingClientRect();
  const cursorX = event.clientX - rect.left;
  const cursorY = event.clientY - rect.top;
  const nextZoom = Math.max(0.45, Math.min(2.4, viewport.zoom * (event.deltaY < 0 ? 1.12 : 0.89)));
  const sceneX = (cursorX - viewport.x) / viewport.zoom;
  const sceneY = (cursorY - viewport.y) / viewport.zoom;
  viewport.x = cursorX - sceneX * nextZoom;
  viewport.y = cursorY - sceneY * nextZoom;
  viewport.zoom = nextZoom;
  applyViewport(view);
}, { passive: false });

const resizeObserver = new ResizeObserver((entries) => {
  if (entries[0]) scheduleRender();
});
resizeObserver.observe(app);

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    ui.railOpen = false;
    scheduleRender();
  }
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    if (ui.dockMode === "question") void submitQuestion();
    if (ui.dockMode === "reply") void submitReply();
  }
});

render();
await refresh();
if (ui.selection.id) void loadActorView(ui.selection.id, ui.selectedThread);
