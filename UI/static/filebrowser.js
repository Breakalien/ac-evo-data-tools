'use strict';
// Shared file-browser UI component - used by the data editor's sidebar, the
// material editor's sidebar, and one-off modals (Settings' folder picker,
// material editor's "Save As"). Lives in UI/ (not in any tool module) since
// it is pure UI plumbing, reused as-is by every module that needs to browse
// the filesystem, and can evolve without any module depending on another.
window.AceFileBrowser = (function () {

  function el(tag, cls, txt) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined) n.textContent = txt;
    return n;
  }

  async function api(path, opts) {
    const r = await fetch(path, opts);
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  function baseName(p) {
    const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
    return parts[parts.length - 1] || p;
  }

  function loadPlaces() {
    let places = { fav: [], recent: [] };
    try { places = JSON.parse(localStorage.getItem('ace_places')) || places; } catch (e) { /* keep defaults */ }
    places.fav = places.fav || [];
    places.recent = places.recent || [];
    return places;
  }
  function savePlaces(places) { localStorage.setItem('ace_places', JSON.stringify(places)); }

  function pushRecent(places, path) {
    if (!path) return;
    places.recent = [path, ...places.recent.filter((p) => p !== path)].slice(0, 8);
    savePlaces(places);
  }

  /**
   * Builds the browsing chrome (navbar + bookmarks + breadcrumbs + listing)
   * into `container`. Shared by mountSidebar and openModal.
   *
   * opts:
   *   apiBase       - e.g. '/api/data' (ls/drives/search live under it)
   *   dirsOnly      - true for a folder-picker (files aren't shown/clickable)
   *   classify(file)- (optional) -> {selectable, label} for a file entry;
   *                   default: every file is selectable
   *   onPick(entry) - called with {path, name, isDir:false} when a file is
   *                   picked (dirsOnly=false), or with {path, isDir:true}
   *                   when "Choose this folder" is used (dirsOnly=true)
   *   startPath()   - async () -> initial path (falls back to drives root)
   *   bookmarks     - true to show the bookmarks/recents row (default true)
   */
  function buildCore(container, opts) {
    const apiBase = opts.apiBase || '/api/data';
    const classify = opts.classify || (() => ({ selectable: true }));
    const showBookmarks = opts.bookmarks !== false;

    let searchInput = null;
    if (opts.showSearch) {
      searchInput = el('input', 'fb-search');
      searchInput.type = 'search';
      searchInput.placeholder = opts.searchPlaceholder || 'Search for a file (min. 2 characters)…';
      container.appendChild(searchInput);
    }

    const navbar = el('div', 'fb-navbar');
    const upBtn = el('button', 'fb-icon', '↑');
    upBtn.type = 'button'; upBtn.title = 'Parent folder';
    const drivesBtn = el('button', 'fb-icon', '💾');
    drivesBtn.type = 'button'; drivesBtn.title = 'Drives';
    const pathInput = el('input', 'fb-pathbar');
    pathInput.type = 'text'; pathInput.spellcheck = false;
    pathInput.placeholder = 'Paste a path, then Enter…';
    navbar.append(upBtn, drivesBtn, pathInput);
    let starBtn = null;
    if (showBookmarks) {
      starBtn = el('button', 'fb-icon', '☆');
      starBtn.type = 'button'; starBtn.title = 'Bookmark this folder';
      navbar.appendChild(starBtn);
    }
    container.appendChild(navbar);

    const placesBox = showBookmarks ? el('div', 'fb-places') : null;
    if (placesBox) container.appendChild(placesBox);

    const crumbs = el('div', 'fb-crumbs');
    container.appendChild(crumbs);

    const listing = el('ul', 'fb-listing');
    container.appendChild(listing);

    let cwd = '';
    let places = showBookmarks ? loadPlaces() : null;

    function renderPlaces() {
      if (!placesBox) return;
      placesBox.textContent = '';
      const group = (title, list, removable) => {
        if (!list.length) return;
        placesBox.appendChild(el('div', 'grp', title));
        list.forEach((p) => {
          const chip = el('span', 'chip');
          const t = el('span', 't', baseName(p));
          t.title = p;
          t.onclick = () => browse(p);
          chip.appendChild(t);
          if (removable) {
            const x = el('b', null, '×');
            x.title = 'Remove';
            x.onclick = (ev) => {
              ev.stopPropagation();
              places.fav = places.fav.filter((q) => q !== p);
              savePlaces(places); renderPlaces();
            };
            chip.appendChild(x);
          }
          placesBox.appendChild(chip);
        });
      };
      group('Bookmarks', places.fav, true);
      group('Recent', places.recent.filter((p) => !places.fav.includes(p)), false);
      if (starBtn) {
        starBtn.classList.toggle('on', places.fav.includes(cwd));
        starBtn.textContent = places.fav.includes(cwd) ? '★' : '☆';
      }
    }

    async function browse(path) {
      let d;
      try {
        d = await api(apiBase + '/ls?path=' + encodeURIComponent(path || ''));
      } catch (e) {
        listing.textContent = '';
        listing.appendChild(el('li', 'dim', e.message));
        return;
      }
      cwd = d.path;
      pathInput.value = d.path;
      upBtn.disabled = d.parent === null;
      if (d.path && places) { pushRecent(places, d.path); renderPlaces(); }

      crumbs.textContent = '';
      if (d.isRoot) {
        crumbs.append('This computer');
      } else {
        const parts = d.path.split(/[\\/]/).filter(Boolean);
        const sep = d.path.includes('\\') ? '\\' : '/';
        let acc = d.path.startsWith('/') ? '/' : '';
        parts.forEach((part, i) => {
          acc = acc && acc !== '/' ? acc + sep + part : acc + part;
          if (i === 0 && sep === '\\') acc = part + sep;
          const a = el('a', null, part);
          const target = acc;
          a.onclick = () => browse(target);
          if (i) crumbs.append(' ' + sep + ' ');
          crumbs.appendChild(a);
        });
        if (d.truncated) crumbs.append('  — listing truncated');
      }

      listing.textContent = '';
      if (d.parent !== null) {
        const li = el('li');
        li.append(el('span', 'ic', '↑'), el('span', 'nm', '..'));
        li.onclick = () => browse(d.parent);
        listing.appendChild(li);
      }
      d.dirs.forEach((x) => {
        const li = el('li');
        li.append(el('span', 'ic', '📁'), el('span', 'nm', x.name));
        li.onclick = () => browse(x.path);
        listing.appendChild(li);
      });
      if (!opts.dirsOnly) {
        (d.files || []).forEach((f) => {
          const info = classify(f);
          const selectable = info ? info.selectable !== false : true;
          const li = el('li', selectable ? '' : 'dim');
          li.append(el('span', 'ic', selectable ? '▪' : '·'),
                    el('span', 'nm', f.name));
          if (f.size !== undefined) {
            const kb = f.size < 1024 ? f.size + ' B'
              : f.size < 1048576 ? (f.size / 1024).toFixed(1) + ' KB'
              : (f.size / 1048576).toFixed(1) + ' MB';
            li.appendChild(el('span', 'sz', kb));
          }
          li.onclick = () => {
            if (!selectable && opts.confirmUnselectable &&
                !confirm('"' + f.name + '" does not match the expected filter. Continue anyway?')) return;
            listing.querySelectorAll('li.sel').forEach((x) => x.classList.remove('sel'));
            li.classList.add('sel');
            if (opts.onPick) opts.onPick({ path: f.path, name: f.name, isDir: false });
          };
          listing.appendChild(li);
        });
      }
    }

    pathInput.onkeydown = (e) => { if (e.key === 'Enter') browse(pathInput.value.trim()); };
    upBtn.onclick = async () => {
      const d = await api(apiBase + '/ls?path=' + encodeURIComponent(cwd));
      browse(d.parent === null ? '' : d.parent);
    };
    drivesBtn.onclick = () => browse('');
    if (starBtn) {
      starBtn.onclick = () => {
        if (!cwd) return;
        if (places.fav.includes(cwd)) places.fav = places.fav.filter((p) => p !== cwd);
        else places.fav = [cwd, ...places.fav].slice(0, 12);
        savePlaces(places); renderPlaces();
      };
    }

    async function doSearch(term) {
      if (!term || term.length < 2) return browse(cwd);
      if (!cwd) return;
      let d;
      try { d = await api(apiBase + '/search?q=' + encodeURIComponent(term) + '&path=' + encodeURIComponent(cwd)); }
      catch (e) { return; }
      listing.textContent = '';
      crumbs.textContent = d.results.length + ' result(s) under ' + cwd + (d.truncated ? ' (truncated at 300)' : '');
      d.results.forEach((f) => {
        const info = classify(f);
        const selectable = info ? info.selectable !== false : true;
        const li = el('li', selectable ? '' : 'dim');
        li.append(el('span', 'ic', selectable ? '▪' : '·'), el('span', 'nm', f.path));
        li.onclick = () => {
          listing.querySelectorAll('li.sel').forEach((x) => x.classList.remove('sel'));
          li.classList.add('sel');
          if (opts.onPick) opts.onPick({ path: f.path, name: baseName(f.path), isDir: false });
        };
        listing.appendChild(li);
      });
    }

    if (searchInput) {
      let searchTimer = null;
      searchInput.oninput = () => {
        clearTimeout(searchTimer);
        const t = searchInput.value.trim();
        searchTimer = setTimeout(() => doSearch(t), 250);
      };
    }

    (async () => {
      let start = '';
      if (opts.startPath) { try { start = await opts.startPath(); } catch (e) { /* fall back to drives */ } }
      if (!start) { try { const d = await api(apiBase + '/drives'); start = d.start || ''; } catch (e) { /* stay at drives root */ } }
      await browse(start);
    })();

    return { browse, getCwd: () => cwd, search: doSearch };
  }

  function mountSidebar(container, opts) {
    container.classList.add('fb-sidebar');
    return buildCore(container, opts);
  }

  function openModal(opts) {
    const backdrop = el('div', 'fb-modal-backdrop');
    const modal = el('div', 'fb-modal');

    const head = el('div', 'fb-modal-head');
    head.appendChild(el('strong', null, opts.title || 'Browse'));
    const closeBtn = el('button', 'fb-icon', '✕');
    closeBtn.type = 'button';
    closeBtn.onclick = () => backdrop.remove();
    head.appendChild(closeBtn);
    modal.appendChild(head);

    const body = el('div', 'fb-modal-body');
    modal.appendChild(body);

    const foot = el('div', 'fb-modal-foot');
    let nameInput = null;
    let actionBtn = null;
    if (opts.mode === 'save-file') {
      nameInput = el('input', 'fb-modal-name');
      nameInput.type = 'text';
      nameInput.value = opts.defaultName || '';
      foot.appendChild(nameInput);
      actionBtn = el('button', 'primary', opts.actionLabel || 'Save here');
    } else if (opts.mode === 'pick-folder') {
      actionBtn = el('button', 'primary', opts.actionLabel || 'Choose this folder');
    }
    if (actionBtn) {
      actionBtn.type = 'button';
      foot.appendChild(actionBtn);
      modal.appendChild(foot);
    }

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    backdrop.onclick = (e) => { if (e.target === backdrop) backdrop.remove(); };

    const core = buildCore(body, {
      apiBase: opts.apiBase,
      dirsOnly: opts.mode === 'pick-folder',
      classify: opts.classify,
      confirmUnselectable: opts.confirmUnselectable,
      startPath: opts.startPath,
      bookmarks: opts.bookmarks,
      onPick: (entry) => {
        if (opts.mode === 'save-file') {
          // clicking an existing file only pre-fills the name - "Save here"
          // (below) is the actual confirmation, so an accidental click can
          // never silently overwrite a file.
          if (nameInput) nameInput.value = entry.name;
          return;
        }
        backdrop.remove();
        if (opts.onPick) opts.onPick(entry);
      },
    });

    if (opts.mode === 'save-file' && actionBtn) {
      actionBtn.onclick = () => {
        const name = nameInput.value.trim();
        if (!name) return;
        const cwd = core.getCwd();
        const sep = cwd.includes('\\') ? '\\' : '/';
        const full = cwd.replace(/[\\/]+$/, '') + sep + name;
        backdrop.remove();
        if (opts.onPick) opts.onPick({ path: full, name, isDir: false });
      };
    } else if (opts.mode === 'pick-folder' && actionBtn) {
      actionBtn.onclick = () => {
        const cwd = core.getCwd();
        if (!cwd) return;
        backdrop.remove();
        if (opts.onPick) opts.onPick({ path: cwd, isDir: true });
      };
    }

    return core;
  }

  return { mountSidebar, openModal };
})();
