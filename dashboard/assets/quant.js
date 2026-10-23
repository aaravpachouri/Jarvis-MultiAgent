(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    data: null, refreshId: null, refreshBusy: false, eventCursor: 0,
    events: [], seen: new Set(), chartKey: 'Nifty 50', chartRange: '60',
    chartOptionsSignature: '', eventPollBusy: false, lastLoadAt: null,
    stockSearchBusy: false, stockQuoteBusy: false, stockResults: [], stockQuote: null,
    stockSearchSequence: 0, stockQuoteSequence: 0, stockRange: '3mo',
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = (value, digits = 2) => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: digits, minimumFractionDigits: 0});
  const pct = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : `${Number(value) > 0 ? '+' : ''}${num(value, 2)}%`;
  const date = value => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString([], {month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit'});
  };
  const age = seconds => {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return 'UNKNOWN';
    const n = Math.max(0, Number(seconds));
    if (n < 60) return `${Math.floor(n)}s`;
    if (n < 3600) return `${Math.floor(n / 60)}m`;
    if (n < 86400) return `${Math.floor(n / 3600)}h`;
    return `${Math.floor(n / 86400)}d`;
  };
  const toast = (message, error = false) => {
    const el = $('#q-toast'); el.textContent = message; el.classList.toggle('error', error); el.classList.add('show');
    clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), 4200);
  };
  let accessKeyRejected = false;
  async function api(path, options = {}) {
    const headers = {...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})};
    if (path.startsWith('/api/')) {
      if (accessKeyRejected) throw new Error('JARVIS access key rejected. Reload this page to try again.');
      let accessKey = localStorage.getItem('jarvisAccessKey');
      // Migrate a key saved by the previous session-only version.
      if (!accessKey) {
        const sessionKey = sessionStorage.getItem('jarvisAccessKey');
        if (sessionKey) {
          accessKey = sessionKey;
          localStorage.setItem('jarvisAccessKey', accessKey);
        }
      }
      if (!accessKey) {
        accessKey = window.prompt('JARVIS MultiAgent is private. Enter the dashboard access key configured in Render:');
        if (!accessKey || !accessKey.trim()) throw new Error('A JARVIS access key is required to connect.');
        accessKey = accessKey.trim();
        localStorage.setItem('jarvisAccessKey', accessKey);
      }
      headers['X-JARVIS-Access-Key'] = accessKey;
    }
    const response = await fetch(path, {cache:'no-store', ...options, headers});
    let payload;
    try { payload = await response.json(); } catch { payload = {error:`Unexpected response (${response.status})`}; }
    if (response.status === 401) {
      localStorage.removeItem('jarvisAccessKey');
      sessionStorage.removeItem('jarvisAccessKey');
      accessKeyRejected = true;
      throw new Error('JARVIS access key was rejected. Reload the page to try again.');
    }
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }
  function findIndex(indexes, needles) {
    const list = Array.isArray(indexes) ? indexes : [];
    return list.find(item => needles.some(needle => String(item.name || '').toLowerCase().includes(needle)));
  }
  function setPercent(el, value) {
    el.textContent = pct(value); el.classList.toggle('q-up', Number(value) > 0); el.classList.toggle('q-down', Number(value) < 0);
  }
  function renderIndex(prefix, item) {
    $(`#q-${prefix}-value`).textContent = item?.value == null ? '—' : num(item.value, 2);
    setPercent($(`#q-${prefix}-change`), item?.change_1d_pct);
    $(`#q-${prefix}-session`).textContent = item?.session || state.data?.latest_session || '—';
  }
  function renderHeader(data) {
    const stateText = String(data.market_status || data.live_snapshot?.status || 'DATA STATUS UNKNOWN');
    $('#q-market-state').innerHTML = `<i class="q-led ${data.available ? '':'amber'}"></i><span>${esc(stateText.toUpperCase())}</span>`;
    $('#q-generated-at').textContent = date(data.generated_at_utc);
    $('#q-latest-session').textContent = data.latest_session || 'UNKNOWN';
    $('#q-sidebar-status').textContent = data.available ? (data.live_snapshot?.available ? 'SNAPSHOT LOADED' : 'HISTORICAL MODE') : 'WAITING FOR DATA';
    $('#q-sidebar-source').textContent = data.provider || 'No provider report loaded.';
    $('#q-sidebar-led').className = `q-led ${data.available ? '':'amber'}`;
    $('#q-history-provider').textContent = data.provider || 'No report';
    $('#q-report-age').textContent = data.report_age_seconds == null ? 'Unknown' : age(data.report_age_seconds);
    $('#q-session-detail').textContent = data.latest_session || 'Unknown';
    $('#q-chart-count').textContent = `${num(Object.keys(data.charts || {}).length, 0)} series available`;
    $('#q-limitations').textContent = data.source_limitations || 'Provider coverage and freshness are not established.';
    $('#q-market-pulse').textContent = data.market_pulse || 'No market pulse summary returned.';
    $('#q-disclaimer').textContent = data.disclaimer || 'Research and education only. No trades placed.';
    $('#q-last-updated').textContent = state.lastLoadAt ? `LOCAL DATA READ ${date(state.lastLoadAt)}` : 'Never refreshed';
    const snap = data.live_snapshot || {};
    $('#q-live-provider').textContent = `${snap.provider || 'NSE MCP'} · ${snap.provider_claimed_delay || 'Provider timing not supplied.'}`;
    $('#q-live-status').textContent = snap.status || 'NOT AVAILABLE';
    $('#q-live-retrieved').textContent = date(snap.retrieved_at_utc);
    $('#q-live-tools').textContent = snap.tools_discovered == null ? '—' : num(snap.tools_discovered, 0);
    $('#q-live-age').textContent = snap.quote_age_max_seconds == null ? 'UNKNOWN' : age(snap.quote_age_max_seconds);
    const isDelayedFallback = snap.data_class === 'delayed_fallback';
    const quoteTimestampFresh = !isDelayedFallback && Number.isFinite(Number(snap.quote_age_max_seconds)) && Number(snap.quote_age_max_seconds) <= 180 && (snap.quotes || []).some(item => item.source_timestamp);
    const liveLedClass = quoteTimestampFresh ? '' : 'amber';
    $('#q-live-badge').innerHTML = `<i class="q-led ${liveLedClass}"></i>${esc(snap.label || 'NO LIVE SNAPSHOT')}`;
    $('#q-live-quote-count').textContent = num((snap.quotes || []).length, 0);
    const statusLed = $('#q-market-state .q-led');
    if (statusLed) statusLed.className = `q-led ${quoteTimestampFresh ? '' : 'amber'}`;
  }
  function renderMetrics(data) {
    const nifty = findIndex(data.indexes, ['nifty 50', 'nifty']);
    const sensex = findIndex(data.indexes, ['sensex']);
    renderIndex('nifty', nifty);
    renderIndex('sensex', sensex);
    const advances = Number(data.advances || 0), declines = Number(data.declines || 0), total = advances + declines;
    $('#q-breadth-value').textContent = data.advances == null && data.declines == null ? '—' : `${advances} / ${declines}`;
    $('#q-advances').textContent = `Advances ${num(data.advances,0)}`;
    $('#q-declines').textContent = `Declines ${num(data.declines,0)}`;
    $('#q-breadth-adv').style.width = total ? `${advances / total * 100}%` : '0%';
    $('#q-breadth-dec').style.width = total ? `${declines / total * 100}%` : '0%';
    $('#q-candidate-count').textContent = num((data.candidates || []).length,0);
    $('#q-watch-count').textContent = num((data.candidates || []).length,0);
    $('#q-universe-count').textContent = `Universe ${num(data.stock_universe_size,0)}`;
  }
  function renderLiveQuotes(data) {
    const snap = data.live_snapshot || {};
    const quotes = snap.quotes || [];
    $('#q-live-quotes').innerHTML = quotes.length ? quotes.slice(0,8).map(item => {
      const change = item.change_pct;
      const cls = Number(change) > 0 ? 'q-up' : Number(change) < 0 ? 'q-down' : '';
      return `<div class="q-live-quote"><strong title="${esc(item.name || item.symbol)}">${esc(item.symbol)}</strong><span>₹${esc(num(item.price))}</span><small class="${cls}">${esc(pct(change))}</small></div>`;
    }).join('') : '<div class="q-empty-inline">No quote records could be normalized from the provider payload. Raw tool previews are shown below instead of fabricated quotes.</div>';
    const results = snap.results_preview || [];
    $('#q-provider-results').innerHTML = results.map(item => `<div class="q-provider-result"><strong>${esc(item.name)}</strong><p>${esc(item.preview)}</p></div>`).join('');
    const errors = Object.entries(snap.tool_errors || {});
    $('#q-provider-errors').innerHTML = errors.map(([key,value]) => `<p><strong>${esc(key)}:</strong> ${esc(value)}</p>`).join('');
  }
  function renderCandidates(data) {
    const items = Array.isArray(data.candidates) ? data.candidates : [];
    if (!items.length) {
      $('#q-candidates').innerHTML = '<div class="q-empty-large">No candidate passed the current screen. This is a valid result — do not force a trade. Refresh market data to rerun the conditional research screen.</div>';
      return;
    }
    const normalizeSymbol = value => String(value || '').toUpperCase().replace(/\.(NS|BO)$/, '').replace(/\s+INDEX$/, '').trim();
    const liveQuotes = new Map();
    for (const quote of (data.live_snapshot?.quotes || [])) {
      liveQuotes.set(String(quote.symbol || '').toUpperCase(), quote);
      liveQuotes.set(normalizeSymbol(quote.symbol), quote);
    }
    const quoteAge = Number(data.live_snapshot?.quote_age_max_seconds);
    const snapshotFresh = data.live_snapshot?.data_class !== 'delayed_fallback' && Number.isFinite(quoteAge) && quoteAge <= 180;
    $('#q-candidates').innerHTML = items.map((item, index) => {
      const quote = liveQuotes.get(String(item.ticker || '').toUpperCase()) || liveQuotes.get(normalizeSymbol(item.ticker));
      const quoteFresh = snapshotFresh && quote && Boolean(quote.source_timestamp);
      const price = quoteFresh ? quote.price : item.close;
      const displayedChange = quoteFresh && quote.change_pct != null ? quote.change_pct : item.change_1d_pct;
      const priceMode = quoteFresh ? 'TIMESTAMPED SNAPSHOT QUOTE' : 'HISTORICAL / DELAYED CLOSE';
      const invalidation = item.invalidation;
      const target = item.target_2r;
      const avoid = typeof item.reasons_to_avoid === 'string' ? item.reasons_to_avoid : Array.isArray(item.reasons_to_avoid) ? item.reasons_to_avoid.join('; ') : 'No avoid-reason metadata returned';
      const intraday = item.intraday_metrics || {};
      const timestamp = item.quote_timestamp_utc || quote?.source_timestamp;
      const signal = timestamp ? 'VERIFY QUOTE AGE' : 'HISTORICAL SCREEN';
      return `<article class="q-candidate">
        <div class="q-candidate-top"><div class="q-candidate-symbol"><strong>${esc(item.ticker || 'Unknown ticker')}</strong><small>${esc(item.name || '')}</small></div><span class="q-score">SCORE ${esc(num(item.score,1))}</span></div>
        <div class="q-candidate-price"><strong>₹${esc(num(price))}</strong><span class="${Number(displayedChange)>0?'q-up':Number(displayedChange)<0?'q-down':''}">${esc(pct(displayedChange))}</span></div><small class="q-price-mode">${esc(priceMode)} · VERIFY BEFORE ANY DECISION</small>
        <div class="q-candidate-metrics"><div><small>5 SESSION</small><strong>${esc(pct(item.change_5d_pct))}</strong></div><div><small>20 SESSION</small><strong>${esc(pct(item.change_20d_pct))}</strong></div><div><small>RSI 14</small><strong>${esc(num(item.rsi14,1))}</strong></div></div>
        <div class="q-candidate-detail"><p><b>Signal basis:</b> ${esc(item.screen_reason || 'Ranked by historical price momentum and relative strength where available.')}</p><p><b>Conditional trigger:</b> ${esc(item.entry_trigger || 'No verified trigger was calculated.')}</p><p><b>Invalidation / 2R:</b> ₹${esc(num(invalidation))} / ₹${esc(num(target))}</p><p><b>Intraday:</b> ${esc(item.intraday_setup || (intraday.vwap != null ? `5m VWAP ₹${num(intraday.vwap)}` : 'Intraday confirmation unavailable.'))}</p><p><b>Reasons to avoid:</b> ${esc(avoid)}</p></div>
        <span class="q-setup-note">${esc(signal)} · RESEARCH ONLY</span>
      </article>`;
    }).join('');
  }
  function renderMovers(data) {
    const stocks = (data.stocks || []).slice().sort((a,b) => Number(b.change_1d_pct || -999) - Number(a.change_1d_pct || -999)).slice(0,14);
    $('#q-movers-body').innerHTML = stocks.length ? stocks.map(item => `<tr><td><strong>${esc(item.ticker || '—')}</strong><small>${esc(item.name || '')}</small></td><td>${esc(num(item.close))}</td><td class="${Number(item.change_1d_pct)>0?'q-up':Number(item.change_1d_pct)<0?'q-down':''}">${esc(pct(item.change_1d_pct))}</td><td>${esc(pct(item.change_5d_pct))}</td><td>${esc(pct(item.change_20d_pct))}</td><td>${esc(num(item.rsi14,1))}</td></tr>`).join('') : '<tr><td colspan="6" class="q-table-empty">No saved historical stock rows. A live snapshot alone may not contain the historical indicators this table requires.</td></tr>';
  }
  function chartOptions(data) {
    const charts = data.charts || {};
    const keys = Object.keys(charts).filter(key => Array.isArray(charts[key]) && charts[key].length >= 2);
    if (!keys.length) {
      $('#q-chart-symbol').innerHTML = '<option value="">No chart series</option>';
      $('#q-chart-symbol').disabled = true;
      state.chartKey = '';
      drawChart();
      return;
    }
    $('#q-chart-symbol').disabled = false;
    const signature = keys.join('|');
    if (signature !== state.chartOptionsSignature) {
      state.chartOptionsSignature = signature;
      $('#q-chart-symbol').innerHTML = keys.map(key => `<option value="${esc(key)}">${esc(key)}</option>`).join('');
      state.chartKey = keys.includes('Nifty 50') ? 'Nifty 50' : keys[0];
      $('#q-chart-symbol').value = state.chartKey;
    } else if (!keys.includes(state.chartKey)) {
      state.chartKey = keys[0]; $('#q-chart-symbol').value = state.chartKey;
    }
    drawChart();
  }
  function drawChart() {
    const canvas = $('#q-price-chart'), ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const series = state.data?.charts?.[state.chartKey] || [];
    const empty = $('#q-chart-empty');
    if (series.length < 2) {
      canvas.style.visibility = 'hidden'; empty.classList.add('visible');
      $('#q-chart-name').textContent = state.chartKey || 'No chart available';
      $('#q-chart-range').textContent = 'NO VERIFIED CANDLES'; $('#q-chart-points').textContent = '0 verified price points'; $('#q-chart-change').textContent = 'Window change —';
      return;
    }
    canvas.style.visibility = 'visible'; empty.classList.remove('visible');
    const count = state.chartRange === 'all' ? series.length : Math.min(series.length, Number(state.chartRange) || series.length);
    const rows = series.slice(-count);
    const values = rows.map(row => Number(row.close)).filter(value => Number.isFinite(value) && value > 0);
    if (values.length < 2) { canvas.style.visibility = 'hidden'; empty.classList.add('visible'); return; }
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    const width = Math.max(320, rect.width); const height = Math.max(190, rect.height);
    canvas.width = Math.floor(width * dpr); canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0,0,width,height);
    const pad = {left:54,right:15,top:14,bottom:28}; const plotW=width-pad.left-pad.right, plotH=height-pad.top-pad.bottom;
    const low=Math.min(...values), high=Math.max(...values); const spread=high-low || Math.max(Math.abs(high)*.01,1);
    ctx.font='9px Cascadia Code, Consolas, monospace'; ctx.textBaseline='middle';
    for(let i=0;i<5;i++){
      const y=pad.top+(plotH*i/4); const value=high-spread*i/4;
      ctx.strokeStyle='rgba(91,158,185,.16)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(width-pad.right,y);ctx.stroke();
      ctx.fillStyle='#67899c';ctx.textAlign='left';ctx.fillText(value.toLocaleString(undefined,{maximumFractionDigits:2}),4,y);
    }
    const pts=values.map((value,index)=>({x:pad.left+(index/Math.max(1,values.length-1))*plotW,y:pad.top+((high-value)/spread)*plotH,value}));
    const gradient=ctx.createLinearGradient(0,pad.top,0,height-pad.bottom);gradient.addColorStop(0,'rgba(77,232,255,.18)');gradient.addColorStop(1,'rgba(77,232,255,0)');
    ctx.beginPath();pts.forEach((point,index)=>index?ctx.lineTo(point.x,point.y):ctx.moveTo(point.x,point.y));ctx.lineTo(pts[pts.length-1].x,height-pad.bottom);ctx.lineTo(pts[0].x,height-pad.bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
    ctx.beginPath();pts.forEach((point,index)=>index?ctx.lineTo(point.x,point.y):ctx.moveTo(point.x,point.y));ctx.strokeStyle='#4de8ff';ctx.lineWidth=2;ctx.lineJoin='round';ctx.stroke();
    const last=pts[pts.length-1];ctx.beginPath();ctx.arc(last.x,last.y,3.4,0,Math.PI*2);ctx.fillStyle='#dfffff';ctx.shadowColor='#4de8ff';ctx.shadowBlur=12;ctx.fill();ctx.shadowBlur=0;
    const first=values[0],end=values[values.length-1],change=first?((end/first)-1)*100:null;
    const formatLabel=row=>{const raw=row.session||row.timestamp_utc||'';if(!raw)return '—'; const parsed=new Date(raw);return Number.isNaN(parsed.getTime())?raw.slice(0,10):parsed.toLocaleDateString([],{month:'short',day:'numeric'});};
    ctx.fillStyle='#688a9e';ctx.textAlign='left';ctx.fillText(formatLabel(rows.find(row=>Number(row.close)>0)||rows[0]),pad.left,height-10);
    ctx.textAlign='right';ctx.fillText(formatLabel([...rows].reverse().find(row=>Number(row.close)>0)||rows[rows.length-1]),width-pad.right,height-10);
    $('#q-chart-name').textContent=state.chartKey;$('#q-chart-range').textContent=state.chartRange==='all'?'FULL AVAILABLE SERIES':`LAST ${count} POINTS`;
    $('#q-chart-points').textContent=`${values.length} verified price points`;
    $('#q-chart-change').textContent=`Window ${change===null?'—':pct(change)}`;$('#q-chart-change').className=change>0?'q-up':change<0?'q-down':'';
  }
  function render(data) {
    state.data=data; renderHeader(data);renderMetrics(data);renderLiveQuotes(data);renderCandidates(data);renderMovers(data);chartOptions(data);
    state.lastLoadAt=new Date().toISOString();$('#q-last-updated').textContent=`LOCAL DATA READ ${date(state.lastLoadAt)}`;
  }
  async function loadDashboard() {
    try { const data=await api('/api/quant/dashboard');render(data); }
    catch(error){toast(`Quant dashboard could not load: ${error.message}`,true);}
  }
  function stockCurrency(value) {
    const currency = String(value || 'Unknown').toUpperCase();
    const symbol = ({INR:'₹', USD:'$', GBP:'£', AUD:'A$', CAD:'C$', SGD:'S$', HKD:'HK$'}[currency]);
    return symbol ? `${symbol}${num(state.stockQuote?.price)}` : `${currency} ${num(state.stockQuote?.price)}`;
  }
  function renderStockResults(results) {
    const container = $('#q-stock-search-results');
    if (!results?.length) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = results.map((item, index) => `
      <button class="q-stock-result" type="button" data-stock-result-index="${index}" aria-label="View ${esc(item.name)} ${esc(item.symbol)}">
        <span class="q-stock-result-symbol">${esc(item.symbol)}</span>
        <span class="q-stock-result-copy"><strong>${esc(item.name)}</strong><small>${esc(item.exchange || 'Unknown exchange')} · ${esc(item.quote_type || 'EQUITY')}</small></span>
        <span class="q-stock-result-arrow" aria-hidden="true">↗</span>
      </button>`).join('');
  }
  async function searchStocks(event) {
    event?.preventDefault();
    if (state.stockSearchBusy) return;
    const query = $('#q-stock-query').value.trim();
    const region = $('#q-stock-region').value;
    if (query.length < 2) { $('#q-stock-search-status').textContent = 'Enter at least two letters or a ticker symbol.'; $('#q-stock-search-status').classList.add('error'); return; }
    const sequence = ++state.stockSearchSequence;
    state.stockSearchBusy = true;
    const button = $('#q-stock-search-button'); button.disabled = true; button.textContent = 'SEARCHING…';
    $('#q-stock-search-status').classList.remove('error');
    $('#q-stock-search-status').textContent = `Searching Yahoo Finance for “${query}”…`;
    $('#q-stock-search-results').innerHTML = '';
    try {
      const result = await api(`/api/quant/stock-search?q=${encodeURIComponent(query)}&region=${encodeURIComponent(region)}&limit=12`);
      if (sequence !== state.stockSearchSequence) return;
      state.stockResults = Array.isArray(result.results) ? result.results : [];
      $('#q-stock-search-status').textContent = state.stockResults.length
        ? `${state.stockResults.length} listing(s) found · select the exact exchange/listing to load price data.`
        : `No matching stock listings found for “${query}”. Try a ticker or change the market.`;
      renderStockResults(state.stockResults);
      if (!state.stockResults.length) $('#q-stock-search-status').classList.add('error');
    } catch (error) {
      if (sequence !== state.stockSearchSequence) return;
      $('#q-stock-search-status').textContent = `Stock search failed: ${error.message}`;
      $('#q-stock-search-status').classList.add('error');
    } finally {
      if (sequence === state.stockSearchSequence) { state.stockSearchBusy = false; button.disabled = false; button.textContent = 'SEARCH SHARES ↗'; }
    }
  }
  function renderStockQuote(quote) {
    state.stockQuote = quote;
    $('#q-stock-selected').hidden = false;
    $('#q-stock-symbol').textContent = quote.symbol || '—';
    $('#q-stock-name').textContent = quote.name || quote.symbol || 'Unknown company';
    $('#q-stock-exchange').textContent = `${quote.exchange || 'Unknown exchange'} · ${quote.instrument_type || 'INSTRUMENT UNKNOWN'}`;
    $('#q-stock-price').textContent = `${({INR:'₹',USD:'$',GBP:'£',AUD:'A$',CAD:'C$',SGD:'S$',HKD:'HK$'}[String(quote.currency || '').toUpperCase()] || `${quote.currency || ''} `)}${num(quote.price)}`;
    const change = quote.change_pct;
    const amount = quote.change;
    const changeText = change == null ? 'Daily change unavailable' : `${pct(change)}${amount == null ? '' : ` · ${amount > 0 ? '+' : ''}${num(amount)} ${quote.currency || ''}`}`;
    $('#q-stock-change').textContent = changeText;
    $('#q-stock-change').classList.toggle('q-up', Number(change) > 0);
    $('#q-stock-change').classList.toggle('q-down', Number(change) < 0);
    $('#q-stock-currency').textContent = `${quote.currency || 'Currency unknown'} · ${quote.data_mode || 'Provider mode unknown'}`;
    $('#q-stock-freshness').textContent = quote.freshness_label || 'TIMESTAMP UNAVAILABLE';
    $('#q-stock-freshness').className = `q-mini-tag ${(quote.source_age_seconds == null || quote.source_age_seconds > 86400) ? 'q-down' : (quote.source_age_seconds > 300 ? '' : 'q-up')}`;
    $('#q-stock-source-time').textContent = date(quote.source_timestamp_utc);
    $('#q-stock-retrieved').textContent = date(quote.retrieved_at_utc);
    $('#q-stock-source-link').href = quote.source_url || 'https://finance.yahoo.com/';
    $('#q-stock-chart-title').textContent = `${quote.symbol || 'Share'} · ${quote.chart_range || state.stockRange} history`;
    $('#q-stock-candle-count').textContent = `${(quote.candles || []).length} verified provider candles`;
    $('#q-stock-data-mode').textContent = quote.cached ? 'SHORT CACHE · SOURCE TIME UNCHANGED' : 'PROVIDER RESPONSE RECEIVED';
    $('#q-stock-disclaimer').textContent = quote.disclaimer || 'Informational research only. Provider delays and availability vary. No orders are placed.';
    drawStockChart();
  }
  async function loadStockQuote(symbol, range = state.stockRange) {
    const safeSymbol = String(symbol || '').trim();
    if (!safeSymbol) return;
    const sequence = ++state.stockQuoteSequence;
    state.stockQuoteBusy = true; state.stockRange = range;
    $$('.q-stock-ranges button').forEach(button => button.classList.toggle('active', button.dataset.stockRange === range));
    $('#q-stock-selected').hidden = false;
    $('#q-stock-symbol').textContent = safeSymbol;
    $('#q-stock-name').textContent = 'Loading latest available price…';
    $('#q-stock-exchange').textContent = 'Fetching provider quote and history';
    $('#q-stock-price').textContent = '—'; $('#q-stock-change').textContent = 'PRICE REQUEST RUNNING';
    $('#q-stock-freshness').textContent = 'CHECKING SOURCE TIMESTAMP';
    $('#q-stock-chart-empty').textContent = 'Loading historical price points from the provider…'; $('#q-stock-chart-empty').classList.add('visible');
    try {
      const result = await api(`/api/quant/stock-quote?symbol=${encodeURIComponent(safeSymbol)}&range=${encodeURIComponent(range)}`);
      if (sequence !== state.stockQuoteSequence) return;
      renderStockQuote(result);
      $('#q-stock-search-status').classList.remove('error');
      $('#q-stock-search-status').textContent = `Selected ${result.symbol}: latest available provider price loaded. Check source time and freshness label before relying on it.`;
    } catch (error) {
      if (sequence !== state.stockQuoteSequence) return;
      $('#q-stock-name').textContent = 'Price lookup unavailable';
      $('#q-stock-exchange').textContent = error.message;
      $('#q-stock-price').textContent = '—'; $('#q-stock-change').textContent = 'NO VERIFIED PRICE';
      $('#q-stock-freshness').textContent = 'LOOKUP FAILED'; $('#q-stock-freshness').className = 'q-mini-tag q-down';
      $('#q-stock-chart-empty').textContent = `No price history returned: ${error.message}`; $('#q-stock-chart-empty').classList.add('visible');
      const ctx = $('#q-stock-chart')?.getContext('2d'); if (ctx) ctx.clearRect(0,0,$('#q-stock-chart').width,$('#q-stock-chart').height);
      $('#q-stock-search-status').textContent = `Price lookup failed for ${safeSymbol}: ${error.message}`; $('#q-stock-search-status').classList.add('error');
    } finally { if (sequence === state.stockQuoteSequence) state.stockQuoteBusy = false; }
  }
  function drawStockChart() {
    const canvas = $('#q-stock-chart'), ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const rows = (state.stockQuote?.candles || []).filter(row => Number.isFinite(Number(row.close)) && Number(row.close) > 0);
    const empty = $('#q-stock-chart-empty');
    if (!rows.length) { canvas.style.visibility = 'hidden'; empty.classList.add('visible'); empty.textContent = 'No verified candle data was returned for this listing.'; $('#q-stock-window-change').textContent = 'Window change —'; return; }
    canvas.style.visibility = 'visible'; empty.classList.remove('visible');
    const rect = canvas.getBoundingClientRect(), dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    const width = Math.max(320, rect.width), height = Math.max(150, rect.height);
    canvas.width = Math.floor(width*dpr); canvas.height = Math.floor(height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    const pad={left:64,right:17,top:15,bottom:25}, plotW=width-pad.left-pad.right, plotH=height-pad.top-pad.bottom;
    const values=rows.map(row=>Number(row.close)), low=Math.min(...values), high=Math.max(...values), spread=high-low||Math.max(Math.abs(high)*.01,1);
    ctx.font='9px Cascadia Code,Consolas,monospace';ctx.textBaseline='middle';
    for(let i=0;i<5;i++){
      const y=pad.top+plotH*i/4, value=high-spread*i/4;
      ctx.strokeStyle='rgba(91,158,185,.16)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(width-pad.right,y);ctx.stroke();
      ctx.fillStyle='#68899e';ctx.textAlign='left';ctx.fillText(value.toLocaleString(undefined,{maximumFractionDigits:2}),4,y);
    }
    const points=values.map((value,index)=>({x:pad.left+index/Math.max(1,values.length-1)*plotW,y:pad.top+(high-value)/spread*plotH}));
    const gradient=ctx.createLinearGradient(0,pad.top,0,height-pad.bottom);gradient.addColorStop(0,'rgba(77,232,255,.2)');gradient.addColorStop(1,'rgba(77,232,255,0)');
    ctx.beginPath();points.forEach((point,index)=>index?ctx.lineTo(point.x,point.y):ctx.moveTo(point.x,point.y));ctx.lineTo(points.at(-1).x,height-pad.bottom);ctx.lineTo(points[0].x,height-pad.bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
    ctx.beginPath();points.forEach((point,index)=>index?ctx.lineTo(point.x,point.y):ctx.moveTo(point.x,point.y));ctx.strokeStyle='#4de8ff';ctx.lineWidth=2;ctx.lineJoin='round';ctx.stroke();
    const last=points.at(-1);ctx.beginPath();ctx.arc(last.x,last.y,3.5,0,Math.PI*2);ctx.fillStyle='#e6fdff';ctx.shadowColor='#4de8ff';ctx.shadowBlur=12;ctx.fill();ctx.shadowBlur=0;
    const first=values[0], end=values.at(-1), change=first?((end/first)-1)*100:null;
    const label=row=>{const parsed=new Date(row.timestamp_utc);return Number.isNaN(parsed.getTime())?String(row.timestamp_utc||'').slice(0,10):parsed.toLocaleDateString([],{month:'short',day:'numeric'});};
    ctx.fillStyle='#68899e';ctx.textAlign='left';ctx.fillText(label(rows[0]),pad.left,height-10);ctx.textAlign='right';ctx.fillText(label(rows.at(-1)),width-pad.right,height-10);
    $('#q-stock-candle-count').textContent=`${values.length} verified provider candles`;
    $('#q-stock-window-change').textContent=`Window ${change===null?'—':pct(change)}`;
    $('#q-stock-window-change').className=change>0?'q-up':change<0?'q-down':'';
  }
  function renderActivity() {
    const events=state.events.slice().reverse().slice(0,14);
    if(!events.length){$('#q-activity').innerHTML='<div class="q-empty-inline">No recorded Quant action events yet. Refresh to submit an actual Quant task.</div>';return;}
    $('#q-activity').innerHTML=events.map(event=>{
      const type=String(event.type||'runtime.event'); const payload=event.payload||{};
      const title=type.replace(/^agent\.action\./,'').replace(/^quant\./,'').replace(/[._-]+/g,' ').toUpperCase();
      const detail=[event.agent_id||event.from_agent_id||'runtime',payload.operation,payload.tool_name,payload.provider,payload.phase].filter(Boolean).join(' · ');
      return `<div class="q-activity-item"><time>${esc(date(event.timestamp).split(',').slice(-1)[0]||'—')}</time><div><strong>${esc(title)}</strong><p>${esc(detail||event.task_id||'Runtime event recorded')}</p><span class="event-status">${esc(payload.status||payload.delivery||'OBSERVED')}</span></div></div>`;
    }).join('');
  }
  async function pollEvents() {
    if(state.eventPollBusy)return;state.eventPollBusy=true;
    try{
      const result=await api(`/api/events?after=${state.eventCursor}`);state.eventCursor=Math.max(state.eventCursor,Number(result.cursor||0));
      for(const event of result.events||[]){
        const id=String(event.event_id||`${event.sequence}-${event.type}-${event.task_id}`);if(state.seen.has(id))continue;state.seen.add(id);
        if(String(event.agent_id||'').includes('trading.quant')||String(event.type||'').includes('quant.refresh')||String(event.type||'').startsWith('agent.action.')&&String(event.payload?.action_name||'').startsWith('quant.'))state.events.push(event);
      }
      if(state.events.length>100)state.events=state.events.slice(-100);if(state.seen.size>2000)state.seen=new Set(state.events.map(e=>e.event_id));renderActivity();
    }catch{/* dashboard remains useful when the event stream is unavailable */}
    finally{state.eventPollBusy=false;}
  }
  async function refreshQuant() {
    if(state.refreshBusy)return;
    state.refreshBusy=true;const button=$('#q-refresh');button.disabled=true;button.classList.add('is-busy');
    button.innerHTML='<span class="q-refresh-glyph">↻</span> QUANT WORKING…';
    try{
      const started=await api('/api/quant/refresh',{method:'POST',body:JSON.stringify({})});state.refreshId=started.job_id;
      let job=started.job;
      while(job && ['queued','running'].includes(String(job.status||'').toLowerCase())){
        const running=(job.stages||[]).filter(stage=>stage.status==='running').map(stage=>stage.name.replaceAll('_',' '));
        $('#q-market-state').innerHTML=`<i class="q-led amber"></i><span>QUANT REFRESH ${esc(job.status.toUpperCase())}${running.length?` · ${esc(running.join(', '))}`:''}</span>`;
        await new Promise(resolve=>setTimeout(resolve,1100));
        const status=await api(`/api/quant/refresh/${encodeURIComponent(state.refreshId)}`);job=status.job;
        await pollEvents();
      }
      await loadDashboard();
      const stages=(job?.stages||[]).map(stage=>`${stage.name}: ${stage.status}${stage.summary?` — ${String(stage.summary).slice(0,180)}`:stage.error?` (${stage.error})`:''}`).join(' | ');
      if(job?.status==='succeeded')toast(`Quant refresh complete. ${stages||'Data report updated.'}`);
      else toast(`Quant refresh ${job?.status||'finished'}: ${job?.error||stages||'See source status and timestamps.'}`,job?.status==='failed');
      if(job?.status==='succeeded'&&state.data?.live_snapshot?.available){const fallback=state.data.live_snapshot?.data_class==='delayed_fallback';$('#q-market-state').innerHTML=`<i class="q-led ${fallback?'amber':''}"></i><span>${esc(String(state.data.live_snapshot?.status||state.data.market_status||'DATA UPDATED').toUpperCase())}</span>`;}
    }catch(error){toast(`Quant refresh failed: ${error.message}`,true);}
    finally{state.refreshBusy=false;button.disabled=false;button.classList.remove('is-busy');button.innerHTML='<span class="q-refresh-glyph">↻</span> REFRESH MARKET DATA';}
  }
  $('#q-refresh').addEventListener('click',refreshQuant);
  $('#q-stock-search-form').addEventListener('submit',searchStocks);
  $('#q-stock-search-results').addEventListener('click',event=>{
    const button=event.target.closest('[data-stock-result-index]');if(!button)return;
    const index=Number(button.dataset.stockResultIndex), item=state.stockResults[index];if(!item)return;
    loadStockQuote(item.symbol,'3mo');
  });
  $$('.q-stock-ranges button').forEach(button=>button.addEventListener('click',()=>{
    $$('.q-stock-ranges button').forEach(node=>node.classList.toggle('active',node===button));
    state.stockRange=button.dataset.stockRange;
    if(state.stockQuote?.symbol)loadStockQuote(state.stockQuote.symbol,state.stockRange);
  }));
  $('#q-chart-symbol').addEventListener('change',event=>{state.chartKey=event.target.value;drawChart();});
  $$('.q-range-buttons button').forEach(button=>button.addEventListener('click',()=>{$$('.q-range-buttons button').forEach(node=>node.classList.toggle('active',node===button));state.chartRange=button.dataset.range;drawChart();}));
  window.addEventListener('resize',()=>{if(state.data)drawChart();if(state.stockQuote)drawStockChart();});
  loadDashboard();pollEvents();renderActivity();
  window.setInterval(loadDashboard,30000);window.setInterval(pollEvents,1500);
})();
