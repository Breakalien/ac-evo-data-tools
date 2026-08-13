'use strict';
// Material editor tab - reimplements UACEC2's Qt material_page.py as a
// stateless web frontend against "material editor/routes.py". Own IIFE,
// own $/el/api/status helpers (not shared with the data editor tab's own
// app.js) so the two tabs stay fully independent.
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

function status(msg, cls) {
  const s = $('#me-status');
  s.textContent = msg || '';
  s.className = 'me-status' + (cls ? ' ' + cls : '');
}

function plural(n, word) {
  return n + ' ' + word + (n === 1 ? '' : 's');
}

const KIND_UNSET = 0, KIND_SCALAR = 1, KIND_VEC4 = 4;
const PREVIEW_BG_COLORS = { pink: '#ff5fd2', white: '#ffffff', black: '#000000' };
const ZOOM_LEVELS = ['fit', '25', '50', '75', '100'];

let state = null;       // full open-material payload from /api/material/open, held & edited in place
let constants = null;   // shaders / blend_mode_labels / kind_labels, loaded once at page load
let dirty = false;
let selectedProp = null;
let previewBg = 'pink';
let detailZoom = 'fit';

async function loadConstants() {
  try {
    constants = await api('/api/material/constants');
  } catch (e) {
    status('Could not load the shader list: ' + e.message, 'err');
    return;
  }
  fillShaderSelect(constants.shaders);
  fillBlendModeSelect(constants.blend_mode_labels);
  ['#me-shader', '#me-shader-select', '#me-blendmode', '#me-blendmode-select', '#me-preset-select'].forEach((s) => {
    $(s).disabled = false;
  });
}

function setDirty(v) {
  dirty = v;
  $('#me-btn-save').disabled = !v || !state;
}

function searchTokens(raw) {
  return raw.trim().toLowerCase().split(/\s+/).filter(Boolean);
}
function matchesTokens(hay, tokens) {
  if (!tokens.length) return true;
  const low = hay.toLowerCase();
  return tokens.every((t) => low.includes(t));
}

