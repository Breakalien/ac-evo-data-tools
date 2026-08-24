'use strict';
// Field Editor tab - add/remove fields on a protobuf content file, at any
// nesting depth, without touching any value. Deliberately does NOT duplicate
// Data Editor's open/save logic: it calls the exact same /api/data/open and
// /api/data/save routes (both already work on the generic {path, data} tree
// acevo_pb produces), and only adds its own /api/field-editor/addable
// lookup. Sync with Data Editor is manual (its own "Reload" button already
// picks up a field added here) - see the "field editor" project discussion.
//
// A message-typed field is rendered as a lazily-expandable node: its own
// children (and its own "add field" picker, resolved for ITS type via a
// path of field numbers from the root - see field editor/routes.py) are
// only built the first time it's opened, so an unopened branch costs
// nothing. Every mutation (add/remove field or array item) re-renders only
// the one node it happened in, in place - ancestor/sibling <details> stay
// exactly as the user left them.
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
  const s = $('#fe-status');
  s.textContent = msg || '';
  s.className = 'fe-status' + (cls ? ' ' + cls : '');
}

let state = null;   // full /api/data/open payload for the open file
let dirty = false;

function setDirty(v) {
  dirty = v;
  $('#fe-btn-save').disabled = !v || !state;
}

/* ---------------------------------------------------------------- node helpers */
// Two shapes coming out of acevo_pb.decode_message: the normal case is a flat
// {"N:type[:name]": value} dict; when the same field number occurs at
// non-contiguous positions it falls back to {"_seq": [{f,t,v,n?,e?}, ...]} to
// preserve exact order. Both need to work at every nesting level, not just
// the root - fieldsOf() normalises either shape into one list, each entry
// carrying its own remove() closure so callers never need to know which
// shape they're holding.

function fieldsOf(obj) {
  if (!obj) return [];
  if (Object.prototype.hasOwnProperty.call(obj, '_seq')) {
    return obj._seq.map((it) => ({
      number: it.f, name: it.n || null, type: it.t, value: it.v,
      remove: () => {
        const idx = obj._seq.indexOf(it);
        if (idx >= 0) obj._seq.splice(idx, 1);
      },
    }));
  }
  return Object.keys(obj).map((key) => ({
    number: parseInt(key.split(':')[0], 10),
    name: key.split(':')[2] || null,
    type: key.split(':')[1],
    value: obj[key],
    remove: () => { delete obj[key]; },
  }));
}

function addFieldTo(obj, item) {
  if (Object.prototype.hasOwnProperty.call(obj, '_seq')) {
    const parts = item.key.split(':'); // "N:type:name"
    const v = Array.isArray(item.default) ? item.default[0] : item.default;
    obj._seq.push({ f: item.number, t: parts[1], v, n: item.name });
  } else {
    // deep copy: several "add field" clicks must not share one mutable object/array
    obj[item.key] = JSON.parse(JSON.stringify(item.default));
  }
}

function fieldLabel(f) {
  const reserved = f.name === '?reserved';
  return el('span', 'fe-name' + (reserved ? ' reserved' : (f.name ? '' : ' unknown')),
    reserved ? 'reserved' : (f.name || '(unknown)'));
}

function confirmRemove(f, extra) {
  return confirm('Remove field #' + f.number +
    (f.name && f.name !== '?reserved' ? ' (' + f.name + ')' : '') +
    (extra || '') + '?');
}

/* ---------------------------------------------------------------- rendering */
// One entry point, renderNodeFields(container, obj, path), (re)builds the
// field list + its own "add field" picker for ONE message node, in place.
// It's the only function that ever clears/rebuilds a container, so a
// mutation deep in the tree only ever touches its own node's DOM.

function buildLeafField(f, onChanged) {
  const row = el('div', 'fe-row');
  row.appendChild(el('span', 'fe-num', '#' + f.number));
  row.appendChild(fieldLabel(f));
  row.appendChild(el('span', 'fe-type', f.type));
  const rm = el('button', 'fe-remove', 'Remove');
  rm.type = 'button';
  rm.onclick = () => {
    if (!confirmRemove(f)) return;
    f.remove();
    setDirty(true);
    onChanged();
  };
  row.appendChild(rm);
  return row;
}

