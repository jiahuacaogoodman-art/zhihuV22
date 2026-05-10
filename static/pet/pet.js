/* === 智护银伴 · 桌面宠物 sprite 动画机 =====================================
   - 根据 validation.json 的 9 个状态播放对应帧
   - 支持 拖拽 / 右键菜单 / 收起召回 / 气泡对话
   - 暴露全局 window.ZhPet：给业务层发事件
       ZhPet.say(text, opts)       快速冒泡
       ZhPet.react('success'|'error'|'thinking'|'greet'|'idle'|'run')
       ZhPet.setState('idle'|'waving'|'running'|'review'|'failed'|...)
       ZhPet.hide() / ZhPet.show() / ZhPet.toggle()
   - 会自动 hook 页面事件：
       1) document 上的 toast 事件（如 ZhToast.show）→ success / error 反应
       2) window 的 'zh-pet' 自定义事件
   ========================================================================== */
(function () {
  'use strict';

  // ---- Sprite 元数据 ----
  const CELL_W = 192;
  const CELL_H = 208;
  const COLS = 8;
  // 每行的状态与实际帧数（基于 validation.json 去掉 used:false 的空位）
  const STATES = {
    idle:            { row: 0, frames: 6, fps: 6,  loop: true  },
    'running-right': { row: 1, frames: 8, fps: 12, loop: true  },
    'running-left':  { row: 2, frames: 8, fps: 12, loop: true  },
    waving:          { row: 3, frames: 4, fps: 6,  loop: true  },
    jumping:         { row: 4, frames: 5, fps: 10, loop: false },
    failed:          { row: 5, frames: 8, fps: 10, loop: false },
    waiting:         { row: 6, frames: 6, fps: 5,  loop: true  },
    running:         { row: 7, frames: 6, fps: 12, loop: true  },
    review:          { row: 8, frames: 6, fps: 7,  loop: true  }
  };

  // ---- 小工具 ----
  const $ = (sel, root = document) => root.querySelector(sel);
  const LS_KEY = 'zh-pet-pos-v2';
  const LS_HIDE = 'zh-pet-hide-v1';

  function supportsWebp() {
    const el = document.createElement('canvas');
    return el.getContext && el.getContext('2d') ? el.toDataURL('image/webp').indexOf('data:image/webp') === 0 : false;
  }

  // ---- 随机话术 ----
  const GREETINGS = [
    '嗨，我是你的银伴小助手 🌿',
    '今天也要好好陪伴老人呀～',
    '已就位，有什么想让我帮忙的吗？',
    '一切本地运行，放心使用。'
  ];
  const THINKING = [
    '正在翻档案和病历… ',
    '整合既往记录中…',
    '让我想想，别急～',
    '正在回忆上一次类似情况怎么处理的'
  ];
  const SUCCESS = [
    '搞定！✨',
    '已保存，可以继续下一步',
    '这一步完成啦',
    '稳了，继续吧'
  ];
  const FAIL = [
    '糟糕… 这里出了点问题',
    '哎呀，再试一次？',
    '好像卡住了，检查一下吧'
  ];
  const IDLE_WHISPERS = [
    '记得定时查房～',
    '可以摸摸我 :)',
    '累了就歇一歇',
    '档案要记得及时上传哦'
  ];

  const pick = arr => arr[Math.floor(Math.random() * arr.length)];

  // =====================================================
  // ZhPet 主类
  // =====================================================
  class ZhPet {
    constructor() {
      this.host = null;
      this.sprite = null;
      this.bubble = null;
      this.bubbleTitle = null;
      this.bubbleText = null;
      this.bubbleState = null;
      this.recall = null;
      this.menu = null;

      this.state = 'waving';
      this.frame = 0;
      this._lastFrameTime = 0;
      this._rafId = null;
      this._stateConfig = STATES.waving;
      this._stateEndCallback = null;

      this._bubbleTimer = null;
      this._idleWhisperTimer = null;

      // 拖拽状态
      this._drag = null;

      // 位置持久化
      this._pos = this._loadPos();
      this._hidden = false;

      this._init();
    }

    // -------- 初始化 --------
    _init() {
      // 基础 DOM
      const host = document.createElement('div');
      host.className = 'pet-host';
      host.setAttribute('role', 'button');
      host.setAttribute('aria-label', '智护银伴宠物');
      host.innerHTML = `
        <div class="pet-shadow"></div>
        <div class="pet-sprite" aria-hidden="true"></div>
        <div class="pet-bubble" role="status" aria-live="polite">
          <div class="pet-bubble-title">银伴</div>
          <div class="pet-bubble-text">hi～</div>
          <div class="pet-bubble-state" style="display:none;">idle</div>
        </div>
      `;
      document.body.appendChild(host);
      this.host = host;
      this.sprite = host.querySelector('.pet-sprite');
      this.bubble = host.querySelector('.pet-bubble');
      this.bubbleTitle = host.querySelector('.pet-bubble-title');
      this.bubbleText = host.querySelector('.pet-bubble-text');
      this.bubbleState = host.querySelector('.pet-bubble-state');

      // webp fallback
      if (!supportsWebp()) this.sprite.classList.add('fallback-png');

      // 召回按钮
      const recall = document.createElement('button');
      recall.className = 'pet-recall';
      recall.setAttribute('aria-label', '呼出宠物');
      recall.innerHTML = '<i class="fa-solid fa-paw" aria-hidden="true"></i>';
      recall.addEventListener('click', () => this.show());
      document.body.appendChild(recall);
      this.recall = recall;

      // 右键菜单
      const menu = document.createElement('div');
      menu.className = 'pet-menu';
      menu.innerHTML = `
        <div class="pet-menu-head">宠物动作</div>
        <button class="pet-menu-item" data-act="state:waving"><i class="fa-solid fa-hand"></i>打招呼</button>
        <button class="pet-menu-item" data-act="state:idle"><i class="fa-solid fa-cloud"></i>待机</button>
        <button class="pet-menu-item" data-act="state:running"><i class="fa-solid fa-person-running"></i>跑步</button>
        <button class="pet-menu-item" data-act="state:review"><i class="fa-solid fa-magnifying-glass"></i>查资料</button>
        <button class="pet-menu-item" data-act="state:jumping"><i class="fa-solid fa-arrow-up"></i>跳一下</button>
        <button class="pet-menu-item" data-act="state:failed"><i class="fa-solid fa-face-tired"></i>挫败</button>
        <div class="pet-menu-sep"></div>
        <button class="pet-menu-item" data-act="say"><i class="fa-regular fa-message"></i>说句话</button>
        <button class="pet-menu-item" data-act="reset"><i class="fa-solid fa-rotate"></i>回原位</button>
        <button class="pet-menu-item" data-act="hide"><i class="fa-solid fa-eye-slash"></i>暂时隐藏</button>
      `;
      document.body.appendChild(menu);
      this.menu = menu;
      menu.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const act = btn.dataset.act;
        this._hideMenu();
        if (act.startsWith('state:')) {
          const s = act.slice(6);
          this.setState(s);
          if (!STATES[s].loop) {
            this._stateEndCallback = () => this.setState('idle');
          }
        } else if (act === 'say') {
          this.say(pick(IDLE_WHISPERS), { title: '悄悄话' });
        } else if (act === 'reset') {
          this._pos = { x: null, y: null };
          this._savePos();
          host.style.left = '';
          host.style.top = '';
          host.style.right = '';
          host.style.bottom = '';
        } else if (act === 'hide') {
          this.hide();
        }
      });

      // 其它全局点击 → 关菜单
      document.addEventListener('click', (e) => {
        if (!menu.contains(e.target)) this._hideMenu();
      });
      // Esc 关菜单
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this._hideMenu();
      });

      // 应用持久化位置 / 隐藏状态
      this._applyPos();
      if (localStorage.getItem(LS_HIDE) === '1') {
        this.hide(false);
      }

      // 窗口变化时不再重新吸附到槽位：宠物固定在左下角（CSS 默认位置）
      // 用户拖动后的位置仍然会通过 _pos 持久化

      // 拖拽
      this._bindDrag();

      // 右键菜单
      this.sprite.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        this._showMenu(e.clientX, e.clientY);
      });

      // 点击/单击：挥手并说一句话
      let clickTimer = null;
      let clickCount = 0;
      this.sprite.addEventListener('click', (e) => {
        if (this._justDragged) { this._justDragged = false; return; }
        clickCount++;
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => {
          if (clickCount === 1) {
            this.react('greet');
          } else if (clickCount >= 2) {
            this.react('jump');
          }
          clickCount = 0;
        }, 240);
      });

      // 开启渲染
      this.setState('waving');
      this._stateEndCallback = () => this.setState('idle');
      this._loop(performance.now());

      // 首次打招呼
      setTimeout(() => {
        this.say(pick(GREETINGS), { title: '你好', duration: 4200 });
      }, 800);

      // 随机悄悄话
      this._scheduleIdleWhisper();

      // 全局事件 hook
      this._hookGlobalEvents();
    }

    // -------- 渲染循环 --------
    _loop(now) {
      const cfg = this._stateConfig;
      const stepMs = 1000 / cfg.fps;
      if (now - this._lastFrameTime >= stepMs) {
        this._lastFrameTime = now;
        this._applyFrame();
        this.frame += 1;
        if (this.frame >= cfg.frames) {
          if (cfg.loop) {
            this.frame = 0;
          } else {
            this.frame = cfg.frames - 1; // 定格最后一帧
            const cb = this._stateEndCallback;
            this._stateEndCallback = null;
            if (cb) cb();
          }
        }
      }
      this._rafId = requestAnimationFrame((t) => this._loop(t));
    }

    _applyFrame() {
      const cfg = this._stateConfig;
      const col = this.frame % COLS;
      const x = -col * CELL_W;
      const y = -cfg.row * CELL_H;
      // 因为有 scale：background-size 已按 scale 缩放，所以 position 也需要用 scale 坐标
      const scale = this._getScale();
      this.sprite.style.backgroundPosition = `${x * scale}px ${y * scale}px`;
    }

    _getScale() {
      const root = getComputedStyle(document.documentElement);
      const v = parseFloat(root.getPropertyValue('--pet-scale'));
      return Number.isFinite(v) ? v : 0.625;
    }

    // -------- 状态切换 --------
    setState(name) {
      if (!STATES[name]) return;
      if (this.state === name) return;
      this.state = name;
      this._stateConfig = STATES[name];
      this.frame = 0;
      this._applyFrame();
    }

    // 对外动作：react('greet'|'success'|'error'|'thinking'|'jump'|'idle'|'run'|'talk')
    react(kind, customText) {
      switch (kind) {
        case 'greet':
          this.setState('waving');
          this._stateEndCallback = () => this.setState('idle');
          this.say(customText || pick(GREETINGS), { title: '你好', duration: 3000 });
          break;
        case 'success':
          this.setState('jumping');
          this._stateEndCallback = () => this.setState('idle');
          this.say(customText || pick(SUCCESS), { title: '完成', duration: 2400, tone: 'success' });
          break;
        case 'error':
          this.setState('failed');
          this._stateEndCallback = () => this.setState('idle');
          this.say(customText || pick(FAIL), { title: '出错了', duration: 3200, tone: 'error' });
          break;
        case 'thinking':
          this.setState('review');
          this.say(customText || pick(THINKING), { title: '思考中', duration: 6000, tone: 'thinking' });
          break;
        case 'jump':
          this.setState('jumping');
          this._stateEndCallback = () => this.setState('idle');
          break;
        case 'idle':
          this.setState('idle');
          break;
        case 'run':
          this.setState('running');
          break;
        case 'talk':
          this.say(customText || pick(IDLE_WHISPERS));
          break;
      }
    }

    // -------- 气泡 --------
    say(text, opts = {}) {
      if (!text) return;
      const { title = '银伴', duration = 3000, tone = 'info' } = opts;
      this.bubbleTitle.textContent = title;
      this.bubbleText.textContent = text;
      const bs = this.bubbleState;
      bs.style.display = 'none'; // 默认不显示状态标签
      // 根据 tone 修改颜色
      this.bubble.style.borderColor = 'rgba(255,255,255,0.9)';
      if (tone === 'success') this.bubble.style.boxShadow = 'inset 0 1px 0 rgba(255,255,255,0.95), 0 14px 30px rgba(16,185,129,0.22)';
      else if (tone === 'error') this.bubble.style.boxShadow = 'inset 0 1px 0 rgba(255,255,255,0.95), 0 14px 30px rgba(239,68,68,0.22)';
      else if (tone === 'thinking') this.bubble.style.boxShadow = 'inset 0 1px 0 rgba(255,255,255,0.95), 0 14px 30px rgba(129,140,248,0.22)';
      else this.bubble.style.boxShadow = '';

      this.bubble.classList.add('show');
      clearTimeout(this._bubbleTimer);
      this._bubbleTimer = setTimeout(() => {
        this.bubble.classList.remove('show');
      }, duration);
    }

    _scheduleIdleWhisper() {
      clearTimeout(this._idleWhisperTimer);
      const next = 16000 + Math.random() * 18000; // 16~34s 间随机
      this._idleWhisperTimer = setTimeout(() => {
        if (!this._hidden && !this.bubble.classList.contains('show')) {
          this.say(pick(IDLE_WHISPERS), { title: '悄悄话', duration: 2400 });
        }
        this._scheduleIdleWhisper();
      }, next);
    }

    // -------- 拖拽 --------
    _bindDrag() {
      const host = this.host;
      const start = (clientX, clientY) => {
        const rect = host.getBoundingClientRect();
        this._drag = {
          startX: clientX,
          startY: clientY,
          offsetX: clientX - rect.left,
          offsetY: clientY - rect.top,
          moved: false
        };
        host.classList.add('dragging');
      };
      const move = (clientX, clientY) => {
        if (!this._drag) return;
        const dx = clientX - this._drag.startX;
        const dy = clientY - this._drag.startY;
        if (!this._drag.moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
          this._drag.moved = true;
        }
        if (this._drag.moved) {
          // 用左上角定位
          const x = clientX - this._drag.offsetX;
          const y = clientY - this._drag.offsetY;
          const maxX = window.innerWidth - host.offsetWidth - 4;
          const maxY = window.innerHeight - host.offsetHeight - 4;
          const cx = Math.max(4, Math.min(maxX, x));
          const cy = Math.max(4, Math.min(maxY, y));
          host.style.left = cx + 'px';
          host.style.top = cy + 'px';
          host.style.right = 'auto';
          host.style.bottom = 'auto';
        }
      };
      const end = () => {
        if (!this._drag) return;
        const wasMoved = this._drag.moved;
        host.classList.remove('dragging');
        if (wasMoved) {
          const rect = host.getBoundingClientRect();
          this._pos = { x: rect.left, y: rect.top };
          this._savePos();
          this._justDragged = true;
        }
        this._drag = null;
      };
      host.addEventListener('pointerdown', (e) => {
        if (e.button && e.button !== 0) return;
        host.setPointerCapture(e.pointerId);
        start(e.clientX, e.clientY);
      });
      host.addEventListener('pointermove', (e) => {
        if (!this._drag) return;
        move(e.clientX, e.clientY);
      });
      host.addEventListener('pointerup', end);
      host.addEventListener('pointercancel', end);
    }

    // -------- 右键菜单 --------
    _showMenu(x, y) {
      const m = this.menu;
      m.classList.add('show');
      // 等一帧拿到尺寸
      requestAnimationFrame(() => {
        const w = m.offsetWidth, h = m.offsetHeight;
        const maxX = window.innerWidth - w - 8;
        const maxY = window.innerHeight - h - 8;
        m.style.left = Math.min(x, maxX) + 'px';
        m.style.top = Math.min(y, maxY) + 'px';
      });
    }
    _hideMenu() { this.menu.classList.remove('show'); }

    // -------- 位置持久化 --------
    _loadPos() {
      try {
        const raw = localStorage.getItem(LS_KEY);
        if (!raw) return { x: null, y: null };
        return JSON.parse(raw);
      } catch { return { x: null, y: null }; }
    }
    _savePos() {
      try { localStorage.setItem(LS_KEY, JSON.stringify(this._pos)); } catch { }
    }
    _applyPos() {
      // 固定在左下角（由 CSS 控制），只有用户主动拖过之后才用绝对坐标覆盖
      const hasSavedPos = this._pos && this._pos.x != null && this._pos.y != null;
      if (hasSavedPos) {
        const h = this.host;
        h.style.left = this._pos.x + 'px';
        h.style.top = this._pos.y + 'px';
        h.style.right = 'auto';
        h.style.bottom = 'auto';
      }
    }

    _dockToSlot(slot) {
      const h = this.host;
      const r = slot.getBoundingClientRect();
      // 把宠物放在槽位中央偏下
      const x = r.left + (r.width - h.offsetWidth) / 2;
      const y = r.top + (r.height - h.offsetHeight) / 2 + 6;
      h.style.left = Math.max(4, x) + 'px';
      h.style.top = Math.max(4, y) + 'px';
      h.style.right = 'auto';
      h.style.bottom = 'auto';
    }

    // -------- 显隐 --------
    hide(persist = true) {
      this._hidden = true;
      this.host.classList.add('hidden');
      this.recall.classList.add('show');
      if (persist) localStorage.setItem(LS_HIDE, '1');
    }
    show() {
      this._hidden = false;
      this.host.classList.remove('hidden');
      this.recall.classList.remove('show');
      localStorage.removeItem(LS_HIDE);
      this.react('greet', '我回来啦～');
    }
    toggle() { this._hidden ? this.show() : this.hide(); }

    // -------- Hook 全局事件 --------
    _hookGlobalEvents() {
      // 1) 自定义事件
      window.addEventListener('zh-pet', (e) => {
        const d = e.detail || {};
        if (d.say) this.say(d.say, d.opts || {});
        if (d.react) this.react(d.react, d.text);
        if (d.state) this.setState(d.state);
      });

      // 2) 观察所有 toast-wrap 里的新节点，根据 class 给出反应
      const tryObserve = () => {
        const wrap = document.querySelector('.toast-wrap');
        if (!wrap) return false;
        const mo = new MutationObserver((muts) => {
          muts.forEach(m => {
            m.addedNodes.forEach(n => {
              if (!(n instanceof HTMLElement)) return;
              const cls = n.className || '';
              if (/toast-success/.test(cls)) this.react('success');
              else if (/toast-error/.test(cls)) this.react('error');
              else if (/toast-warning/.test(cls)) this.setState('waiting');
            });
          });
        });
        mo.observe(wrap, { childList: true });
        return true;
      };
      if (!tryObserve()) {
        // 延迟重试：toast wrap 可能在首次交互时才创建
        const retry = setInterval(() => { if (tryObserve()) clearInterval(retry); }, 400);
      }
    }
  }

  // ---- 启动 ----
  function boot() {
    if (window.ZhPet && typeof window.ZhPet.show === 'function') return;
    const pet = new ZhPet();
    window.ZhPet = pet;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
