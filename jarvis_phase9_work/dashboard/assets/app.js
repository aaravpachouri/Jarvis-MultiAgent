(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { overview: null, agents: [], currentView: 'overview', agentFilter: 'all', selectedReport: null, reportText: '', taskBusy: false };
  const symbols = { 'Athena': '⌕', 'Atlas': '▦', 'Forge': '⌘', 'JARVIS': '◉', 'Launch': '↗', 'Memory & Notes': '▤', 'Planner': '⌁', 'Quant': '⌇', 'Scout': '⌖', 'Scribe': '✎' };
  const titles = { overview: 'Overview', agents: 'Agent network', tasks: 'Task center', markets: 'Market intelligence', reports: 'Reports & artifacts' };
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
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
  const toast = (message, type = '') => {
    const stack = $('#toast-stack'); const el = document.createElement('div');
    el.className = `toast ${type}`; el.textContent = message; stack.append(el);
    window.setTimeout(() => el.remove(), 4200);
  };
  async function api(path, options = {}) {
    const response = await fetch(path, { cache: 'no-store', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } });
    let payload;
    try { payload = await response.json(); } catch { payload = { error: `Unexpected response (${response.status})` }; }
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }
  function navigate(view) {
    if (!titles[view]) return;
    state.currentView = view;
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
  function renderAgentTile(agent, compact = false) {
    const status = String(agent.status || 'registered').toLowerCase();
    const caps = Array.isArray(agent.capabilities) ? agent.capabilities : [];
    const name = escapeHTML(agent.name || agent.agent_id);
    const symbol = escapeHTML(symbols[agent.name] || '◈');
    const id = escapeHTML(agent.agent_id || 'unknown.agent');
    const role = escapeHTML(agent.description || agent.role || 'Registered specialist identity.');
    if (compact) return `<article class="agent-tile" tabindex="0" role="button" data-agent-id="${id}" aria-label="Inspect ${name}"><div class="agent-tile-top"><span class="agent-symbol">${symbol}</span><span class="agent-state ${escapeHTML(status)}">${escapeHTML(status.toUpperCase())}</span></div><h3>${name}</h3><div class="agent-id">${id}</div><div class="agent-role">${role}</div><div class="agent-tile-bottom"><span>CAPABILITIES</span><span class="agent-cap-count">${caps.length} DECLARED</span></div></article>`;
    return `<article class="agent-card" tabindex="0" role="button" data-agent-id="${id}" aria-label="Inspect ${name}"><div class="agent-card-header"><span class="agent-symbol">${symbol}</span><span class="agent-state ${escapeHTML(status)}">${escapeHTML(status.toUpperCase())}</span></div><h3>${name}</h3><div class="agent-id">${id}</div><p>${role}</p><div class="capability-list">${caps.slice(0, 6).map(cap => `<span class="capability-chip">${escapeHTML(cap)}</span>`).join('')}${caps.length > 6 ? `<span class="capability-chip">+${caps.length - 6} more</span>` : ''}</div><div class="agent-card-footer"><span>${escapeHTML(agent.role || 'SPECIALIST AGENT')}</span><span>${caps.length} CAPABILITIES ↗</span></div></article>`;
  }
  function renderLivingNetwork() {
    const host = $('#living-network'); if (!host) return;
    const agents = state.agents;
    if (!agents.length) { host.innerHTML = '<div class="empty-state">No agent identities returned by runtime.</div>'; return; }
    const w=1200,h=700,cx=600,cy=355;
    const colors=['#51d9ff','#6fffd0','#b39aff','#ffb86b','#7ca9ff','#ff7f9b','#e7dc86','#65e6ef','#c09aff','#90f0bb'];
    const positions=[
      [600,68],[850,100],[1045,230],[1045,480],[850,610],
      [600,650],[350,610],[155,480],[155,230],[350,100]
    ];
    const nodes=agents.map((agent,i)=>({agent,x:positions[i%positions.length][0],y:positions[i%positions.length][1],color:colors[i%colors.length]}));
    const links=nodes.map((n,i)=>{
      const next=nodes[(i+1)%nodes.length];
      return `<line class="network-link network-link-orbit" x1="${n.x}" y1="${n.y}" x2="${next.x}" y2="${next.y}"/><line class="network-link" x1="${cx}" y1="${cy}" x2="${n.x}" y2="${n.y}"/>`;
    }).join('');
    const marks=nodes.map(n=>{
      const status=String(n.agent.status||'registered').toLowerCase(),ready=['ready','busy'].includes(status),name=escapeHTML(n.agent.name||n.agent.agent_id),id=escapeHTML(n.agent.agent_id||'unknown.agent');
      return `<g class="living-node ${ready?'node-ready':'node-registered'}" tabindex="0" role="button" aria-label="Open ${name} workspace" data-agent-id="${id}" style="--node-color:${n.color}"><circle class="node-orbit" cx="${n.x}" cy="${n.y}" r="34"/><circle class="node-core" cx="${n.x}" cy="${n.y}" r="23"/><text class="node-symbol" x="${n.x}" y="${n.y+5}">${escapeHTML(symbols[n.agent.name]||'◈')}</text><circle class="node-status" cx="${n.x+27}" cy="${n.y-27}" r="5"/><text class="node-name" x="${n.x}" y="${n.y+52}">${name}</text><text class="node-state" x="${n.x}" y="${n.y+67}">${escapeHTML(status.toUpperCase())}</text></g>`;
    }).join('');
    host.innerHTML=`<svg class="network-svg ironman-network-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="Iron Man inspired JARVIS command interface with ${agents.length} selectable agent identities"><defs><radialGradient id="network-bg"><stop offset="0" stop-color="#063449" stop-opacity=".9"/><stop offset=".55" stop-color="#06101a" stop-opacity=".45"/><stop offset="1" stop-color="#03070c" stop-opacity="0"/></radialGradient><filter id="reactor-glow"><feGaussianBlur stdDeviation="15" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><rect width="${w}" height="${h}" fill="url(#network-bg)"/><circle class="network-grid-ring" cx="${cx}" cy="${cy}" r="150"/><circle class="network-grid-ring" cx="${cx}" cy="${cy}" r="270"/><circle class="network-grid-ring" cx="${cx}" cy="${cy}" r="390"/>${links}<circle cx="${cx}" cy="${cy+100}" r="95" fill="rgba(20,208,255,.08)" stroke="rgba(71,226,255,.42)" stroke-width="2" filter="url(#reactor-glow)"/><image class="ironman-hero-image" href="/assets/ironman-core.png" x="400" y="28" width="400" height="625" preserveAspectRatio="xMidYMid meet"/><circle class="network-core-halo" cx="${cx}" cy="${cy}" r="165"/><circle class="network-core-ring" cx="${cx}" cy="${cy}" r="150"/><text class="network-core-title" x="${cx}" y="${cy-5}">JARVIS /X</text><text class="network-core-sub" x="${cx}" y="${cy+19}">${state.taskBusy?'MISSION ACTIVE':'CORE ONLINE'}</text>${marks}</svg><div class="network-mobile-list">${agents.map(a=>renderAgentTile(a,true)).join('')}</div>`;
    const ready=agents.filter(a=>['ready','busy'].includes(String(a.status).toLowerCase())).length;
    $('#network-state-label').textContent=state.taskBusy?'MISSION IN PROGRESS':`${ready} AGENTS READY`;
    $('#network-agent-summary').textContent=`${ready} executable · ${agents.length} identities`;
    $('#network-mission-state').textContent=state.taskBusy?'EXECUTING':'STANDING BY';
    $$('.living-node',host).forEach(node=>{const open=()=>showAgent(node.dataset.agentId);node.addEventListener('click',open);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});});
  }
  function renderAgentMosaic() {
    const agents = state.agents;
    renderLivingNetwork();
    $('#agent-mosaic').innerHTML = agents.length ? agents.map(a => renderAgentTile(a, true)).join('') : '<div class="empty-state">No agent identities were returned.</div>';
    $('#nav-agent-count').textContent = agents.length;
    $('#filter-all-count').textContent = agents.length;
    const ready = agents.filter(a => ['ready','busy'].includes(String(a.status).toLowerCase())).length;
    const registered = agents.filter(a => String(a.status).toLowerCase() === 'registered').length;
    $('#filter-ready-count').textContent = ready;
    $('#filter-registered-count').textContent = registered;
    $('#agent-status-summary').textContent = `${ready} READY · ${registered} REGISTERED ONLY`;
    const select = $('#agent-target');
    const previous = select.value;
    select.innerHTML = '<option value="voice.jarvis">JARVIS · Auto-coordinate</option>' + agents.filter(a => ['ready','busy'].includes(String(a.status).toLowerCase()) && a.agent_id !== 'voice.jarvis').map(a => `<option value="${escapeHTML(a.agent_id)}">${escapeHTML(a.name)} · Direct</option>`).join('');
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
  }
  function renderAgentGrid() {
    const q = ($('#agent-search').value || '').trim().toLowerCase();
    const agents = state.agents.filter(a => {
      const status = String(a.status || '').toLowerCase();
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
    const status = String(agent.status || 'registered').toLowerCase();
    $('#agent-modal-content').innerHTML = `<div class="modal-agent-top"><span class="agent-symbol">${escapeHTML(symbols[agent.name] || '◈')}</span><div><h2 id="modal-agent-name">${escapeHTML(agent.name)}</h2><p>${escapeHTML(agent.agent_id)} · ${escapeHTML(status.toUpperCase())}</p></div></div><div class="modal-section"><h3>ROLE & PURPOSE</h3><p>${escapeHTML(agent.description || agent.role || 'No description provided.')}</p></div><div class="modal-section"><h3>CAPABILITIES (${(agent.capabilities || []).length})</h3><div class="modal-capabilities">${(agent.capabilities || []).map(c => `<span class="capability-chip">${escapeHTML(c)}</span>`).join('')}</div></div><div class="modal-section"><h3>RUNTIME DETAILS</h3><p>State: <strong>${escapeHTML(status)}</strong><br>Enabled: <strong>${agent.enabled === false ? 'No' : 'Yes'}</strong><br>Max concurrency: <strong>${escapeHTML(agent.max_concurrency ?? '—')}</strong><br>Timeout: <strong>${escapeHTML(agent.timeout_seconds ?? '—')}s</strong><br>Tags: <strong>${escapeHTML((agent.tags || []).join(', ') || 'None')}</strong></p></div><div class="modal-section"><h3>ROUTING KEYWORDS</h3><p>${escapeHTML((agent.routing_keywords || []).join(' · ') || 'No routing keywords declared.')}</p></div>${['ready','busy'].includes(status) ? `<button class="run-button" id="target-agent-task" data-target="${escapeHTML(agent.agent_id)}">Run a task with ${escapeHTML(agent.name)} <span class="run-arrow">↗</span></button>` : '<p class="market-signal-copy">This identity is registered but has no executable handler in the current runtime.</p>'}`;
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
    const recent = (history?.recent_tasks || []).slice().reverse().slice(0, 5);
    const host = $('#recent-activity');
    if (!recent.length) { host.innerHTML = '<div class="empty-state">No persisted task events yet. Run a task to start the timeline.</div>'; return; }
    host.innerHTML = recent.map(item => `<div class="activity-item"><span class="activity-icon">${item.status === 'succeeded' ? '✓' : item.status === 'failed' ? '!' : '↗'}</span><div class="activity-copy"><strong>${escapeHTML(item.agent_id || 'Unknown agent')}</strong><small>${escapeHTML(item.status || item.event || 'event')} · ${escapeHTML(String(item.task_id || '').slice(0, 13))}…</small></div><span class="activity-time">${escapeHTML(fmtDate(item.timestamp))}</span></div>`).join('');
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
    $('#session-results').innerHTML = sessions.length ? sessions.slice().reverse().map((r, i) => `<article class="session-result"><div class="session-result-top"><div><strong>${escapeHTML(r.prompt || 'Task result')}</strong><div class="agent-id">${escapeHTML(r.agent_id)} · ${escapeHTML(r.task_id)}</div></div><span class="status-pill ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></div><pre>${escapeHTML(r.output || r.error || r.summary || 'No output')}</pre></article>`).join('') : '<div class="empty-state">Run a task to see its full result here.</div>';
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
    $('#copy-report').disabled = true;
    try {
      const response = await fetch(`/api/report/${encodeURIComponent(name)}`, { cache: 'no-store' });
      const text = await response.text();
      if (!response.ok) throw new Error('Could not load this report.');
      state.reportText = text; $('#viewer-content').textContent = text; $('#copy-report').disabled = false;
      $$('.report-item').forEach(el => el.classList.toggle('active', el.dataset.report === name));
    } catch (error) { $('#viewer-content').textContent = error.message; }
  }
  async function refreshOverview(showToast = false) {
    try {
      const data = await api('/api/overview'); state.overview = data; state.agents = data.agents || [];
      const rt = data.runtime || {};
      $('#metric-identities').textContent = fmtNum(rt.registered_agents || 0, 0);
      $('#metric-ready').innerHTML = `${fmtNum(rt.executable_agents || 0, 0)}<small> / ${fmtNum(rt.registered_agents || 0, 0)}</small>`;
      $('#metric-tasks').textContent = fmtNum(data.task_history?.terminal_event_count || 0, 0);
      const seconds = data.uptime_seconds || 0;
      $('#metric-uptime').textContent = seconds < 3600 ? `${Math.floor(seconds / 60)}m` : seconds < 86400 ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m` : `${Math.floor(seconds / 86400)}d`;
      $('#side-ready').innerHTML = `${fmtNum(rt.executable_agents || 0, 0)} / ${fmtNum(rt.registered_agents || 0, 0)} <span>agents ready</span>`;
      $('#side-meter-fill').style.width = `${rt.registered_agents ? Math.min(100, 100 * rt.executable_agents / rt.registered_agents) : 0}%`;
      $('#core-status').textContent = rt.executable_agents > 0 ? 'OPERATIONAL' : 'DEGRADED';
      $('#core-status').style.color = rt.executable_agents > 0 ? 'var(--green)' : 'var(--amber)';
      renderAgentMosaic(); renderAgentGrid(); renderMarketOverview(data.market); renderActivity(data.task_history); renderTasks(); renderMarketDetail();
      if (showToast) toast('Dashboard synced with the local runtime.', 'success');
    } catch (error) {
      $('#core-status').textContent = 'OFFLINE'; $('#core-status').style.color = 'var(--red)';
      toast(`Could not connect to JARVIS runtime: ${error.message}`, 'error');
    }
  }
  async function runTask(prompt, agentId) {
    if (state.taskBusy) return;
    state.taskBusy = true;
    const button = $('#run-task'); const original = button.innerHTML;
    button.disabled = true; button.innerHTML = '<span>Executing…</span><span class="spinner"></span>';
    $('#task-result').classList.remove('hidden'); $('#result-title').textContent = 'Task in progress'; $('#result-meta').textContent = 'Request sent to the local JARVIS runtime.'; $('#result-output').textContent = 'Waiting for agent response…'; renderLivingNetwork();
    try {
      const response = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ prompt, agent_id: agentId || 'voice.jarvis' }) });
      const r = response.result || {};
      $('#result-title').textContent = r.status === 'succeeded' ? 'Task completed' : `Task ${r.status || 'finished'}`;
      $('#result-meta').textContent = `${r.agent_id || 'unknown agent'} · ${r.status || 'unknown'} · ${r.task_id || ''} · ${fmtDate(r.finished_at)}`;
      $('#result-output').textContent = r.output || r.error || r.summary || 'The agent returned no output.';
      $('#task-prompt').value = ''; $('#char-count').textContent = '0 / 6000';
      toast(r.status === 'succeeded' ? 'Task completed successfully.' : `Task finished with status: ${r.status}.`, r.status === 'succeeded' ? 'success' : 'error');
      await refreshOverview(false);
      $('#task-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      $('#result-title').textContent = 'Task request failed'; $('#result-meta').textContent = 'The local API did not return a successful response.'; $('#result-output').textContent = error.message;
      toast(error.message, 'error');
    } finally { state.taskBusy = false; renderLivingNetwork(); button.disabled = false; button.innerHTML = original; }
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
  function bindEvents() {
    $$('.nav-item').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.view)));
    $$('[data-navigate]').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.navigate)));
    $('#menu-toggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
    $('#refresh-all').addEventListener('click', () => refreshOverview(true));
    $('#refresh-market').addEventListener('click', requestMarketSnapshot);
    $('#run-market-snapshot').addEventListener('click', requestMarketSnapshot);
    $('#refresh-tasks').addEventListener('click', () => refreshOverview(true));
    $('#refresh-reports').addEventListener('click', loadReports);
    $('#agent-search').addEventListener('input', renderAgentGrid);
    $$('#agent-filters .filter-chip').forEach(btn => btn.addEventListener('click', () => { state.agentFilter = btn.dataset.filter; $$('#agent-filters .filter-chip').forEach(b => b.classList.toggle('active', b === btn)); renderAgentGrid(); }));
    document.addEventListener('click', event => {
      const tile = event.target.closest('[data-agent-id]'); if (tile) showAgent(tile.dataset.agentId);
      const report = event.target.closest('[data-report]'); if (report) openReport(report.dataset.report);
      const promptBtn = event.target.closest('[data-prompt]'); if (promptBtn) { navigate('overview'); $('#task-prompt').value = promptBtn.dataset.prompt; $('#char-count').textContent = `${promptBtn.dataset.prompt.length} / 6000`; $('#task-prompt').focus(); }
    });
    document.addEventListener('keydown', event => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-agent-id], [data-report]')) { event.preventDefault(); event.target.click(); }
      if (event.key === 'Escape') $('#agent-modal').classList.add('hidden');
    });
    $('#close-agent-modal').addEventListener('click', () => $('#agent-modal').classList.add('hidden'));
    $('#agent-modal').addEventListener('click', event => { if (event.target === $('#agent-modal')) $('#agent-modal').classList.add('hidden'); });
    $('#task-form').addEventListener('submit', event => { event.preventDefault(); runTask($('#task-prompt').value, $('#agent-target').value); });
    $('#task-prompt').addEventListener('input', () => { $('#char-count').textContent = `${$('#task-prompt').value.length} / 6000`; });
    $('#task-prompt').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#task-form').requestSubmit(); } });
    $('#dismiss-result').addEventListener('click', () => $('#task-result').classList.add('hidden'));
    $('#copy-report').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.reportText); toast('Report copied to clipboard.', 'success'); } catch { toast('Clipboard access is unavailable in this browser context.', 'error'); } });
  }
  bindEvents(); updateClock(); window.setInterval(updateClock, 1000); refreshOverview(false); window.setInterval(() => refreshOverview(false), 30000);
})();
