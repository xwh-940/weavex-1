import {
  advanceDemo,
  loadDemo,
  readState,
  stepRuntime,
} from "/debug/api.js";

const app = document.querySelector("#demo");
const view = {
  state: null,
  busy: false,
  error: "",
  pollTimer: 0,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function allActs() {
  return Object.values(view.state?.threads ?? {}).flat();
}

function currentQuestion() {
  const id = view.state?.demo?.question_id;
  return allActs().find((act) => act.act_id === id && act.kind === "question");
}

function currentThreadActs() {
  const question = currentQuestion();
  if (!question) return [];
  return Object.values(view.state?.threads ?? {}).find(
    (acts) => acts.some((act) => act.act_id === question.act_id),
  ) ?? [];
}

function currentAnswers() {
  const question = currentQuestion();
  if (!question) return { publicAnswer: null, sourceAnswer: null };
  const answers = currentThreadActs().filter((act) => act.kind === "answer");
  return {
    publicAnswer: answers.find(
      (act) => act.recipient === "human" && act.reply_to === question.act_id,
    ) ?? null,
    sourceAnswer: answers.find((act) => act.recipient !== "human") ?? null,
  };
}

function localMaterialCount(actorId) {
  const local = (view.state?.agent_local_worlds ?? []).find(
    (item) => item.actor_id === actorId,
  );
  return (local?.world?.objects?.length ?? 0) + (local?.world?.references?.length ?? 0);
}

function answerText(answer) {
  return String(answer?.payload?.answer ?? "");
}

function revealToken(text, phase) {
  const patterns = {
    1: /ASTRA-[A-F0-9]+/,
    2: /CAL-[A-F0-9]+/,
    3: /NEBULA-[A-F0-9]+-42/,
  };
  return text.match(patterns[phase])?.[0] ?? "答案已经返回";
}

function chapterCopy(phase) {
  return {
    1: {
      eyebrow: "第一幕 · 看不见",
      title: "引路者自己不能打开日志，\n它会怎么办？",
      intro: "入口就在面前，但真正的口令藏在密封日志里。负责协调的位置只能找答案，不能偷看内容。",
    },
    2: {
      eyebrow: "第二幕 · 记得住",
      title: "日志地址消失以后，\n谁还记得那个秘密？",
      intro: "这一幕不再提供入口。只有上一幕真正读过日志的位置，才拥有那段没有公开的经历。",
    },
    3: {
      eyebrow: "第三幕 · 重新开始",
      title: "怎样得到一个\n没被旧秘密影响的判断？",
      intro: "问题很简单，但答案必须来自一个没有接触过天文台秘密的新视角。忽略秘密不等于从未见过。",
    },
    4: {
      eyebrow: "故事完成",
      title: "它们不是换了名字，\n而是真的知道不同的东西。",
      intro: "同一个系统里，一个位置记得密封日志，另一个位置从干净的起点开始。协调者始终只负责让答案回来。",
    },
  }[phase];
}

function liveStory() {
  const demo = view.state.demo;
  const queue = view.state.queue ?? [];
  const next = queue[0];
  const { publicAnswer, sourceAnswer } = currentAnswers();
  const processing = view.busy || view.state.debug_control?.step_in_flight;
  if (demo.complete) {
    return {
      title: "三件事真的发生了",
      body: "引路者没有偷看日志；同一个读取者记住了秘密；新观察者没有继承这段旧经历。",
      tone: "complete",
    };
  }
  if (processing) {
    return {
      title: "正在发生",
      body: "当前角色正在根据自己真正看见的信息作出选择。",
      tone: "thinking",
    };
  }
  if (publicAnswer) {
    return {
      title: "这一幕完成了",
      body: answerText(publicAnswer),
      tone: "complete",
    };
  }
  if (sourceAnswer && next?.recipient === "broker") {
    return {
      title: "答案已经形成",
      body: "知道答案的位置已经开口，现在引路者只需要把它送回给你。",
      tone: "returning",
    };
  }
  if (next?.recipient === "broker") {
    return {
      title: demo.phase === 3 ? "需要一个干净视角" : "引路者正在选择",
      body: demo.phase === 2
        ? "它必须找到上一幕真正读过日志的位置，不能让陌生位置猜。"
        : demo.phase === 3
          ? "旧读取者知道得太多。这次的来源边界会让另一个位置出现。"
          : "它看不见日志，只能建立或选择一个真正能够取得内容的位置。",
      tone: "coordinating",
    };
  }
  if (next?.recipient === "agent-1") {
    return {
      title: demo.phase === 2 ? "同一个读取者被找到了" : "读取者即将打开日志",
      body: demo.phase === 2
        ? "没有再次交付地址；这次回答依赖的是它上一幕已经拥有的经历。"
        : "接下来只有它会接触日志内容，引路者仍然看不到。",
      tone: "reader",
    };
  }
  if (next?.recipient === "agent-2") {
    return {
      title: "新的观察者出现了",
      body: "它没有继承旧日志，面对的只有眼前这段公开文本。",
      tone: "fresh",
    };
  }
  return {
    title: "故事暂时停住了",
    body: "这一幕没有形成完整答案。可以重新开始一次真实运行。",
    tone: "stopped",
  };
}

function actorCard({ icon, name, description, className = "", badge = "" }) {
  return `<article class="mind-card ${className}">
    <div class="mind-icon">${icon}</div>
    <div><small>${badge}</small><h3>${name}</h3><p>${description}</p></div>
  </article>`;
}

function renderMinds() {
  const actors = new Set((view.state.actors ?? []).map((actor) => actor.actor_id));
  const nextRecipient = view.state.queue?.[0]?.recipient ?? "";
  const phase = view.state.demo.phase;
  const readerExists = actors.has("agent-1");
  const freshExists = actors.has("agent-2");
  const readerMaterials = localMaterialCount("agent-1");
  const freshMaterials = localMaterialCount("agent-2");
  return `<section class="mind-map" aria-label="故事中的认知位置">
    ${actorCard({ icon: "?", name: "你", description: "提出问题，也只收到最终公开的答案。", className: "human" })}
    <div class="mind-link"><i></i><span>问题 / 答案</span></div>
    ${actorCard({ icon: "◇", name: "引路者", description: "知道应该找谁，但永远不能打开密封日志。", className: `guide ${nextRecipient === "broker" ? "active" : ""}`, badge: "只协调，不偷看" })}
    <div class="mind-branches">
      ${readerExists ? actorCard({
        icon: "◉",
        name: "日志读取者",
        description: readerMaterials > 1 ? "已经接触密封日志，会保留这段经历。" : "准备取得日志中的实际内容。",
        className: `reader ${nextRecipient === "agent-1" ? "active" : ""}`,
        badge: readerMaterials > 1 ? `拥有 ${readerMaterials} 项私有材料` : "有限读取能力",
      }) : `<article class="mind-card waiting"><div class="mind-icon">·</div><div><small>尚未出现</small><h3>谁会读取日志？</h3><p>系统还没有预先安排答案来源。</p></div></article>`}
      ${freshExists ? actorCard({
        icon: "✦",
        name: "新观察者",
        description: "没有继承旧日志，只依据眼前的公开文本。",
        className: `fresh ${nextRecipient === "agent-2" ? "active" : ""}`,
        badge: freshMaterials ? `接触 ${freshMaterials} 项材料` : "没有旧秘密",
      }) : phase >= 3 ? `<article class="mind-card waiting fresh-wait"><div class="mind-icon">·</div><div><small>等待处境决定</small><h3>需要新视角吗？</h3><p>如果旧经历不合适，新的起点才有意义。</p></div></article>` : ""}
    </div>
  </section>`;
}

function primaryAction() {
  const demo = view.state?.demo;
  const queue = view.state?.queue ?? [];
  if (view.busy) return { label: "正在思考…", disabled: true };
  if (!demo?.active) return { label: "开始故事", action: "start" };
  if (queue.length) return { label: "继续看看", action: "step" };
  if (demo.complete) return { label: "换一组随机线索再来", action: "restart" };
  if (demo.answered) {
    return {
      label: demo.phase === 3 ? "看看结局" : "进入下一幕",
      action: "next",
    };
  }
  return { label: "重新开始", action: "restart" };
}

function renderLanding() {
  return `<div class="landing">
    <div class="stars" aria-hidden="true"></div>
    <header class="audience-brand"><span>W</span><strong>WeaveX</strong><small>一个真实运行的认知实验</small></header>
    <section class="landing-copy">
      <span class="story-kicker">A LIVE AI STORY</span>
      <h1>失落天文台</h1>
      <p>三个问题，看见一件很少被展示的事：<br>AI 不只是轮流说话，它们可以真的拥有不同经历。</p>
      <div class="promise-row"><span>看不见</span><span>记得住</span><span>重新开始</span></div>
      <button class="story-button" data-action="start">开始故事 <b>→</b></button>
      <small class="live-note"><i></i>每次线索随机生成，真实模型现场决定下一步</small>
    </section>
    <a class="backstage-link" href="/">我是开发者，打开幕后结构</a>
  </div>`;
}

function renderStory() {
  const demo = view.state.demo;
  const copy = chapterCopy(demo.phase);
  const live = liveStory();
  const action = primaryAction();
  const { publicAnswer } = currentAnswers();
  const token = publicAnswer ? revealToken(answerText(publicAnswer), demo.phase) : "";
  const progress = [1, 2, 3].map((phase) => {
    const state = demo.complete || phase < demo.phase ? "done" : phase === demo.phase ? "active" : "";
    return `<i class="${state}"></i>`;
  }).join("");
  return `<div class="story-shell">
    <header class="story-header">
      <a class="audience-brand compact" href="/demo"><span>W</span><strong>WeaveX</strong></a>
      <div class="chapter-progress">${progress}<small>${escapeHtml(copy.eyebrow)}</small></div>
      <a class="quiet-link" href="/">看幕后</a>
    </header>
    <main class="story-grid">
      <section class="story-copy">
        <span class="story-kicker">${escapeHtml(copy.eyebrow)}</span>
        <h1>${escapeHtml(copy.title).replace("\n", "<br>")}</h1>
        <p>${escapeHtml(copy.intro)}</p>
        ${token ? `<div class="answer-reveal"><small>这一幕得到</small><strong>${escapeHtml(token)}</strong></div>` : ""}
        <div class="live-card ${live.tone}"><small>${escapeHtml(live.title)}</small><p>${escapeHtml(live.body)}</p></div>
        <div class="story-actions">
          <button class="story-button" data-action="${action.action ?? ""}" ${action.disabled ? "disabled" : ""}>${escapeHtml(action.label)} <b>→</b></button>
          <span>每次点击只让一个位置行动</span>
        </div>
        ${view.error ? `<details class="story-error" open><summary>这一次没有顺利继续</summary><p>${escapeHtml(view.error)}</p></details>` : ""}
      </section>
      <section class="scene-panel">
        <div class="scene-glow" aria-hidden="true"></div>
        ${renderMinds()}
        <footer><span>关键不在于有几个“角色”</span><strong>而在于谁真正见过什么</strong></footer>
      </section>
    </main>
  </div>`;
}

function render() {
  if (!view.state) {
    app.innerHTML = `<div class="loading"><i></i><span>正在连接天文台…</span></div>`;
    return;
  }
  app.innerHTML = view.state.demo?.active ? renderStory() : renderLanding();
}

async function pollWhileBusy() {
  if (!view.busy) return;
  try {
    view.state = await readState();
    render();
  } catch {
    // The action result remains the authoritative error surface.
  }
  if (view.busy) view.pollTimer = window.setTimeout(pollWhileBusy, 420);
}

async function perform(action) {
  if (view.busy) return;
  view.busy = true;
  view.error = "";
  render();
  view.pollTimer = window.setTimeout(pollWhileBusy, 420);
  try {
    const result = action === "step"
      ? await stepRuntime()
      : action === "next"
        ? await advanceDemo()
        : await loadDemo();
    view.state = result.state;
  } catch (error) {
    view.error = error.message;
    try {
      view.state = await readState();
    } catch {
      // Keep the last visible state when the server itself is unavailable.
    }
  } finally {
    window.clearTimeout(view.pollTimer);
    view.pollTimer = 0;
    view.busy = false;
    render();
  }
}

app.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  if (action === "start" || action === "restart") void perform("start");
  if (action === "step") void perform("step");
  if (action === "next") void perform("next");
});

render();
try {
  view.state = await readState();
} catch (error) {
  view.error = error.message;
  view.state = { demo: { active: false } };
}
render();
