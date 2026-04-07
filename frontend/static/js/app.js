/* ═══════════════════════════════════════════════════════════════════════════
   QuessCorp Timesheet Automation — Frontend JavaScript
   Talks to FastAPI backend at /api/*
══════════════════════════════════════════════════════════════════════════════ */

const API = '';

// ── State ─────────────────────────────────────────────────────────────────────
let queuedFiles  = [];
let allRecords   = [];
let allJobs      = [];
let settings     = {};
let currentJobId = null;

// ── Navigation ────────────────────────────────────────────────────────────────
function nav(btn) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const page = btn.dataset.page;
  document.getElementById('page-' + page).classList.add('active');
  if (page === 'jobs')    loadJobs();
  if (page === 'review')  loadReview();
  if (page === 'records') loadAllRecords();
  if (page === 'qzone')   setupQZonePage();
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function toast(msg, type='info') {
  const el = document.getElementById('toast');
  const colors = { info:'#38bdf8', ok:'#2dd4a0', warn:'#f5a623', err:'#f05252' };
  el.innerHTML = `<span style="color:${colors[type]||colors.info}">●</span> ${msg}`;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3500);
}

// ── Logging ───────────────────────────────────────────────────────────────────
function logLine(boxId, msg, cls='log-info') {
  const box = document.getElementById(boxId);
  if (!box) return;
  const ts = new Date().toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="log-ts">${ts}</span><span class="${cls}">${escapeHtml(msg)}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}
function clearLog() { document.getElementById('live-log').innerHTML = ''; }

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Blank / Missing helpers ───────────────────────────────────────────────────
/**
 * True when a value is blank, null, "null", "none", "n/a" or "-".
 */
function isMissing(v) {
  if (v === null || v === undefined) return true;
  const s = String(v).trim().toLowerCase();
  return s === '' || s === 'null' || s === 'none' || s === 'n/a' || s === '-';
}

/**
 * True when a date was normalised to the sentinel "LEAVE" value
 * (meaning the field was blank in the source document).
 */
function isLeave(v) {
  return String(v || '').trim().toUpperCase() === 'LEAVE';
}

/**
 * Render a date cell:
 *   blank/LEAVE  →  <span class="badge leave">LEAVE</span>
 *   real date    →  the date string
 */
function dateCell(v) {
  if (isMissing(v) || isLeave(v)) return `<span class="badge leave">LEAVE</span>`;
  return escapeHtml(v);
}

/**
 * Render a plain value cell:
 *   blank/missing  →  <span class="missing-val">—</span>   (styled in red/dim)
 *   real value     →  the value
 */
function valCell(v, suffix='') {
  if (isMissing(v)) return `<span class="missing-val">missing</span>`;
  return escapeHtml(String(v)) + suffix;
}

/**
 * Collect missing critical-field names for a record.
 * Returns [] if all present, or an array of violation strings.
 */
const CRITICAL_FIELDS = [
  { key: 'employee_name',      label: 'Employee Name' },
  { key: 'assignment_id',      label: 'Assignment ID' },
  { key: 'quess_id',           label: 'Quess ID' },
  { key: 'total_hours',        label: 'Total Hours' },
  { key: 'total_working_days', label: 'Working Days' },
  { key: 'client_name',        label: 'Client Name' },
  { key: 'location',           label: 'Location' },
];

function clientMissingViolations(rec) {
  return CRITICAL_FIELDS
    .filter(f => isMissing(rec[f.key]))
    .map(f => `Missing value: ${f.label} — needs human review`);
}

// ── Pipeline UI ───────────────────────────────────────────────────────────────
function setPipe(idx, state, text) {
  const el = document.getElementById('ps-' + idx);
  const st = document.getElementById('pst-' + idx);
  if (!el || !st) return;
  el.className = 'pipe-step ' + (state==='active'?'active':state==='done'?'done':state==='error'?'error':'');
  st.innerHTML = state==='active' ? `<span class="spin"></span>${text||'Running'}` : (text || (state==='done'?'✓ Done':'Idle'));
}
function resetPipeline() { for(let i=0;i<5;i++) setPipe(i,'','Idle'); }

