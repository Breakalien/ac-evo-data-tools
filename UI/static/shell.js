'use strict';
// App shell: switches between the top-level tool tabs. Nothing here knows
// about what's inside a tab - each module's own app.js owns its content.
document.querySelectorAll('.app-tabs button[data-app-tab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.appTab;
    document.querySelectorAll('.app-tabs button[data-app-tab]').forEach((b) => {
      b.classList.toggle('on', b === btn);
    });
    document.querySelectorAll('.app-tab-panel').forEach((panel) => {
      panel.hidden = panel.id !== 'tab-' + target;
    });
  });
});
