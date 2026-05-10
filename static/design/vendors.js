/* === 外部资源懒加载器 ========================================================
   用 CDN 引入 GSAP / Lottie / Hero Patterns 等开源资源。
   所有资源带超时兜底，离线时优雅降级为原生 CSS 动画。

   资源清单：
   · GSAP 3.12      (Standard License, 免费) — 入场、数字滚动动画
   · Lottie Player  (MIT License)             — hero 区矢量动画
   · unDraw 插画    (MIT License, 静态 SVG)   — 空态/加载页
   ========================================================================== */
(function(){
  const VENDORS = {
    gsap: {
      url: 'https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js',
      check: () => !!window.gsap
    },
    scrollTrigger: {
      url: 'https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js',
      check: () => !!(window.gsap && window.ScrollTrigger),
      deps: ['gsap']
    },
    lottie: {
      url: 'https://cdn.jsdelivr.net/npm/@lottiefiles/lottie-player@2.0.8/dist/lottie-player.js',
      check: () => !!customElements.get('lottie-player')
    }
  };

  const loaded = {};
  const pending = {};

  function loadScript(url, timeout = 6000){
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      const timer = setTimeout(() => {
        s.remove();
        reject(new Error('timeout: ' + url));
      }, timeout);
      s.onload = () => { clearTimeout(timer); resolve(); };
      s.onerror = () => { clearTimeout(timer); reject(new Error('failed: ' + url)); };
      document.head.appendChild(s);
    });
  }

  async function load(name){
    if(loaded[name]) return true;
    if(pending[name]) return pending[name];

    const cfg = VENDORS[name];
    if(!cfg){ console.warn('[ZhVendors] unknown vendor:', name); return false; }

    // 已在全局可用（可能由其它脚本提前引入）
    if(cfg.check && cfg.check()){ loaded[name] = true; return true; }

    pending[name] = (async () => {
      try{
        // 先装依赖
        if(cfg.deps){
          for(const dep of cfg.deps){ await load(dep); }
        }
        await loadScript(cfg.url);
        loaded[name] = true;
        return true;
      }catch(e){
        console.warn('[ZhVendors] load failed', name, '-', e.message, '| UI fallback to CSS');
        return false;
      }finally{
        delete pending[name];
      }
    })();
    return pending[name];
  }

  async function loadAll(names){
    return Promise.all((names || []).map(load));
  }

  window.ZhVendors = { load, loadAll };
})();
