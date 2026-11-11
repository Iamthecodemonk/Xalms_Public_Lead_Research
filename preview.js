// The same merged dataset powers the preview and both export formats.
let selectedBatchIds = new Set(), batchCatalog = [], previewRows = [], previewPage = 0;
let previewSequence = 0, editTimer;
let reviewRows = [];
const pageSize = 25;
const previewColumns = [
  ['visible_name', 'Name'], ['role', 'Role'], ['company', 'Company'],
  ['candidate_name', 'Profile candidate'], ['email', 'Emails'], ['published_phone_candidates', 'Phones'],
  ['linkedin_url', 'LinkedIn'], ['match_status', 'Match status'],
  ['person_association', 'Email association'], ['mailbox_status', 'Mailbox status'],
  ['discovery', 'Contact source'], ['extraction_status', 'Extraction status'], ['email_text', 'Screenshot email cell'], ['phone_text', 'Screenshot phone cell'],
  ['source_url', 'Public sources'], ['context', 'Contact evidence'], ['reason', 'Review notes'], ['source_file', 'Source files'],
];
const busyStates = ['uploading', 'extracting', 'researching'];

function renderEditor(rows) {
  reviewRows = rows;
  const columns = [['visible_name','Visible name'],['role','Role'],['company','Company'],['email','Email'],['published_phone_candidates','Phone']];
  const head = document.createElement('tr');
  for (const [,title] of columns) { const th=document.createElement('th'); th.textContent=title; head.append(th); }
  $('#reviewTable thead').replaceChildren(head);
  const body=$('#reviewTable tbody'); body.replaceChildren();
  for (const row of reviewRows) {
    const tr=document.createElement('tr');
    for (const [key,title] of columns) {
      const td=document.createElement('td'), input=document.createElement('input');
      input.value=row[key]||''; input.setAttribute('aria-label',title+' for '+(row.visible_name||row.company||'unnamed lead'));
      input.oninput=()=>{
        row[key]=input.value; $('#leads').value=csv(reviewRows);
        localStorage.setItem('xalmsDraft:'+job,$('#leads').value);
        $('#reviewMessage').textContent='Unsaved changes. Click Save edited leads to update this batch.';
        clearTimeout(editTimer);editTimer=setTimeout(refreshPreview,400);
      };
      td.append(input);tr.append(td);
    }
    body.append(tr);
  }
}

async function saveReview() {
  try {
    if (!job) throw Error('Open a batch first.');
    const rows=parse($('#leads').value);
    await api('/review/'+job,{leads:rows});
    localStorage.removeItem('xalmsDraft:'+job);
    renderEditor(rows);
    $('#reviewMessage').textContent='Saved '+rows.length+' edited leads. Edits persist after refresh and extraction retries.';
    await loadBatchChoices();await refreshPreview();
  } catch(error) { $('#reviewMessage').textContent=error.message; }
}

async function loadBatchChoices() {
  batchCatalog = (await api('/batches')).batches;
  if (job) selectedBatchIds.add(job);
  if (!selectedBatchIds.size) {
    const recent = batchCatalog.find(b => b.count > 0 && !busyStates.includes(b.state))
      || batchCatalog.find(b => !busyStates.includes(b.state));
    if (recent) selectedBatchIds.add(recent.id);
  }
  const choices = $('#batchChoices');
  choices.replaceChildren();
  for (const batch of batchCatalog) {
    const label = document.createElement('label');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = selectedBatchIds.has(batch.id);
    check.disabled = busyStates.includes(batch.state);
    check.onchange = () => {
      if (check.checked) selectedBatchIds.add(batch.id);
      else selectedBatchIds.delete(batch.id);
      refreshPreview();
    };
    const title = document.createElement('span');
    title.textContent = batch.files.join(', ') || 'Untitled batch';
    const meta = document.createElement('span');
    meta.className = 'batch-meta';
    meta.textContent = `${batch.count} leads · ${batch.state} · ${(batch.errors || []).length} issues · ${batch.id}`;
    title.append(meta);
    label.append(check, title);
    choices.append(label);
    const open = document.createElement('button');
    open.textContent = 'Open batch for review / retry';
    open.onclick = () => {
      $('#resumeId').value = batch.id;
      resume();
      $('#message').scrollIntoView({behavior: 'smooth', block: 'center'});
    };
    choices.append(open);
  }
  $('#selectedBatches').textContent = `(${selectedBatchIds.size} selected)`;
}

function previewBody() {
  const batches = [...selectedBatchIds];
  const overrides = {};
  if (job && loaded === job && selectedBatchIds.has(job)) overrides[job] = parse($('#leads').value);
  return {batches, overrides};
}

