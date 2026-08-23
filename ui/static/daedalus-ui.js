/**
 * Shared Daedalus UI helpers — toasts, skeleton loaders, badges.
 * Loaded on all /admin/daedalus/* pages via _daedalus_nav.html.
 */
(function (global) {
  'use strict';

  const WIDTHS = ['w-90', 'w-75', 'w-60', 'w-40'];

  function skeleton(type, count) {
    const n = Math.max(1, Math.min(count || 3, 8));
    const lines = [];
    if (type === 'card' || type === 'cards') {
      for (let i = 0; i < n; i++) {
        lines.push('<div class="dx-skel dx-skel-card" aria-hidden="true"></div>');
      }
    } else if (type === 'stat' || type === 'stats') {
      for (let i = 0; i < n; i++) {
        lines.push('<div class="dx-skel dx-skel-stat" aria-hidden="true"></div>');
      }
    } else {
      for (let i = 0; i < n; i++) {
        const w = WIDTHS[i % WIDTHS.length];
        lines.push(`<div class="dx-skel dx-skel-line ${w}" aria-hidden="true"></div>`);
      }
    }
    return `<div class="dx-skel-wrap" role="status" aria-label="Зареждане">${lines.join('')}</div>`;
  }

  function ensureToastContainer() {
    let el = document.getElementById('dx-toast-container');
    if (!el) {
      el = document.createElement('div');
      el.id = 'dx-toast-container';
      document.body.appendChild(el);
    }
    return el;
  }

  function toastType(msg) {
    const s = String(msg || '');
    if (/^❌|грешка|error/i.test(s)) return 'error';
    if (/^⚠|warn/i.test(s)) return 'warn';
    if (/^✅|✓|успеш/i.test(s)) return 'success';
    return 'info';
  }

  function showToast(msg, opts) {
    let duration = 3500;
    let type = 'info';
    if (typeof opts === 'number') {
      duration = opts;
    } else if (opts && typeof opts === 'object') {
      duration = opts.duration ?? duration;
      type = opts.type ?? toastType(msg);
    } else {
      type = toastType(msg);
    }

    const container = ensureToastContainer();
    const toast = document.createElement('div');
    toast.className = `dx-toast dx-toast--${type}`;
    toast.textContent = msg;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(4px)';
      setTimeout(() => toast.remove(), 400);
    }, duration);
  }

  function setBadge(el, count, opts) {
    if (!el) return;
    const hiddenWhenZero = !opts || opts.hiddenWhenZero !== false;
    const n = Number(count) || 0;
    el.textContent = String(n);
    el.classList.toggle('hidden', hiddenWhenZero && n === 0);
    if (n > 0 && opts && opts.pulse) {
      el.classList.add('dx-badge-pulse');
    } else {
      el.classList.remove('dx-badge-pulse');
    }
  }

  function parseSkeletonAttr(raw) {
    const [kind, countStr] = String(raw || 'lines:3').split(':');
    return { kind: kind || 'lines', count: parseInt(countStr, 10) || 3 };
  }

  function hydrateSkeletons(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-dx-skeleton]').forEach((node) => {
      const { kind, count } = parseSkeletonAttr(node.getAttribute('data-dx-skeleton'));
      node.innerHTML = skeleton(kind, count);
      node.removeAttribute('data-dx-skeleton');
    });
  }

  function loadingHtml(count) {
    return skeleton('lines', count || 3);
  }

  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderUnifiedDiffHtml(diff) {
    const lines = String(diff || '(no diff)').split('\n');
    return lines.map((line) => {
      const esc = escapeHtml(line);
      if (line.startsWith('+++') || line.startsWith('---')) return `<span class="text-slate-500">${esc}</span>`;
      if (line.startsWith('@@')) return `<span class="text-cyan-400">${esc}</span>`;
      if (line.startsWith('+')) return `<span class="text-emerald-300 bg-emerald-500/10 block">${esc}</span>`;
      if (line.startsWith('-')) return `<span class="text-red-300 bg-red-500/10 block">${esc}</span>`;
      return `<span class="text-slate-400">${esc}</span>`;
    }).join('\n');
  }

  function ensureDiffModal() {
    let el = document.getElementById('dx-diff-modal-backdrop');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'dx-diff-modal-backdrop';
    el.className = 'dx-diff-modal-backdrop hidden';
    el.innerHTML = `
      <div class="dx-diff-modal" role="dialog" aria-modal="true" aria-labelledby="dx-diff-title">
        <div class="dx-diff-modal-head">
          <div>
            <div class="text-[10px] uppercase tracking-wider text-slate-500" id="dx-diff-eyebrow">Промени</div>
            <div class="text-sm font-mono text-white" id="dx-diff-title">—</div>
          </div>
          <button type="button" id="dx-diff-close" class="text-slate-500 hover:text-white text-2xl leading-none px-2" aria-label="Затвори">×</button>
        </div>
        <div class="dx-diff-modal-meta" id="dx-diff-meta"></div>
        <div class="dx-diff-modal-body">
          <div id="dx-diff-file-tabs" class="dx-diff-file-tabs hidden"></div>
          <pre id="dx-diff-content"></pre>
        </div>
        <div class="dx-diff-modal-foot" id="dx-diff-foot"></div>
      </div>`;
    document.body.appendChild(el);
    el.addEventListener('click', (ev) => {
      if (ev.target === el) closeDiffModal();
    });
    el.querySelector('#dx-diff-close')?.addEventListener('click', closeDiffModal);
    return el;
  }

  let _diffModalState = null;

  function openDiffModal(opts) {
    const backdrop = ensureDiffModal();
    _diffModalState = opts || {};
    const title = backdrop.querySelector('#dx-diff-title');
    const eyebrow = backdrop.querySelector('#dx-diff-eyebrow');
    const meta = backdrop.querySelector('#dx-diff-meta');
    const content = backdrop.querySelector('#dx-diff-content');
    const foot = backdrop.querySelector('#dx-diff-foot');
    const tabs = backdrop.querySelector('#dx-diff-file-tabs');

    eyebrow.textContent = opts.eyebrow || 'Промени';
    title.textContent = opts.title || '—';
    meta.innerHTML = opts.metaHtml || '';
    foot.innerHTML = opts.actionsHtml || `
      <div class="flex-1"></div>
      <button type="button" class="px-4 py-2 rounded text-xs font-mono border border-slate-600 text-slate-300 hover:text-white" data-dx-diff-dismiss>Затвори</button>`;
    foot.querySelector('[data-dx-diff-dismiss]')?.addEventListener('click', closeDiffModal);

    const diffs = opts.diffs || (opts.diff ? [{ path: opts.filePath || 'file', diff: opts.diff }] : []);
    if (diffs.length > 1) {
      tabs.classList.remove('hidden');
      tabs.innerHTML = diffs.map((d, i) =>
        `<button type="button" class="dx-diff-file-tab ${i === 0 ? 'active' : ''}" data-idx="${i}">${escapeHtml(d.path)}</button>`
      ).join('');
      tabs.querySelectorAll('.dx-diff-file-tab').forEach((btn) => {
        btn.addEventListener('click', () => {
          tabs.querySelectorAll('.dx-diff-file-tab').forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          const item = diffs[Number(btn.dataset.idx)];
          content.innerHTML = renderUnifiedDiffHtml(item?.diff || '');
        });
      });
    } else {
      tabs.classList.add('hidden');
      tabs.innerHTML = '';
    }
    content.innerHTML = renderUnifiedDiffHtml(diffs[0]?.diff || opts.diff || '(no diff)');
    backdrop.classList.remove('hidden');
  }

  function closeDiffModal() {
    const el = document.getElementById('dx-diff-modal-backdrop');
    if (el) el.classList.add('hidden');
    _diffModalState = null;
  }

  function toggleNavDropdown(id) {
    const root = document.getElementById(id);
    if (!root) return;
    const open = root.classList.toggle('adn-nav-dropdown--open');
    root.querySelector('.adn-nav-dropdown-btn')?.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function initNavDropdowns() {
    document.querySelectorAll('.adn-nav-dropdown').forEach((root) => {
      root.querySelector('.adn-nav-dropdown-btn')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const id = root.id;
        document.querySelectorAll('.adn-nav-dropdown').forEach((other) => {
          if (other.id !== id) {
            other.classList.remove('adn-nav-dropdown--open');
            other.querySelector('.adn-nav-dropdown-btn')?.setAttribute('aria-expanded', 'false');
          }
        });
        toggleNavDropdown(id);
      });
    });
    document.addEventListener('click', (ev) => {
      if (ev.target.closest('.adn-nav-dropdown')) return;
      document.querySelectorAll('.adn-nav-dropdown').forEach((root) => {
        root.classList.remove('adn-nav-dropdown--open');
        root.querySelector('.adn-nav-dropdown-btn')?.setAttribute('aria-expanded', 'false');
      });
    });
    document.querySelectorAll('.adn-nav-dropdown-menu').forEach((menu) => {
      menu.addEventListener('click', (ev) => ev.stopPropagation());
    });
  }

  const DaedalusUI = {
    skeleton,
    loadingHtml,
    showToast,
    setBadge,
    hydrateSkeletons,
    escapeHtml,
    renderUnifiedDiffHtml,
    openDiffModal,
    closeDiffModal,
    toggleNavDropdown,
  };

  global.DaedalusUI = DaedalusUI;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      hydrateSkeletons();
      initNavDropdowns();
    });
  } else {
    hydrateSkeletons();
    initNavDropdowns();
  }
})(typeof window !== 'undefined' ? window : globalThis);