function fmtNum(v) {
  v = Number(v) || 0;
  if (Object.is(v, -0)) v = 0;
  let s = v.toPrecision(6);
  if (s.includes('.') && !s.includes('e')) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s;
}
function formatValue(p) {
  if (p.kind === KIND_UNSET) return 'disabled';
  const parts = [];
  for (let i = 1; i <= p.kind; i++) parts.push(fmtNum(p.components[String(i)] ?? 0));
  return parts.join(', ');
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

function thumbUrl(slotName, size) {
  return '/api/material/texture_thumb?material=' + encodeURIComponent(state.path) +
         '&slot=' + encodeURIComponent(slotName || '') + '&size=' + size;
}

/* ---------------------------------------------------------------- open / save */

async function openMaterial(path) {
  status('Loading…');
  let data;
  try {
    data = await api('/api/material/open?path=' + encodeURIComponent(path));
  } catch (e) { status(e.message, 'err'); return; }

  state = data;
  dirty = false;
  selectedProp = null;

  $('#me-welcome').hidden = true;
  $('#me-workspace').hidden = false;
  $('#me-filename').textContent = data.name + (data.backup ? ' (.bak present)' : '');
  ['#me-btn-save-as', '#me-search', '#me-btn-preset-load', '#me-btn-preset-save'].forEach((s) => {
    $(s).disabled = false;
  });
  setDirty(false);

  fillShaderSelect(data.shaders);
  fillBlendModeSelect(data.blend_mode_labels);
  syncShaderControl(data.shader_name);
  refreshBlendModeDisplay();
  refreshPresetSelect();
  renderTree();
  renderTextures();
  renderDetail();
  status('');
}

async function saveMaterial(path) {
  if (!state) return;
  const body = {
    path,
    shader_name: $('#me-shader').value.trim(),
    blend_mode: state.blend_mode,
    properties: state.properties,
    textures: state.textures,
    raw_items: state.raw_items,
  };
  status('Saving…');
  try {
    const res = await api('/api/material/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    state.path = res.path;
    state.dir = path.replace(/[\\/][^\\/]*$/, '');
    state.name = path.split(/[\\/]/).pop();
    state.backup = res.backup_created || state.backup;
    $('#me-filename').textContent = state.name + (state.backup ? ' (.bak present)' : '');
    setDirty(false);
    status('"' + state.name + '" saved' + (res.backup_created ? ' (.bak created)' : ''), 'ok');
  } catch (e) {
    status('Failed: ' + e.message, 'err');
  }
}

/* ---------------------------------------------------------------- blend mode */

const CUSTOM_OPTION = '__custom__';

function fillBlendModeSelect(labels) {
  const sel = $('#me-blendmode-select');
  sel.textContent = '';
  Object.entries(labels || {}).forEach(([value, label]) => {
    const o = el('option', null, label);
    o.value = value;
    sel.appendChild(o);
  });
  const custom = el('option', null, 'Other (custom value)…');
  custom.value = CUSTOM_OPTION;
  sel.appendChild(custom);
}

function refreshBlendModeDisplay() {
  if (!state) return;
  const label = state.blend_mode_labels[String(state.blend_mode)];
  $('#me-blendmode').value = label || String(state.blend_mode);
  const sel = $('#me-blendmode-select');
  if (label) {
    sel.value = String(state.blend_mode);
    $('#me-blendmode').hidden = true;
  } else {
    sel.value = CUSTOM_OPTION;
    $('#me-blendmode').hidden = false;
  }
}

function parseBlendModeValue(text) {
  const m = /^\s*(-?\d+)/.exec(text);
  return m ? parseInt(m[1], 10) : null;
}

function commitBlendMode() {
  if (!state) return;
  const value = parseBlendModeValue($('#me-blendmode').value);
  if (value === null) {
    alert('Enter a whole number for the blend mode.');
    refreshBlendModeDisplay();
    return;
  }
  if (value === state.blend_mode) { refreshBlendModeDisplay(); return; }
  state.blend_mode = value;
  if (value === state.blend_mode_opaque) {
    const prop = state.properties.find((p) => p.name === 'blendMode');
    if (prop) {
      prop.kind = KIND_SCALAR;
      prop.components = { '1': 0.0 };
      prop.value_display = formatValue(prop);
      refreshPropRow(prop);
    }
    status("Blend mode (hidden field) -> 0 (Opaque): the 'blendMode' property was also reset to 0.", 'ok');
  } else {
    status('Blend mode (hidden field) -> ' + value + ". No known fixed mapping: check/adjust 'blendMode' yourself if needed.");
  }
  refreshBlendModeDisplay();
  setDirty(true);
}

/* ---------------------------------------------------------------- shader / presets */

function fillShaderSelect(shaders) {
  const sel = $('#me-shader-select');
  sel.textContent = '';
  (shaders || []).forEach((s) => {
    const o = el('option', null, s);
    o.value = s;
    sel.appendChild(o);
  });
  const custom = el('option', null, 'Other (custom name)…');
  custom.value = CUSTOM_OPTION;
  sel.appendChild(custom);
}

function syncShaderControl(name) {
  const sel = $('#me-shader-select');
  const known = [...sel.options].some((o) => o.value === name);
  $('#me-shader').value = name;
  if (known) {
    sel.value = name;
    $('#me-shader').hidden = true;
  } else {
    sel.value = CUSTOM_OPTION;
    $('#me-shader').hidden = false;
  }
}

let shaderTimer = null;
async function onShaderChange() {
  const v = $('#me-shader').value.trim();
  refreshPresetSelect();
  if (!state) { status('Open a .material file first.', 'err'); return; }
  if (v) state.shader_name = v;
  setDirty(true);
  try {
    const data = await api('/api/material/shader_properties', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shader: v,
        existing: state.properties.map((p) => ({ name: p.name })),
        texture_names: state.textures.map((t) => t.name),
      }),
    });
    state.schema = data.schema;
    // Properties the file's raw data never declared, but that the new
    // shader's schema knows about, are added as fresh disabled entries -
    // otherwise a property only ever seen on OTHER shaders would stay
    // invisible forever, even with "show disabled properties" on.
    if (data.missing_properties && data.missing_properties.length) {
      state.properties.push(...data.missing_properties);
    }
  } catch (e) { /* keep previous schema/properties, tree still re-renders unfiltered below */ }

  // The properties that DO already exist never change with the shader label
  // - they're whatever is stored in the file. What DOES change is which of
  // them are actually known/relevant for this shader (from "material
  // editor/schema/<Shader>.json"): the tree re-renders to show only those
  // (plus any newly-added ones from above).
  renderTree();
  if (selectedProp) renderDetail();

  status(schemaIsKnown()
    ? 'Shader: ' + v + ' - property list updated for this shader.'
    : 'Shader: ' + (v || '(empty)') + " - schema unknown for this shader (never scanned), the list isn't filtered.");
}

