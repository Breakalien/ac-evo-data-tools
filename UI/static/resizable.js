'use strict';
// Shared drag-to-resize splitter - lives in UI/ (pure UI plumbing, no module
// owns it) so every tab can use the same resize behavior. Inserts a thin
// draggable handle right after `leftEl` inside its flex-row parent, and
// controls leftEl's width on drag (the sibling on the other side is assumed
// to be flex:1 and simply fills whatever space is left).
window.AceResizable = (function () {

  function makeSplitter(leftEl, opts) {
    opts = opts || {};
    const min = opts.min || 180;
    const max = opts.max || 900;
    const storageKey = opts.storageKey;

    if (storageKey) {
      const saved = parseInt(localStorage.getItem(storageKey), 10);
      if (!Number.isNaN(saved)) {
        leftEl.style.width = Math.max(min, Math.min(max, saved)) + 'px';
        leftEl.style.flex = 'none';
      }
    }

    const handle = document.createElement('div');
    handle.className = 'ace-splitter';
    leftEl.insertAdjacentElement('afterend', handle);

    let dragging = false, startX = 0, startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
      dragging = true;
      startX = e.clientX;
      startWidth = leftEl.getBoundingClientRect().width;
      handle.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const w = Math.max(min, Math.min(max, startWidth + (e.clientX - startX)));
      leftEl.style.width = w + 'px';
      leftEl.style.flex = 'none';
    });
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (storageKey) {
        localStorage.setItem(storageKey, Math.round(leftEl.getBoundingClientRect().width));
      }
    });

    return handle;
  }

  return { makeSplitter };
})();
