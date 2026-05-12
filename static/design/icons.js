/* === 图标系统 · Lucide CDN + 本地 fallback =================================
   策略：优先走 Lucide Icons（1400+ 高质量开源图标），CDN 加载失败或离线时
   自动回退到内置的 SVG path 表。
   使用方式保持不变：<i data-icon="user" class="icon"></i> → ZhIcons.mount()
   Lucide: https://lucide.dev （ISC License，商用免费）
   ========================================================================== */
(function(){
  /* ---------- 1. Lucide 名字映射表（本仓库命名 → Lucide 官方命名） -------- */
  const LUCIDE_NAME = {
    'close':     'x',
    'alert':     'triangle-alert',
    'edit':      'pencil',
    'trash':     'trash-2',
    'chat':      'message-square',
    'refresh':   'refresh-cw',
    'arrow-right': 'arrow-right',
    'chevron-down': 'chevron-down',
    'chevron-right': 'chevron-right',
    'shield-check': 'shield-check',
  };

  /* ---------- 2. 离线 fallback：内置最常用的 40 个 SVG path ----------- */
  const FALLBACK = {
    'home':       '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2h-4v-6H10v6H6a2 2 0 0 1-2-2z"/>',
    'user':       '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    'users':      '<circle cx="9" cy="8" r="4"/><path d="M1 21a8 8 0 0 1 16 0"/><path d="M17 3a4 4 0 0 1 0 8"/><path d="M23 21a8 8 0 0 0-6-7.75"/>',
    'plus':       '<path d="M12 5v14M5 12h14"/>',
    'edit':       '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    'trash':      '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
    'search':     '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
    'close':      '<path d="M18 6L6 18M6 6l12 12"/>',
    'check':      '<path d="M20 6L9 17l-5-5"/>',
    'alert':      '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    'info':       '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    'upload':     '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><path d="M12 3v12"/>',
    'file':       '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    'image':      '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    'brain':      '<path d="M9.5 2A3.5 3.5 0 0 1 13 5.5v13A3.5 3.5 0 0 1 6 18.5a3 3 0 0 1-3-3 3 3 0 0 1-1-6 3 3 0 0 1 1.5-5A3.5 3.5 0 0 1 9.5 2z"/><path d="M14.5 2A3.5 3.5 0 0 0 11 5.5v13a3.5 3.5 0 0 0 7 0 3 3 0 0 0 3-3 3 3 0 0 0 1-6 3 3 0 0 0-1.5-5A3.5 3.5 0 0 0 14.5 2z"/>',
    'sparkles':   '<path d="M12 2l1.8 5.8L20 9l-5.8 1.8L12 16l-1.8-5.2L5 9l5.2-1.2z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    'clock':      '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    'history':    '<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><path d="M12 7v5l4 2"/>',
    'book':       '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    'shield':     '<path d="M12 2l8 4v6c0 5-3.5 9.5-8 10-4.5-.5-8-5-8-10V6z"/>',
    'heart':      '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 1 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    'activity':   '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    'bed':        '<path d="M2 4v16"/><path d="M2 8h18a2 2 0 0 1 2 2v10"/><path d="M2 17h20"/><circle cx="7" cy="12" r="2"/>',
    'arrow-right':'<path d="M5 12h14M13 5l7 7-7 7"/>',
    'chevron-down':'<polyline points="6 9 12 15 18 9"/>',
    'chevron-right':'<polyline points="9 18 15 12 9 6"/>',
    'filter':     '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    'thermometer':'<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>',
    'stethoscope':'<path d="M4.8 2.3A.3.3 0 1 0 5 2H4a2 2 0 0 0-2 2v5a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6V4a2 2 0 0 0-2-2h-1a.2.2 0 1 0 .3.3"/><path d="M8 15v1a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6v-4"/><circle cx="20" cy="10" r="2"/>',
    'droplet':    '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.32 0z"/>',
    'menu':       '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6"  x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>',
    'quote':      '<path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z"/>',
    'chat':       '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    'zap':        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    'refresh':    '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>',
    'bookmark':   '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
    'shield-check':'<path d="M12 2l8 4v6c0 5-3.5 9.5-8 10-4.5-.5-8-5-8-10V6z"/><polyline points="9 12 11 14 15 10"/>',
    'settings':   '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.39.96 1.3 1.65 2.4 1.5H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    'database':   '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    'layers':     '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  };

  /* ---------- 3. Lucide 动态加载（CDN）---------- */
  const LUCIDE_URL = 'https://unpkg.com/lucide@0.454.0/dist/umd/lucide.min.js';
  let lucidePromise = null;
  function loadLucide(){
    if(lucidePromise) return lucidePromise;
    lucidePromise = new Promise((resolve, reject) => {
      if(window.lucide) return resolve(window.lucide);
      const s = document.createElement('script');
      s.src = LUCIDE_URL;
      s.async = true;
      s.onload = () => window.lucide ? resolve(window.lucide) : reject(new Error('lucide not ready'));
      s.onerror = () => reject(new Error('lucide load failed'));
      // 5s 超时兜底
      const t = setTimeout(() => reject(new Error('lucide timeout')), 5000);
      const origResolve = resolve, origReject = reject;
      resolve = v => { clearTimeout(t); origResolve(v); };
      reject  = e => { clearTimeout(t); origReject(e); };
      document.head.appendChild(s);
    }).catch(e => { console.warn('[ZhIcons] Lucide CDN unavailable, using local fallback:', e.message); return null; });
    return lucidePromise;
  }

  /* ---------- 4. 渲染：离线 SVG ---------- */
  function fallbackSvg(name){
    const body = FALLBACK[name];
    if(!body) return '';
    // 显式写 width/height="100%"：否则某些浏览器会按 SVG 规范退化到 300x150
    // 默认尺寸冲破 .icon/.icon-s 容器，表现为"警告变巨大"这类 bug。
    return `<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  /* ---------- 5. 主入口：mount ---------- */
  async function mount(root){
    root = root || document;
    const els = root.querySelectorAll('[data-icon]:not([data-icon-mounted])');
    if(!els.length) return;

    const lucide = await loadLucide();

    els.forEach(el => {
      const name = el.getAttribute('data-icon');
      if(!name) return;
      if(lucide && lucide.icons){
        const lucideName = LUCIDE_NAME[name] || name;
        // Lucide 用 kebab-case + PascalCase 双向，适配一下
        const key = lucideName.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        const iconData = lucide.icons[key] || lucide.icons[lucideName] || null;
        if(iconData){
          const svg = lucide.createElement(iconData);
          svg.setAttribute('stroke-width', '1.5');
          svg.setAttribute('aria-hidden', 'true');
          // 让 SVG 永远跟着容器尺寸走（.icon=20, .icon-s=16, .icon-l=24）。
          // Lucide 默认 width/height=24 会在 16px 容器里溢出 → 变成巨大图标。
          svg.setAttribute('width', '100%');
          svg.setAttribute('height', '100%');
          el.replaceChildren(svg);
          el.dataset.iconMounted = '1';
          return;
        }
      }
      // fallback
      el.innerHTML = fallbackSvg(name);
      el.dataset.iconMounted = '1';
    });
  }

  /* ---------- 6. 暴露 API ---------- */
  window.ZhIcons = {
    mount,
    svg: fallbackSvg,   // 同步版本（离线）
  };

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', () => mount());
  }else{
    mount();
  }
})();
