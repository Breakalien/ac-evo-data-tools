'use strict';
// Data editor tab - ported from ac-evo-data-tools-main's tools/ui/app.js.
// Wrapped in its own IIFE (and endpoints namespaced under /api/data/) so it
// can sit in the same document as the material editor tab without any
// global-scope or DOM-id collisions between the two modules.
(function () {

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

let current = null;   // open file (response from /api/data/open)
let dirty = false;
let splinePatch = {}; // index -> modified row
let hideReserved = true;   // fields the .proto marks as reserved (deleted from the schema)
let hideEmpty = false;     // message-typed fields with no content ("{empty}")

function status(msg, cls) {
  const s = $('#status');
  s.textContent = msg || '';
  s.className = 'status' + (cls ? ' ' + cls : '');
}

function setDirty(v) {
  dirty = v;
  $('#btn-save').disabled = !v;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function baseName(p) {
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/* ---------------------------------------------------------------- navigation */
// The actual browsing chrome (navbar/bookmarks/breadcrumbs/listing) lives in
// the shared UI/static/filebrowser.js component - this tab only supplies
// which files are selectable and what happens when one is picked.

const fileBrowser = AceFileBrowser.mountSidebar($('#sidebar'), {
  apiBase: '/api/data',
  classify: (f) => ({ selectable: !!f.kind }),
  onPick: (entry) => openFile(entry.path),
});

AceResizable.makeSplitter($('#sidebar'), { min: 220, max: 700, storageKey: 'ace_de_sidebar_w' });

/* ---------------------------------------------------------------- opening */

async function openFile(path) {
  if (dirty && current && path !== current.path) {
    const ok = confirm('"' + current.name + '" has unsaved changes.\n\n'
                     + 'Discard them and open ' + baseName(path) + '?');
    if (!ok) {
      status('Open cancelled — still editing ' + current.name, 'err');
      return;
    }
  }
  status('Loading…');
  try {
    current = await api('/api/data/open?path=' + encodeURIComponent(path));
  } catch (e) {
    status(e.message, 'err');
    return;
  }
  splinePatch = {};
  setDirty(false);

  $('#welcome').hidden = true;
  $('#pane').hidden = false;
  $('#fname').textContent = current.name;

  const m = $('#fmeta');
  m.textContent = '';
  const tag = (t, c) => { const s = el('span', 'tag' + (c ? ' ' + c : ''), t); m.appendChild(s); };
  if (current.kind === 'proto') {
    tag(current.message || 'unknown schema', current.message ? '' : 'warn');
    tag(current.roundtrip ? 'byte-exact' : 'round-trip failed',
        current.roundtrip ? 'ok' : 'warn');
  } else if (current.kind === 'spline') {
    tag('splinedata v' + current.version);
    tag(current.count + ' points');
    if (current.track_length_m) tag(current.track_length_m.toFixed(1) + ' m');
  } else {
    tag('text');
  }
  tag(fmtSize(current.size));
  if (current.backup) tag('.bak present');
  m.append(document.createTextNode(' ' + current.path));

  $('#find').value = '';
  render();
  updateFindInfo();
  status('');
}

function render() {
  const form = $('#tab-form');
  // the tree is rebuilt, so previous hits point at detached nodes
  openedByFind = []; hits = []; hitIdx = -1;
  form.textContent = '';
  if (current.kind === 'proto') {
    form.appendChild(buildTree(current.data, current.data));
    $('#rawjson').value = JSON.stringify(current.data, null, 2);
    applyFieldFilters();
  } else if (current.kind === 'spline') {
    buildSpline(form);
    $('#rawjson').value = JSON.stringify(
      { version: current.version, aicardata: current.aicardata,
        ideal_line: current.ideal_line, track_length_m: current.track_length_m,
        count: current.count, columns: current.columns }, null, 2);
  } else {
    const ta = el('textarea');
    ta.id = 'textedit';
    ta.value = current.text;
    ta.spellcheck = false;
    ta.style.cssText = 'width:100%;height:70vh;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--line);border-radius:6px;padding:10px;' +
      'font-family:Consolas,monospace;font-size:12px;resize:none';
    ta.oninput = () => setDirty(true);
    form.appendChild(ta);
    $('#rawjson').value = current.text;
  }
}

/* ---------------------------------------------------------------- protobuf tree */

// Keys look like "<number>:<type>[:<name>[:<enum value>]]"
function parseKey(k) {
  const p = k.split(':');
  return {
    num: p[0],
    type: p[1],
    name: p.length > 2 ? p[2] : null,
    enumName: p.length > 3 ? p.slice(3).join(':') : null,
  };
}

function keyWithEnum(k, enumName) {
  const p = k.split(':');
  return [p[0], p[1], p[2]].concat(enumName ? [enumName] : []).join(':');
}

// Replace a key in place: the encoder writes fields in insertion order.
function renameKeyInPlace(obj, oldKey, newKey, value) {
  if (oldKey === newKey) { obj[oldKey] = value; return; }
  const entries = Object.entries(obj);
  for (const k of Object.keys(obj)) delete obj[k];
  for (const [k, v] of entries) {
    if (k === oldKey) obj[newKey] = value; else obj[k] = v;
  }
}

function labelFor(k) {
  const { num, type, name, enumName } = parseKey(k);
  // "?reserved" is a marker, not a name: the .proto reserves this number,
  // meaning the field was deleted. Older files still carry a value for it.
  const isReserved = name === '?reserved';
  const wrap = el('span', (name && !isReserved) ? '' : 'unnamed');
  if (isReserved) {
    wrap.appendChild(el('span', 'fname', 'field ' + num));
    wrap.append(' ');
    wrap.appendChild(el('span', 'gone', 'reserved'));
  } else {
    wrap.appendChild(el('span', 'fname', name || 'field ' + num));
  }
  if (enumName) {
    wrap.append(' ');
    wrap.appendChild(el('span', 'enumval', '= ' + enumName));
  }
  wrap.append(' ');
  wrap.appendChild(el('span', 'tech', '#' + num + ' ' + type));
  return wrap;
}

function enumChoices(key) {
  const { name } = parseKey(key);
  return (current && current.enums && name) ? current.enums[name] : null;
}

function isLeaf(v) {
  return v === null || typeof v !== 'object';
}

// A packed repeated enum (light_functions & co): one dropdown per element.
// The key carries the joined names for readability, so it is rebuilt after
// every pick to stay in sync with the values.
function packedEnumNode(parent, key, arr) {
  const choices = enumChoices(key);
  const nameOf = (n) => (choices.find(([c]) => c === n) || [])[1] || null;
  let curKey = key;

  const d = el('details', 'node');
  const s = el('summary');
  const refreshLabel = () => {
    const names = arr.map(nameOf);
    const next = keyWithEnum(curKey, names.every(Boolean) ? names.join(',') : null);
    renameKeyInPlace(parent, curKey, next, arr);
    curKey = next;
    s.textContent = '';
    s.appendChild(labelFor(curKey));
    s.appendChild(el('span', 'count', ' ×' + arr.length));
  };
  refreshLabel();
  d.appendChild(s);

  const kids = el('div', 'kids');
  arr.forEach((cur, i) => {
    const row = el('div', 'row');
    const lab = el('label');
    lab.appendChild(el('span', 'fname', '[' + i + ']'));
    row.appendChild(lab);
    const sel = el('select', 'enumsel');
    let known = false;
    choices.forEach(([n, nm]) => {
      const o = el('option', null, nm + '  (' + n + ')');
      o.value = String(n);
      if (n === cur) { o.selected = true; known = true; }
      sel.appendChild(o);
    });
    if (!known) {
      const o = el('option', null, 'value outside schema (' + cur + ')');
      o.value = String(cur); o.selected = true;
      sel.insertBefore(o, sel.firstChild);
    }
    sel.onchange = () => {
      arr[i] = parseInt(sel.value, 10);
      refreshLabel();
      sel.classList.add('dirty');
      $('#rawjson').value = JSON.stringify(current.data, null, 2);
      setDirty(true);
    };
    row.appendChild(sel);
    kids.appendChild(row);
  });
  d.appendChild(kids);
  return d;
}

function buildTree(obj, rootRef) {
  const box = el('div');
  if (obj && obj._seq) {
    box.appendChild(el('p', 'muted',
      'Sequential representation (interleaved fields) — edit via the Raw JSON tab.'));
    return box;
  }
  Object.keys(obj).forEach((k) => {
    const v = obj[k];
    // "?reserved" marks a field number the .proto has deleted - see labelFor().
    // Stamped on whatever node this key ends up as (row or details), whichever
    // branch below builds it, so the Hide reserved/Hide empty toggles can find
    // it later without re-parsing the tree - see applyFieldFilters().
    const isReserved = parseKey(k).name === '?reserved';
    let node;
    if (Array.isArray(v)) {
      const isLeafArr = v.every(isLeaf);
      if (isLeafArr && parseKey(k).type === 'packed_varint' && enumChoices(k)) {
        node = packedEnumNode(obj, k, v);
      } else if (isLeafArr) {
        const labs = (current.index_labels || {})[parseKey(k).name];
        const d = el('details', 'node');
        const s = el('summary');
        s.appendChild(labelFor(k));
        s.appendChild(el('span', 'count', ' ×' + v.length));
        d.appendChild(s);
        const kids = el('div', 'kids');
        // the container is the array, not the parent object
        v.forEach((item, i) => kids.appendChild(
          leafRow(k, v, i, labs && labs[i] ? ' [' + i + '] ' + labs[i] : ' [' + i + ']')));
        d.appendChild(kids);
        node = d;
      } else {
        const d = el('details', 'node');
        const s = el('summary');
        s.appendChild(labelFor(k));
        s.appendChild(el('span', 'count', ' ×' + v.length));
        d.appendChild(s);
        const kids = el('div', 'kids');
        v.forEach((item, i) => {
          const dd = el('details', 'node');
          const ss = el('summary');
          ss.appendChild(el('span', 'fname', '[' + i + ']'));
          dd.appendChild(ss);
          const kk = el('div', 'kids');
          kk.appendChild(isLeaf(item) ? leafRow(k, v, i, '') : buildTree(item, rootRef));
          dd.appendChild(kk);
          kids.appendChild(dd);
        });
        d.appendChild(kids);
        node = d;
      }
    } else if (isLeaf(v)) {
      node = leafRow(k, obj, k, '');
    } else {
      const d = el('details', 'node');
      const s = el('summary');
      s.appendChild(labelFor(k));
      const n = Object.keys(v).length;
      s.appendChild(el('span', 'count', n ? ' {' + n + '}' : ' {empty}'));
      d.appendChild(s);
      const kids = el('div', 'kids');
      kids.appendChild(buildTree(v, rootRef));
      d.appendChild(kids);
      node = d;
      if (n === 0) node.dataset.empty = '1';
    }
    if (isReserved) node.dataset.reserved = '1';
    box.appendChild(node);
  });
  return box;
}

/* ---------------------------------------------------------------- reserved/empty filters */

function applyFieldFilters() {
  const form = $('#tab-form');
  form.querySelectorAll('.row[data-reserved], details.node[data-reserved], details.node[data-empty]')
    .forEach((n) => {
      n.hidden = (hideReserved && n.dataset.reserved === '1') ||
                 (hideEmpty && n.dataset.empty === '1');
    });
}

function setHideReserved(v) {
  hideReserved = v;
  $('#btn-hide-reserved').classList.toggle('on', v);
  $('#btn-hide-reserved').setAttribute('aria-pressed', String(v));
  applyFieldFilters();
}

function setHideEmpty(v) {
  hideEmpty = v;
  $('#btn-hide-empty').classList.toggle('on', v);
  $('#btn-hide-empty').setAttribute('aria-pressed', String(v));
  applyFieldFilters();
}

$('#btn-hide-reserved').onclick = () => setHideReserved(!hideReserved);
$('#btn-hide-empty').onclick = () => setHideEmpty(!hideEmpty);

function leafRow(key, container, prop, suffix) {
  const row = el('div', 'row');
  const lab = el('label');
  lab.appendChild(labelFor(key));
  if (suffix) lab.append(suffix);
  row.appendChild(lab);

  // Enum field: dropdown of the schema's values. prop === key means the field
  // is a direct property, so its key can be renamed; a repeated enum (array
  // element) falls back to free text entry.
  const choices = enumChoices(key);
  if (choices && prop === key) {
    const sel = el('select', 'enumsel');
    const cur = container[prop];
    let known = false;
    choices.forEach(([n, nm]) => {
      const o = el('option', null, nm + '  (' + n + ')');
      o.value = String(n);
      if (n === cur) { o.selected = true; known = true; }
      sel.appendChild(o);
    });
    if (!known) {
      const o = el('option', null, 'value outside schema (' + cur + ')');
      o.value = String(cur); o.selected = true;
      sel.insertBefore(o, sel.firstChild);
    }
    // track the key in the closure: rebuilding the tree would collapse it
    let curKey = key;
    sel.onchange = () => {
      const v = parseInt(sel.value, 10);
      const nm = (choices.find(([n]) => n === v) || [])[1] || null;
      const next = keyWithEnum(curKey, nm);
      renameKeyInPlace(container, curKey, next, v);
      curKey = next;
      lab.textContent = '';
      lab.appendChild(labelFor(curKey));
      if (suffix) lab.append(suffix);
      sel.classList.add('dirty');
      $('#rawjson').value = JSON.stringify(current.data, null, 2);
      setDirty(true);
    };
    row.appendChild(sel);
    return row;
  }

  const { type } = parseKey(key);
  const inp = el('input');
  const val = container[prop];
  inp.value = val === null ? '' : String(val);
  inp.oninput = () => {
    let nv = inp.value;
    if (type === 'varint' || type === 'i32' || type === 'i64'
        || type === 'packed_varint') {
      const n = parseInt(nv, 10);
      if (!Number.isNaN(n)) nv = n; else return inp.classList.add('dirty');
    } else if (type === 'f32' || type === 'f64' || type === 'packed_f32') {
      // packed_f32 elements are floats too
      const n = parseFloat(nv);
      if (!Number.isNaN(n)) nv = n; else return inp.classList.add('dirty');
    } else if (type === 'str' || type === 'bytes') {
      // kept as-is
    } else {
      return inp.classList.add('dirty');   // type not editable as it stands
    }
    container[prop] = nv;
    inp.classList.add('dirty');
    $('#rawjson').value = JSON.stringify(current.data, null, 2);
    setDirty(true);
  };
  row.appendChild(inp);
  return row;
}

/* ---------------------------------------------------------------- splinedata */

function buildSpline(form) {
  const head = el('div', 'section');
  head.appendChild(el('h3', null, 'Header'));
  [['aicardata', '.aicardata path', 'text'],
   ['ideal_line', '.ideal_line path', 'text'],
   ['track_length_m', 'Track length (m)', 'number']].forEach(([k, lbl, t]) => {
    if (current[k] === null || current[k] === undefined) return;
    const row = el('div', 'row');
    const lab = el('label');
    lab.appendChild(el('span', 'fname', lbl));
    row.appendChild(lab);
    const inp = el('input');
    inp.type = 'text';
    inp.value = current[k];
    inp.style.maxWidth = '520px';
    inp.oninput = () => {
      current[k] = t === 'number' ? parseFloat(inp.value) : inp.value;
      inp.classList.add('dirty');
      setDirty(true);
    };
    row.appendChild(inp);
    head.appendChild(row);
  });
  form.appendChild(head);

  const sec = el('div', 'section');
  sec.appendChild(el('h3', null, 'Points'));
  const pager = el('div', 'pager');
  const prev = el('button', 'ghost', '← previous');
  const next = el('button', 'ghost', 'next →');
  const info = el('span', 'muted');
  const from = current.start, to = Math.min(current.start + current.page, current.count);
  info.textContent = (from + 1) + '–' + to + ' of ' + current.count;
  prev.disabled = from === 0;
  next.disabled = to >= current.count;
  prev.onclick = () => gotoPage(Math.max(0, from - current.page));
  next.onclick = () => gotoPage(to);
  pager.append(prev, next, info);
  sec.appendChild(pager);

  const tbl = el('table', 'pts');
  const thead = el('thead');
  const hr = el('tr');
  hr.appendChild(el('th', null, '#'));
  current.columns.forEach((c) => {
    hr.appendChild(el('th', c.startsWith('col_') ? '' : 'named', c));
  });
  thead.appendChild(hr);
  tbl.appendChild(thead);
  const tb = el('tbody');
  current.points.forEach((row, i) => {
    const tr = el('tr');
    tr.appendChild(el('td', 'idx', from + i));
    row.forEach((v, c) => {
      const td = el('td');
      const inp = el('input');
      inp.value = String(v);
      inp.oninput = () => {
        const idx = from + i;
        if (!splinePatch[idx]) splinePatch[idx] = row.slice();
        const raw = inp.value;
        let nv;
        if (/^0x[0-9a-fA-F]+$/.test(raw)) nv = raw;
        else if (Number.isInteger(v) && /^-?\d+$/.test(raw)) nv = parseInt(raw, 10);
        else nv = parseFloat(raw);
        if (typeof nv === 'number' && Number.isNaN(nv)) return;
        splinePatch[idx][c] = nv;
        inp.classList.add('dirty');
        setDirty(true);
      };
      td.appendChild(inp);
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  sec.appendChild(tbl);
  form.appendChild(sec);
}

async function gotoPage(start) {
  if (dirty && !confirm('Unsaved changes. Change page anyway?')) return;
  const path = current.path;
  current = await api('/api/data/open?path=' + encodeURIComponent(path) + '&start=' + start);
  splinePatch = {};
  setDirty(false);
  render();
}

/* ---------------------------------------------------------------- saving */

async function save() {
  if (!current) return;
  const body = { path: current.path };
  if (current.kind === 'proto') {
    body.data = current.data;
  } else if (current.kind === 'spline') {
    body.aicardata = current.aicardata;
    body.ideal_line = current.ideal_line;
    body.track_length_m = current.track_length_m;
    body.patch = splinePatch;
  } else {
    body.text = $('#textedit').value;
  }
  status('Saving…');
  try {
    const r = await api('/api/data/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    setDirty(false);
    status('"' + current.name + '" saved'
           + (r.backup_created ? ' (.bak created)' : ''), 'ok');
    document.querySelectorAll('input.dirty').forEach((i) => i.classList.remove('dirty'));
  } catch (e) {
    status('Failed: ' + e.message, 'err');
  }
}

/* ---------------------------------------------------------------- find in file */

// Nodes opened by the filter, so clearing it restores the fold state.
let openedByFind = [];
let hits = [];        // matching elements in the structured view
let hitIdx = -1;

function clearFind() {
  const form = $('#tab-form');
  form.querySelectorAll('[hidden]').forEach((e) => { e.hidden = false; });
  form.querySelectorAll('.hit').forEach((e) => e.classList.remove('hit', 'cursor'));
  openedByFind.forEach((d) => { d.open = false; });
  openedByFind = [];
  hits = [];
  hitIdx = -1;
}

function findInForm(term) {
  clearFind();
  const form = $('#tab-form');
  const t = term.toLowerCase();
  if (!t) return 0;

  const rowText = (r) => {
    const lab = r.querySelector('label');
    const inp = r.querySelector('input');
    const sel = r.querySelector('select');
    return ((lab ? lab.textContent : '') + ' ' +
            (inp ? inp.value : '') + ' ' +
            (sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : ''))
           .toLowerCase();
  };

  form.querySelectorAll('.row').forEach((r) => {
    const hit = rowText(r).includes(t);
    r.hidden = !hit;
    if (hit) { r.classList.add('hit'); hits.push(r); }
  });

  // deepest first, so a parent sees whether its children survived
  const nodes = [...form.querySelectorAll('details.node')].reverse();
  nodes.forEach((d) => {
    const sum = d.querySelector(':scope > summary');
    const sumHit = sum.textContent.toLowerCase().includes(t);
    const kept = d.querySelector('.row:not([hidden]), details.node:not([hidden])');
    if (sumHit) {
      d.querySelectorAll('[hidden]').forEach((e) => { e.hidden = false; });
      sum.classList.add('hit');
      hits.push(sum);
    }
    d.hidden = !(sumHit || kept);
    if (!d.hidden && !d.open) { d.open = true; openedByFind.push(d); }
  });

  // document order, so the next/prev buttons walk top to bottom
  hits.sort((a, b) => (a.compareDocumentPosition(b) & 4) ? -1 : 1);
  return hits.length;
}

function gotoHit(delta) {
  if (!hits.length) return;
  if (hitIdx >= 0 && hits[hitIdx]) hits[hitIdx].classList.remove('cursor');
  hitIdx = (hitIdx + delta + hits.length) % hits.length;
  const h = hits[hitIdx];
  h.classList.add('cursor');
  h.scrollIntoView({ block: 'center' });
  updateFindInfo();
}

function findInRaw(term, delta, keepFocus) {
  const ta = $('#rawjson');
  const hay = ta.value.toLowerCase();
  const t = term.toLowerCase();
  if (!t) { hits = []; hitIdx = -1; return 0; }
  const pos = [];
  let i = hay.indexOf(t);
  while (i !== -1) { pos.push(i); i = hay.indexOf(t, i + t.length); }
  hits = pos;
  if (!pos.length) { hitIdx = -1; return 0; }
  hitIdx = delta === 0 ? 0 : (hitIdx + delta + pos.length) % pos.length;
  // Typing in the find box re-runs this on every keystroke: focusing the
  // textarea there would kick focus out of the find box after each letter,
  // forcing a click back in to type the next one. Only steal focus for an
  // explicit jump (Enter / next / previous), not for live-as-you-type search.
  if (!keepFocus) ta.focus();
  ta.setSelectionRange(pos[hitIdx], pos[hitIdx] + t.length);
    // no scrollIntoView for a textarea selection: approximate by line
  const line = ta.value.slice(0, pos[hitIdx]).split('\n').length;
  ta.scrollTop = Math.max(0, (line - 8) * 15);
  return pos.length;
}

function rawMode() {
  return !$('#tab-raw').hidden;
}

function updateFindInfo() {
  const info = $('#findinfo');
  const term = $('#find').value.trim();
  if (!term) { info.textContent = ''; info.className = 'findinfo'; return; }
  const n = hits.length;
  info.textContent = n ? ((hitIdx >= 0 ? hitIdx + 1 : 1) + ' / ' + n) : 'no match';
  info.className = 'findinfo' + (n ? '' : ' none');
  $('#find-prev').disabled = $('#find-next').disabled = !n;
}

function runFind(delta, keepFocus) {
  const term = $('#find').value.trim();
  if (!current) return;
  if (rawMode()) {
    findInRaw(term, delta, keepFocus);
  } else if (delta === 0) {
    findInForm(term);
    hitIdx = -1;
    if (hits.length) gotoHit(1);
  } else {
    gotoHit(delta);
  }
  updateFindInfo();
}

let findTimer = null;
$('#find').oninput = () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => runFind(0, true), 180);
};
$('#find').onkeydown = (e) => {
  if (e.key === 'Enter') { e.preventDefault(); runFind(e.shiftKey ? -1 : 1); }
  if (e.key === 'Escape') { $('#find').value = ''; runFind(0, true); }
};
$('#find-next').onclick = () => runFind(1);
$('#find-prev').onclick = () => runFind(-1);

/* ---------------------------------------------------------------- events */

$('#btn-save').onclick = save;
$('#btn-reload').onclick = () => { if (current) { setDirty(false); openFile(current.path); } };

document.querySelectorAll('#tab-data-editor .tabs button[data-tab]').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('#tab-data-editor .tabs button[data-tab]')
      .forEach((x) => x.classList.toggle('on', x === b));
    $('#tab-form').hidden = b.dataset.tab !== 'form';
    $('#tab-raw').hidden = b.dataset.tab !== 'raw';
    // the two views search differently
    hits = []; hitIdx = -1;
    if ($('#find').value.trim()) runFind(0); else { clearFind(); updateFindInfo(); }
  };
});

$('#btn-apply').onclick = () => {
  try {
    const parsed = JSON.parse($('#rawjson').value);
    if (current.kind === 'proto') current.data = parsed;
    else if (current.kind === 'text') current.text = $('#rawjson').value;
    $('#rawerr').textContent = '';
    render();
    setDirty(true);
  } catch (e) {
    $('#rawerr').textContent = e.message;
  }
};

let searchTimer = null;
$('#search').oninput = (e) => {
  clearTimeout(searchTimer);
  const t = e.target.value.trim();
  searchTimer = setTimeout(() => fileBrowser.search(t), 300);
};

window.addEventListener('beforeunload', (e) => {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

document.addEventListener('keydown', (e) => {
  if (document.getElementById('tab-data-editor').hidden) return;
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save(); }
  // Ctrl+F goes to the in-file search: the browser's cannot see collapsed nodes
  if ((e.ctrlKey || e.metaKey) && e.key === 'f' && current) {
    e.preventDefault();
    $('#find').focus();
    $('#find').select();
  }
});

})();