async function refreshPreview() {
  const sequence = ++previewSequence;
  $('#exportCsv').disabled = $('#exportExcel').disabled = true;
  $('#selectedBatches').textContent = `(${selectedBatchIds.size} selected)`;
  try {
    const issues = batchCatalog.filter(b => selectedBatchIds.has(b.id)).flatMap(b =>
      (b.errors || []).map(error => `${error.source_file || error.company || 'Batch'}: ${error.error || 'Review this extraction issue.'}`));
    $('#previewIssues').textContent = [...new Set(issues)].join('\n');
    $('#previewIssues').hidden = !issues.length;
    if (!selectedBatchIds.size) {
      previewRows = []; renderPreview();
      $('#previewStats').textContent = '';
      $('#previewMessage').textContent = 'Select a saved batch above, or upload files to get started.';
      return;
    }
    $('#previewMessage').textContent = 'Merging leads and research evidence...';
    const result = await api('/preview', previewBody());
    if (sequence !== previewSequence) return;
    previewRows = result.rows;
    previewPage = 0;
    const emailCount = previewRows.filter(r => r.email).length;
    $('#previewStats').textContent = `${result.total} unique leads · ${emailCount} with emails · ${result.duplicates_merged} duplicate rows merged · ${selectedBatchIds.size} batches`;
    $('#previewMessage').textContent = job && selectedBatchIds.has(job)
      ? 'Includes the current CSV editor values. Refresh after changes; exports always use your latest edits.'
      : 'Showing saved leads with available research evidence. Select a batch with Resume to edit its extracted rows.';
    if (!result.total) $('#previewMessage').textContent = 'These batches have no extracted leads yet. Resume a batch and check extraction errors, or import a CSV. Image and scanned PDF extraction requires Tesseract OCR.';
    renderPreview();
  } catch (error) {
    if (sequence !== previewSequence) return;
    previewRows = []; renderPreview();
    $('#previewStats').textContent = '';
    $('#previewMessage').textContent = error.message;
  }
}

function filteredRows() {
  const query = $('#leadSearch').value.trim().toLowerCase();
  return previewRows.filter(row => Object.values(row).some(value => value.toLowerCase().includes(query)));
}

function renderPreview() {
  const rows = filteredRows();
  const pages = Math.ceil(rows.length / pageSize);
  previewPage = Math.max(0, Math.min(previewPage, pages - 1));
  const head = document.createElement('tr');
  for (const [, title] of previewColumns) {
    const th = document.createElement('th'); th.scope = 'col'; th.textContent = title; head.append(th);
  }
  $('#leadTable thead').replaceChildren(head);
  const body = $('#leadTable tbody'); body.replaceChildren();
  for (const row of rows.slice(previewPage * pageSize, (previewPage + 1) * pageSize)) {
    const tr = document.createElement('tr');
    for (const [key] of previewColumns) {
      const td = document.createElement('td');
      if (key === 'visible_name') td.className = 'lead-name';
      if (['match_status', 'person_association', 'mailbox_status'].includes(key) && row[key]) {
        for (const status of row[key].split('\n')) {
          const badge = document.createElement('span'); badge.className = 'status-tag';
          badge.textContent = status.replaceAll('_', ' '); td.append(badge, document.createElement('br'));
        }
      } else if (['linkedin_url', 'source_url'].includes(key) && row[key]) {
        for (const url of row[key].split('\n')) {
          try {
            const parsed = new URL(url);
            if (!['https:', 'http:'].includes(parsed.protocol)) throw Error();
            const a = document.createElement('a'); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
            a.textContent = key === 'linkedin_url' ? 'View profile ↗' : 'Open source ↗'; td.append(a, document.createElement('br'));
          } catch { td.append(document.createTextNode(url)); }
        }
      } else td.textContent = row[key] || '—';
      tr.append(td);
    }
    body.append(tr);
  }
  if (!rows.length) {
    const tr = document.createElement('tr'), td = document.createElement('td');
    td.colSpan = previewColumns.length;
    td.textContent = previewRows.length ? 'No leads match this search.' : 'Your merged leads will appear here.';
    tr.append(td); body.append(tr);
  }
  $('#pageLabel').textContent = rows.length ? `${previewPage * pageSize + 1}–${Math.min((previewPage + 1) * pageSize, rows.length)} of ${rows.length}` : '0 leads';
  $('#previousPage').disabled = previewPage === 0;
  $('#nextPage').disabled = previewPage + 1 >= pages;
  $('#exportCsv').disabled = $('#exportExcel').disabled = !rows.length;
}

function filterPreview() { previewPage = 0; renderPreview(); }
function changePage(direction) { previewPage += direction; renderPreview(); }
async function chooseAllBatches() {
  selectedBatchIds = new Set(batchCatalog.filter(b => !busyStates.includes(b.state)).map(b => b.id));
  await loadBatchChoices(); await refreshPreview();
}
async function exportLeads(format) {
  try {
    const body = {...previewBody(), format, query: $('#leadSearch').value};
    const response = await fetch('/export', {method: 'POST', headers: {'X-Token': token}, body: JSON.stringify(body)});
    if (!response.ok) throw Error((await response.json()).error || 'Export failed');
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement('a'); a.href = url; a.download = `xalms-merged-leads.${format}`;
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
    $('#previewMessage').textContent = `Exported ${format === 'xlsx' ? 'Excel workbook' : 'CSV'} with all matching leads and evidence fields.`;
  } catch (error) { $('#previewMessage').textContent = error.message; }
}

$('#leads').addEventListener('input', () => {
  if (job) localStorage.setItem('xalmsDraft:'+job,$('#leads').value);
  try { renderEditor(parse($('#leads').value)); } catch { /* Keep incomplete CSV editable. */ }
  clearTimeout(editTimer);
  editTimer = setTimeout(refreshPreview, 400);
});
loadBatchChoices().then(refreshPreview).catch(error => { $('#previewMessage').textContent = error.message; });