function buildMessageField(f, parentPath, onChanged) {
  const details = el('details', 'fe-node-details');
  const summary = el('summary');
  summary.appendChild(el('span', 'fe-num', '#' + f.number));
  summary.appendChild(fieldLabel(f));
  summary.appendChild(el('span', 'fe-type', 'msg'));

  const rm = el('button', 'fe-remove', 'Remove');
  rm.type = 'button';
  rm.onclick = (e) => {
    e.preventDefault(); // clicking inside <summary> would otherwise also toggle it
    if (!confirmRemove(f)) return;
    f.remove();
    setDirty(true);
    onChanged();
  };
  summary.appendChild(rm);
  details.appendChild(summary);

  const body = el('div', 'fe-node-body');
  details.appendChild(body);

  const path = parentPath.concat(f.number);
  let built = false;
  details.addEventListener('toggle', () => {
    if (details.open && !built) {
      built = true;
      renderNodeFields(body, f.value, path);
    }
  });

  return details;
}

function buildArrayItem(item, idx, arrayField, parentPath, onArrayChanged) {
  const details = el('details', 'fe-item');
  const summary = el('summary', null, 'item ' + idx);
  const rm = el('button', 'fe-remove', 'Remove item');
  rm.type = 'button';
  rm.onclick = (e) => {
    e.preventDefault();
    if (!confirm('Remove item ' + idx + '?')) return;
    const i = arrayField.value.indexOf(item);
    if (i >= 0) arrayField.value.splice(i, 1);
    setDirty(true);
    onArrayChanged();
  };
  summary.appendChild(rm);
  details.appendChild(summary);

  const body = el('div', 'fe-node-body');
  details.appendChild(body);

  const path = parentPath.concat(arrayField.number);
  let built = false;
  details.addEventListener('toggle', () => {
    if (details.open && !built) {
      built = true;
      renderNodeFields(body, item, path);
    }
  });

  return details;
}

function buildArrayField(f, parentPath, onChanged) {
  const wrap = el('div', 'fe-node fe-array-node');
  const header = el('div', 'fe-row');
  header.appendChild(el('span', 'fe-num', '#' + f.number));
  header.appendChild(fieldLabel(f));
  const typeSpan = el('span', 'fe-type', 'repeated msg (' + f.value.length + ')');
  header.appendChild(typeSpan);

  const addItemBtn = el('button', null, '+ Add item');
  addItemBtn.type = 'button';
  header.appendChild(addItemBtn);

  const rm = el('button', 'fe-remove', 'Remove field');
  rm.type = 'button';
  rm.onclick = () => {
    if (!confirmRemove(f, ' (all ' + f.value.length + ' item(s))')) return;
    f.remove();
    setDirty(true);
    onChanged();
  };
  header.appendChild(rm);
  wrap.appendChild(header);

  const itemsBox = el('div', 'fe-items');
  wrap.appendChild(itemsBox);

  function renderItems() {
    itemsBox.textContent = '';
    f.value.forEach((item, idx) => {
      itemsBox.appendChild(buildArrayItem(item, idx, f, parentPath, renderItems));
    });
    typeSpan.textContent = 'repeated msg (' + f.value.length + ')';
  }

  addItemBtn.onclick = () => {
    f.value.push({});
    setDirty(true);
    renderItems();
  };

  renderItems();
  return wrap;
}