async function refreshPresetSelect() {
  const sel = $('#me-preset-select');
  sel.textContent = '';
  const shader = $('#me-shader').value.trim();
  if (!shader) { $('#me-preset-hint').textContent = ''; return; }
  let data;
  try { data = await api('/api/material/presets?shader=' + encodeURIComponent(shader)); }
  catch (e) { return; }
  data.presets.forEach((name) => sel.appendChild(el('option', null, name)));
  $('#me-preset-hint').textContent = plural(data.presets.length, 'preset') + " for '" + shader + "'";
}

async function loadPreset(confirmMismatch) {
  if (!state) { alert('Open a .material file first.'); return; }
  const name = $('#me-preset-select').value;
  if (!name) { alert('Pick a preset from the list.'); return; }
  const shader = $('#me-shader').value.trim();
  const body = {
    shader, name,
    shader_name: state.shader_name,
    load_values: $('#me-opt-load-values').checked,
    load_textures: $('#me-opt-load-textures').checked,
    texture_mode: $('#me-texture-mode').value,
    properties: state.properties,
    textures: state.textures,
    blend_mode: state.blend_mode,
    path: state.path,
    confirm: confirmMismatch || false,
  };
  let res;
  try {
    res = await api('/api/material/preset/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) { status(e.message, 'err'); return; }

  if (!res.ok && res.shader_mismatch) {
    const ok = confirm(
      "This preset was saved for shader '" + res.saved_shader + "', the open file uses '" +
      state.shader_name + "'.\nOnly properties with the same name will be copied. Continue?");
    if (ok) return loadPreset(true);
    return;
  }

  state.properties = res.properties;
  state.textures = res.textures;
  state.blend_mode = res.blend_mode;
  selectedProp = null;
  refreshBlendModeDisplay();
  renderTree();
  renderTextures();
  renderDetail();
  setDirty(true);

  let msg = "Preset '" + name + "' loaded (" + (res.mode === 'values' ? 'type + values' : 'activation/deactivation only') +
    '): ' + plural(res.applied, 'property') + ' applied, ' + plural(res.skipped, 'property') + ' skipped.';
  if ($('#me-opt-load-textures').checked) {
    msg += res.tex_status === 'no_textures_in_preset'
      ? ' No textures in this preset.'
      : ' | Textures (' + $('#me-texture-mode').value + '): ' + plural(res.tex_applied, 'texture') + ' applied, ' +
        plural(res.tex_skipped, 'texture') + ' skipped.';
  }
  status(msg, 'ok');
}

async function savePresetAs() {
  if (!state) { alert('Open a .material file first.'); return; }
  const shader = $('#me-shader').value.trim();
  if (!shader) { alert('Enter a shader name before saving a preset.'); return; }
  const name = prompt("Preset name for shader '" + shader + "':");
  if (!name || !name.trim()) return;
  const body = {
    shader, name: name.trim(),
    save_textures: $('#me-opt-save-textures').checked,
    properties: state.properties, textures: state.textures, blend_mode: state.blend_mode,
  };
  let res;
  try {
    res = await api('/api/material/preset/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) { status(e.message, 'err'); return; }
  refreshPresetSelect();
  status('Preset saved' + (body.save_textures ? ' (with textures)' : '') + ': ' + res.path, 'ok');
}

/* ---------------------------------------------------------------- property tree */

// The schema (material editor/schema/<Shader>.json, built by scanning real
// game files) is the closest thing to "the list of properties this shader
// actually uses". schemaIsKnown() is false when the current shader has never
// been scanned (empty schema) - in that case we have no information to
// filter with, so every property is treated as in-schema.
function schemaIsKnown() {
  return !!(state && state.schema && Object.keys(state.schema).length > 0);
}
function isOutOfSchema(p) {
  if (!schemaIsKnown()) return false;
  return !Object.prototype.hasOwnProperty.call(state.schema, p.name);
}

function refreshPropRow(p) {
  const rows = document.querySelectorAll('#me-tree .me-prow');
  for (const row of rows) {
    if (row.dataset.name === p.name) {
      row.classList.toggle('unset', p.kind === KIND_UNSET);
      row.querySelector('.kind').textContent = state.kind_labels[String(p.kind)];
      row.querySelector('.val').textContent = p.value_display;
      break;
    }
  }
}

function buildPropRow(p) {
  const row = el('div', 'me-prow' + (p.kind === KIND_UNSET ? ' unset' : ''));
  row.dataset.name = p.name;
  const nm = el('span', 'nm', p.name);
  if (p.channel_color && p.kind !== KIND_UNSET) nm.style.color = p.channel_color;
  row.appendChild(nm);
  row.appendChild(el('span', 'kind', state.kind_labels[String(p.kind)]));
  row.appendChild(el('span', 'val', p.value_display));
  row.appendChild(el('span', 'lt' + (p.linked_texture_resolved ? ' resolved' : ''), p.linked_texture || ''));
  if (selectedProp === p) row.classList.add('sel');
  row.onclick = () => selectProp(p, row);
  return row;
}

function renderTree() {
  const box = $('#me-tree');
  box.textContent = '';
  if (!state) return;
  const tokens = searchTokens($('#me-search').value);
  const showUnset = $('#me-opt-show-unset').checked;
  const knownSchema = schemaIsKnown();

  const grouped = new Map(); // category -> Map(sub|'' -> [prop])
  for (const p of state.properties) {
    if (!showUnset && p.kind === KIND_UNSET) continue;
    // A property nobody has ever seen used for this shader isn't relevant
    // to it - hide it, whether it's currently active or not.
    if (knownSchema && isOutOfSchema(p)) continue;
    const hay = p.name + ' ' + p.category + ' ' + (p.sub || '') + ' ' + (p.linked_texture || '');
    if (!matchesTokens(hay, tokens)) continue;
    if (!grouped.has(p.category)) grouped.set(p.category, new Map());
    const subMap = grouped.get(p.category);
    const key = p.sub || '';
    if (!subMap.has(key)) subMap.set(key, []);
    subMap.get(key).push(p);
  }

  for (const top of state.category_order) {
    const subMap = grouped.get(top);
    if (!subMap) continue;

    const topDetails = el('details', 'me-node');
    if (tokens.length) topDetails.open = true;
    const topSummary = el('summary');
    topSummary.appendChild(el('span', null, top));
    const topCount = el('span', 'me-count');
    topSummary.appendChild(topCount);
    topDetails.appendChild(topSummary);
    const topKids = el('div', 'me-kids');
    topDetails.appendChild(topKids);

    let visibleCount = 0;
    const subKeys = [...subMap.keys()].sort((a, b) => (a === '' ? 1 : 0) - (b === '' ? 1 : 0));
    for (const subKey of subKeys) {
      const props = subMap.get(subKey).slice()
        .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      let parentKids = topKids;
      if (subKey) {
        const subDetails = el('details', 'me-node');
        if (tokens.length) subDetails.open = true;
        const subSummary = el('summary');
        subSummary.appendChild(el('span', null, subKey + '  (' + props.length + ')'));
        subDetails.appendChild(subSummary);
        const subKids = el('div', 'me-kids');
        subDetails.appendChild(subKids);
        topKids.appendChild(subDetails);
        parentKids = subKids;
      }
      props.forEach((p) => parentKids.appendChild(buildPropRow(p)));
      visibleCount += props.length;
    }
    topCount.textContent = '  (' + visibleCount + ')';
    box.appendChild(topDetails);
  }
}

/* ---------------------------------------------------------------- detail panel */

function selectProp(p, rowEl) {
  document.querySelectorAll('#me-tree .me-prow.sel').forEach((r) => r.classList.remove('sel'));
  if (rowEl) rowEl.classList.add('sel');
  selectedProp = p;
  renderDetail();
}

function renderDetail() {
  const box = $('#me-detail');
  box.textContent = '';
  const p = selectedProp;
  if (!state) return;
  if (!p) {
    box.appendChild(el('p', 'me-detail-name', 'Select a property...'));
    return;
  }

  box.appendChild(el('p', 'me-detail-name', p.name));

  const texP = el('p', 'me-detail-tex');
  texP.style.whiteSpace = 'pre-line';
  if (p.linked_texture) {
    const tex = state.textures.find((t) => t.name === p.linked_texture);
    texP.textContent = 'Linked texture: ' + p.linked_texture +
      (tex && tex.path ? '\n-> ' + tex.path : '\n(no texture assigned to this slot)');
  } else {
    texP.textContent = 'No associated texture (global setting / system without a dedicated slot)';
  }
  box.appendChild(texP);

  const kindRow = el('div', 'me-detail-row');
  kindRow.appendChild(el('label', null, 'Type:'));
  const kindSel = el('select');
  for (let k = 0; k <= 4; k++) {
    const o = el('option', null, state.kind_labels[String(k)]);
    o.value = String(k);
    if (k === p.kind) o.selected = true;
    kindSel.appendChild(o);
  }
  kindSel.onchange = () => onDetailKindChange(parseInt(kindSel.value, 10));
  kindRow.appendChild(kindSel);
  box.appendChild(kindRow);

  const valuesRow = el('div', 'me-detail-row');
  const entries = [];
  for (let i = 0; i < 4; i++) {
    const inp = el('input', 'me-comp-input');
    inp.type = 'text';
    if (i < p.kind) {
      inp.value = fmtNum(p.components[String(i + 1)] ?? 0);
    } else {
      inp.style.display = 'none';
    }
    inp.onchange = () => commitDetailComponent(i, inp);
    valuesRow.appendChild(inp);
    entries.push(inp);
  }
  if (p.kind === KIND_VEC4) {
    const colorBtn = el('input', 'me-color-btn');
    colorBtn.type = 'color';
    colorBtn.value = rgbToHex(p.components['1'] || 0, p.components['2'] || 0, p.components['3'] || 0);
    colorBtn.oninput = () => onColorPicked(colorBtn.value);
    valuesRow.appendChild(colorBtn);

    valuesRow.appendChild(el('span', null, 'A:'));
    const alphaSlider = el('input', 'me-alpha-slider');
    alphaSlider.type = 'range'; alphaSlider.min = '0'; alphaSlider.max = '1000';
    alphaSlider.value = String(Math.round((p.components['4'] || 0) * 1000));
    alphaSlider.oninput = () => onAlphaChanged(parseInt(alphaSlider.value, 10), entries[3]);
    valuesRow.appendChild(alphaSlider);
  }
  box.appendChild(valuesRow);

  const toggleBtn = el('button', null, '');
  toggleBtn.type = 'button';
  if (p.kind === KIND_UNSET) {
    const known = state.schema && Object.prototype.hasOwnProperty.call(state.schema, p.name);
    const schemaKind = known ? state.schema[p.name] : KIND_SCALAR;
    toggleBtn.textContent = 'Enable (guessed type: ' + state.kind_labels[String(schemaKind)] + ')';
    if (!known) {
      box.appendChild(el('p', 'me-guess-hint',
        "Type unknown in the schema -> defaults to scalar, check/correct with 'Type' above."));
    }
  } else {
    toggleBtn.textContent = 'Disable this property';
  }
  toggleBtn.onclick = () => toggleSelected();
  box.appendChild(toggleBtn);

  box.appendChild(el('p', 'me-help',
    'The type (scalar/vec2/vec3/vec4) is guessed automatically on activation, based on other ' +
    "materials using the same shader. The 'Type' menu lets you correct it manually if needed."));

  if (p.linked_texture) {
    const zoomRow = el('div', 'me-detail-row');
    zoomRow.appendChild(el('label', null, 'Zoom:'));
    const zoomSel = el('select');
    ZOOM_LEVELS.forEach((z) => {
      const o = el('option', null, z === 'fit' ? 'Fit' : z + '%');
      o.value = z;
      if (z === detailZoom) o.selected = true;
      zoomSel.appendChild(o);
    });
    zoomSel.onchange = () => { detailZoom = zoomSel.value; renderDetail(); };
    zoomRow.appendChild(zoomSel);
    box.appendChild(zoomRow);

    const wrap = el('div', 'me-thumb-wrap');
    wrap.style.background = PREVIEW_BG_COLORS[previewBg];
    const img = el('img');
    img.alt = p.linked_texture;
    img.onload = () => {
      if (detailZoom === 'fit') {
        img.style.maxWidth = '100%'; img.style.maxHeight = '512px'; img.style.width = '';
      } else {
        const pct = parseInt(detailZoom, 10) / 100;
        img.style.maxWidth = 'none'; img.style.maxHeight = 'none';
        img.style.width = Math.round(img.naturalWidth * pct) + 'px';
      }
    };
    img.src = thumbUrl(p.linked_texture, 1024);
    wrap.appendChild(img);
    box.appendChild(wrap);
  }
}

function onDetailKindChange(newKind) {
  const p = selectedProp;
  if (!p) return;
  if (newKind !== KIND_UNSET && p.kind === KIND_UNSET) {
    p.components = {};
    for (let i = 1; i <= newKind; i++) p.components[String(i)] = 0.0;
  } else if (newKind === KIND_UNSET) {
    p.components = {};
  }
  p.kind = newKind;
  p.value_display = formatValue(p);
  refreshPropRow(p);
  setDirty(true);
  renderDetail();
}

function commitDetailComponent(idx, inputEl) {
  const p = selectedProp;
  if (!p || idx >= p.kind) return;
  const text = inputEl.value.trim().replace(',', '.');
  const val = parseFloat(text);
  if (Number.isNaN(val)) { inputEl.style.borderColor = 'var(--err)'; return; }
  inputEl.style.borderColor = '';
  p.components[String(idx + 1)] = val;
  p.value_display = formatValue(p);
  refreshPropRow(p);
  setDirty(true);
  if (p.kind === KIND_VEC4) {
    if (idx === 3) {
      const slider = document.querySelector('#me-detail .me-alpha-slider');
      if (slider) slider.value = String(Math.round(Math.max(0, Math.min(1, val)) * 1000));
    } else {
      const colorInput = document.querySelector('#me-detail .me-color-btn');
      if (colorInput) colorInput.value = rgbToHex(p.components['1'] || 0, p.components['2'] || 0, p.components['3'] || 0);
    }
  }
}

function onColorPicked(hex) {
  const p = selectedProp;
  if (!p || p.kind !== KIND_VEC4) return;
  const [r, g, b] = hexToRgbF(hex);
  p.components['1'] = round4(r); p.components['2'] = round4(g); p.components['3'] = round4(b);
  p.value_display = formatValue(p);
  refreshPropRow(p);
  setDirty(true);
  const inputs = document.querySelectorAll('#me-detail .me-comp-input');
  if (inputs[0]) inputs[0].value = fmtNum(p.components['1']);
  if (inputs[1]) inputs[1].value = fmtNum(p.components['2']);
  if (inputs[2]) inputs[2].value = fmtNum(p.components['3']);
}

function onAlphaChanged(rawValue, entryInput) {
  const p = selectedProp;
  if (!p || p.kind !== KIND_VEC4) return;
  const alpha = rawValue / 1000;
  p.components['4'] = alpha;
  p.value_display = formatValue(p);
  refreshPropRow(p);
  setDirty(true);
  if (entryInput) entryInput.value = fmtNum(alpha);
}

function toggleSelected() {
  const p = selectedProp;
  if (!p) return;
  let newKind;
  if (p.kind === KIND_UNSET) {
    const known = state.schema && Object.prototype.hasOwnProperty.call(state.schema, p.name);
    newKind = known ? state.schema[p.name] : KIND_SCALAR;
  } else {
    newKind = KIND_UNSET;
  }
  onDetailKindChange(newKind);
}

/* ---------------------------------------------------------------- textures tab */

function buildTexRow(t) {
  const row = el('div', 'me-tex-row');
  const img = el('img', 'me-tex-thumb');
  img.style.background = PREVIEW_BG_COLORS[previewBg];
  img.alt = t.name;
  img.src = t.path ? thumbUrl(t.name, 64) : thumbUrl('', 64);
  row.appendChild(img);

  const name = el('span', 'me-tex-name', t.name);
  if (t.channel_color) name.style.color = t.channel_color;
  row.appendChild(name);

  const input = el('input', 'me-tex-path');
  input.type = 'text';
  input.value = t.path || '';
  input.onchange = () => {
    t.path = input.value.trim() || null;
    setDirty(true);
  };
  row.appendChild(input);

  const clearBtn = el('button', 'me-tex-clear', 'Clear');
  clearBtn.type = 'button';
  clearBtn.onclick = () => { input.value = ''; t.path = null; setDirty(true); };
  row.appendChild(clearBtn);

  return row;
}

function renderTextures() {
  const box = $('#me-tex-list');
  box.textContent = '';
  if (!state) return;
  const tokens = searchTokens($('#me-search').value);
  const showUnassigned = $('#me-opt-show-unassigned').checked;
  const sorted = state.textures.slice().sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  for (const t of sorted) {
    if (!showUnassigned && !t.path) continue;
    if (!matchesTokens(t.name + ' ' + (t.path || ''), tokens)) continue;
    box.appendChild(buildTexRow(t));
  }
}

/* ---------------------------------------------------------------- file browsing */
// The sidebar (navbar/bookmarks/breadcrumbs/listing) is the same shared
// component the data editor tab uses - only which files are selectable and
// what happens on pick differ. Reuses /api/data/ls: material editor has no
// filesystem-browsing endpoints of its own, no need to duplicate them.

function classifyMaterialFile(f) {
  return { selectable: f.name.toLowerCase().endsWith('.material') };
}

const materialBrowser = AceFileBrowser.mountSidebar($('#me-sidebar'), {
  apiBase: '/api/data',
  showSearch: true,
  searchPlaceholder: 'Search for a .material file…',
  classify: classifyMaterialFile,
  confirmUnselectable: true,
  startPath: () => (state && state.dir) || '',
  onPick: (entry) => {
    if (dirty && !confirm('The open file has unsaved changes.\n\nDiscard them and open "' + entry.name + '"?')) return;
    openMaterial(entry.path);
  },
});

AceResizable.makeSplitter($('#me-sidebar'), { min: 220, max: 700, storageKey: 'ace_me_sidebar_w' });
AceResizable.makeSplitter($('.me-tree-col'), { min: 260, max: 1200, storageKey: 'ace_me_tree_w' });

/* ---------------------------------------------------------------- sub-tabs & events */

document.querySelectorAll('.me-subtabs button[data-me-tab]').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('.me-subtabs button[data-me-tab]').forEach((x) => x.classList.toggle('on', x === b));
    $('#me-pane-props').hidden = b.dataset.meTab !== 'props';
    $('#me-pane-textures').hidden = b.dataset.meTab !== 'textures';
  };
});

$('#me-btn-save').onclick = () => { if (state) saveMaterial(state.path); };
$('#me-btn-save-as').onclick = () => {
  if (!state) return;
  AceFileBrowser.openModal({
    mode: 'save-file', title: 'Save as', apiBase: '/api/data',
    defaultName: state.name || 'skin.material',
    classify: classifyMaterialFile,
    startPath: () => state.dir || '',
    onPick: (entry) => saveMaterial(entry.path),
  });
};

$('#me-shader').oninput = () => { clearTimeout(shaderTimer); shaderTimer = setTimeout(onShaderChange, 300); };
$('#me-shader-select').onchange = () => {
  const v = $('#me-shader-select').value;
  if (v === CUSTOM_OPTION) {
    $('#me-shader').hidden = false;
    $('#me-shader').focus();
    $('#me-shader').select();
    return;
  }
  $('#me-shader').hidden = true;
  $('#me-shader').value = v;
  onShaderChange();
};
$('#me-blendmode').onchange = commitBlendMode;
$('#me-blendmode-select').onchange = () => {
  const sel = $('#me-blendmode-select');
  if (sel.value === CUSTOM_OPTION) {
    $('#me-blendmode').hidden = false;
    $('#me-blendmode').focus();
    $('#me-blendmode').select();
    return;
  }
  $('#me-blendmode').hidden = true;
  $('#me-blendmode').value = sel.selectedOptions[0].textContent;
  commitBlendMode();
};
$('#me-btn-preset-load').onclick = () => loadPreset(false);
$('#me-btn-preset-save').onclick = () => savePresetAs();

let searchTimer = null;
$('#me-search').oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { renderTree(); renderTextures(); }, 200);
};
$('#me-opt-show-unset').onchange = renderTree;
$('#me-opt-show-unassigned').onchange = renderTextures;
$('#me-bg-select').onchange = () => {
  previewBg = $('#me-bg-select').value;
  renderTextures();
  if (selectedProp) renderDetail();
};

window.addEventListener('beforeunload', (e) => {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

document.addEventListener('keydown', (e) => {
  const panel = document.getElementById('tab-material-editor');
  if (!panel || panel.hidden) return;
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    if (state && !$('#me-btn-save').disabled) saveMaterial(state.path);
  }
});

loadConstants();

})();
