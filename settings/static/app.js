'use strict';
// Settings tab - app-wide config (currently just the AC EVO install folder,
// used to extract protobuf schemas). Own IIFE, own helpers, same pattern as
// the other tab modules.
(function () {

const $ = (s) => document.querySelector(s);

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

function status(msg, cls) {
  const s = $('#st-status');
  s.textContent = msg || '';
  s.className = 'st-status' + (cls ? ' ' + cls : '');
}

async function loadSettings() {
  try {
    const data = await api('/api/settings');
    if (data.acevo_dir) $('#st-acevo-dir').value = data.acevo_dir;
  } catch (e) { /* defaults stay empty */ }
}

async function saveAcevoDir(dir) {
  try {
    await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ acevo_dir: dir }),
    });
  } catch (e) { /* non-fatal: extraction still works, just won't be remembered */ }
}

$('#st-acevo-dir').addEventListener('change', () => saveAcevoDir($('#st-acevo-dir').value.trim()));

$('#st-btn-browse').onclick = () => {
  AceFileBrowser.openModal({
    mode: 'pick-folder', title: 'Choose the Assetto Corsa EVO folder', apiBase: '/api/data',
    startPath: () => $('#st-acevo-dir').value.trim(),
    bookmarks: false,
    onPick: (entry) => { $('#st-acevo-dir').value = entry.path; saveAcevoDir(entry.path); },
  });
};

$('#st-btn-extract').onclick = async () => {
  const dir = $('#st-acevo-dir').value.trim();
  if (!dir) { status('Enter the Assetto Corsa EVO folder first.', 'err'); return; }
  saveAcevoDir(dir);
  $('#st-btn-extract').disabled = true;
  status('Extracting…');
  $('#st-log').hidden = true;
  try {
    const res = await api('/api/settings/extract_protos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dir }),
    });
    status(res.n_protos + ' .proto files rebuilt (' + res.n_messages + ' messages, ' +
      res.n_fields + ' fields). Restart the app for the Data Editor to use them.', 'ok');
    $('#st-log').hidden = false;
    $('#st-log').textContent = 'Exe: ' + res.exe + '\n' + (res.log || []).join('\n');
  } catch (e) {
    status('Failed: ' + e.message, 'err');
  } finally {
    $('#st-btn-extract').disabled = false;
  }
};

loadSettings();

})();