function buildAddRow(obj, path, onChanged) {
  const row = el('div', 'fe-addbar');
  const sel = el('select');
  sel.disabled = true;
  const btn = el('button', null, 'Add field');
  btn.type = 'button';
  btn.disabled = true;
  row.appendChild(sel);
  row.appendChild(btn);

  const present = fieldsOf(obj).map((f) => f.number).join(',');
  const url = '/api/field-editor/addable?message=' + encodeURIComponent(state.message) +
    '&path=' + encodeURIComponent(path.join(',')) + '&present=' + encodeURIComponent(present);

  api(url).then((data) => {
    if (!data.addable.length) {
      sel.appendChild(el('option', null, '(nothing left to add)'));
      return;
    }
    data.addable.forEach((item) => {
      const o = el('option', null, item.name + '  (field ' + item.number + ', ' +
        (Array.isArray(item.default) ? 'repeated ' : '') + item.key.split(':')[1] + ')');
      o.value = JSON.stringify(item);
      sel.appendChild(o);
    });
    sel.disabled = false;
    btn.disabled = false;
  }).catch(() => { sel.appendChild(el('option', null, '(unavailable)')); });

  btn.onclick = () => {
    if (!sel.value) return;
    addFieldTo(obj, JSON.parse(sel.value));
    setDirty(true);
    onChanged();
  };

  return row;
}

function renderNodeFields(container, obj, path) {
  container.textContent = '';
  const fields = fieldsOf(obj).sort((a, b) => a.number - b.number);
  if (!fields.length) {
    container.appendChild(el('p', 'fe-empty', 'No fields.'));
  }
  fields.forEach((f) => {
    const onChanged = () => renderNodeFields(container, obj, path);
    if (f.type === 'msg' && Array.isArray(f.value)) {
      container.appendChild(buildArrayField(f, path, onChanged));
    } else if (f.type === 'msg') {
      container.appendChild(buildMessageField(f, path, onChanged));
    } else {
      container.appendChild(buildLeafField(f, onChanged));
    }
  });
  container.appendChild(buildAddRow(obj, path, () => renderNodeFields(container, obj, path)));
}

function renderFields() {
  const box = $('#fe-fields');
  box.textContent = '';
  if (!state) return;
  renderNodeFields(box, state.data, []);
}

/* ---------------------------------------------------------------- open / save */

async function openFile(path) {
  status('Loading…');
  let data;
  try {
    data = await api('/api/data/open?path=' + encodeURIComponent(path));
  } catch (e) { status(e.message, 'err'); return; }

  state = data;
  setDirty(false);

  $('#fe-welcome').hidden = true;
  $('#fe-workspace').hidden = false;
  $('#fe-filename').textContent = data.name;

  if (data.kind !== 'proto') {
    $('#fe-unsupported').hidden = false;
    $('#fe-fields-wrap').hidden = true;
    $('#fe-btn-remove-reserved').disabled = true;
    $('#fe-btn-remove-empty').disabled = true;
    status('');
    return;
  }
  $('#fe-unsupported').hidden = true;
  $('#fe-fields-wrap').hidden = false;
  $('#fe-btn-remove-reserved').disabled = false;
  $('#fe-btn-remove-empty').disabled = false;
  $('#fe-message').textContent = data.message
    ? 'message: ' + data.message
    : 'message type unknown (schema not extracted, or unrecognised extension) - nothing can be added, only removed.';

  renderFields();
  status('');
}

async function saveFile() {
  if (!state) return;
  status('Saving…');
  try {
    const res = await api('/api/data/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: state.path, data: state.data }),
    });
    state.backup = res.backup_created || state.backup;
    setDirty(false);
    status('"' + state.name + '" saved' + (res.backup_created ? ' (.bak created)' : '') +
      ' - reload this file in Data Editor to see the change there.', 'ok');
  } catch (e) {
    status('Failed: ' + e.message, 'err');
  }
}

/* ---------------------------------------------------------------- bulk cleanup */
// "Remove reserved" and "Remove empty" walk the WHOLE tree (every nesting
// depth, not just the level currently expanded in the UI) - reserved fields
// in particular can be hiding several levels down, same as in Data Editor's
// Hide reserved/Hide empty toggles this mirrors. Unlike those, this actually
// deletes rather than just hiding, so both ask for confirmation first.

function removeReservedRecursive(obj) {
  let count = 0;
  fieldsOf(obj).forEach((f) => {
    if (f.name === '?reserved') {
      f.remove();
      count += 1;
      return; // its whole subtree goes with it, nothing left to recurse into
    }
    if (f.type === 'msg') {
      const items = Array.isArray(f.value) ? f.value : [f.value];
      items.forEach((item) => { count += removeReservedRecursive(item); });
    }
  });
  return count;
}

