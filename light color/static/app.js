'use strict';
// Light Color tab - batch-edit a fixed set of light-color properties across
// a whole car folder at once: the vec4 light-color properties of every
// "materials/**/*_FIXED.material" file, and the vec3 emitter colors declared
// in the car's ".actor" file. Own IIFE, own helpers, same pattern as the
// other tab modules.
(function () {

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

function scanStatus(msg, cls) {
  const s = $('#lc-scan-status');
  s.textContent = msg || '';
  s.className = 'lc-status' + (cls ? ' ' + cls : '');
}
function saveStatus(msg, cls) {
  const s = $('#lc-save-status');
  s.textContent = msg || '';
  s.className = 'lc-status' + (cls ? ' ' + cls : '');
}

function round4(v) { return Math.round(v * 10000) / 10000; }
function rgbToHex(r, g, b) {
  const c = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, '0');
  return '#' + c(r) + c(g) + c(b);
}
function hexToRgbF(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
function colorsEqual(a, b) {
  return Math.abs(a.r - b.r) < 1e-4 && Math.abs(a.g - b.g) < 1e-4 && Math.abs(a.b - b.b) < 1e-4;
}

/* ---------------------------------------------------------------- material fields */

const GROUPS = [
  { title: 'Front', fields: ['DaylightColor', 'LowBeamColor', 'HighBeamColor'] },
  { title: 'Back', fields: ['RearLightColor', 'BrakeLightColor', 'ReverseLightColor'] },
  { title: 'Indicator', fields: ['FrontIndicatorColor', 'RearIndicatorColor'] },
  { title: 'Special', fields: ['LFSpecialLightColor', 'RFSpecialLightColor', 'LRSpecialLightColor', 'RRSpecialLightColor'] },
];
const ALL_FIELDS = GROUPS.flatMap((g) => g.fields);

let currentDir = '';
let materialFiles = [];
let values = {};        // fieldName -> {1:r, 2:g, 3:b, 4:a}
let fieldSync = {};     // fieldName -> () => refresh its row's controls from `values`

let actorPath = null;
let emitters = [];             // [{index, debug_name, color:{r,g,b}}] as scanned
let emitterCurrent = {};       // index -> {r,g,b} (live, edited)
let emitterTouched = {};       // index -> bool
let emitterSync = {};          // index -> () => refresh its row from emitterCurrent/touched

function defaultComponents() {
  return { 1: 1, 2: 1, 3: 1, 4: 1 };
}

function buildFieldRow(name) {
  const row = el('div', 'lc-row');
  row.appendChild(el('span', 'lc-field-name', name));

  const colorInput = el('input', 'lc-color');
  colorInput.type = 'color';

  // Alpha stays whatever it was scanned as (or the 1 default for a field
  // that didn't exist yet) - no control for it here, it was never actually
  // used for these light-color fields in practice.
  function sync() {
    const c = values[name];
    colorInput.value = rgbToHex(c[1] || 0, c[2] || 0, c[3] || 0);
  }
  colorInput.oninput = () => {
    const [r, g, b] = hexToRgbF(colorInput.value);
    values[name][1] = round4(r); values[name][2] = round4(g); values[name][3] = round4(b);
  };

  fieldSync[name] = sync;
  sync();

  row.append(colorInput);
  return row;
}

function buildGroups() {
  const box = $('#lc-groups');
  box.textContent = '';
  GROUPS.forEach((g) => {
    const section = el('div', 'lc-group');
    section.appendChild(el('h3', null, g.title));
    g.fields.forEach((name) => section.appendChild(buildFieldRow(name)));
    box.appendChild(section);
  });
}

function renderFilesList() {
  $('#lc-files-summary').textContent = materialFiles.length + (materialFiles.length === 1 ? ' file' : ' files');
  const ul = $('#lc-files-ul');
  ul.textContent = '';
  materialFiles.forEach((f) => ul.appendChild(el('li', null, f)));
}

/* ---------------------------------------------------------------- emitters (.actor) */

function buildEmitterRow(light) {
  const row = el('div', 'lc-emitter-row');
  row.appendChild(el('span', 'lc-emitter-name', light.debug_name));

  const colorInput = el('input', 'lc-color');
  colorInput.type = 'color';
  const badge = el('span', 'lc-emitter-touched-badge', 'modified');
  const resetBtn = el('button', 'lc-emitter-reset', 'Reset');
  resetBtn.type = 'button';

  function sync() {
    const c = emitterCurrent[light.index];
    colorInput.value = rgbToHex(c.r, c.g, c.b);
    const touched = !!emitterTouched[light.index];
    row.classList.toggle('touched', touched);
    badge.hidden = !touched;
    resetBtn.hidden = !touched;
  }
  colorInput.oninput = () => {
    const [r, g, b] = hexToRgbF(colorInput.value);
    emitterCurrent[light.index] = { r: round4(r), g: round4(g), b: round4(b) };
    emitterTouched[light.index] = !colorsEqual(emitterCurrent[light.index], light.color);
    sync();
  };
  resetBtn.onclick = () => {
    emitterCurrent[light.index] = { ...light.color };
    emitterTouched[light.index] = false;
    sync();
  };

  emitterSync[light.index] = sync;
  sync();

  row.append(colorInput, badge, resetBtn);
  return row;
}

function buildEmitters() {
  const box = $('#lc-emitters');
  box.textContent = '';
  emitterCurrent = {}; emitterTouched = {}; emitterSync = {};
  emitters.forEach((light) => {
    emitterCurrent[light.index] = { ...light.color };
    emitterTouched[light.index] = false;
    box.appendChild(buildEmitterRow(light));
  });
  $('#lc-emitters-group').hidden = emitters.length === 0;
  const hint = $('#lc-emitters-hint');
  hint.textContent = actorPath ? actorPath.split(/[\\/]/).pop() : '';
  hint.title = actorPath || '';
}

/* ---------------------------------------------------------------- scan */

async function scan(dir) {
  currentDir = dir;
  $('#lc-dir').value = dir;
  $('#lc-btn-rescan').disabled = false;
  scanStatus('Scanning…');
  $('#lc-log').hidden = true;

  const [matResult, actorResult] = await Promise.allSettled([
    api('/api/light_color/materials/scan?dir=' + encodeURIComponent(dir)),
    api('/api/light_color/actor/scan?dir=' + encodeURIComponent(dir)),
  ]);

  let msg = '';
  if (matResult.status === 'fulfilled') {
    const res = matResult.value;
    materialFiles = res.files;
    renderFilesList();
    ALL_FIELDS.forEach((name) => {
      const found = res.values[name];
      values[name] = found
        ? { 1: found['1'] || 0, 2: found['2'] || 0, 3: found['3'] || 0, 4: found['4'] ?? 1 }
        : defaultComponents();
      if (fieldSync[name]) fieldSync[name]();
    });
    msg += materialFiles.length + (materialFiles.length === 1 ? ' material file. ' : ' material files. ');
  } else {
    materialFiles = [];
    renderFilesList();
    msg += 'Materials: ' + matResult.reason.message + '. ';
  }

  if (actorResult.status === 'fulfilled') {
    const res = actorResult.value;
    actorPath = res.path;
    emitters = res.lights;
    buildEmitters();
    msg += actorPath ? emitters.length + ' emitter(s) in ' + actorPath.split(/[\\/]/).pop() + '.'
                      : 'No .actor file found at the root of this folder.';
  } else {
    actorPath = null;
    emitters = [];
    buildEmitters();
    msg += 'Emitters: ' + actorResult.reason.message + '.';
  }

  const hasAnything = materialFiles.length > 0 || emitters.length > 0;
  $('#lc-body').hidden = !hasAnything;
  scanStatus(msg, hasAnything ? 'ok' : 'warn');
}

$('#lc-btn-browse').onclick = () => {
  AceFileBrowser.openModal({
    mode: 'pick-folder', title: "Choose a car's root folder", apiBase: '/api/data',
    startPath: () => currentDir,
    onPick: (entry) => scan(entry.path),
  });
};

$('#lc-btn-rescan').onclick = () => { if (currentDir) scan(currentDir); };

/* ---------------------------------------------------------------- save */

$('#lc-btn-save').onclick = async () => {
  const touchedLights = emitters.filter((l) => emitterTouched[l.index]);
  if (!materialFiles.length && !touchedLights.length) return;

  const parts = [];
  if (materialFiles.length) parts.push(materialFiles.length + ' material file(s)');
  if (touchedLights.length) parts.push(touchedLights.length + ' emitter color(s)');
  const ok = confirm(
    'Apply changes to ' + parts.join(' and ') + '?\n\n' +
    'A .bak backup is created for any file that does not already have one.');
  if (!ok) return;

  $('#lc-btn-save').disabled = true;
  saveStatus('Applying…');
  $('#lc-log').hidden = true;
  const logLines = [];
  let anyFail = false;

  if (materialFiles.length) {
    try {
      const res = await api('/api/light_color/materials/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dir: currentDir, values }),
      });
      logLines.push('-- materials: ' + res.n_updated + ' / ' + res.n_files + ' updated --');
      res.results.forEach((r) => {
        logLines.push(r.ok ? '[ok] ' + r.path + (r.backup_created ? ' (.bak created)' : '')
                            : '[FAILED] ' + r.path + ' - ' + r.error);
        if (!r.ok) anyFail = true;
      });
    } catch (e) {
      logLines.push('-- materials: FAILED - ' + e.message + ' --');
      anyFail = true;
    }
  }

  if (touchedLights.length) {
    try {
      const res = await api('/api/light_color/actor/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dir: currentDir,
          lights: touchedLights.map((l) => ({ index: l.index, ...emitterCurrent[l.index] })),
        }),
      });
      logLines.push('-- emitters: ' + res.n_lights_changed + ' changed in ' + res.path +
        (res.backup_created ? ' (.bak created)' : '') + ' --');
      // scanned values now match what's on disk - clear the "touched" state.
      touchedLights.forEach((l) => {
        l.color = { ...emitterCurrent[l.index] };
        emitterTouched[l.index] = false;
        if (emitterSync[l.index]) emitterSync[l.index]();
      });
    } catch (e) {
      logLines.push('-- emitters: FAILED - ' + e.message + ' --');
      anyFail = true;
    }
  }

  saveStatus(anyFail ? 'Done, with errors - see log below.' : 'Done.', anyFail ? 'warn' : 'ok');
  $('#lc-log').hidden = false;
  $('#lc-log').textContent = logLines.join('\n');
  $('#lc-btn-save').disabled = false;
};

ALL_FIELDS.forEach((name) => { values[name] = defaultComponents(); });
buildGroups();

})();
