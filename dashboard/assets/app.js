(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { overview: null, agents: [], currentView: 'overview', agentFilter: 'all', selectedReport: null, reportText: '', taskBusy: false, hudPanel: null, selectedAgentId: 'coding.forge', focusTab: 'chat', lastHudTask: null, speechRecognition: null, eventCursor: 0, liveEvents: [], seenEventIds: new Set(), eventPollInFlight: false, activeMissionId: null, activeMissions: [], agentDockTab: 'overview', missionTarget: 'voice.jarvis' };
  const symbols = { 'Athena': '⌕', 'Atlas': '▦', 'Forge': '⌘', 'JARVIS': '◉', 'Launch': '↗', 'Memory & Notes': '▤', 'Planner': '⌁', 'Quant': '⌇', 'Scout': '⌖', 'Scribe': '✎' };
  const agentIdentities = {
    'voice.jarvis': { slug: 'jarvis', short: 'JARVIS', specialty: 'COMMAND & VOICE', motto: '“I’m always here.”', persona: 'Calm, precise, and quietly anticipatory', color: '#ff5968' },
    'research.athena': { slug: 'athena', short: 'Athena', specialty: 'RESEARCH & ANALYSIS', motto: '“Turning information into intelligence.”', persona: 'Curious, evidence-led, and methodical', color: '#42dcff' },
    'desktop.atlas': { slug: 'atlas', short: 'Atlas', specialty: 'DESKTOP AUTOMATION', motto: '“I’ll handle the system.”', persona: 'Grounded, cautious, and operational', color: '#45e6c5' },
    'coding.forge': { slug: 'forge', short: 'Forge', specialty: 'CODE & DEVELOPMENT', motto: '“Building what’s next.”', persona: 'Direct, systematic, and quality-focused', color: '#ff5965' },
    'trading.quant': { slug: 'quant', short: 'Quant', specialty: 'MARKET & TRADING RESEARCH', motto: '“Data. Patterns. Edge.”', persona: 'Quantitative, skeptical, and timestamp-aware', color: '#35e0b8' },
    'search.scout': { slug: 'scout', short: 'Scout', specialty: 'WEB SEARCH & INTEL', motto: '“Finding what others miss.”', persona: 'Fast, curious, and source-aware', color: '#b58cff' },
    'planning.planner': { slug: 'planner', short: 'Planner', specialty: 'PLANNING & STRATEGY', motto: '“A plan for every mission.”', persona: 'Structured, pragmatic, and dependency-aware', color: '#e5bc6d' },
    'documentation.scribe': { slug: 'scribe', short: 'Scribe', specialty: 'DOCUMENTATION', motto: '“Recording progress for the future.”', persona: 'Clear, composed, and context-preserving', color: '#58dff2' },
    'memory.notes': { slug: 'memory', short: 'Memory', specialty: 'MEMORY & KNOWLEDGE', motto: '“I remember so you don’t have to.”', persona: 'Discreet, user-controlled, and detail-oriented', color: '#45dff5' },
    'deployment.launch': { slug: 'launch', short: 'Launch', specialty: 'DEPLOYMENT & TOOLS', motto: '“From idea to impact.”', persona: 'Mission-minded, measured, and release-conscious', color: '#ff974c' },
  };
  const agentDockOrder = ['voice.jarvis', 'research.athena', 'desktop.atlas', 'coding.forge', 'trading.quant', 'search.scout', 'planning.planner', 'documentation.scribe', 'memory.notes', 'deployment.launch'];
  const identityFor = agent => agentIdentities[agent?.agent_id] || { slug: 'jarvis', short: agent?.name || agent?.agent_id || 'Agent', specialty: agent?.role || 'SPECIALIST', motto: 'Runtime-registered specialist.', persona: 'Identity details were not supplied by the local registry.', color: '#45eaff' };
  const titles = { overview: 'Overview', agents: 'Agent network', tasks: 'Task center', markets: 'Market intelligence', reports: 'Reports & artifacts' };
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
  const renderTaskResult = (output, options = {}) => window.JARVISResultFormatter
    ? window.JARVISResultFormatter.render(output, options)
    : `<pre class="hud-result-output">${escapeHTML(output || 'No output returned.')}</pre>`;
  const summarizeTaskResult = output => window.JARVISResultFormatter
    ? window.JARVISResultFormatter.summary(output)
    : String(output || '').replace(/\s+/g, ' ').trim().slice(0, 240);
  const fmtNum = (value, max = 2) => value === null || value === undefined || value === '' ? '—' : (typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: max }) : String(value));
  const fmtAge = seconds => {
    if (seconds === null || seconds === undefined) return 'TIME UNKNOWN';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };
  const fmtDate = value => {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };
  // The dashboard bridge can append dashboard lifecycle records before its async
  // bus observer drains older runtime records. Sort by occurrence time, with the
  // delivery sequence only as a stable tie-breaker; sequence alone is not time.
  const runtimeEventTime = event => {
    const value = Date.parse(event?.timestamp || '');
    return Number.isFinite(value) ? value : 0;
  };
  const compareRuntimeEvents = (a, b) => runtimeEventTime(a) - runtimeEventTime(b)
    || Number(a?.sequence || 0) - Number(b?.sequence || 0);
  const compareRuntimeEventsNewestFirst = (a, b) => compareRuntimeEvents(b, a);
  const toast = (message, type = '') => {
    const hudToast = $('#hud-status-toast');
    if (document.body.classList.contains('hud-active') && hudToast) {
      hudToast.textContent = message;
      hudToast.className = `hud-status-toast show ${type === 'error' ? 'error' : ''}`.trim();
      window.clearTimeout(toast._hudTimer);
      toast._hudTimer = window.setTimeout(() => hudToast.classList.remove('show'), 4200);
      return;
    }
    const stack = $('#toast-stack'); const el = document.createElement('div');
    el.className = `toast ${type}`; el.textContent = message; stack.append(el);
    window.setTimeout(() => el.remove(), 4200);
  };
  let accessKeyRejected = false;
  async function api(path, options = {}) {
    const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
    if (path.startsWith('/api/')) {
      if (accessKeyRejected) throw new Error('JARVIS access key rejected. Reload this page to try again.');
      let accessKey = sessionStorage.getItem('jarvisAccessKey');
      if (!accessKey) {
        accessKey = window.prompt('JARVIS MultiAgent is private. Enter the dashboard access key configured in Render:');
        if (!accessKey || !accessKey.trim()) throw new Error('A JARVIS access key is required to connect.');
        accessKey = accessKey.trim();
        sessionStorage.setItem('jarvisAccessKey', accessKey);
      }
      headers['X-JARVIS-Access-Key'] = accessKey;
    }
    const response = await fetch(path, { cache: 'no-store', ...options, headers });
    let payload;
    try { payload = await response.json(); } catch { payload = { error: `Unexpected response (${response.status})` }; }
    if (response.status === 401) {
      sessionStorage.removeItem('jarvisAccessKey');
      accessKeyRejected = true;
      throw new Error('JARVIS access key was rejected. Reload the page to try again.');
    }
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }
  function navigate(view) {
    if (!titles[view]) return;
    state.currentView = view;
    document.body.classList.toggle('hud-active', view === 'overview');
    hideHudPanel();
    $$('.view').forEach(el => el.classList.toggle('active', el.id === `view-${view}`));
    $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
    $('#breadcrumb-current').textContent = titles[view];
    $('#sidebar').classList.remove('open');
    if (view === 'agents') renderAgentGrid();
    if (view === 'tasks') renderTasks();
    if (view === 'markets') renderMarketDetail();
    if (view === 'reports') loadReports();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function updateClock() {
    const now = new Date();
    $('#clock-time').textContent = now.toLocaleTimeString([], { hour12: false });
    $('#clock-date').textContent = now.toLocaleDateString([], { weekday: 'short', month: 'short', day: '2-digit', year: 'numeric' }).toUpperCase();
  }
  function hideHudPanel() {
    const drawer = $('#hud-drawer');
    if (drawer) drawer.classList.add('hidden');
    state.hudPanel = null;
  }
  function hudStatus(agent) {
    if (!agent) return 'registered';
    const id = String(agent.agent_id || '');
    if ((state.activeMissions || []).some(mission => mission.agent_id === id && ['queued', 'running'].includes(String(mission.status || '').toLowerCase()))) return 'busy';
    if (state.taskBusy && state.lastHudTask?.agentId === id) return 'busy';
    const latestByTask = new Map();
    for (const event of [...(state.liveEvents || [])].sort(compareRuntimeEvents)) {
      if (!event.task_id || !String(event.type || '').startsWith('task.')) continue;
      latestByTask.set(event.task_id, event);
    }
    for (const event of latestByTask.values()) {
      if (event.agent_id === id && event.type === 'task.started') return 'busy';
    }
    return String(agent.status || 'registered').toLowerCase();
  }

  function currentHudMission() {
    const active = (state.activeMissions || []).find(item => item.task_id === state.activeMissionId) || (state.activeMissions || [])[0] || null;
    if (state.taskBusy && state.lastHudTask) return { ...state.lastHudTask, status: active?.status || state.lastHudTask.status || 'running' };
    if (active) {
      const session = state.lastHudTask?.taskId === active.task_id ? state.lastHudTask : null;
      return { ...(session || {}), ...active, taskId: active.task_id, agentId: active.agent_id, prompt: session?.prompt || active.prompt || '', status: active.status || session?.status || 'running', activeFromRuntime: true };
    }
    if (state.lastHudTask) return state.lastHudTask;
    const history = state.overview?.task_history?.recent_tasks || [];
    const audit = history[history.length - 1];
    if (!audit) return null;
    const result = (state.overview?.recent_results || []).find(item => item.task_id === audit.task_id);
    const status = String(audit.status || String(audit.event || '').split('.').pop() || 'unknown').toLowerCase();
    return {
      taskId: audit.task_id || '', agentId: audit.agent_id || 'voice.jarvis', status,
      prompt: result?.prompt || '', output: result?.output || result?.error || result?.summary || '',
      finishedAt: audit.timestamp || null, auditOnly: !result,
    };
  }
  function hudAgentOption(agent, selected) {
    const status = hudStatus(agent);
    const canRun = ['ready', 'busy'].includes(status);
    return `<option value="${escapeHTML(agent.agent_id)}" ${selected ? 'selected' : ''} ${canRun ? '' : 'disabled'}>${escapeHTML(agent.name)} · ${escapeHTML(status.toUpperCase())}</option>`;
  }
  function hudTaskForm(agentId = 'voice.jarvis', direct = false) {
    const options = state.agents.map(agent => hudAgentOption(agent, agent.agent_id === agentId)).join('');
    return `<form class="hud-panel-task" id="hud-panel-task-form" data-default-target="${escapeHTML(agentId)}"><label class="hud-form-label" for="hud-panel-prompt">MISSION OBJECTIVE</label><textarea id="hud-panel-prompt" maxlength="6000" required placeholder="Describe the task to execute…"></textarea><label class="hud-form-label" for="hud-panel-target">EXECUTION ROUTE</label><select id="hud-panel-target"><option value="voice.jarvis" ${agentId === 'voice.jarvis' ? 'selected' : ''}>JARVIS · AUTO-COORDINATE</option>${options}</select><button class="hud-panel-action" type="submit">${direct ? 'Execute with specialist ↗' : 'Launch mission ↗'}</button><p class="hud-muted">Only handlers marked ready can be targeted directly. JARVIS can coordinate supported workflows.</p></form>`;
  }
  function hudAgentRow(agent) {
    const status = hudStatus(agent);
    const symbol = symbols[agent.name] || '◈';
    return `<button type="button" class="hud-agent-row" data-hud-agent-select="${escapeHTML(agent.agent_id)}"><span class="hud-agent-orb">${escapeHTML(symbol)}</span><span class="hud-agent-row-main"><strong>${escapeHTML(agent.name)}</strong><small>${escapeHTML(agent.agent_id)}</small></span><span class="hud-agent-state-tag ${escapeHTML(status)}">${escapeHTML(status)}</span></button>`;
  }
  function hudTaskSummary(item) {
    const status = String(item.status || 'unknown').toLowerCase();
    return `<div class="hud-event"><time>${escapeHTML(fmtDate(item.finished_at || item.started_at || item.timestamp))}</time><div><strong>${escapeHTML(item.agent_id || 'JARVIS')} · ${escapeHTML(status.toUpperCase())}</strong><p>${escapeHTML(item.summary || item.prompt || item.error || item.output || 'Task lifecycle event recorded.')}</p></div></div>`;
  }
  function missionCinematicModel(last = currentHudMission()) {
    const running = Boolean(state.taskBusy || (state.activeMissions || []).some(item => ['queued', 'running'].includes(String(item.status || '').toLowerCase())));
    const status = String(last?.status || '').toLowerCase();
    const succeeded = !running && status === 'succeeded';
    const failed = !running && ['failed', 'timed_out', 'cancelled'].includes(status);
    const taskIds = new Set(last?.taskId ? [last.taskId] : []);
    const events = state.liveEvents || [];
    for (let pass = 0; pass < 8; pass++) {
      let changed = false;
      for (const event of events) {
        const parentId = event.payload?.parent_task_id;
        if (parentId && taskIds.has(parentId) && event.task_id && !taskIds.has(event.task_id)
            && (event.type === 'task.started' || event.type === 'agent.task.delegated' || event.type === 'agent.task.result' || event.type.startsWith('agent.action.'))) {
          taskIds.add(event.task_id); changed = true;
        }
      }
      if (!changed) break;
    }
    const missionEvents = last?.taskId ? events.filter(event => taskIds.has(event.task_id) || taskIds.has(event.payload?.parent_task_id)).sort(compareRuntimeEvents) : [];
    const rootStarted = missionEvents.some(event => event.type === 'task.started');
    const handoffs = missionEvents.filter(event => event.type === 'agent.task.delegated').length;
    const phase = running ? (handoffs > 0 ? 3 : rootStarted ? 2 : 1) : last ? 4 : 0;
    const missionState = running ? 'running' : succeeded ? 'success' : failed ? 'failed' : last ? 'recorded' : 'standby';
    const title = running
      ? handoffs > 0 ? 'COORDINATING SPECIALISTS' : rootStarted ? 'MISSION IN EXECUTION' : 'COMMAND ACCEPTED'
      : succeeded ? 'MISSION COMPLETE'
      : failed ? 'MISSION TERMINATED'
      : last ? 'LAST MISSION ARCHIVED' : 'AWAITING DIRECTIVE';
    const subtitle = running
      ? String(last?.prompt || 'The local runtime has accepted the mission.').replace(/\s+/g, ' ').trim().slice(0, 112)
      : succeeded ? 'Runtime returned a successful outcome. Review the mission result below.'
      : failed ? `Runtime recorded ${status || 'an unsuccessful terminal outcome'}. Review the failure details below.`
      : last ? String(last.prompt || 'The last recorded task is available below.').replace(/\s+/g, ' ').trim().slice(0, 112)
      : 'Stand by for an operator directive. Mission telemetry will illuminate as the runtime reports actual events.';
    const liveLine = running
      ? handoffs > 0 ? `${handoffs} OBSERVED HANDOFF${handoffs === 1 ? '' : 'S'} · COORDINATION LIVE` : rootStarted ? 'RUNTIME EXECUTING · EVENT TRACE LIVE' : 'QUEUED · WAITING FOR RUNTIME START'
      : succeeded ? 'SEQUENCE COMPLETE · RESULT RECEIVED'
      : failed ? `SEQUENCE ENDED · ${status.toUpperCase()}`
      : last ? 'NO ACTIVE MISSION · LAST OUTCOME RETAINED' : 'LOCAL RUNTIME · STANDING BY';
    return { running, status, succeeded, failed, missionState, title, subtitle, liveLine, phase, handoffs, rootStarted, missionEvents, last, taskIds };
  }

  function missionCinematicMarkup(last = currentHudMission()) {
    const model = missionCinematicModel(last);
    const progress = model.phase * 25;
    const phaseText = String(model.phase).padStart(2, '0');
    const traceText = last?.taskId ? `TRACE · ${String(last.taskId).slice(0, 13).toUpperCase()}` : 'TRACE · AWAITING TASK ID';
    const stages = [
      ['01', 'COMMAND', 'Operator directive received'],
      ['02', 'RUNTIME', 'Local runtime executing'],
      ['03', 'COORDINATION', 'Observed specialist handoffs'],
      ['04', 'RESULT', 'Terminal result returned'],
    ];
    return `<section class="mission-cinematic-banner" id="hud-mission-cinematic" data-mission-state="${model.missionState}" aria-label="Cinematic mission status" aria-live="polite">
      <div class="mission-cinema-raster" aria-hidden="true"></div><div class="mission-cinema-scan" aria-hidden="true"></div>
      <div class="mission-cinema-topline"><span><i class="mission-cinema-led ${model.running ? 'is-live' : ''}"></i> SUIT LINK <b>//</b> MISSION CONTROL</span><span id="hud-cinema-status">${escapeHTML(model.liveLine)}</span></div>
      <div class="mission-cinema-main">
        <div class="mission-cinema-reactor" aria-hidden="true"><svg viewBox="0 0 100 100"><circle class="mission-reactor-halo" cx="50" cy="50" r="46"/><circle class="mission-reactor-orbit" cx="50" cy="50" r="39"/><circle class="mission-reactor-ring" cx="50" cy="50" r="30"/><circle class="mission-reactor-core" cx="50" cy="50" r="21"/><circle class="mission-reactor-hotspot" cx="50" cy="50" r="8"/><path d="M50 3V15M50 85V97M3 50H15M85 50H97M17 17 25 25M75 75 83 83M83 17 75 25M25 75 17 83"/></svg><small>ARC / XI</small></div>
        <div class="mission-cinema-copy"><span class="mission-cinema-eyebrow">DIRECTIVE / MARK XI</span><h3 id="hud-cinema-title">${escapeHTML(model.title)}</h3><p id="hud-cinema-subtitle">${escapeHTML(model.subtitle)}</p></div>
        <div class="mission-cinema-phase"><strong id="hud-cinema-phase">${phaseText}</strong><span>PHASE<br><b>/ 04</b></span></div>
      </div>
      <div class="mission-cinema-progress" role="progressbar" aria-label="Mission lifecycle phase" aria-valuemin="0" aria-valuemax="4" aria-valuenow="${model.phase}" aria-valuetext="Phase ${model.phase} of 4"><span id="hud-cinema-progress-fill" class="${model.running ? 'indeterminate' : ''}" style="width:${progress}%"></span></div>
      <div class="mission-cinema-stages">${stages.map(([number, label, title]) => `<div class="mission-cinema-stage" data-mission-cinematic-step="${Number(number)}" title="${title}"><i>${number}</i><span>${label}</span><b>○</b></div>`).join('')}</div>
      <div class="mission-cinema-footer"><span id="hud-cinema-footer">${escapeHTML(model.liveLine)}</span><span><i></i>${escapeHTML(traceText)}</span></div>
    </section>`;
  }

  const suitProfiles = [
    { id: 'mark-iii', label: 'MARK III', title: 'Mark III · Classic red and gold armor' },
    { id: 'mark-v', label: 'MARK V', title: 'Mark V · Crimson and silver suitcase armor' },
    { id: 'mark-xlii', label: 'MARK XLII', title: 'Mark XLII · Gold and red autonomous armor' },
    { id: 'war-machine', label: 'WAR MACHINE', title: 'War Machine · Graphite and steel tactical armor' },
  ];
  function cycleHudSuitProfile() {
    const button = $('#hud-mini-suit');
    const label = $('#hud-mini-suit-label');
    if (!button || !label) return;
    const current = suitProfiles.findIndex(profile => profile.id === button.dataset.suitProfile);
    const next = suitProfiles[(current + 1 + suitProfiles.length) % suitProfiles.length];
    button.dataset.suitProfile = next.id;
    label.textContent = next.label;
    button.title = `SUIT BAY · ${next.title} · Click to cycle armor profiles`;
    button.setAttribute('aria-label', `Armory profile: ${next.title}. Activate to cycle armor profiles.`);
    button.classList.remove('armor-profile-change');
    void button.offsetWidth;
    button.classList.add('armor-profile-change');
    // The suit button itself reflects the selected profile; avoid a toast that could cover the HUD.
  }

  function openHudPanel(kind, agentId = null) {
    const drawer = $('#hud-drawer');
    const body = $('#hud-drawer-body');
    if (!drawer || !body) return;
    state.hudPanel = kind;
    const requestedAgentId = agentId || state.selectedAgentId || 'coding.forge';
    let title = 'Agent panel';
    let kicker = 'JARVIS /X · COMMAND';
    let markup = '';
    if (kind === 'agent') {
      const agent = state.agents.find(item => item.agent_id === requestedAgentId);
      if (!agent) { setHudToast('Agent details are not available until the runtime responds.', 'error'); return; }
      state.selectedAgentId = agent.agent_id;
      const status = hudStatus(agent);
      title = agent.name;
      kicker = 'SPECIALIST WORKSPACE · LIVE RUNTIME';
      const ready = ['ready', 'busy'].includes(status);
      const identity = identityFor(agent);
      markup = `<div class="hud-specialist-identity" style="--agent-accent:${escapeHTML(identity.color)}"><img src="/assets/agents/${identity.slug}.svg" alt=""><div><strong>${escapeHTML(identity.short)}</strong><small>${escapeHTML(identity.specialty)}</small><p>${escapeHTML(identity.persona)}</p></div></div><div class="hud-panel-status"><span>●</span><span>${escapeHTML(agent.role || agent.name)}</span><strong class="${ready ? 'state-live' : 'state-registered'}">${escapeHTML(status)}</strong></div><div class="hud-panel-block"><div class="hud-panel-kicker">IDENTITY</div><div class="hud-panel-id">${escapeHTML(agent.agent_id)}</div><p class="hud-panel-description">${escapeHTML(agent.description || 'Registered specialist identity; no description supplied.')}</p></div><div class="hud-panel-block"><div class="hud-panel-kicker">CAPABILITIES · ${(agent.capabilities || []).length}</div><div class="hud-capabilities">${(agent.capabilities || []).map(cap => `<span>${escapeHTML(cap)}</span>`).join('') || '<span>No capabilities declared</span>'}</div></div><div class="hud-panel-block"><div class="hud-panel-kicker">RUNTIME DETAILS</div><p class="hud-panel-description">Enabled: ${agent.enabled === false ? 'No' : 'Yes'}<br>Concurrency limit: ${escapeHTML(agent.max_concurrency ?? '—')}<br>Timeout: ${escapeHTML(agent.timeout_seconds ?? '—')} seconds</p></div>${ready ? hudTaskForm(agent.agent_id, true) : `<div class="hud-panel-block"><p class="hud-muted">This identity is registered but has no executable handler. You can ask JARVIS to coordinate a task related to ${escapeHTML(agent.name)}; direct execution is not available.</p>${hudTaskForm('voice.jarvis', false)}</div>`}`;
    } else if (kind === 'agents') {
      title = 'Agent registry'; kicker = 'NETWORK · 10 IDENTITIES';
      const ready = state.agents.filter(a => ['ready', 'busy'].includes(hudStatus(a))).length;
      markup = `<div class="hud-panel-status"><span>●</span><span>${ready} executable · ${state.agents.length} identities</span><strong class="state-live">SYNCED</strong></div><p class="hud-muted">Select any identity to inspect its capabilities and available task routing.</p><div class="hud-agent-list">${state.agents.map(hudAgentRow).join('') || '<p class="hud-muted">Waiting for agent registry…</p>'}</div>`;
    } else if (kind === 'mission' || kind === 'tasks') {
      title = 'Mission control'; kicker = state.taskBusy ? 'MISSION · EXECUTION IN PROGRESS' : 'MISSION · READY FOR INPUT';
      const latest = currentHudMission();
      const hist = state.overview?.recent_results || [];
      const events = hist.slice(-6).reverse();
      const missionCinematic = missionCinematicMarkup(latest);
      markup = `${missionCinematic}<div class="hud-panel-status"><span>${state.taskBusy ? '◉' : '●'}</span><span>${state.taskBusy ? 'Task request running in local runtime' : 'Local runtime ready for a mission'}</span><strong class="${state.taskBusy ? 'state-registered' : 'state-live'}">${state.taskBusy ? 'EXECUTING' : 'STANDING BY'}</strong></div>${latest ? `<div class="hud-panel-block"><div class="hud-panel-kicker">CURRENT MISSION</div><p class="hud-panel-description">${escapeHTML(latest.prompt || 'No prompt is stored for this task.')}</p><div class="hud-event"><time>STATE</time><div><strong>${escapeHTML(latest.status || 'submitted')}</strong><p>${escapeHTML(latest.agentId || latest.agent_id || 'voice.jarvis')} · ${state.taskBusy ? 'Waiting for the runtime response…' : 'Last returned outcome'}</p></div></div>${latest.output ? `<div class="hud-result-mount">${renderTaskResult(latest.output)}</div><button type="button" class="hud-panel-action" id="hud-speak-latest" style="margin-top:8px">Speak latest result ◖))</button>` : ''}</div>` : ''}${!state.taskBusy ? hudTaskForm('voice.jarvis', false) : '<p class="hud-muted">JARVIS is waiting for the active task to return. Runtime status is refreshed while the mission runs.</p>'}<div class="hud-panel-block"><div class="hud-panel-kicker">RECENT EXECUTIONS</div><div class="hud-event-list">${events.length ? events.map(hudTaskSummary).join('') : '<p class="hud-muted">No session results are available yet. Persisted lifecycle history is available in the full task center.</p>'}</div><button type="button" class="hud-panel-action" data-open-workspace="tasks">Open task history ↗</button></div>`;
    } else if (kind === 'intelligence') {
      title = 'Market intelligence'; kicker = 'QUANT · READ-ONLY';
      const market = state.overview?.market;
      const indexes = market?.indexes || [];
      markup = `<div class="hud-panel-status"><span>●</span><span>${escapeHTML(market?.label || 'No saved market report')}</span><strong class="${market?.kind === 'nse_mcp' ? 'state-live' : 'state-registered'}">${market?.kind === 'nse_mcp' ? 'NSE MCP' : 'REPORT'}</strong></div><p class="hud-muted">${market?.available ? `${escapeHTML(market.market || 'India markets')} · ${escapeHTML(market.report_type || 'report')} · ${market.age_seconds == null ? 'Quote age unavailable' : `Report generated ${escapeHTML(fmtAge(market.age_seconds))}`}` : 'No saved market report is available. Request a read-only snapshot to populate this panel.'}</p>${indexes.map(item => `<div class="hud-market-index"><strong>${escapeHTML(item.name)}</strong><span>${escapeHTML(fmtNum(item.value))}${item.change == null ? '' : ` · ${Number(item.change) > 0 ? '+' : ''}${escapeHTML(fmtNum(Number(item.change), 2))}%`}</span></div>`).join('')}${market?.latest_session ? `<p class="hud-muted">Latest session: ${escapeHTML(market.latest_session)}</p>` : ''}${market?.generated_at ? `<p class="hud-muted">Report timestamp: ${escapeHTML(fmtDate(market.generated_at))}</p>` : ''}<div class="hud-panel-block"><button type="button" class="hud-panel-action" id="hud-request-snapshot">Request read-only snapshot ↗</button><button type="button" class="hud-panel-action" data-open-workspace="markets" style="margin-top:7px">Open full intelligence view ↗</button><p class="hud-muted">Report retrieval time is not necessarily the quote time. Source timestamps are authoritative; JARVIS does not place orders.</p></div>`;
    } else if (kind === 'settings') {
      title = 'System diagnostics'; kicker = 'LOCAL-FIRST · LOOPBACK ONLY';
      const runtime = state.overview?.runtime || {};
      const total = runtime.registered_agents || state.agents.length || 0;
      const ready = runtime.executable_agents || state.agents.filter(a => ['ready', 'busy'].includes(hudStatus(a))).length;
      const uptime = state.overview?.uptime_seconds || 0;
      const upText = uptime < 3600 ? `${Math.floor(uptime / 60)}m` : `${Math.floor(uptime / 3600)}h ${Math.floor((uptime % 3600) / 60)}m`;
      markup = `<div class="hud-panel-status"><span>●</span><span>Python dashboard runtime</span><strong class="${ready ? 'state-live' : 'state-registered'}">${ready ? 'ONLINE' : 'DEGRADED'}</strong></div><div class="hud-metric-grid"><div class="hud-metric"><span>Agent identities</span><strong>${escapeHTML(fmtNum(total, 0))}</strong></div><div class="hud-metric"><span>Executable</span><strong>${escapeHTML(fmtNum(ready, 0))}/${escapeHTML(fmtNum(total, 0))}</strong></div><div class="hud-metric"><span>Task outcomes</span><strong>${escapeHTML(fmtNum(state.overview?.task_history?.terminal_event_count || 0, 0))}</strong></div><div class="hud-metric"><span>Session uptime</span><strong>${escapeHTML(upText)}</strong></div></div><div class="hud-panel-block"><div class="hud-panel-kicker">SAFETY & CONNECTION</div><p class="hud-panel-description">Dashboard bound to loopback by default.<br>Market data is read-only.<br>No order placement endpoint is exposed.<br>Secrets and environment values are not shown here.</p><button type="button" class="hud-panel-action" id="hud-refresh-runtime">Refresh runtime ↻</button></div>`;
    }
    $('#hud-drawer-title').textContent = title;
    $('#hud-drawer-kicker').textContent = kicker;
    drawer.dataset.panel = kind;
    body.innerHTML = markup;
    drawer.classList.remove('hidden');
    if (kind === 'mission' || kind === 'tasks') renderMissionWidget();
    renderHudFocusPanel();
    const taskForm = $('#hud-panel-task-form', body);
    if (taskForm) taskForm.addEventListener('submit', event => {
      event.preventDefault();
      const prompt = $('#hud-panel-prompt', taskForm).value.trim();
      const target = $('#hud-panel-target', taskForm).value || 'voice.jarvis';
      runTask(prompt, target);
    });
    const snapshot = $('#hud-request-snapshot', body);
    if (snapshot) snapshot.addEventListener('click', () => runTask('Live Indian market snapshot', 'voice.jarvis'));
    const speak = $('#hud-speak-latest', body);
    if (speak) speak.addEventListener('click', speakHudResult);
    const refresh = $('#hud-refresh-runtime', body);
    if (refresh) refresh.addEventListener('click', () => refreshOverview(true));
  }
  function setHudToast(message, type = '') {
    const target = $('#hud-status-toast');
    if (!target) { toast(message, type); return; }
    target.textContent = message;
    target.className = `hud-status-toast show ${type === 'error' ? 'error' : ''}`.trim();
    window.clearTimeout(setHudToast._timer);
    setHudToast._timer = window.setTimeout(() => target.classList.remove('show'), 4200);
  }
  function startHudDictation(target) {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) { setHudToast('Voice input is not supported by this browser. You can type your task.', 'error'); return; }
    try {
      if (state.speechRecognition) state.speechRecognition.abort();
      const recognition = new Recognition();
      state.speechRecognition = recognition;
      recognition.lang = 'en-IN';
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;
      recognition.onstart = () => setHudToast('Listening… speak your task now.');
      recognition.onresult = event => {
        let transcript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) transcript += event.results[i][0].transcript;
        target.value = transcript;
      };
      recognition.onerror = event => setHudToast(`Voice input error: ${event.error || 'unknown error'}`, 'error');
      recognition.onend = () => { state.speechRecognition = null; if (target.value.trim()) target.focus(); };
      recognition.start();
    } catch (error) { setHudToast(`Could not start voice input: ${error.message}`, 'error'); }
  }
  function speakHudResult() {
    const output = state.lastHudTask?.output;
    if (!output) { setHudToast('No task output is available to speak yet.', 'error'); return; }
    if (!('speechSynthesis' in window)) { setHudToast('Speech playback is not supported by this browser.', 'error'); return; }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(String(output).slice(0, 5000));
    utterance.lang = 'en-IN';
    window.speechSynthesis.speak(utterance);
  }
  function renderAgentTile(agent, compact = false) {
    const status = hudStatus(agent);
    const caps = Array.isArray(agent.capabilities) ? agent.capabilities : [];
    const identity = identityFor(agent);
    const name = escapeHTML(identity.short || agent.name || agent.agent_id);
    const img = `/assets/agents/${identity.slug}.svg`;
    const id = escapeHTML(agent.agent_id || 'unknown.agent');
    const role = escapeHTML(agent.description || agent.role || 'Registered specialist identity.');
    const specialty = escapeHTML(identity.specialty || agent.role || 'SPECIALIST');
    const portrait = `<span class="agent-portrait-wrap" style="--agent-accent:${escapeHTML(identity.color)}"><img class="agent-portrait" src="${img}" alt="" loading="lazy" decoding="async"><span class="agent-portrait-scan" aria-hidden="true"></span></span>`;
    if (compact) return `<article class="agent-tile" tabindex="0" role="button" data-agent-id="${id}" aria-label="Inspect ${name}">${portrait}<div class="agent-tile-main"><div class="agent-tile-top"><span class="agent-symbol">${escapeHTML(symbols[agent.name] || '◈')}</span><span class="agent-state ${escapeHTML(status)}">${escapeHTML(status.toUpperCase())}</span></div><h3>${name}</h3><div class="agent-id">${id}</div><div class="agent-role">${role}</div><div class="agent-tile-bottom"><span>CAPABILITIES</span><span class="agent-cap-count">${caps.length} DECLARED</span></div></div></article>`;
    return `<article class="agent-card" tabindex="0" role="button" data-agent-id="${id}" aria-label="Inspect ${name}" style="--agent-accent:${escapeHTML(identity.color)}">${portrait}<div class="agent-card-body"><div class="agent-card-header"><span class="agent-symbol">${escapeHTML(symbols[agent.name] || '◈')}</span><span class="agent-state ${escapeHTML(status)}">${escapeHTML(status.toUpperCase())}</span></div><h3>${name}</h3><div class="agent-specialty">${specialty}</div><div class="agent-id">${id}</div><p>${role}</p><div class="capability-list">${caps.slice(0, 6).map(cap => `<span class="capability-chip">${escapeHTML(cap)}</span>`).join('')}${caps.length > 6 ? `<span class="capability-chip">+${caps.length - 6} more</span>` : ''}</div><div class="agent-card-footer"><span>${escapeHTML(agent.role || 'SPECIALIST AGENT')}</span><span>${caps.length} CAPABILITIES ↗</span></div></div></article>`;
  }
  function renderAgentDock() {
    const host = $('#mk-agent-cards');
    if (!host) return;
    const tab = state.agentDockTab || 'overview';
    const byId = new Map((state.agents || []).map(agent => [agent.agent_id, agent]));
    const ordered = [...agentDockOrder.map(id => byId.get(id)).filter(Boolean), ...(state.agents || []).filter(agent => !agentDockOrder.includes(agent.agent_id))];
    const readyCount = ordered.filter(agent => ['ready', 'busy'].includes(hudStatus(agent))).length;
    const count = $('#mk-dock-online-count');
    if (count) count.textContent = `${readyCount} EXECUTABLE · ${ordered.length} IDENTITIES`;
    $$('[data-dock-tab]').forEach(button => button.classList.toggle('active', button.dataset.dockTab === tab));
    if (!ordered.length) { host.innerHTML = '<div class="mk-empty">Waiting for agent registry from the local runtime.</div>'; return; }
    host.innerHTML = ordered.map(agent => {
      const identity = identityFor(agent);
      const status = hudStatus(agent);
      const statusText = status === 'ready' ? 'ONLINE' : status === 'busy' ? 'WORKING' : status.toUpperCase();
      const selected = agent.agent_id === state.selectedAgentId;
      let subline = identity.specialty;
      let quote = identity.motto;
      if (tab === 'capabilities') {
        subline = 'CAPABILITY PROFILE';
        quote = (agent.capabilities || []).slice(0, 2).join(' · ') || 'No capabilities declared by the runtime.';
      } else if (tab === 'tasks') {
        const observed = (state.liveEvents || []).filter(event => event.agent_id === agent.agent_id && (String(event.type || '').startsWith('task.') || String(event.type || '').startsWith('agent.action.'))).length;
        subline = 'OBSERVED RUNTIME EVENTS';
        quote = `${observed} event${observed === 1 ? '' : 's'} in the active dashboard trace`;
      } else if (tab === 'personalities') {
        subline = 'PERSONALITY PROFILE';
        quote = identity.persona;
      }
      const stableName = escapeHTML(identity.short || agent.name || agent.agent_id);
      const slug = escapeHTML(identity.slug);
      const safeColor = escapeHTML(identity.color);
      return `<button type="button" class="mk-agent-card ${status === 'busy' ? 'is-busy' : ''} ${status === 'registered' ? 'is-registered' : ''} ${selected ? 'is-selected' : ''}" style="--agent-accent:${safeColor}" data-hud-agent-select="${escapeHTML(agent.agent_id)}" aria-pressed="${selected ? 'true' : 'false'}" title="Open ${stableName} specialist panel · ${escapeHTML(statusText)}"><span class="mk-agent-portrait"><img src="/assets/agents/${slug}.svg" alt="" loading="lazy" decoding="async"><i class="mk-portrait-ring" aria-hidden="true"></i><i class="mk-portrait-scan" aria-hidden="true"></i><span class="mk-portrait-status" aria-label="${escapeHTML(statusText)}"></span></span><span class="mk-agent-card-info"><strong>${stableName}</strong><small>${escapeHTML(subline)}</small><span class="mk-agent-card-status"><i></i>${escapeHTML(statusText)}</span><q>${escapeHTML(quote)}</q></span><span class="mk-agent-sparkline" aria-hidden="true">${Array.from({length:12}, (_,i) => `<i style="--bar:${[24,42,31,64,46,86,35,57,74,28,53,40][(i + identity.slug.length) % 12]}%;--delay:${(i%5)*.08}s"></i>`).join('')}</span></button>`;
    }).join('');
  }
  function renderLivingNetwork() {
    const host = $('#living-network');
    if (!host) return;
    const agents = state.agents || [];
    const linkHost = $('#mk-network-links');
    if (!agents.length) {
      host.innerHTML = '<div class="mk-empty">No agent identities returned by the runtime.</div>';
      if (linkHost) linkHost.innerHTML = '';
      return;
    }
    const positions = {
      'research.athena': [31, 12],
      'desktop.atlas': [69, 12],
      'documentation.scribe': [16, 29],
      'coding.forge': [84, 29],
      'search.scout': [9, 48],
      'deployment.launch': [91, 48],
      'planning.planner': [18, 69],
      'memory.notes': [82, 69],
      'trading.quant': [32, 85],
      'voice.jarvis': [68, 85],
    };
    const colors = {
      'research.athena': '#38d8ff', 'desktop.atlas': '#35efc5',
      'documentation.scribe': '#35efc5', 'coding.forge': '#ff605f',
      'search.scout': '#9d8aff', 'deployment.launch': '#9d8aff',
      'planning.planner': '#ffd36b', 'memory.notes': '#f3ca68',
      'trading.quant': '#49dcf0', 'voice.jarvis': '#ff705d',
    };
    const center = [500, 430];
    // Historical relationships are muted. A connection becomes live only while
    // a child task is still running, or briefly when a real result message returns.
    const persistedEvents = state.overview?.task_history?.recent_tasks || [];
    const taskById = new Map(persistedEvents.filter(item => item.task_id).map(item => [item.task_id, item]));
    const persistedHandoffs = persistedEvents
      .filter(item => item.parent_task_id && taskById.has(item.parent_task_id))
      .map(child => ({ fromAgentId: taskById.get(child.parent_task_id).agent_id, toAgentId: child.agent_id, eventId: `${child.parent_task_id}:${child.task_id}`, live: false }));
    const liveEvents = state.liveEvents || [];
    const terminalTaskTypes = new Set(['task.succeeded', 'task.failed', 'task.timed_out', 'task.cancelled']);
    const chronologicalLiveEvents = [...liveEvents].sort(compareRuntimeEvents);
    const latestTaskEvent = new Map();
    chronologicalLiveEvents.forEach(event => {
      if (event.task_id && (event.type === 'task.started' || terminalTaskTypes.has(event.type))) latestTaskEvent.set(event.task_id, event);
    });
    const activeTaskIds = new Set((state.activeMissions || []).map(item => item.task_id).filter(Boolean));
    const activeAgentIds = new Set((state.activeMissions || []).map(item => item.agent_id).filter(Boolean));
    latestTaskEvent.forEach((event, taskId) => {
      if (event.type === 'task.started') {
        activeTaskIds.add(taskId);
        if (event.agent_id) activeAgentIds.add(event.agent_id);
      }
    });
    if (state.taskBusy && state.lastHudTask?.taskId) {
      activeTaskIds.add(state.lastHudTask.taskId);
      if (state.lastHudTask.agentId) activeAgentIds.add(state.lastHudTask.agentId);
    }
    if (state.taskBusy && !state.lastHudTask?.taskId && state.lastHudTask?.agentId) activeAgentIds.add(state.lastHudTask.agentId);
    // Active operations are determined by unmatched action.started/terminal pairs.
    const activeActions = new Map();
    for (const event of chronologicalLiveEvents) {
      const payload = event.payload || {};
      if (!payload.action_id) continue;
      if (event.type === 'agent.action.started') {
        const startedAt = Date.parse(event.timestamp || '');
        // A crashed/disconnected action must not leave a station permanently lit.
        if (Number.isFinite(startedAt) && Date.now() - startedAt > 120000) activeActions.delete(payload.action_id);
        else activeActions.set(payload.action_id, event);
      } else if (['agent.action.succeeded', 'agent.action.failed', 'agent.action.cancelled'].includes(event.type)) {
        activeActions.delete(payload.action_id);
      }
    }
    activeActions.forEach(event => { if (event.agent_id) activeAgentIds.add(event.agent_id); });
    // Expand from actual handoff or child-task lineage only; ignore completed children.
    const childResultIds = new Set(liveEvents.filter(event => event.type === 'agent.task.result' && event.task_id).map(event => event.task_id));
    for (let pass = 0; pass < 6; pass++) {
      let changed = false;
      for (const event of chronologicalLiveEvents) {
        if (!['task.started', 'agent.task.delegated'].includes(event.type)) continue;
        const parentId = event.payload?.parent_task_id;
        const childId = event.task_id;
        const childLast = latestTaskEvent.get(childId);
        if (!parentId || !childId || !activeTaskIds.has(parentId) || terminalTaskTypes.has(childLast?.type) || childResultIds.has(childId)) continue;
        if (!activeTaskIds.has(childId)) {
          activeTaskIds.add(childId);
          if (event.agent_id) activeAgentIds.add(event.agent_id);
          if (event.to_agent_id) activeAgentIds.add(event.to_agent_id);
          changed = true;
        }
      }
      if (!changed) break;
    }
    const missionRunning = activeTaskIds.size > 0 || state.taskBusy;
    const liveTaskById = new Map([...latestTaskEvent.entries()].filter(([taskId, item]) => item.type === 'task.started' && activeTaskIds.has(taskId) && item.agent_id).map(([taskId, item]) => [taskId, item]));
    const liveHandoffsFromTasks = chronologicalLiveEvents
      .filter(item => item.type === 'task.started' && item.payload?.parent_task_id && activeTaskIds.has(item.task_id) && activeTaskIds.has(item.payload.parent_task_id) && liveTaskById.has(item.payload.parent_task_id))
      .map(item => ({ fromAgentId: liveTaskById.get(item.payload.parent_task_id).agent_id, toAgentId: item.agent_id, eventId: `${item.payload.parent_task_id}:${item.task_id}`, live: true }));
    const liveHandoffsFromMessages = chronologicalLiveEvents
      .filter(item => item.type === 'agent.task.delegated' && item.from_agent_id && item.to_agent_id && activeTaskIds.has(item.payload?.parent_task_id || '') && activeTaskIds.has(item.task_id) && !childResultIds.has(item.task_id))
      .map(item => ({ fromAgentId: item.from_agent_id, toAgentId: item.to_agent_id, eventId: `${item.payload?.parent_task_id || 'handoff'}:${item.task_id || item.event_id}`, live: true }));
    const nowMs = Date.now();
    const liveResultReturns = chronologicalLiveEvents
      .filter(item => item.type === 'agent.task.result' && item.from_agent_id && item.to_agent_id && activeTaskIds.has(item.payload?.parent_task_id || '') && nowMs - new Date(item.timestamp || 0).getTime() < 5000)
      .map(item => ({ fromAgentId: item.from_agent_id, toAgentId: item.to_agent_id, eventId: `${item.payload?.parent_task_id}:${item.task_id}:return`, live: true }));
    const liveAgentMessages = chronologicalLiveEvents
      .filter(item => item.type.startsWith('agent.message.') && item.from_agent_id && item.to_agent_id && activeTaskIds.has(item.task_id))
      .map(item => ({ fromAgentId: item.from_agent_id, toAgentId: item.to_agent_id, eventId: `message:${item.event_id}`, live: true }));
    const liveHandoffs = [...liveHandoffsFromTasks, ...liveHandoffsFromMessages, ...liveResultReturns, ...liveAgentMessages];
    const handoffMap = new Map([...persistedHandoffs, ...liveHandoffs].filter(item => item.fromAgentId !== item.toAgentId && positions[item.fromAgentId] && positions[item.toAgentId]).map(item => [item.eventId, item]));
    const handoffs = [...handoffMap.values()].slice(-10);
    const collaboratingIds = new Set(handoffs.filter(item => item.live).flatMap(item => [item.fromAgentId, item.toAgentId]));
    const activeActionAgentIds = new Set([...activeActions.values()].map(event => event.agent_id).filter(Boolean));
    const visibleMission = currentHudMission();
    const nodes = agents.map((agent, index) => {
      const [x, y] = positions[agent.agent_id] || [[30, 12], [70, 12], [14, 30], [86, 30], [8, 48], [92, 48], [18, 68], [82, 68], [34, 84], [66, 84]][index % 10];
      const status = hudStatus(agent);
      const ready = ['ready', 'busy'].includes(status);
      const name = escapeHTML(agent.name || agent.agent_id);
      const id = escapeHTML(agent.agent_id || 'unknown.agent');
      const symbol = escapeHTML(symbols[agent.name] || '◇');
      const color = colors[agent.agent_id] || '#45eaff';
      const isSelected = agent.agent_id === state.selectedAgentId;
      const actionEvent = [...activeActions.values()].reverse().find(event => event.agent_id === agent.agent_id);
      const actionWorking = Boolean(actionEvent);
      const activePath = missionRunning && (activeAgentIds.has(agent.agent_id) || actionWorking);
      return { agent, x, y, status, ready, name, id, symbol, color, isSelected, activePath, actionWorking, actionName: actionEvent?.payload?.action_name || '' };
    });
    if (linkHost) {
      linkHost.innerHTML = nodes.map(node => {
        const px = node.x * 10, py = node.y * 8;
        const curveX = (px + center[0]) / 2;
        const curveY = (py + center[1]) / 2 - (py < center[1] ? 20 : -18);
        return `<path class="mk-link ${node.activePath ? 'selected' : ''}" d="M ${px} ${py} Q ${curveX} ${curveY} ${center[0]} ${center[1]}" data-link-agent="${node.id}"></path>`;
      }).join('') + handoffs.map(({ fromAgentId, toAgentId, eventId, live }) => {
        const [px, py] = positions[fromAgentId];
        const [cx, cy] = positions[toAgentId];
        const mx = (px * 10 + cx * 10) / 2;
        const my = (py * 8 + cy * 8) / 2 - 18;
        return `<path class="mk-handoff-link ${live ? 'is-live-handoff' : ''}" d="M ${px * 10} ${py * 8} Q ${mx} ${my} ${cx * 10} ${cy * 8}" data-handoff="${escapeHTML(eventId)}" aria-label="${live ? 'Live runtime delegation event' : 'Recorded task handoff'}"></path>`;
      }).join('');
    }
    host.innerHTML = nodes.map(node => {
      const statusClass = node.actionWorking || node.status === 'busy' ? 'is-busy' : node.status === 'registered' ? 'is-registered' : node.ready ? 'is-ready' : 'is-registered';
      const collaborationClass = collaboratingIds.has(node.agent.agent_id) ? 'is-collaborating' : '';
      const visibleStatus = node.actionWorking ? 'WORKING' : node.status.toUpperCase();
      const title = `${node.name} · ${visibleStatus}${node.actionName ? ` · ${node.actionName}` : ''}${collaboratingIds.has(node.agent.agent_id) ? ' · observed live communication' : ''}`;
      const identity = identityFor(node.agent);
      return `<button type="button" class="mk-agent-node ${statusClass} ${collaborationClass} ${node.isSelected ? 'selected' : ''} ${node.activePath ? 'is-selected-path' : ''}" style="--nx:${node.x};--ny:${node.y};--node-color:${node.color}" data-agent-id="${node.id}" aria-label="Select ${node.name}, status ${visibleStatus}" aria-pressed="${node.isSelected ? 'true' : 'false'}" title="${escapeHTML(title)}"><span class="mk-node-orb"><img class="mk-node-portrait" src="/assets/agents/${identity.slug}.svg" alt="" loading="lazy" decoding="async"><span class="mk-node-symbol">${node.symbol}</span></span><span class="mk-node-name">${escapeHTML(identity.short || node.name)}</span><span class="mk-node-state">${escapeHTML(visibleStatus)}</span></button>`;
    }).join('');
    const ready = agents.filter(agent => ['ready', 'busy'].includes(hudStatus(agent))).length;
    const currentStatus = String(visibleMission?.status || '').toLowerCase();
    const missionLabel = missionRunning ? 'MISSION RUNNING' : currentStatus === 'succeeded' ? 'LAST MISSION COMPLETE' : ['failed','timed_out','cancelled'].includes(currentStatus) ? 'LAST MISSION FAILED' : 'STANDING BY';
    if ($('#network-state-label')) $('#network-state-label').textContent = missionRunning ? `${activeTaskIds.size || 1} ACTIVE TASK${(activeTaskIds.size || 1) === 1 ? '' : 'S'} / LIVE ROUTE` : handoffs.some(item => item.live) ? `${ready} EXECUTABLE / LIVE HANDOFF TRACE` : handoffs.length ? `${ready} EXECUTABLE / ${handoffs.length} RECORDED HANDOFFS` : `${ready} EXECUTABLE / NEURAL LINKS STABLE`;
    if ($('#network-agent-summary')) $('#network-agent-summary').textContent = `${ready} READY · ${agents.length} IDENTITIES`;
    if ($('#network-agent-summary-footer')) $('#network-agent-summary-footer').textContent = `${ready} EXECUTABLE · ${agents.length} IDENTITIES`;
    if ($('#network-mission-state')) $('#network-mission-state').textContent = missionLabel;
    if ($('#hud-global-agent-count')) $('#hud-global-agent-count').textContent = `${agents.length} / ${agents.length}`;
    if ($('#hud-active-missions')) $('#hud-active-missions').textContent = String(activeTaskIds.size || (state.taskBusy ? 1 : 0));
    if ($('#hud-network-health')) $('#hud-network-health').textContent = ready === agents.length ? 'NOMINAL' : `${ready}/${agents.length} READY`;
    if ($('#hud-network-link')) $('#hud-network-link').textContent = missionRunning ? 'ACTIVE ROUTE' : 'CONNECTED';
    if ($('#hud-sys-status')) $('#hud-sys-status').textContent = ready ? 'SYSTEM ONLINE' : 'SYSTEM DEGRADED';
    if ($('#hud-system-subtitle')) $('#hud-system-subtitle').textContent = missionRunning ? 'MISSION SIGNAL DETECTED' : 'LOCAL RUNTIME CONNECTED';
    renderAgentDock();
  }

  function renderHudFocusPanel() {
    const agent = state.agents.find(item => item.agent_id === state.selectedAgentId) || state.agents.find(item => item.agent_id === 'coding.forge') || state.agents[0];
    if (!agent) return;
    const status = hudStatus(agent);
    const canRun = ['ready', 'busy'].includes(status);
    const symbol = symbols[agent.name] || '◇';
    const setText = (selector, value) => { const el = $(selector); if (el) el.textContent = value; };
    const identity = identityFor(agent);
    setText('#hud-focus-name', identity.short || agent.name || agent.agent_id);
    setText('#hud-focus-role', String(identity.specialty || agent.role || agent.agent_id || 'SPECIALIST').toUpperCase());
    setText('#hud-focus-status', status === 'ready' ? '● ONLINE' : status === 'busy' ? '◉ BUSY' : '● REGISTERED');
    setText('#hud-focus-state-head', status.toUpperCase());
    setText('#hud-focus-symbol', symbol);
    setText('#hud-direct-route', `ROUTE · ${String(agent.agent_id || '').toUpperCase()}`);
    const box = $('#hud-focus-description');
    if (box) box.textContent = agent.description || 'Registered specialist identity. No description is available from the runtime.';
    const prompt = $('#hud-direct-prompt');
    if (prompt) prompt.placeholder = canRun ? `Send a task directly to ${agent.name}…` : `${agent.name} is registered only. Ask JARVIS to coordinate…`;
    const send = $('#hud-direct-send');
    if (send) { send.setAttribute('aria-label', `Send task to ${agent.name}`); send.title = canRun ? `Send to ${agent.name}` : 'Route through JARVIS'; }
    $$('.mk-agent-tabs [data-focus-tab]').forEach(tab => tab.classList.toggle('active', tab.dataset.focusTab === (state.focusTab || 'chat')));
    const feed = $('#hud-focus-feed');
    if (!feed) return;
    const tab = state.focusTab || 'chat';
    if (tab === 'tools') {
      feed.innerHTML = `<div class="mk-chat-system"><i class="mk-led"></i> DECLARED CAPABILITIES · ${(agent.capabilities || []).length}</div><div class="hud-capabilities mk-capability-cloud">${(agent.capabilities || []).map(cap => `<span>${escapeHTML(cap)}</span>`).join('') || '<span>No capabilities declared by runtime.</span>'}</div>`;
      return;
    }
    if (tab === 'logs') {
      const liveItems = (state.liveEvents || []).filter(item => item.agent_id === agent.agent_id || item.from_agent_id === agent.agent_id || item.to_agent_id === agent.agent_id).sort(compareRuntimeEventsNewestFirst).slice(0, 8);
      const resultItems = [...(state.overview?.recent_results || [])].filter(item => !item.agent_id || item.agent_id === agent.agent_id).reverse().slice(0, 5);
      const eventMarkup = liveItems.map(item => `<div class="mk-chat-bubble ${item.type === 'agent.task.delegated' ? 'is-handoff-event' : ''}"><strong>${escapeHTML(String(item.type || 'RUNTIME EVENT').replace(/[._-]+/g, ' ').toUpperCase())} · ${escapeHTML(item.agent_id || agent.agent_id)}</strong><p>${escapeHTML(item.to_agent_id ? `${item.from_agent_id} → ${item.to_agent_id}` : (item.payload?.parent_task_id ? `Parent task ${item.payload.parent_task_id}` : 'Lifecycle event recorded by runtime.'))}</p><small>${escapeHTML(fmtDate(item.timestamp))} · ${escapeHTML(String(item.task_id || '').slice(0, 14))}</small></div>`).join('');
      const resultMarkup = resultItems.map(item => `<div class="mk-chat-bubble"><strong>${escapeHTML(String(item.status || 'RESULT').toUpperCase())} · ${escapeHTML(item.agent_id || agent.agent_id)}</strong><p>${escapeHTML(item.prompt || item.summary || 'Runtime task completed.')}</p><small>${escapeHTML(fmtDate(item.finished_at || item.started_at))}</small></div>`).join('');
      feed.innerHTML = `<div class="mk-chat-system"><i class="mk-led pulse"></i> LIVE + PERSISTED EVENT TRACE</div>${eventMarkup}${resultMarkup || (!eventMarkup ? '<div class="mk-chat-bubble"><strong>NO SESSION EVENTS</strong><p>No runtime events or returned task results for this specialist yet.</p></div>' : '')}`;
      return;
    }
    const last = state.lastHudTask && (state.lastHudTask.agentId === agent.agent_id || agent.agent_id === 'voice.jarvis' && state.lastHudTask.agentId === 'voice.jarvis') ? state.lastHudTask : null;
    if (last) {
      const promptText = escapeHTML(last.prompt || 'Mission request');
      const output = String(last.output || '');
      feed.innerHTML = `<div class="mk-chat-system"><i class="mk-led ${state.taskBusy ? 'pulse' : ''}"></i> ${state.taskBusy ? 'EXECUTION ACTIVE · WAITING FOR RUNTIME' : `LAST RESULT · ${escapeHTML(String(last.status || 'UNKNOWN').toUpperCase())}`}</div><div class="mk-chat-bubble operator-bubble"><strong>OPERATOR</strong><p>${promptText}</p></div><div class="mk-chat-bubble agent-bubble result-agent-bubble"><strong>${escapeHTML(agent.name || agent.agent_id)}</strong>${state.taskBusy ? '<p>Task is still executing in the local runtime. This display reflects the request lifecycle; it does not invent intermediate tool events.</p>' : `<div class="hud-result-mount">${renderTaskResult(output, { compact: true })}</div>`}${!state.taskBusy && last.taskId ? `<small>${escapeHTML(last.taskId)}</small>` : ''}</div>`;
    } else {
      const agentEvents = (state.liveEvents || []).filter(item => item.agent_id === agent.agent_id || item.from_agent_id === agent.agent_id || item.to_agent_id === agent.agent_id).sort(compareRuntimeEventsNewestFirst).slice(0, 3);
      const activityMarkup = agentEvents.map(item => `<div class="mk-chat-bubble"><strong>${escapeHTML(String(item.type || 'RUNTIME EVENT').replace(/[._-]+/g, ' ').toUpperCase())}</strong><p>${escapeHTML(item.to_agent_id ? `${item.from_agent_id} delegated to ${item.to_agent_id}.` : item.payload?.parent_task_id ? `Child task linked to parent ${item.payload.parent_task_id}.` : 'Observed lifecycle event from the local runtime.')}</p><small>${escapeHTML(fmtDate(item.timestamp))}</small></div>`).join('');
      feed.innerHTML = `<div class="mk-chat-system"><i class="mk-led ${canRun ? 'pulse' : ''}"></i> ${canRun ? 'SPECIALIST CHANNEL READY' : 'IDENTITY REGISTERED · NO HANDLER'}</div><div class="mk-chat-bubble agent-bubble"><strong>${escapeHTML(agent.name || agent.agent_id)}</strong><p>${escapeHTML(agent.description || 'Specialist identity connected to the JARVIS network.')}</p></div>${activityMarkup}<div class="mk-chat-bubble"><strong>CAPABILITIES</strong><p>${escapeHTML((agent.capabilities || []).slice(0, 3).join(' · ') || 'No capabilities declared.')}</p></div>`;
    }
  }

  function renderMissionWidget() {
    const last = currentHudMission();
    const running = Boolean(state.taskBusy || (state.activeMissions || []).some(item => ['queued', 'running'].includes(String(item.status || '').toLowerCase())));
    const status = String(last?.status || '').toLowerCase();
    const succeeded = !running && status === 'succeeded';
    const failed = !running && ['failed', 'timed_out', 'cancelled'].includes(status);
    const missionTaskIds = new Set(last?.taskId ? [last.taskId] : []);
    const allEvents = state.liveEvents || [];
    // Resolve nested descendants from observed delegation/task lineage so mission
    // details include grandchildren without mixing in unrelated concurrent tasks.
    for (let pass = 0; pass < 8; pass++) {
      let changed = false;
      for (const event of allEvents) {
        const parentId = event.payload?.parent_task_id;
        if (parentId && missionTaskIds.has(parentId) && event.task_id && !missionTaskIds.has(event.task_id)
            && (event.type === 'task.started' || event.type === 'agent.task.delegated' || event.type === 'agent.task.result' || event.type.startsWith('agent.action.'))) {
          missionTaskIds.add(event.task_id); changed = true;
        }
      }
      if (!changed) break;
    }
    const missionEvents = last?.taskId ? allEvents.filter(event => missionTaskIds.has(event.task_id) || missionTaskIds.has(event.payload?.parent_task_id)).sort(compareRuntimeEvents) : [];
    const rootStarted = missionEvents.some(event => event.type === 'task.started');
    const observedHandoffs = missionEvents.filter(event => event.type === 'agent.task.delegated').length;
    const stage = !running ? (succeeded || failed ? 2 : last ? 0 : -1) : rootStarted ? 1 : 0;
    const setText = (selector, value) => { const el = $(selector); if (el) el.textContent = value; };
    const completedTitle = succeeded ? 'LAST MISSION COMPLETE' : status === 'timed_out' ? 'LAST MISSION TIMED OUT' : status === 'cancelled' ? 'LAST MISSION CANCELLED' : failed ? 'LAST MISSION FAILED' : last ? 'LAST MISSION RECORDED' : 'MISSION READY';
    setText('#hud-mission-title', running ? (stage === 0 ? 'MISSION QUEUED' : stage === 2 ? 'MISSION COORDINATING' : 'MISSION EXECUTING') : completedTitle);
    let subtitle = 'Awaiting a command from the operator.';
    if (running) subtitle = last?.prompt || 'Runtime is executing the submitted task.';
    else if (last?.prompt) subtitle = last.prompt;
    else if (last?.auditOnly) subtitle = `Latest recorded outcome · ${String(last.agentId || 'voice.jarvis')} · prompt/output are not retained in audit metadata.`;
    else if (succeeded) subtitle = `Last task completed successfully · ${String(last.agentId || 'voice.jarvis')}.`;
    else if (failed) subtitle = `Last task ended ${status} · inspect the Task Center for available details.`;
    else if (last) subtitle = `Latest recorded task · ${String(last.agentId || 'voice.jarvis')} · ${String(last.taskId || '').slice(0, 18)}.`;
    setText('#hud-mission-subtitle', subtitle);
    setText('#hud-mission-chip', running ? (stage === 0 ? 'QUEUED' : 'EXECUTING') : succeeded ? 'SUCCEEDED' : failed ? status.toUpperCase() : last ? 'RECORDED' : 'STANDBY');
    const chip = $('#hud-mission-chip'); if (chip) chip.className = `mk-mission-chip${running ? ' running' : succeeded ? ' done' : failed ? ' running' : ''}`;
    const bar = $('#hud-mission-progress-bar');
    if (bar) {
      bar.classList.toggle('indeterminate', running);
      bar.style.width = running ? (stage === 0 ? '18%' : observedHandoffs ? '68%' : '42%') : (succeeded || failed ? '100%' : last ? '0%' : '0%');
    }
    const runningMessage = stage === 0 ? 'QUEUED · WAITING FOR RUNTIME' : observedHandoffs ? `COORDINATING · ${observedHandoffs} OBSERVED HANDOFF${observedHandoffs === 1 ? '' : 'S'}` : 'EXECUTING · RUNTIME TASK STARTED';
    setText('#hud-mission-progress-text', running ? runningMessage : succeeded ? 'RESULT RETURNED · SUCCEEDED' : status === 'timed_out' ? 'RESULT RETURNED · TIMED OUT' : status === 'cancelled' ? 'RESULT RETURNED · CANCELLED' : failed ? 'RESULT RETURNED · REVIEW ERROR' : last ? 'NO ACTIVE MISSION · LAST RESULT SHOWN' : '0% · NO ACTIVE MISSION');
    setText('#hud-mission-agent', last ? String(last.agentId || 'voice.jarvis').toUpperCase() : 'JARVIS / AUTO');
    const steps = $$('.mk-mission-step');
    steps.forEach((step, index) => {
      const done = succeeded || failed || (running && index < stage) || (!running && last && index === 0);
      const active = running && index === stage;
      step.classList.toggle('done', Boolean(done));
      step.classList.toggle('active', Boolean(active));
      const indicator = $('b', step); if (indicator) indicator.textContent = done ? '✓' : active ? '◉' : '○';
    });
    const latestEvent = missionEvents[missionEvents.length - 1];
    const latestAction = [...missionEvents].reverse().find(event => String(event.type || '').startsWith('agent.action.'));
    const latestMessage = [...missionEvents].reverse().find(event => ['agent.task.delegated', 'agent.task.result'].includes(event.type) || String(event.type || '').startsWith('agent.message.'));
    let latestCopy = '';
    if (latestAction) {
      const payload = latestAction.payload || {};
      const agent = state.agents.find(item => item.agent_id === latestAction.agent_id);
      const phase = String(latestAction.type).split('.').pop().toUpperCase();
      latestCopy = `${agent?.name || latestAction.agent_id} · ${payload.action_name || 'agent action'} · ${phase}${payload.duration_ms !== undefined ? ` · ${payload.duration_ms} ms` : ''}.`;
    } else if (latestMessage) {
      latestCopy = `${latestMessage.from_agent_id || latestMessage.agent_id} → ${latestMessage.to_agent_id || 'parent agent'} · ${String(latestMessage.type).replace(/[._-]+/g, ' ')}.`;
    } else if (latestEvent) {
      latestCopy = `Latest runtime event: ${String(latestEvent.type).replace(/[._-]+/g, ' ')}.`;
    }
    if (!latestCopy && last?.auditOnly) latestCopy = 'Final status is confirmed by persisted audit metadata; prompt and output are not retained in that log.';
    if (!latestCopy && succeeded) latestCopy = 'Runtime confirmed successful completion.';
    if (!latestCopy && failed) latestCopy = `Runtime recorded terminal status: ${status}.`;
    if (!latestCopy) latestCopy = 'Waiting for the runtime event stream…';
    const outputText = last?.output || (last?.auditOnly ? 'Audit history confirms the final status. Prompt and output are not stored in the audit log.' : failed ? 'Runtime returned an unsuccessful status. Inspect task results for the error.' : succeeded ? 'The runtime confirmed success, but no output text is available in this session.' : 'Runtime returned no output text.');
    setText('#hud-mission-latest', running ? `${latestCopy} ${last?.prompt || ''}`.trim() : last ? summarizeTaskResult(last.output || outputText) : 'Mission outputs will appear here when a task completes.');
    const resultButton = $('#hud-open-result');
    if (resultButton) resultButton.textContent = !running && last?.output ? 'VIEW FORMATTED RESULT ↗' : 'EXPAND ↗';
    const cinema = $('#hud-mission-cinematic');
    if (cinema) {
      const model = missionCinematicModel(last);
      cinema.dataset.missionState = model.missionState;
      const setCinemaText = (selector, value) => { const el = $(selector, cinema); if (el) el.textContent = value; };
      const phaseText = String(model.phase).padStart(2, '0');
      setCinemaText('#hud-cinema-status', model.liveLine);
      setCinemaText('#hud-cinema-title', model.title);
      setCinemaText('#hud-cinema-subtitle', model.subtitle);
      setCinemaText('#hud-cinema-phase', phaseText);
      const cinemaTrace = $('#hud-cinema-footer', cinema);
      if (cinemaTrace) cinemaTrace.textContent = model.liveLine;
      const cinemaBar = $('#hud-cinema-progress-fill', cinema);
      if (cinemaBar) {
        cinemaBar.style.width = `${model.phase * 25}%`;
        cinemaBar.classList.toggle('indeterminate', model.running);
      }
      const progressTrack = $('.mission-cinema-progress', cinema);
      if (progressTrack) {
        progressTrack.setAttribute('aria-valuenow', String(model.phase));
        progressTrack.setAttribute('aria-valuetext', `Phase ${model.phase} of 4`);
      }
      const traceId = $('.mission-cinema-footer span:last-child', cinema);
      if (traceId) traceId.innerHTML = `<i></i>${escapeHTML(last?.taskId ? `TRACE · ${String(last.taskId).slice(0, 13).toUpperCase()}` : 'TRACE · AWAITING TASK ID')}`;
      $$('.mission-cinema-stage', cinema).forEach(step => {
        const index = Number(step.dataset.missionCinematicStep);
        const hasCommand = Boolean(model.last?.taskId || model.last?.prompt);
        const terminal = !model.running && (model.succeeded || model.failed);
        const done = index === 1 ? hasCommand
          : index === 2 ? Boolean(model.rootStarted)
          : index === 3 ? model.handoffs > 0
          : index === 4 ? Boolean(model.succeeded) : false;
        const active = model.running && index === model.phase;
        const isFailed = model.failed && index === 4;
        const skipped = index === 3 && terminal && model.handoffs === 0;
        step.classList.toggle('is-done', Boolean(done && !skipped));
        step.classList.toggle('is-active', Boolean(active));
        step.classList.toggle('is-failed', Boolean(isFailed));
        step.classList.toggle('is-skipped', Boolean(skipped));
        const indicator = $('b', step);
        if (indicator) indicator.textContent = isFailed ? '!' : skipped ? '—' : done ? '✓' : active ? '◉' : '○';
        if (terminal && skipped) step.title = 'No handoff was observed for this mission; coordination was not required or not recorded.';
        else if (terminal && model.failed && index === 4) step.title = `Mission ended with status ${model.status}`;
      });
    }
    document.body.classList.toggle('mission-running', running);
  }

  function renderAgentMosaic() {
    const agents = state.agents;
    renderLivingNetwork();
    renderAgentDock();
    if ($('#agent-mosaic')) $('#agent-mosaic').innerHTML = agents.length ? agents.map(a => renderAgentTile(a, true)).join('') : '<div class="empty-state">No agent identities were returned.</div>';
    renderHudFocusPanel(); renderMissionWidget();
    $('#nav-agent-count').textContent = agents.length;
    $('#filter-all-count').textContent = agents.length;
    const ready = agents.filter(a => ['ready','busy'].includes(hudStatus(a))).length;
    const registered = agents.filter(a => hudStatus(a) === 'registered').length;
    $('#filter-ready-count').textContent = ready;
    $('#filter-registered-count').textContent = registered;
    $('#agent-status-summary').textContent = `${ready} READY · ${registered} REGISTERED ONLY`;
    const select = $('#agent-target');
    const previous = select.value;
    select.innerHTML = '<option value="voice.jarvis">JARVIS · Auto-coordinate</option>' + agents.filter(a => ['ready','busy'].includes(hudStatus(a)) && a.agent_id !== 'voice.jarvis').map(a => `<option value="${escapeHTML(a.agent_id)}">${escapeHTML(a.name)} · Direct</option>`).join('');
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
  }
  function renderAgentGrid() {
    const q = ($('#agent-search').value || '').trim().toLowerCase();
    const agents = state.agents.filter(a => {
      const status = hudStatus(a);
      if (state.agentFilter === 'ready' && !['ready','busy'].includes(status)) return false;
      if (state.agentFilter === 'registered' && status !== 'registered') return false;
      const haystack = [a.name, a.agent_id, a.role, a.description, ...(a.capabilities || []), ...(a.tags || [])].join(' ').toLowerCase();
      return !q || haystack.includes(q);
    });
    $('#agent-grid').innerHTML = agents.length ? agents.map(a => renderAgentTile(a, false)).join('') : '<div class="empty-state large-empty">No agents match this filter.</div>';
  }
  function showAgent(agentId) {
    const agent = state.agents.find(a => a.agent_id === agentId);
    if (!agent) return;
    if (document.body.classList.contains('hud-active') || state.currentView === 'overview') {
      state.selectedAgentId = agentId;
      state.focusTab = 'chat';
      renderLivingNetwork();
      renderHudFocusPanel();
      openHudPanel('agent', agentId);
      return;
    }
    const status = hudStatus(agent);
    const identity = identityFor(agent);
    $('#agent-modal-content').innerHTML = `<div class="modal-agent-top"><img class="modal-agent-portrait" src="/assets/agents/${identity.slug}.svg" alt=""><span class="agent-symbol">${escapeHTML(symbols[agent.name] || '◈')}</span><div><h2 id="modal-agent-name">${escapeHTML(identity.short || agent.name)}</h2><p>${escapeHTML(identity.specialty)} · ${escapeHTML(status.toUpperCase())}</p><p>${escapeHTML(identity.persona)}</p></div></div><div class="modal-section"><h3>ROLE & PURPOSE</h3><p>${escapeHTML(agent.description || agent.role || 'No description provided.')}</p></div><div class="modal-section"><h3>CAPABILITIES (${(agent.capabilities || []).length})</h3><div class="modal-capabilities">${(agent.capabilities || []).map(c => `<span class="capability-chip">${escapeHTML(c)}</span>`).join('')}</div></div><div class="modal-section"><h3>RUNTIME DETAILS</h3><p>State: <strong>${escapeHTML(status)}</strong><br>Enabled: <strong>${agent.enabled === false ? 'No' : 'Yes'}</strong><br>Max concurrency: <strong>${escapeHTML(agent.max_concurrency ?? '—')}</strong><br>Timeout: <strong>${escapeHTML(agent.timeout_seconds ?? '—')}s</strong><br>Tags: <strong>${escapeHTML((agent.tags || []).join(', ') || 'None')}</strong></p></div><div class="modal-section"><h3>ROUTING KEYWORDS</h3><p>${escapeHTML((agent.routing_keywords || []).join(' · ') || 'No routing keywords declared.')}</p></div>${['ready','busy'].includes(status) ? `<button class="run-button" id="target-agent-task" data-target="${escapeHTML(agent.agent_id)}">Run a task with ${escapeHTML(agent.name)} <span class="run-arrow">↗</span></button>` : '<p class="market-signal-copy">This identity is registered but has no executable handler in the current runtime.</p>'}`;
    $('#agent-modal').classList.remove('hidden');
    const runBtn = $('#target-agent-task');
    if (runBtn) runBtn.addEventListener('click', () => { $('#agent-target').value = agent.agent_id; $('#agent-modal').classList.add('hidden'); navigate('overview'); $('#task-prompt').focus(); toast(`Direct target set to ${agent.name}. Enter a task to continue.`); });
  }
  function renderMarketOverview(market) {
    const host = $('#market-overview');
    if (!market || !market.available) {
      host.innerHTML = '<span class="market-badge none">NO SAVED REPORT</span><div class="market-signal-title">No market report found</div><div class="market-signal-copy">Run a snapshot through Quant to generate a report. Live feed availability depends on your configured provider.</div>';
      $('#market-updated').textContent = 'No report metadata'; return;
    }
    const isNse = market.kind === 'nse_mcp';
    host.innerHTML = `<span class="market-badge ${isNse ? 'live' : ''}"><i class="status-dot ${isNse ? 'green-bg' : ''}"></i>${escapeHTML(isNse ? 'NSE MCP REPORT' : 'SAVED MARKET BRIEF')}</span><div class="market-signal-title">${escapeHTML(market.label || 'Market report')}</div><div class="market-signal-copy">${escapeHTML(market.market || 'India markets')} · ${escapeHTML(market.report_type || 'report type not specified')}. ${market.age_seconds === null ? 'Source time unavailable.' : `Report generated ${escapeHTML(fmtAge(market.age_seconds))}.`}</div>${market.indexes?.length ? `<div class="market-index-row">${market.indexes.slice(0,2).map(i => `<div class="market-index"><div class="market-index-name">${escapeHTML(i.name)}</div><div class="market-index-val">${escapeHTML(fmtNum(i.value))}</div><div class="market-index-change">${i.change === null || i.change === undefined ? 'CHANGE —' : `${Number(i.change) > 0 ? '+' : ''}${escapeHTML(fmtNum(Number(i.change), 2))}%`}</div></div>`).join('')}</div>` : ''}`;
    $('#market-updated').textContent = market.generated_at ? `REPORT TIME · ${fmtDate(market.generated_at)}` : 'Source timestamp not supplied';
  }
  function renderMarketDetail() {
    const market = state.overview?.market;
    if (!market || !market.available) {
      $('#market-detail').innerHTML = '<section class="panel"><div class="empty-state large-empty">No market report is saved yet. Use <strong>Request snapshot</strong> to ask the Quant agent for one. The report may require a working provider and network access.</div></section>';
      return;
    }
    const isNse = market.kind === 'nse_mcp';
    const indexes = market.indexes || [];
    const stocks = market.stocks || [];
    $('#market-detail').innerHTML = `<section class="market-hero-card"><div><span class="market-badge ${isNse ? 'live' : ''}"><i class="status-dot ${isNse ? 'green-bg' : ''}"></i>${escapeHTML(isNse ? 'NSE MCP SNAPSHOT' : 'SAVED REPORT · NOT VERIFIED LIVE')}</span><h2>${escapeHTML(market.label || 'Market report')}</h2><p>${escapeHTML(market.market || 'India markets')} · Report type: ${escapeHTML(market.report_type || 'not specified')}. A successful retrieval is not proof that every quote is current; source timestamps are authoritative.</p></div><div class="market-hero-meta"><span class="section-kicker">REPORT GENERATED</span><strong>${escapeHTML(fmtDate(market.generated_at))}</strong><small>${escapeHTML(market.age_seconds === null ? 'Timestamp unavailable' : fmtAge(market.age_seconds))}</small><small>${market.tool_count ? `${escapeHTML(market.tool_count)} tools discovered` : ''}</small></div></section><div class="market-detail-grid"><section class="market-data-card"><div class="panel-heading"><div><span class="section-kicker">INDEX / SNAPSHOT</span><h2>Market indicators</h2></div></div>${indexes.length ? indexes.map(i => `<div class="market-index-detail"><div><strong>${escapeHTML(i.name)}</strong><small>${i.source_time ? `Source time · ${escapeHTML(i.source_time)}` : 'Source quote time not supplied'}</small></div><div style="text-align:right"><div class="price">${escapeHTML(fmtNum(i.value))}</div><div class="change">${i.change === null || i.change === undefined ? 'CHANGE —' : `${Number(i.change) > 0 ? '+' : ''}${escapeHTML(fmtNum(Number(i.change), 2))}%`}</div></div></div>`).join('') : '<div class="empty-state">The saved report does not expose normalized index fields.</div>'}</section><section class="market-data-card"><div class="panel-heading"><div><span class="section-kicker">EQUITIES / SAMPLE</span><h2>Returned instruments</h2></div><span class="session-tag">${stocks.length} SHOWN</span></div>${stocks.length ? stocks.map(s => `<div class="market-index-detail"><div><strong>${escapeHTML(s.symbol)}</strong><small>${escapeHTML(s.name || '')}${s.source_time ? ` · ${escapeHTML(s.source_time)}` : ''}</small></div><div style="text-align:right"><div class="price">${escapeHTML(fmtNum(s.price))}</div><div class="change">${s.change === null || s.change === undefined ? 'CHANGE —' : `${Number(s.change) > 0 ? '+' : ''}${escapeHTML(fmtNum(Number(s.change), 2))}%`}</div></div></div>`).join('') : '<div class="empty-state">No normalized stock rows were found in this report. Open the JSON artifact for the full payload.</div>'}</section></div><section class="panel"><div class="panel-heading"><div><span class="section-kicker">SOURCE / PROVENANCE</span><h2>Report metadata</h2></div></div><div class="activity-list"><div class="activity-item"><span class="activity-icon">▤</span><div class="activity-copy"><strong>Report file</strong><small>${escapeHTML(market.report_path || 'data/reports/market_brief_latest.json')}</small></div></div><div class="activity-item"><span class="activity-icon">◷</span><div class="activity-copy"><strong>Latest session</strong><small>${escapeHTML(market.latest_session || 'Not supplied by source')}</small></div></div><div class="activity-item"><span class="activity-icon">⬡</span><div class="activity-copy"><strong>Trading safety</strong><small>Read-only market research · no order execution endpoints</small></div></div></div></section>`;
  }
  function renderActivity(history) {
    const host = $('#recent-activity');
    if (!host) return;

    const relevantTypes = type => type.startsWith('task.')
      || type.startsWith('dashboard.mission.')
      || type.startsWith('agent.action.')
      || type.startsWith('agent.message.')
      || type === 'agent.task.delegated'
      || type === 'agent.task.result';
    const eventKey = item => String(item.event_id || `${item.sequence || ''}:${item.type || ''}:${item.task_id || ''}:${item.timestamp || ''}`);
    const dedupedLive = new Map();
    for (const item of state.liveEvents || []) {
      const type = String(item.type || 'runtime.event');
      if (!relevantTypes(type)) continue;
      dedupedLive.set(eventKey(item), item);
    }
    const orderedLive = [...dedupedLive.values()].sort(compareRuntimeEventsNewestFirst);

    const activeTaskIds = new Set((state.activeMissions || []).map(item => item.task_id).filter(Boolean));
    if (state.taskBusy && state.lastHudTask?.taskId) activeTaskIds.add(state.lastHudTask.taskId);
    const resultById = new Map((state.overview?.recent_results || []).filter(item => item.task_id).map(item => [item.task_id, item]));
    const recentLive = orderedLive.slice(0, 7).map(item => {
      const type = String(item.type || 'runtime.event');
      const payload = item.payload || {};
      const from = item.from_agent_id || item.agent_id || 'voice.jarvis';
      const to = item.to_agent_id || '';
      const result = resultById.get(item.task_id);
      const current = state.lastHudTask?.taskId === item.task_id ? state.lastHudTask : null;
      let status = String(payload.status || '').toLowerCase();
      let context = '';
      let label = type.replace(/[._-]+/g, ' ');
      let sourceLabel = 'RUNTIME EVENT';

      if (type.startsWith('agent.action.')) {
        const suffix = type.slice('agent.action.'.length);
        status = status || suffix;
        const action = String(payload.action_name || payload.operation || payload.tool_name || 'agent operation');
        label = `${suffix.toUpperCase()} · ${String(payload.action_type || 'ACTION').toUpperCase()}`;
        const details = [payload.tool_name, payload.provider, payload.model_provider, payload.operation, payload.duration_ms !== undefined ? `${payload.duration_ms} ms` : null]
          .filter(Boolean).map(value => String(value).slice(0, 80));
        context = `${action}${details.length ? ` · ${details.join(' · ')}` : ''}`;
        sourceLabel = 'OBSERVED ACTION SPAN';
      } else if (type === 'agent.task.delegated') {
        status = 'delegated';
        label = 'TASK DELEGATED';
        context = `${from} → ${to || 'capability router'} · child task created`;
        sourceLabel = 'OBSERVED HANDOFF';
      } else if (type === 'agent.task.result') {
        status = status || 'returned';
        label = `TASK RESULT · ${status.toUpperCase()}`;
        context = `${from} → ${to || 'parent agent'} · result returned${payload.duration_ms !== undefined ? ` in ${payload.duration_ms} ms` : ''}`;
        sourceLabel = 'OBSERVED RETURN MESSAGE';
      } else if (type.startsWith('agent.message.')) {
        status = status || (type.endsWith('.received') ? 'received' : 'delivered');
        label = type.replace(/[._-]+/g, ' ').toUpperCase();
        context = `${from}${to ? ` → ${to}` : ''} · ${String(payload.source_message_type || payload.delivery || 'agent message')}`;
        sourceLabel = 'OBSERVED AGENT MESSAGE';
      } else if (type === 'dashboard.mission.queued') {
        status = 'queued'; label = 'MISSION ACCEPTED'; context = 'Mission accepted by the dashboard runtime.'; sourceLabel = 'DASHBOARD LIFECYCLE';
      } else if (type === 'dashboard.mission.running' || type === 'task.started') {
        status = 'running'; label = 'TASK STARTED'; context = `Runtime started execution for ${item.agent_id || from}.`; sourceLabel = 'RUNTIME LIFECYCLE';
      } else if (type === 'dashboard.mission.result') {
        status = status || current?.status || result?.status || 'completed';
        label = `MISSION ${status.toUpperCase()}`;
        context = `Mission returned from ${item.agent_id || from}.`; sourceLabel = 'DASHBOARD LIFECYCLE';
      } else if (type.startsWith('task.')) {
        status = status || type.split('.').pop();
        label = `TASK ${type.split('.').pop().replace(/_/g, ' ').toUpperCase()}`;
        context = status === 'succeeded' ? 'Task completed successfully.' : status === 'failed' ? 'Task failed; inspect task metadata for diagnostics.' : status === 'timed_out' ? 'Task exceeded its execution time limit.' : status === 'cancelled' ? 'Task execution was cancelled.' : `Runtime recorded ${status}.`;
        sourceLabel = 'RUNTIME LIFECYCLE';
      } else {
        status = status || 'event'; context = 'Runtime event recorded.';
      }
      return {
        key: eventKey(item), agent_id: item.agent_id || from, status, event: label, rawEvent: type,
        task_id: item.task_id, parent_task_id: payload.parent_task_id || null,
        delegation_depth: Number(payload.delegation_depth || (payload.parent_task_id ? 1 : 0)),
        timestamp: item.timestamp, prompt: context, active: activeTaskIds.has(item.task_id) || ['running', 'queued'].includes(status),
        from_agent_id: from, to_agent_id: to, sequence: Number(item.sequence || 0), source: 'live', sourceLabel,
      };
    });

    // Fall back to persisted task summaries only when the current runtime has no
    // actionable events. Do not pad an event trace with duplicate lifecycle rows.
    let items = recentLive;
    if (!items.length) {
      const seenTasks = new Set();
      items = (history?.recent_tasks || []).slice().reverse().filter(item => {
        if (!item.task_id || seenTasks.has(item.task_id)) return false;
        seenTasks.add(item.task_id); return true;
      }).slice(0, 7).map(item => {
        const result = resultById.get(item.task_id);
        const status = String(item.status || String(item.event || '').split('.').pop() || 'unknown').toLowerCase();
        let context = result?.summary || '';
        if (!context) {
          if (status === 'succeeded') context = 'Completed successfully · persistent audit stores lifecycle metadata, not prompt/output.';
          else if (status === 'failed') context = 'Failed · open Task Center to inspect recorded metadata.';
          else if (status === 'timed_out') context = 'Timed out · inspect Task Center for saved lifecycle metadata.';
          else if (status === 'cancelled') context = 'Cancelled · persistent lifecycle record.';
          else context = `Persisted task state: ${status}.`;
        }
        return { ...item, key: `audit:${item.task_id}:${status}`, agent_id: item.agent_id || 'voice.jarvis', status, event: `TASK ${status.toUpperCase()}`, rawEvent: item.event || `task.${status}`, prompt: context, active: false, source: 'audit', sourceLabel: 'PERSISTED AUDIT · METADATA ONLY' };
      });
    }

    if (!items.length) {
      const count = Number(history?.terminal_event_count || 0);
      host.innerHTML = `<div class="mk-activity-empty"><strong>NO NEW LIVE EVENTS</strong><p>The runtime event bus is connected. ${count ? `${escapeHTML(fmtNum(count, 0))} terminal outcomes exist in persisted history.` : 'Submit a mission to see actual tool spans, messages and task lifecycle events here.'}</p><button type="button" data-open-workspace="tasks">OPEN TASK HISTORY ↗</button></div>`;
      return;
    }
    host.innerHTML = items.map(item => {
      const status = String(item.status || 'unknown').toLowerCase();
      const agent = state.agents.find(entry => entry.agent_id === item.agent_id);
      const agentName = agent?.name || item.agent_id || 'Unknown agent';
      const rawEvent = String(item.rawEvent || item.event || `task.${status}`);
      const event = String(item.event || rawEvent.replace(/[._-]+/g, ' ').toUpperCase());
      const delegated = Boolean(item.parent_task_id) || rawEvent === 'agent.task.delegated' || rawEvent === 'agent.task.result';
      const depth = Number(item.delegation_depth || (delegated ? 1 : 0));
      const taskId = String(item.task_id || 'runtime event');
      const context = item.prompt || 'No additional detail is exposed for this event.';
      const eventTime = item.timestamp ? fmtDate(item.timestamp) : 'TIME UNKNOWN';
      const mark = item.active ? '<i class="mk-led pulse"></i>' : ['succeeded', 'received', 'returned'].includes(status) ? '✓' : ['failed','timed_out','cancelled'].includes(status) ? '!' : delegated ? '↗' : '◉';
      const sourceLabel = item.sourceLabel || (item.source === 'audit' ? 'PERSISTED AUDIT · METADATA ONLY' : item.active ? 'ACTIVE RUNTIME EVENT' : delegated ? `OBSERVED HANDOFF · DEPTH ${depth}` : 'RUNTIME EVENT');
      return `<div class="mk-activity-entry status-${escapeHTML(status.replace(/[^a-z-]/g, ''))} ${item.active ? 'is-active-event' : ''} ${delegated ? 'is-handoff-event' : ''}" data-event-key="${escapeHTML(item.key || '')}"><span class="mk-activity-mark">${mark}</span><div class="mk-activity-main"><div class="mk-activity-title"><strong>${escapeHTML(agentName)}</strong><span>${escapeHTML(event)}</span></div><div class="mk-activity-context" title="${escapeHTML(context)}">${escapeHTML(context)}</div><div class="mk-activity-tags"><small>${escapeHTML(sourceLabel)}</small><code title="${escapeHTML(taskId)}">${escapeHTML(taskId.slice(0, 12))}${taskId.length > 12 ? '…' : ''}</code></div></div><time>${escapeHTML(eventTime)}</time></div>`;
    }).join('');
  }

  async function pollRuntimeEvents() {
    if (state.eventPollInFlight) return;
    state.eventPollInFlight = true;
    try {
      const data = await api(`/api/events?after=${encodeURIComponent(state.eventCursor)}`);
      const rawIncoming = Array.isArray(data.events) ? data.events : [];
      const incoming = [];
      for (const item of rawIncoming) {
        const key = String(item.event_id || `${item.sequence || ''}:${item.type || ''}:${item.task_id || ''}:${item.timestamp || ''}`);
        if (state.seenEventIds.has(key)) continue;
        state.seenEventIds.add(key);
        incoming.push(item);
      }
      const previousActiveSignature = (state.activeMissions || []).map(item => `${item.task_id}:${item.status}`).sort().join('|');
      if (Array.isArray(data.active_missions)) state.activeMissions = data.active_missions;
      const activeSignatureChanged = previousActiveSignature !== (state.activeMissions || []).map(item => `${item.task_id}:${item.status}`).sort().join('|');
      const streamState = $('#runtime-stream-state');
      if (streamState) {
        streamState.textContent = data.connected ? 'RUNTIME EVENT BUS · LIVE' : 'RECONNECTING EVENT BUS';
        streamState.classList.toggle('is-connected', Boolean(data.connected));
      }
      // Advance the cursor even for duplicate-only polls; keep a bounded seen-ID set
      // so proxy retries or overlapping poll windows cannot replay the same event.
      state.eventCursor = Math.max(state.eventCursor, Number(data.cursor || 0));
      if (incoming.length) {
        state.liveEvents = [...state.liveEvents, ...incoming].slice(-800);
        if (state.seenEventIds.size > 1600) {
          state.seenEventIds = new Set(state.liveEvents.map(item => String(item.event_id || `${item.sequence || ''}:${item.type || ''}:${item.task_id || ''}:${item.timestamp || ''}`)));
        }
        renderLivingNetwork();
        renderActivity(state.overview?.task_history);
        renderMissionWidget();
        renderHudFocusPanel();
        const meaningful = incoming.some(item => ['task.started', 'task.succeeded', 'task.failed', 'task.timed_out', 'task.cancelled', 'dashboard.mission.result', 'agent.task.delegated', 'agent.task.result'].includes(item.type));
        if (meaningful) refreshOverview(false);
      } else if (Number(data.cursor || 0) > state.eventCursor) {
        state.eventCursor = Number(data.cursor || 0);
      }
      if (activeSignatureChanged && !incoming.length) {
        renderLivingNetwork(); renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
      }
      if (data.truncated && !incoming.length) state.eventCursor = Number(data.cursor || state.eventCursor);
    } catch (error) {
      const streamState = $('#runtime-stream-state');
      if (streamState) { streamState.textContent = 'EVENT FEED OFFLINE'; streamState.classList.remove('is-connected'); }
      // Runtime event transport can reconnect on the next poll; do not mark agents failed
      // because a single telemetry request failed.
    } finally {
      state.eventPollInFlight = false;
    }
  }

  function renderTasks() {
    const overview = state.overview;
    if (!overview) return;
    const history = overview.task_history || {};
    const counts = history.status_counts || {};
    $('#task-event-count').textContent = fmtNum(history.event_count || 0, 0);
    $('#task-succeeded-count').textContent = fmtNum(counts.succeeded || 0, 0);
    $('#task-failed-count').textContent = fmtNum((counts.failed || 0) + (counts.timed_out || 0) + (counts.cancelled || 0), 0);
    $('#task-malformed-count').textContent = fmtNum(history.malformed_lines || 0, 0);
    $('#task-log-path').textContent = history.path || 'data/task_history.jsonl';
    $('#nav-task-count').textContent = history.terminal_event_count ?? '—';
    const events = (history.recent_tasks || []).slice().reverse();
    $('#task-table-body').innerHTML = events.length ? events.map((item, i) => `<tr><td><span class="status-pill ${escapeHTML(item.status || 'unknown')}">${escapeHTML(item.status || 'unknown')}</span></td><td>${escapeHTML(item.agent_id || '—')}</td><td class="id-cell" title="${escapeHTML(item.task_id || '')}">${escapeHTML(String(item.task_id || '—').slice(0, 18))}${item.task_id ? '…' : ''}</td><td>${escapeHTML(fmtDate(item.timestamp))}</td><td><button class="detail-link" data-history-index="${i}">Inspect metadata ↗</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">No terminal task events recorded yet.</td></tr>';
    const sessions = state.overview.recent_results || [];
    $('#session-results').innerHTML = sessions.length ? sessions.slice().reverse().map((r, i) => `<article class="session-result"><div class="session-result-top"><div><strong>${escapeHTML(r.prompt || 'Task result')}</strong><div class="agent-id">${escapeHTML(r.agent_id)} · ${escapeHTML(r.task_id)}</div></div><span class="status-pill ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></div><div class="session-result-body">${renderTaskResult(r.output || r.error || r.summary || 'No output')}</div></article>`).join('') : '<div class="empty-state">Run a task to see its full result here.</div>';
    $('#task-table-body').onclick = e => {
      const btn = e.target.closest('[data-history-index]'); if (!btn) return;
      const item = events[Number(btn.dataset.historyIndex)];
      showTaskMetadata(item);
    };
  }
  function showTaskMetadata(item) {
    $('#task-result').classList.remove('hidden');
    $('#result-title').textContent = 'Persisted task metadata';
    $('#result-meta').textContent = `${item.agent_id || 'unknown'} · ${item.status || 'unknown'} · ${item.task_id || 'no task id'} · ${item.timestamp || ''}`;
    $('#result-output').textContent = JSON.stringify(item, null, 2);
    $('#task-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  async function loadReports() {
    try {
      const data = await api('/api/reports'); const reports = data.reports || [];
      $('#report-list').innerHTML = reports.length ? reports.map(r => `<div class="report-item ${state.selectedReport === r.name ? 'active' : ''}" tabindex="0" role="button" data-report="${escapeHTML(r.name)}"><span class="file-icon">${escapeHTML(r.kind.toUpperCase())}</span><div class="report-copy"><strong>${escapeHTML(r.name)}</strong><small>${escapeHTML(fmtDate(r.modified_at))}</small></div><span class="report-size">${escapeHTML(r.size < 1024 ? `${r.size} B` : `${(r.size / 1024).toFixed(1)} KB`)}</span></div>`).join('') : '<div class="empty-state">No reports found in data/reports.</div>';
      if (state.selectedReport && reports.some(r => r.name === state.selectedReport)) await openReport(state.selectedReport, false);
    } catch (error) { $('#report-list').innerHTML = `<div class="empty-state">${escapeHTML(error.message)}</div>`; }
  }
  async function openReport(name, updateSelection = true) {
    if (updateSelection) state.selectedReport = name;
    $('#viewer-title').textContent = name;
    $('#viewer-content').textContent = 'Loading report…';
    $('#viewer-content').classList.remove('is-markdown');
    $('#copy-report').disabled = true;
    try {
      const response = await fetch(`/api/report/${encodeURIComponent(name)}`, { cache: 'no-store' });
      const text = await response.text();
      if (!response.ok) throw new Error('Could not load this report.');
      state.reportText = text;
      const viewer = $('#viewer-content');
      if (name.toLowerCase().endsWith('.md') && window.JARVISResultFormatter) {
        viewer.innerHTML = window.JARVISResultFormatter.renderMarkdownDocument(text);
        viewer.classList.add('is-markdown');
      } else {
        viewer.innerHTML = `<pre class="report-raw">${escapeHTML(text)}</pre>`;
        viewer.classList.remove('is-markdown');
      }
      $('#copy-report').disabled = false;
      $$('.report-item').forEach(el => el.classList.toggle('active', el.dataset.report === name));
    } catch (error) { $('#viewer-content').textContent = error.message; }
  }
  async function refreshOverview(showToast = false) {
    try {
      const data = await api('/api/overview'); state.overview = data; state.agents = data.agents || []; state.activeMissions = Array.isArray(data.active_missions) ? data.active_missions : state.activeMissions;
      const rt = data.runtime || {};
      const seconds = data.uptime_seconds || 0;
      const uptimeText = seconds < 3600 ? `${Math.floor(seconds / 60)}m` : seconds < 86400 ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m` : `${Math.floor(seconds / 86400)}d`;
      const registered = rt.registered_agents || state.agents.length || 0;
      const executable = rt.executable_agents || state.agents.filter(agent => ['ready', 'busy'].includes(hudStatus(agent))).length;
      const outcomes = data.task_history?.terminal_event_count || 0;
      if ($('#metric-identities')) $('#metric-identities').textContent = fmtNum(registered, 0);
      if ($('#metric-ready')) $('#metric-ready').innerHTML = `${fmtNum(executable, 0)}<small> / ${fmtNum(registered, 0)}</small>`;
      if ($('#metric-tasks')) $('#metric-tasks').textContent = fmtNum(outcomes, 0);
      if ($('#metric-uptime')) $('#metric-uptime').textContent = uptimeText;
      if ($('#side-ready')) $('#side-ready').innerHTML = `${fmtNum(executable, 0)} / ${fmtNum(registered, 0)} <span>agents ready</span>`;
      if ($('#side-meter-fill')) $('#side-meter-fill').style.width = `${registered ? Math.min(100, 100 * executable / registered) : 0}%`;
      if ($('#core-status')) { $('#core-status').textContent = executable > 0 ? 'OPERATIONAL' : 'DEGRADED'; $('#core-status').style.color = executable > 0 ? 'var(--green)' : 'var(--amber)'; }
      if ($('#hud-ready-count')) $('#hud-ready-count').textContent = fmtNum(executable, 0);
      if ($('#hud-identity-count')) $('#hud-identity-count').textContent = fmtNum(registered, 0);
      if ($('#hud-response-count')) $('#hud-response-count').textContent = fmtNum(outcomes, 0);
      if ($('#hud-gauge-ready-value')) $('#hud-gauge-ready-value').textContent = registered ? `${Math.round(executable / registered * 100)}%` : '—';
      if ($('#hud-gauge-tasks-value')) $('#hud-gauge-tasks-value').textContent = fmtNum(outcomes, 0);
      if ($('#hud-gauge-up-value')) $('#hud-gauge-up-value').textContent = uptimeText;
      if ($('#hud-registered-inline')) $('#hud-registered-inline').textContent = fmtNum(registered, 0);
      if ($('#hud-executable-inline')) $('#hud-executable-inline').textContent = fmtNum(executable, 0);
      if ($('#hud-gauge-ready')) $('#hud-gauge-ready').style.setProperty('--gauge', `${registered ? Math.min(100, executable / registered * 100) * 3.6 : 0}deg`);
      if ($('#hud-gauge-tasks')) $('#hud-gauge-tasks').style.setProperty('--gauge', `${outcomes ? Math.min(100, outcomes % 101) * 3.6 : 0}deg`);
      if ($('#hud-gauge-up')) $('#hud-gauge-up').style.setProperty('--gauge', `${Math.min(100, seconds / 3600 * 100) * 3.6}deg`);
      if (!state.agents.some(agent => agent.agent_id === state.selectedAgentId)) state.selectedAgentId = state.agents.find(agent => agent.agent_id === 'coding.forge')?.agent_id || state.agents[0]?.agent_id || 'voice.jarvis';
      renderAgentMosaic(); renderAgentGrid(); renderMarketOverview(data.market); renderActivity(data.task_history); renderTasks(); renderMarketDetail(); renderMissionWidget(); renderHudFocusPanel();
      if (showToast) toast('Dashboard synced with the local runtime.', 'success');
    } catch (error) {
      $('#core-status').textContent = 'OFFLINE'; $('#core-status').style.color = 'var(--red)';
      toast(`Could not connect to JARVIS runtime: ${error.message}`, 'error');
    }
  }
  async function runTask(prompt, agentId) {
    prompt = String(prompt || '').trim();
    if (!prompt) { setHudToast('Enter a task before sending it to JARVIS.', 'error'); return; }
    if (state.taskBusy) { setHudToast('A mission is already running. Wait for its result before starting another.', 'error'); return; }
    const targetAgentId = agentId || 'voice.jarvis';
    state.taskBusy = true;
    state.lastHudTask = { prompt, agentId: targetAgentId, status: 'queued', output: '', taskId: '' };
    if (state.hudPanel === 'mission') openHudPanel('mission');
    document.body.classList.add('mission-running');
    const button = $('#run-task'); const original = button?.innerHTML;
    const sendButton = $('#hud-command-send');
    if (button) { button.disabled = true; button.innerHTML = '<span>Executing…</span><span class="spinner"></span>'; }
    if (sendButton) sendButton.disabled = true;
    if ($('#mk-mission-send')) $('#mk-mission-send').disabled = true;
    if ($('#task-result')) {
      $('#task-result').classList.remove('hidden');
      $('#result-title').textContent = 'Mission queued';
      $('#result-meta').textContent = `Accepted by local runtime · ${targetAgentId}`;
      $('#result-output').textContent = 'Waiting for the runtime to start the task…';
    }
    renderLivingNetwork(); renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
    setHudToast(`Mission accepted by ${targetAgentId}. Live runtime events will appear as they occur.`);
    const mainPrompt = $('#hud-command-prompt');
    const directPrompt = $('#hud-direct-prompt');
    let terminalRecord = null;
    try {
      const accepted = await api('/api/missions', { method: 'POST', body: JSON.stringify({ prompt, agent_id: targetAgentId }) });
      const taskId = accepted.task_id || accepted.job?.task_id;
      if (!taskId) throw new Error('The runtime accepted the request without returning a mission identifier.');
      state.activeMissionId = taskId;
      state.lastHudTask = { ...state.lastHudTask, taskId, status: accepted.job?.status || 'queued' };
      if (state.hudPanel === 'mission') openHudPanel('mission');
      if (mainPrompt) mainPrompt.value = '';
      if ($('#mk-mission-prompt')) $('#mk-mission-prompt').value = '';
      if (directPrompt) directPrompt.value = '';
      const legacyPrompt = $('#task-prompt');
      if (legacyPrompt) { legacyPrompt.value = ''; $('#char-count').textContent = '0 / 6000'; }
      const deadline = Date.now() + 390000;
      while (Date.now() < deadline) {
        await pollRuntimeEvents();
        const response = await api(`/api/missions/${encodeURIComponent(taskId)}`);
        const job = response.job || {};
        const result = job.result || {};
        const status = String(job.status || 'queued').toLowerCase();
        state.lastHudTask = {
          ...state.lastHudTask,
          taskId,
          agentId: result.agent_id || job.agent_id || targetAgentId,
          status,
          output: result.output || result.error || job.error || result.summary || '',
          finishedAt: result.finished_at || job.finished_at || null,
          startedAt: job.started_at || null,
        };
        if ($('#result-title')) {
          $('#result-title').textContent = ['succeeded','failed','timed_out','cancelled'].includes(status) ? (status === 'succeeded' ? 'Task completed' : `Task ${status}`) : 'Task executing';
          $('#result-meta').textContent = `${state.lastHudTask.agentId} · ${status} · ${taskId} · ${fmtDate(job.started_at || job.accepted_at)}`;
          $('#result-output').textContent = state.lastHudTask.output || (status === 'running' ? 'Runtime is working. Waiting for a terminal event…' : 'Mission accepted; waiting for task.started.');
        }
        renderLivingNetwork(); renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
        if (['succeeded','failed','timed_out','cancelled'].includes(status)) {
          terminalRecord = result;
          state.lastHudTask.status = status;
          break;
        }
        await new Promise(resolve => window.setTimeout(resolve, 650));
      }
      if (!terminalRecord && Date.now() >= deadline) {
        state.lastHudTask.status = 'running';
        state.lastHudTask.output = 'The dashboard stopped waiting after 6.5 minutes. The server-side mission may still be running; check task history before retrying.';
        setHudToast('Still waiting for the runtime. The task may still be executing; check task history before resubmitting.', 'error');
      } else {
        const succeeded = state.lastHudTask.status === 'succeeded';
        setHudToast(succeeded ? 'Mission completed. The result came from the local runtime.' : `Mission finished with status: ${state.lastHudTask.status}.`, succeeded ? 'success' : 'error');
      }
      await refreshOverview(false);
      renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
      if (state.currentView === 'tasks') renderTasks();
      // Put the readable result in front of the operator when a HUD mission ends;
      // the raw response remains available inside the result card for debugging.
      if (state.currentView === 'overview' && state.lastHudTask?.output) openHudPanel('mission');
      if ($('#task-result') && state.currentView !== 'overview') $('#task-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      state.lastHudTask = { ...state.lastHudTask, status: 'failed', output: error.message };
      if ($('#result-title')) {
        $('#result-title').textContent = 'Task request failed';
        $('#result-meta').textContent = 'The local API did not accept or return this mission.';
        $('#result-output').textContent = error.message;
      }
      setHudToast(`Task request failed: ${error.message}`, 'error');
      renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
    } finally {
      state.taskBusy = false;
      state.activeMissionId = null;
      document.body.classList.remove('mission-running');
      renderLivingNetwork(); renderMissionWidget(); renderHudFocusPanel(); renderActivity(state.overview?.task_history);
      if (document.body.classList.contains('hud-active') && state.hudPanel === 'mission') openHudPanel('mission');
      if (button) { button.disabled = false; button.innerHTML = original || '<span>Run task</span>'; }
      if (sendButton) sendButton.disabled = false;
      if ($('#mk-mission-send')) $('#mk-mission-send').disabled = false;
    }
  }

  async function requestMarketSnapshot() {
    const button = $('#run-market-snapshot') || $('#refresh-market');
    if (button) { button.disabled = true; button.dataset.originalText ||= button.innerHTML; button.innerHTML = 'Requesting…'; }
    toast('Requesting a read-only market snapshot from JARVIS…');
    try {
      const response = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ prompt: 'Live Indian market snapshot', agent_id: 'voice.jarvis' }) });
      const r = response.result || {};
      toast(`Snapshot request ${r.status || 'finished'}. Checking the saved report…`, r.status === 'succeeded' ? 'success' : 'error');
      await refreshOverview(false);
      if (state.currentView === 'markets') renderMarketDetail();
      if (state.currentView === 'reports') await loadReports();
      if (r.output) { $('#task-result').classList.remove('hidden'); $('#result-title').textContent = 'Market snapshot task'; $('#result-meta').textContent = `${r.agent_id} · ${r.status} · ${r.task_id}`; $('#result-output').textContent = r.output; }
    } catch (error) { toast(`Snapshot request failed: ${error.message}`, 'error'); }
    finally { if (button) { button.disabled = false; button.innerHTML = button.dataset.originalText || (button.id === 'run-market-snapshot' ? '<span>Request snapshot</span><span class="run-arrow">↗</span>' : '↻'); delete button.dataset.originalText; } }
  }
  async function openResultReport(reportName) {
    const safeName = String(reportName || '').split(/[\\/]/).filter(Boolean).pop() || '';
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/i.test(safeName)) {
      setHudToast('The report name was not a safe Markdown filename.', 'error');
      return;
    }
    state.selectedReport = safeName;
    navigate('reports');
    await loadReports();
    const reports = await api('/api/reports').catch(() => ({ reports: [] }));
    if (!(reports.reports || []).some(item => item.name === safeName)) {
      setHudToast(`The mission output names ${safeName}, but the dashboard cannot find that saved report.`, 'error');
      return;
    }
    await openReport(safeName);
  }

  function bindEvents() {
    document.addEventListener('click', event => {
      const button = event.target.closest('[data-open-result-report]');
      if (button) { event.preventDefault(); openResultReport(button.dataset.openResultReport); }
    });
    $$('.nav-item').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.view)));
    $$('[data-navigate]').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.navigate)));
    $('#menu-toggle')?.addEventListener('click', () => $('#sidebar').classList.toggle('open')); 
    $('#refresh-all')?.addEventListener('click', () => refreshOverview(true));
    $('#refresh-market')?.addEventListener('click', requestMarketSnapshot);
    $('#run-market-snapshot')?.addEventListener('click', requestMarketSnapshot);
    $('#refresh-tasks')?.addEventListener('click', () => refreshOverview(true));
    $('#refresh-reports')?.addEventListener('click', loadReports);
    $('#agent-search')?.addEventListener('input', renderAgentGrid);
    $$('#agent-filters .filter-chip').forEach(btn => btn.addEventListener('click', () => { state.agentFilter = btn.dataset.filter; $$('#agent-filters .filter-chip').forEach(b => b.classList.toggle('active', b === btn)); renderAgentGrid(); }));
    document.addEventListener('click', event => {
      if (event.target.closest('#hud-mini-suit')) { event.preventDefault(); cycleHudSuitProfile(); return; }
      const focusTab = event.target.closest('[data-focus-tab]');
      if (focusTab) { if (focusTab.dataset.openWorkspace) { navigate(focusTab.dataset.openWorkspace); return; } state.focusTab = focusTab.dataset.focusTab; renderHudFocusPanel(); return; }
      if (event.target.closest('[data-hud-refresh]')) { refreshOverview(true); return; }
      const agentPick = event.target.closest('[data-hud-agent-select]');
      if (agentPick) { openHudPanel('agent', agentPick.dataset.hudAgentSelect); return; }
      const hudPanel = event.target.closest('[data-hud-panel]');
      if (hudPanel) { openHudPanel(hudPanel.dataset.hudPanel, hudPanel.dataset.agentId || null); return; }
      const workspace = event.target.closest('[data-open-workspace]');
      if (workspace) { navigate(workspace.dataset.openWorkspace); return; }
      const tile = event.target.closest('[data-agent-id]'); if (tile) { showAgent(tile.dataset.agentId); return; }
      const report = event.target.closest('[data-report]'); if (report) { openReport(report.dataset.report); return; }
      const promptBtn = event.target.closest('[data-prompt]'); if (promptBtn) { navigate('overview'); $('#task-prompt').value = promptBtn.dataset.prompt; $('#char-count').textContent = `${promptBtn.dataset.prompt.length} / 6000`; $('#task-prompt').focus(); }
    });
    document.addEventListener('keydown', event => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-agent-id], [data-hud-agent-select], [data-report]')) { event.preventDefault(); event.target.click(); }
      if (event.key === 'Escape') { hideHudPanel(); $('#agent-modal').classList.add('hidden'); }
    });
    $('#hud-drawer-close').addEventListener('click', hideHudPanel);
    $('#mk-mission-form')?.addEventListener('submit', event => {
      event.preventDefault();
      const prompt = $('#mk-mission-prompt')?.value || '';
      let target = state.missionTarget || 'voice.jarvis';
      const requestedAgent = state.agents.find(agent => agent.agent_id === target);
      if (target !== 'voice.jarvis' && (!requestedAgent || !['ready', 'busy'].includes(hudStatus(requestedAgent)))) {
        setHudToast(`${requestedAgent?.name || target} has no executable handler. Routing through JARVIS for safe coordination.`);
        target = 'voice.jarvis';
      }
      runTask(prompt, target);
    });
    $('#mk-mission-prompt')?.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#mk-mission-form').requestSubmit(); }
    });
    $$('[data-mission-route]').forEach(button => button.addEventListener('click', () => {
      state.missionTarget = button.dataset.missionRoute || 'voice.jarvis';
      $$('[data-mission-route]').forEach(item => item.classList.toggle('active', item === button));
      const target = state.agents.find(agent => agent.agent_id === state.missionTarget);
      const executable = state.missionTarget === 'voice.jarvis' || (target && ['ready', 'busy'].includes(hudStatus(target)));
      const label = state.missionTarget === 'voice.jarvis' ? 'AUTO-COORDINATE' : (target?.name || state.missionTarget);
      const prompt = $('#mk-mission-prompt');
      if (prompt) prompt.placeholder = `Route: ${label}. Describe the mission…`;
      button.title = executable ? `Route mission to ${label}` : `${label} is registered-only; submit will route through JARVIS`;
    }));
    $$('[data-dock-tab]').forEach(button => button.addEventListener('click', () => {
      state.agentDockTab = button.dataset.dockTab || 'overview';
      renderAgentDock();
    }));
    $('#hud-command-form').addEventListener('submit', event => {
      event.preventDefault();
      runTask($('#hud-command-prompt').value, 'voice.jarvis');
    });
    $('#hud-command-prompt').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#hud-command-form').requestSubmit(); } });
    $('#hud-direct-prompt')?.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#hud-direct-form').requestSubmit(); } });
    $('#hud-direct-form').addEventListener('submit', event => {
      event.preventDefault();
      const selected = state.agents.find(a => a.agent_id === state.selectedAgentId) || state.agents.find(a => a.agent_id === 'coding.forge');
      const target = selected && ['ready', 'busy'].includes(hudStatus(selected)) ? selected.agent_id : 'voice.jarvis';
      if (target === 'voice.jarvis' && selected?.agent_id !== 'voice.jarvis') setHudToast(`${selected?.name || 'Selected agent'} has no executable handler. Routing this request through JARVIS instead.`);
      runTask($('#hud-direct-prompt').value, target);
    });
    $('#hud-command-mic')?.addEventListener('click', () => startHudDictation($('#hud-command-prompt')));
    $('#hud-command-options')?.addEventListener('click', () => openHudPanel('mission'));
    $('#task-form')?.addEventListener('submit', event => { event.preventDefault(); runTask($('#task-prompt').value, $('#agent-target').value); });
    $('#task-prompt')?.addEventListener('input', () => { if ($('#char-count')) $('#char-count').textContent = `${$('#task-prompt').value.length} / 6000`; });
    $('#task-prompt')?.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#task-form').requestSubmit(); } });
    $('#dismiss-result')?.addEventListener('click', () => $('#task-result').classList.add('hidden'));
    $('#copy-report')?.addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.reportText); toast('Report copied to clipboard.', 'success'); } catch { toast('Clipboard access is unavailable in this browser context.', 'error'); } });
    $('#close-agent-modal')?.addEventListener('click', () => $('#agent-modal').classList.add('hidden'));
    $('#agent-modal')?.addEventListener('click', event => { if (event.target === $('#agent-modal')) $('#agent-modal').classList.add('hidden'); });
  }
  bindEvents(); updateClock(); window.setInterval(updateClock, 1000); refreshOverview(false); pollRuntimeEvents(); window.setInterval(() => refreshOverview(false), 5000); window.setInterval(pollRuntimeEvents, 700);
})();
