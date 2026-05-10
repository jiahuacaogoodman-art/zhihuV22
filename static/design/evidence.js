/* === 证据面板 + 引用链接化 ===============================================
   用法：
     const panel = new EvidencePanel(element);   // element 是一个空 div
     panel.render(evidenceList, memoryList);      // 任意时候重新渲染
     panel.linkifyNode(textNode);                  // 把一段文本里的 [E1] 变成可点芯片
     panel.onOutcomeRecord(fn);                    // 决策记忆上"记录结果"的回调
   ========================================================================== */
(function(){
  const esc = (s) => String(s==null?'':s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

  const OUTCOME_LABEL = {
    pending: '未回填',
    effective: '有效',
    ineffective: '无效',
    partial: '部分有效',
  };

  class EvidencePanel{
    constructor(root, opts={}){
      this.root = root;
      this.opts = opts;
      this.evidence = [];
      this.memory = [];
      this.scope = opts.scope || ('ev-'+Math.random().toString(36).slice(2,8));
      this.outcomeHandler = null;
      this._renderSkeleton();
    }

    _renderSkeleton(){
      this.root.classList.add('evidence-panel');
      this.root.innerHTML = `
        <div class="evidence-panel-head">
          <span class="icon-s" data-icon="book"></span>
          <span class="label">引用证据</span>
          <span class="meta" data-evidence-count>0 条</span>
        </div>
        <div class="evidence-list" data-evidence-list>
          <div class="empty body-s">等待检索…</div>
        </div>
        <div class="glass-divider" data-memory-divider style="display:none"></div>
        <div class="evidence-panel-head" data-memory-head style="display:none">
          <span class="icon-s" data-icon="history"></span>
          <span class="label">近期决策记忆</span>
          <span class="meta" data-memory-count>0 条</span>
        </div>
        <div class="evidence-list" data-memory-list></div>
      `;
      if(window.ZhIcons) window.ZhIcons.mount(this.root);
    }

    onOutcomeRecord(fn){ this.outcomeHandler = fn; }

    clear(){
      this.evidence = [];
      this.memory = [];
      this._renderSkeleton();
    }

    render(evidence, memory){
      this.evidence = Array.isArray(evidence) ? evidence : [];
      this.memory = Array.isArray(memory) ? memory : [];

      const list = this.root.querySelector('[data-evidence-list]');
      const count = this.root.querySelector('[data-evidence-count]');
      count.textContent = `${this.evidence.length} 条`;
      if(!this.evidence.length){
        list.innerHTML = '<div class="empty body-s">未检索到该患者的相关档案</div>';
      }else{
        list.innerHTML = this.evidence.map(e => this._evidenceCard(e)).join('');
      }

      const memDiv = this.root.querySelector('[data-memory-divider]');
      const memHead = this.root.querySelector('[data-memory-head]');
      const memList = this.root.querySelector('[data-memory-list]');
      const memCount = this.root.querySelector('[data-memory-count]');
      if(this.memory.length){
        memDiv.style.display = '';
        memHead.style.display = '';
        memCount.textContent = `${this.memory.length} 条`;
        memList.innerHTML = this.memory.map(m => this._memoryCard(m)).join('');
        memList.querySelectorAll('[data-record-outcome]').forEach(btn => {
          btn.addEventListener('click', (e)=>{
            e.preventDefault();
            const did = btn.getAttribute('data-record-outcome');
            this._openOutcomeDialog(did);
          });
        });
      }else{
        memDiv.style.display = 'none';
        memHead.style.display = 'none';
        memList.innerHTML = '';
      }
      if(window.ZhIcons) window.ZhIcons.mount(this.root);
    }

    _evidenceCard(e){
      const eid = esc(e.evidence_id || '');
      return `
        <div class="evidence-card" data-evidence-scope="${this.scope}" data-evidence-id="${eid}">
          <div class="evidence-card-head">
            <span class="evidence-id">${eid}</span>
            <span class="evidence-source">${esc(e.source_label || e.source_type || '档案')}</span>
          </div>
          <div class="evidence-snippet">${esc(e.snippet || '')}</div>
        </div>
      `;
    }

    _memoryCard(m){
      const status = m.outcome_status || 'pending';
      const statusLabel = OUTCOME_LABEL[status] || status;
      const statusCls = 'outcome-'+status;
      const btn = status === 'pending'
        ? `<button class="btn btn-sm btn-outline" data-record-outcome="${esc(m.decision_id)}"><span class="icon-s" data-icon="check"></span>记录结果</button>`
        : `<span class="memory-outcome ${statusCls}"><span class="icon-s" data-icon="check"></span>${esc(statusLabel)}</span>`;
      return `
        <div class="memory-item">
          <div class="memory-head">
            <span class="meta">${esc(m.timestamp || '—')}</span>
            ${btn}
          </div>
          <div class="memory-symptom">${esc((m.symptom||'').slice(0,80))}</div>
          <div class="memory-advice">${esc((m.advice_preview||'').slice(0,140))}</div>
          ${m.outcome_note ? `<div class="meta">备注：${esc(m.outcome_note)}</div>` : ''}
        </div>
      `;
    }

    _openOutcomeDialog(decisionId){
      if(!window.ZhDialog) return;
      // 先问 status：用 confirm 三选一不够方便，这里直接用 prompt 给自由文本 + 下拉
      // 简化：先 confirm 选 effective/ineffective，再 prompt 备注
      const html = `
        <div style="display:flex; gap:8px; margin-bottom: 12px;">
          <button class="btn btn-sm btn-outline" data-outcome="effective">有效</button>
          <button class="btn btn-sm btn-outline" data-outcome="partial">部分有效</button>
          <button class="btn btn-sm btn-outline" data-outcome="ineffective">无效</button>
        </div>`;
      // 自建一个小对话（复用样式）
      const ov = document.createElement('div');
      ov.className = 'dialog-ov';
      ov.innerHTML = `
        <div class="dialog">
          <div class="dialog-title">回填该决策的执行结果</div>
          <div class="dialog-body">
            <div class="body-s" style="margin-bottom:12px">选择结果 → 下一步填写备注</div>
            ${html}
          </div>
          <div class="dialog-actions"><button class="btn btn-ghost" data-cancel>取消</button></div>
        </div>`;
      document.body.appendChild(ov);
      const done = () => ov.remove();
      ov.querySelector('[data-cancel]').onclick = done;
      ov.addEventListener('click', e=>{ if(e.target===ov) done(); });
      ov.querySelectorAll('[data-outcome]').forEach(b => {
        b.onclick = async () => {
          const status = b.getAttribute('data-outcome');
          done();
          const v = await window.ZhDialog.prompt({
            title: '备注（可选）',
            confirmText: '提交',
            fields: [{ id:'note', label:'执行详情', placeholder:'例如：复测血糖 3.8，已按预案处理' }],
          });
          if(v === null) return;
          if(this.outcomeHandler){
            try{
              await this.outcomeHandler(decisionId, status, v.note || '');
              window.ZhToast && window.ZhToast.show('结果已记录，AI 下次检索会看到', 'success');
            }catch(err){
              window.ZhToast && window.ZhToast.show(String(err), 'error');
            }
          }
        };
      });
    }

    /** 把一个 text node 里的 [E1] [E2] 替换成可点芯片 */
    linkifyNode(node){
      if(!node || node.nodeType !== Node.TEXT_NODE) return;
      const text = node.nodeValue;
      if(!text || text.indexOf('[E') < 0) return;
      const re = /\[E(\d+)\]/g;
      let match, lastIdx = 0;
      const frag = document.createDocumentFragment();
      while((match = re.exec(text)) !== null){
        if(match.index > lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
        const eid = 'E' + match[1];
        const chip = document.createElement('button');
        chip.className = 'cite';
        chip.type = 'button';
        chip.textContent = eid;
        chip.setAttribute('data-cite', eid);
        chip.setAttribute('data-target', this.scope);
        chip.addEventListener('click', ()=>this.highlight(eid));
        frag.appendChild(chip);
        lastIdx = match.index + match[0].length;
      }
      if(lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      node.parentNode.replaceChild(frag, node);
    }

    /** 把容器里剩下的 text 节点都 linkify 一遍（流式 append 时调用） */
    linkifyContainer(root){
      if(!root) return;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const texts = [];
      let n; while((n = walker.nextNode())) texts.push(n);
      texts.forEach(t => this.linkifyNode(t));
    }

    highlight(eid){
      const card = this.root.querySelector(`[data-evidence-scope="${this.scope}"][data-evidence-id="${eid}"]`);
      if(!card) return;
      this.root.querySelectorAll('.evidence-card.highlight').forEach(c=>c.classList.remove('highlight'));
      card.classList.add('highlight');
      card.scrollIntoView({ behavior:'smooth', block:'center' });
      setTimeout(()=>card.classList.remove('highlight'), 2400);
    }
  }

  window.EvidencePanel = EvidencePanel;
})();
