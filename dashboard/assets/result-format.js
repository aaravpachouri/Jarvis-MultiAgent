(() => {
  'use strict';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

  function asText(value) {
    if (typeof value === 'string') return value;
    if (value === null || value === undefined) return '';
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  }

  function matchNumber(text, patterns) {
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match) return Number(match[1]);
    }
    return null;
  }

  function parseStages(text) {
    const rows = [];
    const pattern = /stage=([^;\n]+);\s*agent(?:_id)?=([^;\n]+);\s*capability=([^;\n]+);\s*task(?:_id)?=([^;\n]+);\s*status=([a-z_-]+)/gi;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      rows.push({ stage: match[1].trim(), agent: match[2].trim(), capability: match[3].trim(), taskId: match[4].trim(), status: match[5].trim().toLowerCase() });
    }
    return rows;
  }

  function parseMarketPulse(raw) {
    let data = null;
    if (raw && typeof raw === 'object' && raw.report_type === 'market_pulse_india') data = raw;
    const text = data ? asText(data) : asText(raw);
    const looksLikeMarketPulse = /MARKET\s+PULSE\s*(?:—|-|:)\s*INDIA/i.test(text) || /"report_type"\s*:\s*"market_pulse_india"/i.test(text);
    if (!looksLikeMarketPulse && !data) return null;

    const statusMatch = text.match(/MARKET\s+PULSE\s*(?:—|-|:)\s*INDIA\s*(?:—|-|:)\s*(SUCCESS|PARTIAL|FAILED)/i);
    const reportMatch = text.match(/Report:\s*(.+?)(?=\s*(?:##\s*(?:Stage records|Limitations|Evidence review)|Stage records\b|Limitations\b)|$)/i);
    const rootMatch = text.match(/Root task:\s*([0-9a-f-]{12,})/i);
    const quantMatch = text.match(/Quant task:\s*([0-9a-f-]{12,})/i);
    const indexMatch = text.match(/Usable index quotes\s*:\s*(\d+)\s*\/\s*(\d+)/i);
    const searched = data?.attempted_company_searches ?? matchNumber(text, [/Company searches attempted\s*:\s*(\d+)/i, /Attempted\s+(\d+)\s+company searches/i]);
    const usable = data?.successful_company_quotes ?? matchNumber(text, [/Usable company quotes\s*:\s*(\d+)/i, /usable quotes\s+(\d+)/i]);
    const candidates = data?.research_shortlist?.length ?? matchNumber(text, [/Research candidates\s*:\s*(\d+)/i, /(\d+)\s+research candidates/i]);
    const indexesUsable = data?.successful_index_quotes ?? (indexMatch ? Number(indexMatch[1]) : null);
    const indexesTotal = indexMatch ? Number(indexMatch[2]) : 2;
    let reportPath = data?.report_path || reportMatch?.[1]?.trim() || '';
    reportPath = reportPath.replace(/[\s.;]+$/, '');
    const reportName = reportPath.split(/[\\/]/).filter(Boolean).pop() || '';
    const safeReportName = /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/i.test(reportName) ? reportName : '';
    const stages = data?.stage_records ? data.stage_records.map(stage => ({
      stage: stage.stage || stage.name || 'Stage', agent: stage.agent_id || stage.agent || 'unknown.agent',
      capability: stage.capability || '—', taskId: stage.task_id || '', status: String(stage.status || 'unknown').toLowerCase(),
    })) : parseStages(text);
    const limitationsStart = text.search(/##\s*Limitations\b|\bLimitations\s*:/i);
    let limitations = '';
    if (limitationsStart >= 0) {
      limitations = text.slice(limitationsStart).replace(/^##\s*Limitations\s*:?/i, '').replace(/^Limitations\s*:?/i, '').trim();
      limitations = limitations.split(/\s*##\s*(?:Evidence review|Planning output|Final verification)\b/i)[0].trim();
      limitations = limitations.replace(/(?:^|\s)[•]\s*/g, ' ').replace(/\s+/g, ' ').slice(0, 700);
    }
    const declaredStatus = String(data?.mission_status || data?.status || statusMatch?.[1] || 'UNKNOWN').toLowerCase();
    const sessionStatusUnverified = /no such status endpoint was used|market status[^.]{0,70}not verified|session status[^.]{0,70}unknown/i.test(text);
    const freshnessUnverified = /not guaranteed real[- ]time|freshness[^.]{0,70}unknown|quote age[^.]{0,70}unknown/i.test(text);
    return {
      kind: 'market-pulse', status: declaredStatus, title: 'Market Pulse — India',
      sessionStatusUnverified, freshnessUnverified,
      searched: Number.isFinite(Number(searched)) ? Number(searched) : null,
      usable: Number.isFinite(Number(usable)) ? Number(usable) : null,
      indexesUsable: Number.isFinite(Number(indexesUsable)) ? Number(indexesUsable) : null,
      indexesTotal: Number.isFinite(Number(indexesTotal)) ? Number(indexesTotal) : 2,
      candidates: Number.isFinite(Number(candidates)) ? Number(candidates) : null,
      reportPath, reportName: safeReportName,
      rootTaskId: data?.root_task_id || rootMatch?.[1] || '',
      quantTaskId: data?.quant_task_id || quantMatch?.[1] || '',
      stages, limitations, raw: text,
    };
  }

  function prettyStageName(value) {
    return String(value || 'Stage').replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
  }

  function renderRawDetails(raw, label = 'Full runtime output') {
    return `<details class="result-raw"><summary>${esc(label)}</summary><pre>${esc(raw)}</pre></details>`;
  }

  function renderMarketPulse(model, options = {}) {
    const compact = Boolean(options.compact);
    const statusClass = ['succeeded', 'success'].includes(model.status) ? 'success' : ['failed', 'partial'].includes(model.status) ? 'warning' : 'unknown';
    const statusLabel = model.status.toUpperCase();
    const kpi = (label, value, detail = '') => `<article class="result-kpi"><small>${esc(label)}</small><strong>${esc(value)}</strong>${detail ? `<span>${esc(detail)}</span>` : ''}</article>`;
    const searchedValue = model.searched === null ? '—' : model.searched;
    const usableValue = model.usable === null ? '—' : model.usable;
    const indexValue = model.indexesUsable === null ? '—' : `${model.indexesUsable} / ${model.indexesTotal}`;
    const candidateValue = model.candidates === null ? '—' : model.candidates;
    const outcomeText = model.candidates === 0
      ? 'No candidates passed the configured research screen. This is a valid no-pick result; no stock was promoted without meeting the evidence criteria.'
      : model.candidates !== null && model.candidates > 0
        ? `${model.candidates} research candidate${model.candidates === 1 ? '' : 's'} passed the configured screen. Review the detailed report for the evidence, price timestamps and risk conditions.`
        : 'Candidate count was not present in the returned summary. Open the detailed report to inspect the outcome.';
    const stages = model.stages.length ? `<section class="result-section"><div class="result-section-title">WORKFLOW TRACE <span>${model.stages.length} recorded stages</span></div><div class="result-stage-list">${model.stages.map(stage => `<article class="result-stage"><span class="result-stage-mark ${esc(stage.status)}">${['succeeded','success','passed'].includes(stage.status) ? '✓' : ['failed','error'].includes(stage.status) ? '!' : '•'}</span><div class="result-stage-main"><strong>${esc(prettyStageName(stage.stage))}</strong><span>${esc(stage.agent)} · ${esc(stage.capability)}</span></div><span class="result-stage-status ${esc(stage.status)}">${esc(stage.status.toUpperCase())}</span></article>`).join('')}</div></section>` : '';
    const report = model.reportName ? `<section class="result-report-link"><div class="result-report-symbol">▤</div><div class="result-report-main"><small>DETAILED REPORT</small><strong>${esc(model.reportName)}</strong><p>Open the saved report for individual company results, timestamps, indicators and limitations.</p></div><button type="button" data-open-result-report="${esc(model.reportName)}">Open report ↗</button></section>` : `<div class="result-note warning">The output did not include a safe Markdown report filename. Use Reports &amp; Artifacts to locate the saved report.</div>`;
    const trace = [model.rootTaskId ? `Root task · ${model.rootTaskId}` : '', model.quantTaskId ? `Quant task · ${model.quantTaskId}` : ''].filter(Boolean).join(' · ');
    return `<section class="result-rich result-market-pulse${compact ? ' compact' : ''}" aria-label="Formatted Market Pulse result">
      <header class="result-head"><div><div class="result-eyebrow">QUANT INTELLIGENCE / INDIA</div><h3>Market Pulse</h3><p>Provider-backed research run summary</p></div><span class="result-status ${statusClass}">${esc(statusLabel)}</span></header>
      <div class="result-kpis">${kpi('COMPANY SEARCHES', searchedValue, 'attempted')}${kpi('USABLE QUOTES', usableValue, 'company prices')}${kpi('INDEX QUOTES', indexValue, 'Nifty 50 / Sensex')}${kpi('RESEARCH PICKS', candidateValue, 'passed screen')}</div>
      <section class="result-outcome"><div class="result-outcome-icon">${model.candidates === 0 ? '∅' : '◎'}</div><div><strong>${model.candidates === 0 ? 'No qualifying stocks in this run' : 'Research outcome'}</strong><p>${esc(outcomeText)}</p></div></section>
      ${(model.sessionStatusUnverified || model.freshnessUnverified) ? `<section class="result-data-warning"><span>!</span><div><strong>Verify market context before relying on the result</strong><p>${model.sessionStatusUnverified ? 'Exchange session open/closed status was not independently verified. ' : ''}${model.freshnessUnverified ? 'The feed is not guaranteed real-time; inspect each quote source timestamp and freshness in the detailed report.' : ''}</p></div></section>` : ''}
      ${limitationsHTML(model.limitations)}
      ${stages}
      ${report}
      ${trace ? `<div class="result-trace">${esc(trace)}</div>` : ''}
      ${renderRawDetails(model.raw)}
    </section>`;
  }

  function limitationsHTML(value) {
    if (!value) return '';
    return `<details class="result-limitations"><summary>Data-quality notes</summary><p>${esc(value)}</p></details>`;
  }

  function renderGeneric(raw, options = {}) {
    const compact = Boolean(options.compact);
    const text = asText(raw).trim();
    if (!text) return '<div class="result-note">The runtime did not return result text.</div>';
    const lines = text.replace(/\r/g, '').split('\n').map(line => line.trim()).filter(Boolean);
    const content = [];
    let bullets = [];
    const flushBullets = () => {
      if (bullets.length) { content.push(`<ul>${bullets.map(line => `<li>${esc(line)}</li>`).join('')}</ul>`); bullets = []; }
    };
    for (const line of lines.slice(0, 120)) {
      const heading = line.match(/^#{1,4}\s+(.+)$/);
      const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.+)$/);
      if (heading) { flushBullets(); content.push(`<h4>${esc(heading[1])}</h4>`); continue; }
      if (bullet) { bullets.push(bullet[1]); continue; }
      flushBullets();
      const pair = line.match(/^([A-Za-z][A-Za-z0-9 _/-]{1,36}):\s*(.{1,220})$/);
      if (pair) content.push(`<div class="result-field"><span>${esc(pair[1])}</span><strong>${esc(pair[2])}</strong></div>`);
      else content.push(`<p>${esc(line)}</p>`);
    }
    flushBullets();
    if (!content.length) content.push(`<p>${esc(text.slice(0, 2400))}</p>`);
    const primary = content.slice(0, compact ? 8 : 60).join('');
    const remaining = content.length > (compact ? 8 : 60) ? `<details class="result-more"><summary>Show ${content.length - (compact ? 8 : 60)} more lines</summary>${content.slice(compact ? 8 : 60).join('')}</details>` : '';
    return `<section class="result-rich result-generic${compact ? ' compact' : ''}">${primary}${remaining}${renderRawDetails(text)}</section>`;
  }

  function render(raw, options = {}) {
    const marketPulse = parseMarketPulse(raw);
    if (marketPulse) return renderMarketPulse(marketPulse, options);
    return renderGeneric(raw, options);
  }

  function renderInlineMarkdown(raw) {
    let value = esc(raw);
    value = value.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_all, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    value = value.replace(/`([^`]+)`/g, '<code>$1</code>');
    value = value.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    value = value.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>');
    return value;
  }

  function markdownCells(line) {
    return String(line || '').trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  }

  function renderMarkdownDocument(raw) {
    const source = asText(raw).replace(/\r/g, '');
    const lines = source.split('\n');
    const blocks = [];
    let paragraph = [];
    let listType = '';
    let listItems = [];
    let codeLines = null;
    const flushParagraph = () => {
      if (paragraph.length) { blocks.push(`<p>${renderInlineMarkdown(paragraph.join(' '))}</p>`); paragraph = []; }
    };
    const flushList = () => {
      if (listItems.length) { blocks.push(`<${listType}>${listItems.map(item => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</${listType}>`); listItems = []; listType = ''; }
    };
    const flushText = () => { flushParagraph(); flushList(); };

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (/^\s*```/.test(line)) {
        if (codeLines) { blocks.push(`<pre class="result-code"><code>${esc(codeLines.join('\n'))}</code></pre>`); codeLines = null; }
        else { flushText(); codeLines = []; }
        continue;
      }
      if (codeLines) { codeLines.push(line); continue; }
      if (!line.trim()) { flushText(); continue; }

      const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (heading) {
        flushText();
        const level = Math.min(heading[1].length + 1, 5);
        blocks.push(`<h${level}>${renderInlineMarkdown(heading[2].replace(/\s+#+\s*$/, ''))}</h${level}>`);
        continue;
      }
      if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) { flushText(); blocks.push('<hr>'); continue; }

      if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])) {
        flushText();
        const headers = markdownCells(line);
        index += 1; // separator row
        const rows = [];
        while (index + 1 < lines.length && lines[index + 1].includes('|') && lines[index + 1].trim()) {
          rows.push(markdownCells(lines[index + 1])); index += 1;
        }
        blocks.push(`<div class="result-table-wrap"><table class="result-table"><thead><tr>${headers.map(cell => `<th>${renderInlineMarkdown(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_cell, column) => `<td>${renderInlineMarkdown(row[column] || '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }

      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        const nextType = ordered ? 'ol' : 'ul';
        if (listType && listType !== nextType) flushList();
        listType = nextType; listItems.push((unordered || ordered)[1]);
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        flushText(); blocks.push(`<blockquote>${renderInlineMarkdown(line.replace(/^\s*>\s?/, ''))}</blockquote>`); continue;
      }
      paragraph.push(line.trim());
    }
    flushText();
    if (codeLines) blocks.push(`<pre class="result-code"><code>${esc(codeLines.join('\n'))}</code></pre>`);
    return `<article class="markdown-document">${blocks.join('') || '<p>No report content.</p>'}</article>`;
  }

  function summary(raw) {
    const model = parseMarketPulse(raw);
    if (!model) return asText(raw).replace(/\s+/g, ' ').trim().slice(0, 240);
    const count = model.candidates === null ? 'unknown' : model.candidates;
    return `${model.searched ?? '—'} companies searched · ${model.usable ?? '—'} usable quotes · ${model.indexesUsable ?? '—'}/${model.indexesTotal} index quotes · ${count} research candidates`;
  }

  window.JARVISResultFormatter = { parseMarketPulse, render, renderMarkdownDocument, summary, escapeHTML: esc };
})();
