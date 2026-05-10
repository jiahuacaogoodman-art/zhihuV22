/* === Dialog + Toast ========================================================
   替代 SweetAlert2，保留常用 API：
     ZhDialog.confirm({title, text, confirmText, cancelText}) → Promise<bool>
     ZhDialog.prompt({title, fields:[{id,label,placeholder,unit}]}) → Promise<values|null>
     ZhDialog.alert({title, text}) → Promise<void>
     ZhToast.show(msg, type='info')   type: success|error|warning|info
   ========================================================================== */
(function(){
  const iconSvg = (name) => (window.ZhIcons && window.ZhIcons.svg(name)) || '';

  function mkOverlay(){
    const ov = document.createElement('div');
    ov.className = 'dialog-ov';
    ov.setAttribute('role','dialog');
    ov.setAttribute('aria-modal','true');
    return ov;
  }
  function close(ov){ ov.style.opacity='0'; setTimeout(()=>ov.remove(), 160); }

  function confirm(opts={}){
    return new Promise(resolve=>{
      const ov = mkOverlay();
      const box = document.createElement('div'); box.className = 'dialog';
      const title = document.createElement('div'); title.className = 'dialog-title'; title.textContent = opts.title || '确认';
      const body  = document.createElement('div'); body.className = 'dialog-body'; body.textContent = opts.text || '';
      const acts  = document.createElement('div'); acts.className = 'dialog-actions';
      const cancel = document.createElement('button'); cancel.className = 'btn btn-ghost'; cancel.textContent = opts.cancelText || '取消';
      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');
      confirmBtn.textContent = opts.confirmText || '确定';
      acts.append(cancel, confirmBtn);
      box.append(title, body, acts);
      ov.append(box);
      document.body.appendChild(ov);
      const done = (v)=>{ close(ov); resolve(v); };
      cancel.onclick = ()=>done(false);
      confirmBtn.onclick = ()=>done(true);
      ov.addEventListener('click', e=>{ if(e.target===ov) done(false); });
      document.addEventListener('keydown', function esc(e){ if(e.key==='Escape'){ done(false); document.removeEventListener('keydown', esc); } });
      confirmBtn.focus();
    });
  }

  function alertDialog(opts={}){
    return confirm({ ...opts, confirmText: opts.confirmText || '知道了', cancelText: null })
      .then(()=>undefined);
  }
  // 覆盖：alert 没有取消按钮
  function alertFn(opts={}){
    return new Promise(resolve=>{
      const ov = mkOverlay();
      const box = document.createElement('div'); box.className = 'dialog';
      const title = document.createElement('div'); title.className = 'dialog-title'; title.textContent = opts.title || '提示';
      const body  = document.createElement('div'); body.className = 'dialog-body'; body.textContent = opts.text || '';
      const acts  = document.createElement('div'); acts.className = 'dialog-actions';
      const ok = document.createElement('button'); ok.className = 'btn btn-primary'; ok.textContent = opts.confirmText || '知道了';
      acts.append(ok);
      box.append(title, body, acts);
      ov.append(box);
      document.body.appendChild(ov);
      ok.onclick = ()=>{ close(ov); resolve(); };
      ov.addEventListener('click', e=>{ if(e.target===ov){ close(ov); resolve(); } });
      ok.focus();
    });
  }

  function prompt(opts={}){
    const fields = opts.fields || [{id:'value', label:opts.label || '', placeholder:opts.placeholder || ''}];
    return new Promise(resolve=>{
      const ov = mkOverlay();
      const box = document.createElement('div'); box.className = 'dialog';
      const title = document.createElement('div'); title.className = 'dialog-title'; title.textContent = opts.title || '请输入';
      const form = document.createElement('form');
      form.style.display='flex'; form.style.flexDirection='column'; form.style.gap='12px'; form.style.marginBottom='16px';
      fields.forEach((f,i)=>{
        const wrap = document.createElement('label');
        wrap.className = 'field-group';
        const lbl = document.createElement('span'); lbl.className = 'field-label'; lbl.textContent = f.label || '';
        const inp = document.createElement('input');
        inp.className = 'field'; inp.id = '_dlg_'+f.id; inp.placeholder = f.placeholder || '';
        inp.value = f.value || '';
        wrap.append(lbl, inp);
        if(f.hint){
          const h = document.createElement('span'); h.className='field-hint'; h.textContent=f.hint; wrap.append(h);
        }
        form.append(wrap);
        if(i===0) setTimeout(()=>inp.focus(), 50);
      });
      const acts  = document.createElement('div'); acts.className = 'dialog-actions';
      const cancel = document.createElement('button'); cancel.type='button'; cancel.className = 'btn btn-ghost'; cancel.textContent = opts.cancelText || '取消';
      const ok = document.createElement('button'); ok.type='submit'; ok.className = 'btn btn-primary'; ok.textContent = opts.confirmText || '确定';
      acts.append(cancel, ok);
      box.append(title, form, acts);
      ov.append(box);
      document.body.appendChild(ov);
      const done = (v)=>{ close(ov); resolve(v); };
      cancel.onclick = ()=>done(null);
      form.onsubmit = (e)=>{ e.preventDefault(); const out = {}; fields.forEach(f=>{ out[f.id] = document.getElementById('_dlg_'+f.id).value.trim(); }); done(out); };
      ov.addEventListener('click', e=>{ if(e.target===ov) done(null); });
    });
  }

  /* ── Toast ───────────────────────────────────────── */
  function getToastWrap(){
    let wrap = document.querySelector('.toast-wrap');
    if(!wrap){
      wrap = document.createElement('div');
      wrap.className = 'toast-wrap';
      document.body.appendChild(wrap);
    }
    return wrap;
  }
  const ICONS = {
    success: iconSvg('check'),
    error:   iconSvg('alert'),
    warning: iconSvg('alert'),
    info:    iconSvg('info'),
  };
  function toast(msg, type='info', ms=3200){
    const wrap = getToastWrap();
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.innerHTML = `<span class="toast-icon">${ICONS[type]||ICONS.info}</span><span>${String(msg).replace(/[<>&]/g, c=>({ '<':'&lt;','>':'&gt;','&':'&amp;' }[c]))}</span>`;
    wrap.appendChild(el);
    setTimeout(()=>{
      el.style.transition = 'opacity 200ms ease, transform 200ms ease';
      el.style.opacity = '0'; el.style.transform = 'translateY(-6px)';
      setTimeout(()=>el.remove(), 220);
    }, ms);
  }

  window.ZhDialog = { confirm, prompt, alert: alertFn };
  window.ZhToast  = { show: toast };
})();