function removeEmptyRecursive(obj) {
  let count = 0;
  fieldsOf(obj).forEach((f) => {
    if (f.type !== 'msg') return;
    if (Array.isArray(f.value)) {
      // Repeated field: clean each item's own contents, but never drop an
      // item just because it ends up empty - that changes the array's
      // length/count, a different (and riskier) kind of edit than this
      // button is for.
      f.value.forEach((item) => { count += removeEmptyRecursive(item); });
    } else {
      count += removeEmptyRecursive(f.value);
      const empty = Object.prototype.hasOwnProperty.call(f.value, '_seq')
        ? f.value._seq.length === 0
        : Object.keys(f.value).length === 0;
      if (empty) {
        f.remove();
        count += 1;
      }
    }
  });
  return count;
}

$('#fe-btn-remove-reserved').onclick = () => {
  if (!state) return;
  const ok = confirm(
    "Remove every reserved field, anywhere in this file?\n\n" +
    "These are field numbers the game's own .proto has deleted - in theory " +
    "unused, but deleting them could still break the game if that turns out " +
    "not to be true for one of them. A .bak backup of the original file is " +
    "made on first save, so you can always put it back."
  );
  if (!ok) return;
  const n = removeReservedRecursive(state.data);
  if (!n) { status('No reserved fields found.', 'ok'); return; }
  setDirty(true);
  renderFields();
  status('Removed ' + n + ' reserved field' + (n === 1 ? '' : 's') + '.', 'ok');
};

$('#fe-btn-remove-empty').onclick = () => {
  if (!state) return;
  const ok = confirm(
    'Remove every empty message field, anywhere in this file?\n\n' +
    "Fields with content, and items inside repeated/array fields, are left alone."
  );
  if (!ok) return;
  const n = removeEmptyRecursive(state.data);
  if (!n) { status('No empty fields found.', 'ok'); return; }
  setDirty(true);
  renderFields();
  status('Removed ' + n + ' empty field' + (n === 1 ? '' : 's') + '.', 'ok');
};

$('#fe-btn-save').onclick = saveFile;

/* ---------------------------------------------------------------- clean all datas (whole folder) */
// Same reserved/empty rules as the two buttons above, but server-side and
// applied to every supported content file under a chosen folder, not just
// the one open above - see field editor/routes.py's _remove_reserved/
// _remove_empty (a direct Python port of removeReservedRecursive/
// removeEmptyRecursive) and api_clean_all.

let cleanRoot = '';

function cleanStatus(msg, cls) {
  const s = $('#fe-clean-status');
  s.textContent = msg || '';
  s.className = 'fe-status' + (cls ? ' ' + cls : '');
}

function showCleanLog(lines) {
  const pre = $('#fe-clean-log');
  if (!lines || !lines.length) { pre.hidden = true; pre.textContent = ''; return; }
  pre.hidden = false;
  pre.textContent = lines.join('\n');
}

$('#fe-btn-clean-all').onclick = () => {
  $('#fe-clean-modal').hidden = false;
  cleanStatus('');
  showCleanLog([]);
};
$('#fe-clean-close').onclick = () => { $('#fe-clean-modal').hidden = true; };

$('#fe-clean-browse').onclick = () => {
  AceFileBrowser.openModal({
    mode: 'pick-folder', title: "Choose a car's root folder (or any folder)", apiBase: '/api/data',
    startPath: () => cleanRoot,
    onPick: (entry) => { cleanRoot = entry.path; $('#fe-clean-root').value = entry.path; },
  });
};