function animatePipeline() {
  resetPipeline();
  const delays = [0, 500, 2000, 3500, 5000];
  delays.forEach((d,i) => { setTimeout(() => setPipe(i,'active','Processing...'), d); });
}
function markPipelineDone(status) {
  for(let i=0;i<5;i++) setPipe(i, status==='COMPLETE'?'done':'error', status==='COMPLETE'?'✓ Done':'Error');
}

// ── File Upload ───────────────────────────────────────────────────────────────
const uploadZone = document.getElementById('upload-zone');
const fileInput  = document.getElementById('file-input');

uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => { e.preventDefault(); uploadZone.classList.remove('drag-over'); handleFiles(e.dataTransfer.files); });
fileInput.addEventListener('change', e => handleFiles(e.target.files));

function handleFiles(files) { queuedFiles = [...files]; renderFileList(); }

const EMOJI = { xlsx:'📊', xls:'📊', csv:'📋', pdf:'📄', txt:'📝', eml:'✉️', msg:'✉️' };
function fileEmoji(name) { return EMOJI[name.split('.').pop().toLowerCase()] || '📄'; }

function renderFileList() {
  const wrap  = document.getElementById('file-list');
  const items = document.getElementById('file-items');
  if (!queuedFiles.length) { wrap.style.display='none'; return; }
  wrap.style.display='block';
  items.innerHTML = queuedFiles.map((f,i) => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
      <span style="font-size:18px;">${fileEmoji(f.name)}</span>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:500;">${escapeHtml(f.name)}</div>
        <div style="font-size:11px;color:var(--muted);">${(f.size/1024).toFixed(1)} KB</div>
      </div>
      <button class="btn sm secondary" onclick="removeFile(${i})">✕</button>
    </div>`).join('');
}
function removeFile(i) { queuedFiles.splice(i,1); renderFileList(); }
function clearFiles()  { queuedFiles=[]; fileInput.value=''; renderFileList(); }

// ── Run Pipeline ──────────────────────────────────────────────────────────────
async function runPipeline() {
  if (!queuedFiles.length) { toast('No files queued','warn'); return; }
  const apiKey = localStorage.getItem('api_key') || '';
  if (!apiKey) {
    toast('Set API key in Settings first','warn');
    document.getElementById('notice-apikey').style.display='flex';
    return;
  }

  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> Running...';
  clearLog();
  animatePipeline();
  logLine('live-log','═══ Supervisor Agent — Pipeline Started ═══','log-agent');

  const cfg = getConfig();

  for (const file of queuedFiles) {
    logLine('live-log', `▶ Processing: ${file.name}`, 'log-info');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('api_key', apiKey);
      fd.append('qzone_endpoint', localStorage.getItem('qzone_endpoint') || '');
      fd.append('qzone_token', localStorage.getItem('qzone_token') || '');
      fd.append('confidence_threshold', cfg.confidence_threshold);
      fd.append('max_daily_hours', cfg.max_daily_hours);
      fd.append('max_weekly_hours', cfg.max_weekly_hours);
      fd.append('batch_size', cfg.batch_size);
      fd.append('period_start', cfg.period_start || '');
      fd.append('period_end', cfg.period_end || '');

      const resp = await fetch(`${API}/api/process`, { method:'POST', body:fd });
      const data = await resp.json();

      if (data.error) {
        logLine('live-log', `✕ Error: ${data.error}`, 'log-err');
        markPipelineDone('FAILED');
      } else {
        currentJobId = data.job_id;

        // Echo server logs to UI
        (data.logs || []).forEach(l => {
          const cls = l.includes('Agent') ? 'log-agent'
                    : l.includes('✕') || l.includes('ERROR') || l.includes('FAILED') ? 'log-err'
                    : l.includes('✓') || l.includes('complete') || l.includes('approved') ? 'log-ok'
                    : l.includes('⚠') || l.includes('FLAGGED') || l.includes('WARNING') ? 'log-warn'
                    : 'log-info';
          logLine('live-log', l, cls);
        });

        // ── Client-side missing-value detection ────────────────────────────
        // Any record with blank critical fields gets a warning logged and is
        // counted as needing review even if the backend hasn't flagged it yet.
        const records = data.records || [];
        let extraReview = 0;
        records.forEach(rec => {
          const missingViolations = clientMissingViolations(rec);
          if (missingViolations.length) {
            extraReview++;
            missingViolations.forEach(m =>
              logLine('live-log', `⚠ ${rec.employee_name || rec.assignment_id || 'Record'}: ${m}`, 'log-warn')
            );
          }
          // Date fields blank → log as LEAVE
          ['period_start','period_end'].forEach(dk => {
            if (isLeave(rec[dk]) || isMissing(rec[dk])) {
              logLine('live-log',
                `ℹ ${rec.employee_name || rec.assignment_id || 'Record'}: ${dk} is blank — marked as LEAVE`,
                'log-info');
            }
          });
        });

        markPipelineDone(data.state === 'COMPLETE' ? 'COMPLETE' : 'error');

        const recs   = records.length;
        const rev    = (data.review_queue || []).filter(r=>r.validation_status==='review').length + extraReview;
        const pushed = data.pushed || 0;

        logLine('live-log', `═══ Complete: ${recs} records | ${rev} for review | ${pushed} pushed ═══`, 'log-ok');
        toast(`Done — ${recs} records extracted, ${rev} need review`, 'ok');

        // ── Populate QZone JSON output panel with ALL records ─────────────
        updateJsonPanel(records);

        await refreshStats();
      }
    } catch(e) {
      logLine('live-log', `✕ Network error: ${e.message}`, 'log-err');
      markPipelineDone('error');
    }
  }

  btn.disabled = false;
  btn.innerHTML = '▶ Run Agent Pipeline';
}

// ── Build QZone JSON payload for one record (mirrors Agent 5 schema) ─────────
function buildQZonePayload(rec) {
  return {
    employeeId:       rec.quess_id || rec.assignment_id || null,
    assignmentId:     rec.assignment_id || null,
    employeeName:     rec.employee_name || null,
    clientAccount:    rec.client_name   || null,
    location:         rec.location      || null,
    attendancePeriod: {
      from: (isLeave(rec.period_start) || isMissing(rec.period_start)) ? 'LEAVE' : rec.period_start,
      to:   (isLeave(rec.period_end)   || isMissing(rec.period_end))   ? 'LEAVE' : rec.period_end,
    },
    totalHours:       isMissing(rec.total_hours)        ? null : rec.total_hours,
    workingDays:      isMissing(rec.total_working_days) ? null : rec.total_working_days,
    woDays:           isMissing(rec.wo_days)            ? null : rec.wo_days,
    plDays:           isMissing(rec.pl_days)            ? null : rec.pl_days,
    missingDays:      isMissing(rec.missing_days)       ? null : rec.missing_days,
    timesheetStatus:  rec.timesheet_status  || 'PENDING',
    validationStatus: rec.validation_status || 'review',
    confidence:       rec.confidence        ?? null,
    submittedBy:      'AUTOMATION-AGENT',
    approvalStatus:   rec.validation_status === 'approved' ? 'APPROVED' : 'PENDING_REVIEW',
    submittedAt:      new Date().toISOString(),
  };
}

// ── JSON panel state ──────────────────────────────────────────────────────────
let _jsonAllRecords = [];

/**
 * Build and render the full JSON output array in the QZone page panel.
 * Called after every pipeline run and when the filter dropdown changes.
 */
function updateJsonPanel(records) {
  if (records && records.length) {
    _jsonAllRecords = records;
  }
  const filter  = document.getElementById('json-filter')?.value || 'all';
  const filtered = _jsonAllRecords.filter(r => {
    if (filter === 'approved') return r.validation_status === 'approved';
    if (filter === 'review')   return r.validation_status === 'review';
    return true;
  });

  const payloads = filtered.map(r => buildQZonePayload(r));
  const json = JSON.stringify(payloads, null, 2);

  const el = document.getElementById('payload-json');
  const ct = document.getElementById('json-record-count');
  if (el) el.textContent = payloads.length ? json : '-- No records match the selected filter --';
  if (ct) ct.textContent = `${payloads.length} record${payloads.length !== 1 ? 's' : ''} (${_jsonAllRecords.length} total)`;

  const nb = document.getElementById('nb-json');
  if (nb) nb.textContent = _jsonAllRecords.length;
}

function filterJsonOutput() {
  updateJsonPanel(null);
}

function copyJson() {
  const text = document.getElementById('payload-json')?.textContent || '';
  if (text.startsWith('--')) { toast('No JSON to copy yet', 'warn'); return; }
  navigator.clipboard.writeText(text).then(() => toast('JSON copied ✓', 'ok'));
}

function downloadJson() {
  const text = document.getElementById('payload-json')?.textContent || '';
  if (text.startsWith('--')) { toast('No JSON to download yet', 'warn'); return; }
  const blob = new Blob([text], { type: 'application/json' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = `qzone_payload_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast('JSON downloaded ✓', 'ok');
}

// ── Config helpers ────────────────────────────────────────────────────────────
function getConfig() {
  return {
    confidence_threshold: parseFloat(document.getElementById('conf-slider')?.value || 0.90),
    max_daily_hours:      parseFloat(document.getElementById('cfg-maxday')?.value  || 24),
    max_weekly_hours:     parseFloat(document.getElementById('cfg-maxweek')?.value || 60),
    batch_size:           parseInt(document.getElementById('cfg-batch')?.value     || 50),
    period_start:         document.getElementById('cfg-period-start')?.value || '',
    period_end:           document.getElementById('cfg-period-end')?.value   || '',
  };
}

// ── Stats ─────────────────────────────────────────────────────────────────────
async function refreshStats() {
  try {
    const res = await fetch(`${API}/api/stats`);
    const s = await res.json();
    document.getElementById('s-jobs').textContent    = s.total_jobs    || 0;
    document.getElementById('s-records').textContent = s.total_records || 0;
    document.getElementById('s-review').textContent  = s.total_review  || 0;
    document.getElementById('s-pushed').textContent  = s.total_pushed  || 0;
    document.getElementById('nb-jobs').textContent   = s.total_jobs    || 0;
    const nb = document.getElementById('nb-review');
    nb.textContent = s.total_review || 0;
    nb.style.display = s.total_review > 0 ? '' : 'none';
    document.getElementById('qz-ready').textContent  = (s.total_records - s.total_review) || 0;
    document.getElementById('qz-pushed').textContent = s.total_pushed || 0;
  } catch(e) { /* ignore */ }
}

// ── Jobs Page ─────────────────────────────────────────────────────────────────
async function loadJobs() {
  try {
    const res = await fetch(`${API}/api/jobs`);
    const d   = await res.json();
    allJobs   = d.jobs || [];
    renderJobs(allJobs);
  } catch(e) { toast('Failed to load jobs','err'); }
}

function renderJobs(jobs) {
  const el = document.getElementById('jobs-list');
  if (!jobs.length) {
    el.innerHTML = '<div class="empty"><div class="empty-icon">📋</div><div class="empty-title">No jobs</div></div>';
    return;
  }
  el.innerHTML = jobs.map(j => {
    const records = (j.records||[]).length;
    const review  = (j.review_queue||[]).filter(r=>r.validation_status==='review').length;
    const pushed  = j.pushed || 0;
    const badgeCls = j.state==='COMPLETE'?'success':j.state==='ERROR'?'danger':'info';
    return `
    <div class="job-card">
      <div class="job-head">
        <div style="flex:1;">
          <div style="font-weight:600;font-size:14px;">${escapeHtml(j.source_file||'')}</div>
          <div class="job-id">${j.job_id}</div>
        </div>
        <span class="badge ${badgeCls}">${j.state}</span>
      </div>
      <div style="display:flex;gap:16px;font-size:12px;color:var(--muted);margin-bottom:10px;">
        <span>📄 ${records} records</span>
        <span>👁 ${review} in review</span>
        <span>↗ ${pushed} pushed</span>
        <span>🕐 ${j.created_at ? new Date(j.created_at).toLocaleString('en-IN') : ''}</span>
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn sm secondary" onclick="viewJobRecords('${j.job_id}')">View Records</button>
        <a href="/api/jobs/${j.job_id}/export/csv" class="btn sm secondary" download>⬇ CSV</a>
      </div>
    </div>`;
  }).join('');
}

async function viewJobRecords(jobId) {
  currentJobId = jobId;
  document.querySelectorAll('.nav-item').forEach(b => { if(b.dataset.page==='records') nav(b); });
}

// ── Review Page ───────────────────────────────────────────────────────────────
async function loadReview() {
  try {
    const res = await fetch(`${API}/api/records/review`);
    const d   = await res.json();
    renderReview(d.review_queue || []);
  } catch(e) { toast('Failed to load review queue','err'); }
}

function renderReview(queue) {
  const list  = document.getElementById('review-list');
  const empty = document.getElementById('review-empty');
  const pending = queue.filter(r => r.validation_status === 'review');
  if (!pending.length) { empty.style.display='block'; list.innerHTML=''; return; }
  empty.style.display='none';

  list.innerHTML = pending.map((r,i) => {
    // Combine backend violations + any client-side missing-field detection
    const backendIssues  = r.validation_issues || [];
    const missingIssues  = clientMissingViolations(r);
    // Deduplicate
    const allIssues = [...new Set([...backendIssues, ...missingIssues])];

    // Date display with LEAVE badge
    const psDisplay = isLeave(r.period_start) || isMissing(r.period_start)
      ? `<span class="badge leave">LEAVE</span>`
      : escapeHtml(r.period_start || '');
    const peDisplay = isLeave(r.period_end) || isMissing(r.period_end)
      ? `<span class="badge leave">LEAVE</span>`
      : escapeHtml(r.period_end || '');

    return `
  <div class="review-card" id="rc-${i}">
    <div class="review-head">
      <div class="review-avatar">${(r.employee_name||'?')[0]}</div>
      <div style="flex:1;">
        <div style="font-weight:600;">${escapeHtml(r.employee_name||'Unknown')}</div>
        <div style="font-size:11px;color:var(--muted);">
          Asgn ${r.assignment_id || '<span class="missing-val">missing</span>'}
          · Quess ${r.quess_id   || '<span class="missing-val">missing</span>'}
          · ${escapeHtml(r.source_file||'')}
        </div>
      </div>
      <span class="badge warn">Needs Review</span>
    </div>

    ${allIssues.length ? `
    <div class="notice warn" style="margin-bottom:12px;">
      ${allIssues.map(x => `<div>⚠ ${escapeHtml(x)}</div>`).join('')}
    </div>` : ''}

    <div class="field-row">
      <label>Employee Name</label>
      <input type="text" id="rv-name-${i}"
        value="${escapeHtml(r.employee_name||'')}"
        class="${isMissing(r.employee_name)?'input-missing':''}">
    </div>
    <div class="field-row">
      <label>Assignment ID</label>
      <input type="text" id="rv-asgn-${i}"
        value="${escapeHtml(r.assignment_id||'')}"
        class="${isMissing(r.assignment_id)?'input-missing':''}">
    </div>
    <div class="field-row">
      <label>Quess ID</label>
      <input type="text" id="rv-qid-${i}"
        value="${escapeHtml(r.quess_id||'')}"
        class="${isMissing(r.quess_id)?'input-missing':''}">
    </div>
    <div class="field-row">
      <label>Period Start</label>
      <div style="display:flex;align-items:center;gap:8px;">
        ${psDisplay}
        <input type="date" id="rv-ps-${i}"
          value="${(!isLeave(r.period_start) && !isMissing(r.period_start)) ? r.period_start : ''}"
          placeholder="Override date">
      </div>
    </div>
    <div class="field-row">
      <label>Period End</label>
      <div style="display:flex;align-items:center;gap:8px;">
        ${peDisplay}
        <input type="date" id="rv-pe-${i}"
          value="${(!isLeave(r.period_end) && !isMissing(r.period_end)) ? r.period_end : ''}"
          placeholder="Override date">
      </div>
    </div>
    <div class="field-row">
      <label>Total Hours</label>
      <input type="number" id="rv-hrs-${i}"
        value="${isMissing(r.total_hours)?'':r.total_hours}"
        placeholder="${isMissing(r.total_hours)?'missing — enter value':''}"
        style="max-width:130px;"
        class="${isMissing(r.total_hours)?'input-missing':''}">
    </div>
    <div class="field-row">
      <label>TS Status</label>
      <select id="rv-status-${i}">
        <option ${r.timesheet_status==='APPROVED' ?'selected':''}>APPROVED</option>
        <option ${r.timesheet_status==='PENDING'  ?'selected':''}>PENDING</option>
        <option ${r.timesheet_status==='MISSING'  ?'selected':''}>MISSING</option>
      </select>
    </div>
    <div class="field-row">
      <label>Confidence</label>
      <div class="conf-bar">
        <span class="conf-pct">${Math.round((r.confidence||0)*100)}%</span>
        <div class="conf-track">
          <div class="conf-fill ${confCls(r.confidence)}" style="width:${Math.round((r.confidence||0)*100)}%"></div>
        </div>
      </div>
    </div>

    <div style="display:flex;gap:10px;margin-top:14px;">
      <button class="btn success sm" onclick="decide('${r.job_id}','${r.assignment_id}','approved',${i})">✓ Approve</button>
      <button class="btn danger sm"  onclick="decide('${r.job_id}','${r.assignment_id}','rejected',${i})">✕ Reject</button>
    </div>
  </div>`;
  }).join('');
}

async function decide(jobId, asgId, decision, idx) {
  const body = {
    job_id:           jobId,
    assignment_id:    asgId,
    decision,
    employee_name:    document.getElementById(`rv-name-${idx}`)?.value,
    total_hours:      parseFloat(document.getElementById(`rv-hrs-${idx}`)?.value) || null,
    timesheet_status: document.getElementById(`rv-status-${idx}`)?.value,
  };
  try {
    const res = await fetch(`${API}/api/review/decide`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const d = await res.json();
    if (d.status==='ok') {
      toast(`${decision==='approved'?'✓ Approved':'✕ Rejected'}: ${body.employee_name}`, decision==='approved'?'ok':'warn');
      document.getElementById(`rc-${idx}`).remove();
      await refreshStats();
      loadAllRecords();
    }
  } catch(e) { toast('Failed to submit decision','err'); }
}

// ── All Records Page ──────────────────────────────────────────────────────────
async function loadAllRecords() {
  try {
    const res = await fetch(`${API}/api/records/all`);
    const d   = await res.json();
    allRecords = d.records || [];
    filterRecords();
  } catch(e) { toast('Failed to load records','err'); }
}

function filterRecords() {
  const q  = document.getElementById('rec-search')?.value.toLowerCase() || '';
  const st = document.getElementById('rec-status')?.value || '';
  const filtered = allRecords.filter(r => {
    const match = !q || (r.employee_name||'').toLowerCase().includes(q)
                     || (r.assignment_id||'').includes(q)
                     || (r.quess_id||'').includes(q)
                     || (r.location||'').toLowerCase().includes(q);
    const sm = !st || r.validation_status === st;
    return match && sm;
  });
  renderRecordsTable(filtered);
}

function renderRecordsTable(rows) {
  const tbody = document.getElementById('records-tbody');
  if (!rows || !rows.length) {
    tbody.innerHTML = `<tr><td colspan="12"><div class="empty"><div class="empty-icon">📄</div><div class="empty-title">No records</div></div></td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const vs     = r.validation_status || 'review';
    const vbadge = vs==='approved'?'success':vs==='rejected'?'danger':'warn';
    const ts     = r.timesheet_status || '';
    const tbadge = ts==='APPROVED'?'success':ts==='MISSING'?'danger':'warn';
    const conf   = r.confidence || 0;

    // Missing days — highlight red when > 0
    const missingDaysHtml = (r.missing_days||0) > 0
      ? `<span style="color:var(--danger);font-weight:600;">${r.missing_days}</span>`
      : (isMissing(r.missing_days) ? '<span class="missing-val">missing</span>' : '0');

    return `<tr>
      <td style="font-weight:500;">${isMissing(r.employee_name)
        ? '<span class="missing-val">missing</span>'
        : escapeHtml(r.employee_name)}</td>
      <td><span class="chip">${isMissing(r.assignment_id)
        ? '<span class="missing-val">missing</span>'
        : escapeHtml(r.assignment_id)}</span></td>
      <td><span class="chip">${isMissing(r.quess_id)
        ? '<span class="missing-val">missing</span>'
        : escapeHtml(r.quess_id)}</span></td>
      <td>${isMissing(r.location)
        ? '<span class="missing-val">missing</span>'
        : escapeHtml(r.location)}</td>
      <td style="font-size:12px;white-space:nowrap;">
        ${dateCell(r.period_start)} → ${dateCell(r.period_end)}
      </td>
      <td style="font-family:'DM Mono',monospace;">${isMissing(r.total_hours)
        ? '<span class="missing-val">missing</span>'
        : r.total_hours + 'h'}</td>
      <td style="text-align:center;">${isMissing(r.wo_days)
        ? '<span class="missing-val">—</span>'
        : r.wo_days}</td>
      <td style="text-align:center;">${isMissing(r.pl_days)
        ? '<span class="missing-val">—</span>'
        : r.pl_days}</td>
      <td style="text-align:center;">${missingDaysHtml}</td>
      <td>${ts ? `<span class="badge ${tbadge}">${ts}</span>` : '<span class="missing-val">—</span>'}</td>
      <td><span class="badge ${vbadge}">${vs}</span></td>
      <td>
        <div class="conf-bar">
          <span class="conf-pct">${Math.round(conf*100)}%</span>
          <div class="conf-track">
            <div class="conf-fill ${confCls(conf)}" style="width:${Math.round(conf*100)}%"></div>
          </div>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function confCls(c) { return c>=0.90?'high':c>=0.75?'mid':'low'; }

async function exportAllCSV() {
  if (!allJobs.length) { toast('No jobs to export','warn'); return; }
  const jobId = allJobs[allJobs.length-1].job_id;
  window.open(`${API}/api/jobs/${jobId}/export/csv`);
}

// ── QZone Page ────────────────────────────────────────────────────────────────
async function setupQZonePage() {
  await refreshStats();
  try {
    const res = await fetch(`${API}/api/jobs`);
    const d   = await res.json();
    allJobs   = d.jobs || [];
    const sel = document.getElementById('qz-job-select');
    sel.innerHTML = allJobs.map(j =>
      `<option value="${j.job_id}">${escapeHtml(j.source_file||j.job_id)} (${j.job_id})</option>`
    ).join('') || '<option value="">No jobs</option>';

    // Populate JSON panel with ALL records from all jobs
    const allRecs = allJobs.flatMap(j => j.records || []);
    if (allRecs.length) updateJsonPanel(allRecs);
  } catch(e) {}
}

async function pushToQZone() {
  const jobId    = document.getElementById('qz-job-select').value;
  const endpoint = document.getElementById('qz-endpoint').value;
  const token    = document.getElementById('qz-token').value;
  const batch    = parseInt(document.getElementById('qz-batch').value) || 50;

  if (!jobId)     { toast('Select a job first','warn'); return; }
  if (!endpoint)  { toast('Enter QZone endpoint','warn'); return; }

  const btn = document.getElementById('push-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span> Pushing...';
  document.getElementById('qzone-log').innerHTML = '';

  try {
    const body = { job_id: jobId, qzone_endpoint: endpoint, qzone_token: token || '', batch_size: batch };
    const res  = await fetch(`${API}/api/qzone/push`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const d = await res.json();

    (d.logs || []).forEach(l => {
      const cls = l.includes('✕')||l.includes('FAILED') ? 'log-err'
                : l.includes('OK')||l.includes('SUCCESS') ? 'log-ok'
                : l.includes('Agent') ? 'log-agent' : 'log-info';
      logLine('qzone-log', l, cls);
    });

    document.getElementById('qz-pushed').textContent = d.pushed || 0;
    document.getElementById('qz-failed').textContent = d.failed || 0;

    // Refresh JSON panel with updated records after push
    await loadJobs();
    const job = allJobs.find(j => j.job_id === jobId);
    if (job && job.records && job.records.length) {
      updateJsonPanel(job.records);
    }

    toast(`Pushed ${d.pushed} records — ${d.status}`, d.status.includes('SUCCESS')?'ok':'warn');
    await refreshStats();
  } catch(e) {
    logLine('qzone-log', `Network error: ${e.message}`, 'log-err');
    toast('Push failed: ' + e.message, 'err');
  }

  btn.disabled = false;
  btn.innerHTML = '↗ Push Approved Records to QZone';
}

// ── Settings ──────────────────────────────────────────────────────────────────
function saveApiKey() {
  const key = document.getElementById('api-key-input').value.trim();
  if (!key) { toast('API key cannot be empty','warn'); return; }
  localStorage.setItem('api_key', key);
  document.getElementById('notice-apikey').style.display = 'none';
  toast('API key saved ✓','ok');
  fetch(`${API}/api/settings/apikey`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ api_key: key })
  }).catch(() => {});
}

function saveSettings() {
  const cfg = getConfig();
  localStorage.setItem('settings', JSON.stringify(cfg));
  localStorage.setItem('qzone_endpoint', document.getElementById('qz-endpoint')?.value || '');
  localStorage.setItem('qzone_token',    document.getElementById('qz-token')?.value || '');
  fetch(`${API}/api/settings`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)
  }).then(() => toast('Settings saved ✓','ok'))
    .catch(() => toast('Saved locally (backend unreachable)','warn'));
}

function loadStoredSettings() {
  try {
    const s = JSON.parse(localStorage.getItem('settings') || '{}');
    if (s.confidence_threshold && document.getElementById('conf-slider')) {
      document.getElementById('conf-slider').value = s.confidence_threshold;
      document.getElementById('confVal').textContent = s.confidence_threshold;
    }
    if (s.max_daily_hours  && document.getElementById('cfg-maxday'))  document.getElementById('cfg-maxday').value  = s.max_daily_hours;
    if (s.max_weekly_hours && document.getElementById('cfg-maxweek')) document.getElementById('cfg-maxweek').value = s.max_weekly_hours;
    if (s.batch_size       && document.getElementById('cfg-batch'))   document.getElementById('cfg-batch').value   = s.batch_size;
    if (s.period_start) document.getElementById('cfg-period-start').value = s.period_start;
    if (s.period_end)   document.getElementById('cfg-period-end').value   = s.period_end;
  } catch(e) {}
  document.getElementById('notice-apikey').style.display =
    localStorage.getItem('api_key') ? 'none' : 'flex';
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadStoredSettings();
  refreshStats();

  const now = new Date();
  const y = now.getFullYear(), m = String(now.getMonth()+1).padStart(2,'0');
  const lastDay = new Date(y, now.getMonth()+1, 0).getDate();
  const ps = document.getElementById('cfg-period-start');
  const pe = document.getElementById('cfg-period-end');
  if (ps && !ps.value) ps.value = `${y}-${m}-01`;
  if (pe && !pe.value) pe.value = `${y}-${m}-${String(lastDay).padStart(2,'0')}`;

  if (!localStorage.getItem('api_key')) {
    document.getElementById('notice-apikey').style.display = 'flex';
  }
});