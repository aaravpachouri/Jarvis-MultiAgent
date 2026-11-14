(function (root, factory) {
  const api = factory();
  if (root) root.JARVISConversationUI = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
  }

  function formatCapability(value) {
    return String(value || "specialist.function")
      .split(".")
      .map(part => part.replace(/[_-]+/g, " "))
      .join(" · ");
  }

  function renderOperatorGuide(agent) {
    const guide = agent?.metadata?.operator_guide || {};
    const capabilities = Array.isArray(agent?.capabilities) ? agent.capabilities : [];
    const functions = Array.isArray(guide.functions) && guide.functions.length
      ? guide.functions
      : capabilities.map(capability => ({
          capability,
          description: "Declared in the live agent manifest."
        }));
    const bestFor = Array.isArray(guide.best_for) ? guide.best_for : [];
    const boundaries = Array.isArray(guide.boundaries) ? guide.boundaries : [];

    const functionMarkup = functions.length
      ? functions.map(item => `<article class="operator-guide-function">
          <strong>${escapeHTML(formatCapability(item.capability))}</strong>
          <code>${escapeHTML(item.capability)}</code>
          <p>${escapeHTML(item.description || "Declared capability.")}</p>
        </article>`).join("")
      : '<p class="operator-guide-empty">No capabilities are declared by this agent.</p>';

    return `<section class="operator-guide" aria-label="Agent capability guide">
      <div class="operator-guide-section">
        <div class="operator-guide-kicker">FUNCTIONS THIS AGENT CAN PERFORM · ${functions.length}</div>
        <div class="operator-guide-functions">${functionMarkup}</div>
      </div>
      ${bestFor.length ? `<div class="operator-guide-section">
        <div class="operator-guide-kicker">BEST USED FOR</div>
        <ul>${bestFor.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
      </div>` : ""}
      ${boundaries.length ? `<div class="operator-guide-section operator-guide-limits">
        <div class="operator-guide-kicker">EXECUTION BOUNDARIES</div>
        <ul>${boundaries.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
      </div>` : ""}
    </section>`;
  }

  function getMissionCoordination(events, taskId) {
    const rootId = String(taskId || "").trim();
    if (!rootId) return { taskIds: [], handoffs: [], relatedEvents: [] };

    const taskIds = new Set([rootId]);
    const source = Array.isArray(events) ? events : [];
    for (let pass = 0; pass < 12; pass += 1) {
      let changed = false;
      for (const event of source) {
        const parentId = String(event?.payload?.parent_task_id || "");
        const childId = String(event?.task_id || "");
        if (parentId && taskIds.has(parentId) && childId && !taskIds.has(childId)) {
          taskIds.add(childId);
          changed = true;
        }
      }
      if (!changed) break;
    }

    const relatedEvents = source.filter(event =>
      taskIds.has(String(event?.task_id || "")) ||
      taskIds.has(String(event?.payload?.parent_task_id || ""))
    );
    const handoffEvents = relatedEvents.filter(event => event?.type === "agent.task.delegated");
    const latestForTask = new Map();
    for (const event of relatedEvents) {
      const id = String(event?.task_id || "");
      if (!id || id === rootId) continue;
      const type = String(event?.type || "");
      if (!(type.startsWith("task.") || type === "agent.task.result")) continue;
      const current = latestForTask.get(id);
      const time = Date.parse(event?.timestamp || "") || 0;
      const currentTime = Date.parse(current?.timestamp || "") || 0;
      if (!current || time >= currentTime) latestForTask.set(id, event);
    }

    const seen = new Set();
    const handoffs = handoffEvents.filter(event => {
      const id = String(event?.task_id || "");
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    }).map(event => {
      const childId = String(event.task_id);
      const outcome = latestForTask.get(childId);
      const outcomeType = String(outcome?.type || "");
      const outcomeStatus = String(outcome?.payload?.status || "");
      let status = outcomeStatus;
      if (!status && outcomeType.startsWith("task.")) status = outcomeType.slice(5);
      if (!status) status = "delegated";
      return {
        taskId: childId,
        parentTaskId: String(event?.payload?.parent_task_id || ""),
        fromAgentId: String(event?.from_agent_id || event?.agent_id || ""),
        toAgentId: String(event?.to_agent_id || ""),
        capabilities: Array.isArray(event?.payload?.capabilities)
          ? event.payload.capabilities.map(String)
          : [],
        status,
        durationMs: Number(outcome?.payload?.duration_ms ?? 0) || 0
      };
    });

    return { taskIds: [...taskIds], handoffs, relatedEvents };
  }

  function renderCoordinationMarkup(trace, options = {}) {
    const agents = Array.isArray(options.agents) ? options.agents : [];
    const isRunning = Boolean(options.running);
    const rootAgentId = String(options.rootAgentId || "voice.jarvis");
    const nameFor = id => {
      const agent = agents.find(item => item.agent_id === id);
      return agent?.name || id || "JARVIS";
    };
    const handoffs = Array.isArray(trace?.handoffs) ? trace.handoffs : [];

    if (!handoffs.length) {
      const message = isRunning
        ? "No specialist handoff has been observed yet. The runtime trace will show one when a real delegation occurs."
        : `No specialist handoff was recorded. This request was handled directly by ${nameFor(rootAgentId)}.`;
      return `<section class="mk-turn-coordination">
        <div class="mk-turn-coordination-head">COORDINATION TRACE <span>0 HANDOFFS</span></div>
        <p class="mk-turn-coordination-empty">${escapeHTML(message)}</p>
      </section>`;
    }

    const items = handoffs.map(item => {
      const target = item.toAgentId || "Capability router";
      const status = String(item.status || "delegated").toUpperCase().replace(/[_-]+/g, " ");
      const capabilities = item.capabilities.length
        ? `<small>${escapeHTML(item.capabilities.join(" · "))}</small>`
        : "";
      const duration = item.durationMs ? `<small>${item.durationMs.toLocaleString()} ms</small>` : "";
      return `<article class="mk-turn-handoff">
        <span class="mk-turn-handoff-node" aria-hidden="true">↳</span>
        <div class="mk-turn-handoff-copy">
          <strong>${escapeHTML(nameFor(item.fromAgentId))} <span>→</span> ${escapeHTML(nameFor(item.toAgentId) || target)}</strong>
          ${capabilities}
        </div>
        <span class="mk-turn-handoff-status status-${escapeHTML(String(item.status).toLowerCase())}">${escapeHTML(status)}</span>
        ${duration}
      </article>`;
    }).join("");

    return `<section class="mk-turn-coordination">
      <div class="mk-turn-coordination-head">OBSERVED SPECIALIST HANDOFFS <span>${handoffs.length}</span></div>
      <div class="mk-turn-handoff-list">${items}</div>
    </section>`;
  }

  return {
    escapeHTML,
    formatCapability,
    renderOperatorGuide,
    getMissionCoordination,
    renderCoordinationMarkup
  };
});