$('#fe-clean-btn-clean').onclick = async () => {
  if (!cleanRoot) { cleanStatus('Choose a folder first.', 'err'); return; }
  const cleanEmpty = $('#fe-clean-opt-empty').checked;
  const cleanReserved = $('#fe-clean-opt-reserved').checked;
  const createBackup = $('#fe-clean-opt-backup').checked;
  if (!cleanEmpty && !cleanReserved) {
    cleanStatus('Nothing to do - check "Clean empty" and/or "Clean reserved".', 'err');
    return;
  }

  const parts = [];
  if (cleanEmpty) parts.push('empty fields');
  if (cleanReserved) parts.push('reserved fields');
  const ok = confirm(
    'Clean ' + parts.join(' and ') + ' from every supported content file under:\n' + cleanRoot +
    '\n\n' + (createBackup
      ? 'A .bak backup is made for every file this touches (skipped for a file that already has one).'
      : 'No backup will be made - files are overwritten directly. Tick "Create backup" above first if you want one.') +
    '\n\nContinue?'
  );
  if (!ok) return;

  cleanStatus('Cleaning…');
  showCleanLog([]);
  try {
    const res = await api('/api/field-editor/clean_all', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        root: cleanRoot, clean_empty: cleanEmpty, clean_reserved: cleanReserved,
        create_backup: createBackup,
      }),
    });
    cleanStatus(res.scanned + ' file(s) scanned, ' + res.changed + ' changed (' +
      res.reserved_removed + ' reserved, ' + res.empty_removed + ' empty removed)' +
      (res.errors ? ', ' + res.errors + ' error(s)' : '') + '.', res.errors ? 'warn' : 'ok');
    showCleanLog(res.results.map((r) => r.error
      ? '[FAILED] ' + r.path + ' - ' + r.error
      : '[ok] ' + r.path + '  (reserved: ' + r.reserved + ', empty: ' + r.empty + ')'));
  } catch (e) {
    cleanStatus('Failed: ' + e.message, 'err');
  }
};

$('#fe-clean-btn-restore').onclick = async () => {
  if (!cleanRoot) { cleanStatus('Choose a folder first.', 'err'); return; }
  const ok = confirm('Restore every .bak file under:\n' + cleanRoot +
    '\n\nback over its original file? This overwrites each matching file\'s current content.');
  if (!ok) return;
  cleanStatus('Restoring…');
  showCleanLog([]);
  try {
    const res = await api('/api/field-editor/restore_backups', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: cleanRoot }),
    });
    cleanStatus(res.restored + ' file(s) restored from backup' +
      (res.errors ? ', ' + res.errors + ' error(s)' : '') + '.', res.errors ? 'warn' : 'ok');
    showCleanLog(res.results.map((r) => '[FAILED] ' + r.path + ' - ' + r.error));
  } catch (e) {
    cleanStatus('Failed: ' + e.message, 'err');
  }
};

$('#fe-clean-btn-delete-backups').onclick = async () => {
  if (!cleanRoot) { cleanStatus('Choose a folder first.', 'err'); return; }
  const ok = confirm('Permanently delete every .bak file under:\n' + cleanRoot + '\n\nThis cannot be undone.');
  if (!ok) return;
  cleanStatus('Deleting…');
  showCleanLog([]);
  try {
    const res = await api('/api/field-editor/delete_backups', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: cleanRoot }),
    });
    cleanStatus(res.deleted + ' backup file(s) deleted' +
      (res.errors ? ', ' + res.errors + ' error(s)' : '') + '.', res.errors ? 'warn' : 'ok');
  } catch (e) {
    cleanStatus('Failed: ' + e.message, 'err');
  }
};

/* ---------------------------------------------------------------- file browsing */

const fieldEditorBrowser = AceFileBrowser.mountSidebar($('#fe-sidebar'), {
  apiBase: '/api/data',
  showSearch: true,
  searchPlaceholder: 'Search for a file…',
  classify: () => ({ selectable: true }),
  startPath: () => (state && state.dir) || '',
  onPick: (entry) => {
    if (dirty && !confirm('Unsaved field changes will be lost.\n\nDiscard them and open "' + entry.name + '"?')) return;
    openFile(entry.path);
  },
});

AceResizable.makeSplitter($('#fe-sidebar'), { min: 220, max: 700, storageKey: 'ace_fe_sidebar_w' });

window.addEventListener('beforeunload', (e) => {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

})();
