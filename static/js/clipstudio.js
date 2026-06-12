/* Clip Studio (Auto Klip VIP) — halaman utama: input -> progress -> grid hasil. */
(function () {
  'use strict';

  const API = '/api/v1/clipstudio';
  const $ = (id) => document.getElementById(id);

  // ---------- auth ----------
  function token() { return localStorage.getItem('token'); }
  function authHeaders(extra) {
    return Object.assign({ 'Authorization': 'Bearer ' + token() }, extra || {});
  }
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = authHeaders(opts.headers);
    const r = await fetch(API + path, opts);
    if (r.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/?relogin=1';
      throw new Error('Sesi berakhir, silakan login ulang.');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.error || ('HTTP ' + r.status));
    return data;
  }
  if (!token()) { window.location.href = '/?relogin=1'; return; }

  // ---------- state ----------
  let pollTimer = null;
  let currentProject = null;
  let uploadFile = null;

  const STAGES = [
    { key: 'downloading', icon: '⬇️', name: 'Downloading', desc: 'Mengunduh video sumber' },
    { key: 'transcribing', icon: '🎙️', name: 'Transcribing', desc: 'Transkripsi per kata (Whisper AI)' },
    { key: 'analyzing', icon: '🧠', name: 'Analyzing (AI)', desc: 'AI memilih momen paling viral' },
    { key: 'reframing', icon: '🎯', name: 'Reframing', desc: 'Tracking wajah & crop 9:16' },
    { key: 'rendering', icon: '✨', name: 'Rendering captions', desc: 'Menyiapkan thumbnail & aset editor' },
    { key: 'done', icon: '✅', name: 'Done', desc: 'Semua klip siap!' },
  ];

  // ---------- views ----------
  function show(view) {
    ['viewInput', 'viewProgress', 'viewResults'].forEach((v) => $(v).classList.add('hidden'));
    $(view).classList.remove('hidden');
    window.scrollTo(0, 0);
  }

  // ---------- templates ----------
  async function loadTemplates() {
    try {
      const d = await api('/templates');
      const sel = $('optTemplate');
      sel.innerHTML = '';
      d.templates.forEach((t) => {
        const o = document.createElement('option');
        o.value = t.id; o.textContent = t.name;
        sel.appendChild(o);
      });
    } catch (e) { /* non-fatal */ }
  }

  // ---------- riwayat ----------
  async function loadHistory() {
    try {
      const d = await api('/projects');
      const box = $('historyList');
      box.innerHTML = '';
      let credits = 0;
      d.projects.forEach((p) => { credits += p.credits_used || 0; });
      $('creditsVal').textContent = credits;
      if (!d.projects.length) {
        box.innerHTML = '<p style="color:var(--muted);font-size:.85rem">Belum ada project. Paste link YouTube di atas untuk mulai!</p>';
        return;
      }
      d.projects.forEach((p) => {
        const row = document.createElement('div');
        row.className = 'hrow';
        const st = p.status === 'done' ? ['st-done', 'Selesai'] :
                   p.status === 'error' ? ['st-error', 'Gagal'] : ['st-proc', 'Proses ' + p.percent + '%'];
        row.innerHTML =
          '<img src="' + (p.thumbnail || '') + '" onerror="this.style.visibility=\'hidden\'">' +
          '<div class="meta"><div class="t"></div>' +
          '<div class="s">' + fmtDur(p.duration) + ' • ' + (p.credits_used || 0) + ' credit • ' + fmtDate(p.created_at) + '</div></div>' +
          '<span class="hstatus ' + st[0] + '">' + st[1] + '</span>' +
          '<button class="delbtn" title="Hapus project">🗑</button>';
        row.querySelector('.t').textContent = p.title || '(tanpa judul)';
        row.addEventListener('click', () => {
          if (p.status === 'done') openResults(p.id);
          else if (p.status !== 'error') watchProgress(p.id);
        });
        row.querySelector('.delbtn').addEventListener('click', async (ev) => {
          ev.stopPropagation();
          if (!confirm('Hapus project ini beserta semua klipnya?')) return;
          await api('/projects/' + p.id, { method: 'DELETE' });
          loadHistory();
        });
        box.appendChild(row);
      });
    } catch (e) { console.error(e); }
  }

  function fmtDur(s) {
    s = Math.round(s || 0);
    const m = Math.floor(s / 60), ss = s % 60;
    return m + ':' + String(ss).padStart(2, '0');
  }
  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  // ---------- opsi ----------
  function collectOptions() {
    const rs = parseFloat($('optRangeStart').value);
    const re = parseFloat($('optRangeEnd').value);
    return {
      clip_length: $('optLen').value,
      range_start: isNaN(rs) ? 0 : rs * 60,
      range_end: isNaN(re) ? 0 : re * 60,
      language: $('optLang').value,
      max_clips: parseInt($('optMax').value, 10),
      aspect_ratio: $('optAspect').value,
      caption_template: $('optTemplate').value || 'opus-green',
      prompt: ($('optPrompt') ? $('optPrompt').value.trim() : ''),   // ClipAnything
    };
  }

  // ---------- submit ----------
  async function startProject() {
    const btn = $('btnGo');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin" style="display:inline-block;vertical-align:-2px"></span> Memulai...';
    try {
      let d;
      if (uploadFile) {
        const fd = new FormData();
        fd.append('file', uploadFile);
        fd.append('options', JSON.stringify(collectOptions()));
        d = await api('/projects/upload', { method: 'POST', body: fd });
      } else {
        const url = $('ytUrl').value.trim();
        if (!url) { alert('Masukkan link YouTube dulu, atau pilih file upload.'); return; }
        d = await api('/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_url: url, options: collectOptions() }),
        });
      }
      watchProgress(d.project_id);
    } catch (e) {
      alert(e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = '⚡ Get clips';
    }
  }

  // ---------- progress ----------
  function renderStages(activeKey, percent, isError) {
    const box = $('stagesBox');
    box.innerHTML = '';
    const order = STAGES.map((s) => s.key);
    const ai = order.indexOf(activeKey);
    STAGES.forEach((s, i) => {
      const div = document.createElement('div');
      div.className = 'stage' + (i < ai || activeKey === 'done' ? ' done' : i === ai && !isError ? ' active' : '');
      const icon = (i < ai || activeKey === 'done') ? '✓' : s.icon;
      const spin = (i === ai && !isError && activeKey !== 'done') ? '<div class="spin" style="margin-left:auto"></div>' : '';
      div.innerHTML = '<div class="ic">' + icon + '</div><div><div class="nm">' + s.name + '</div><div class="ds">' + s.desc + '</div></div>' + spin;
      box.appendChild(div);
    });
  }

  function watchProgress(projectId) {
    currentProject = projectId;
    show('viewProgress');
    $('progErr').classList.add('hidden');
    renderStages('downloading', 0, false);
    clearInterval(pollTimer);

    const tick = async () => {
      try {
        const st = await api('/projects/' + projectId + '/status');
        $('progBar').style.width = st.percent + '%';
        $('progPct').textContent = st.percent + '% — ' + (st.status || '');
        if (st.title) $('progTitle').textContent = st.title;
        renderStages(st.status === 'queued' ? 'downloading' : st.status, st.percent, st.status === 'error');
        if (st.status === 'done') {
          clearInterval(pollTimer);
          openResults(projectId);
        } else if (st.status === 'error') {
          clearInterval(pollTimer);
          const eb = $('progErr');
          eb.textContent = '❌ ' + (st.error_message || 'Terjadi kesalahan.');
          eb.classList.remove('hidden');
        }
      } catch (e) { console.error(e); }
    };
    tick();
    pollTimer = setInterval(tick, 2000); // polling tiap 2 detik sesuai spec
  }

  // ---------- hasil ----------
  async function openResults(projectId) {
    currentProject = projectId;
    try {
      const d = await api('/projects/' + projectId);
      show('viewResults');
      $('resTitle').textContent = d.project.title || 'Hasil Klip';
      $('resSub').textContent = d.clips.length + ' klip • ' + fmtDur(d.project.duration) +
        ' • ' + (d.project.credits_used || 0) + ' credit dipakai';
      const grid = $('clipGrid');
      grid.innerHTML = '';
      d.clips.forEach((c) => {
        const card = document.createElement('div');
        card.className = 'clipcard';
        // breakdown skor ala Opus (hook/flow/value/trend)
        const bd = c.score_breakdown || {};
        let bdHtml = '';
        if (bd.hook != null) {
          bdHtml = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:8px">' +
            [['Hook', bd.hook], ['Flow', bd.flow], ['Value', bd.value], ['Trend', bd.trend]].map(([n, v]) =>
              '<div><div style="font-size:.58rem;color:var(--muted)">' + n + ' <b style="color:var(--text)">' + (v || 0) + '</b></div>' +
              '<div style="background:var(--card2);height:4px;border-radius:3px;overflow:hidden">' +
              '<div style="height:100%;width:' + (v || 0) + '%;background:linear-gradient(90deg,var(--accent),var(--accent2))"></div></div></div>'
            ).join('') + '</div>';
        }
        card.innerHTML =
          '<div class="thumbwrap">' +
            '<img loading="lazy" src="' + (c.thumbnail || '') + '" onerror="this.style.visibility=\'hidden\'">' +
            '<span class="scorebadge">🔥 ' + c.score + '</span>' +
            '<span class="durbadge">' + fmtDur(c.duration) + '</span>' +
          '</div>' +
          '<div class="body"><h3></h3>' + bdHtml +
          '<div class="reason"></div><div class="snip"></div><div class="tags"></div></div>';
        card.querySelector('h3').textContent = c.title || 'Klip';
        card.querySelector('.reason').textContent = c.reason ? '💡 ' + c.reason : '';
        card.querySelector('.snip').textContent = c.snippet ? '“' + c.snippet + '”' : '';
        const tags = card.querySelector('.tags');
        (c.hashtags || []).forEach((h) => {
          const s = document.createElement('span');
          s.textContent = h; tags.appendChild(s);
        });
        card.addEventListener('click', () => {
          window.location.href = '/clipstudio/editor?clip=' + c.id;
        });
        grid.appendChild(card);
      });
      window.__lastClips = d.clips;
      loadHistory();
    } catch (e) { alert(e.message); }
  }

  // ---------- reprompt (ClipAnything ulang) ----------
  function openResDlg(html) {
    $('resDlg').classList.remove('hidden');
    $('resDlgBox').innerHTML = html;
    $('resDlg').onclick = (e) => { if (e.target === $('resDlg')) $('resDlg').classList.add('hidden'); };
  }
  $('btnReprompt').addEventListener('click', () => {
    openResDlg(
      '<h3 style="font-family:Outfit,sans-serif;margin-bottom:10px">🪄 Reprompt AI</h3>' +
      '<p style="font-size:.82rem;color:var(--muted);margin-bottom:10px">Kurasi ulang klip dengan instruksi baru — tanpa download & transkripsi ulang. Klip lama akan diganti.</p>' +
      '<textarea id="repromptText" rows="3" placeholder="Contoh: fokus momen paling emosional / cari semua bagian tentang harga produk..." style="width:100%;background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:10px;color:var(--text);font-size:.9rem;outline:none;font-family:inherit"></textarea>' +
      '<div style="display:flex;gap:10px;margin-top:14px"><button class="btn btn-ghost" style="flex:1" onclick="document.getElementById(\'resDlg\').classList.add(\'hidden\')">Batal</button>' +
      '<button class="btn btn-primary" style="flex:1" id="repromptGo">🚀 Jalankan</button></div>'
    );
    $('repromptGo').addEventListener('click', async () => {
      const prompt = $('repromptText').value.trim();
      $('repromptGo').disabled = true;
      try {
        await api('/projects/' + currentProject + '/reprompt', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        });
        $('resDlg').classList.add('hidden');
        watchProgress(currentProject);
      } catch (e) { alert(e.message); $('repromptGo').disabled = false; }
    });
  });

  // ---------- bulk export semua klip ----------
  $('btnBulkExport').addEventListener('click', async () => {
    const clips = window.__lastClips || [];
    if (!clips.length) return;
    if (!confirm('Render semua ' + clips.length + ' klip ke MP4 1080p? Proses berjalan berurutan.')) return;
    let rows = clips.map((c, i) =>
      '<div style="display:flex;align-items:center;gap:8px;font-size:.8rem;padding:6px 0;border-bottom:1px solid var(--border)">' +
      '<span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + (c.title || 'Klip ' + (i + 1)) + '</span>' +
      '<span id="bx' + i + '" style="color:var(--muted)">antri…</span></div>').join('');
    openResDlg('<h3 style="font-family:Outfit,sans-serif;margin-bottom:10px">📦 Export semua klip</h3>' + rows +
      '<p style="font-size:.75rem;color:var(--muted);margin-top:10px">Biarkan halaman terbuka. File bisa diunduh dari tiap klip → Export → riwayat.</p>');
    for (let i = 0; i < clips.length; i++) {
      const el = () => document.getElementById('bx' + i);
      try {
        const e = await api('/clips/' + clips[i].id + '/export', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ resolution: '1080p', watermark: false }),
        });
        // poll sampai selesai (berurutan agar tidak membebani server)
        for (;;) {
          await new Promise((r) => setTimeout(r, 2500));
          const st = await api('/exports/' + e.export_id);
          if (!el()) return; // dialog ditutup
          if (st.status === 'done') {
            el().innerHTML = '<a href="' + st.file_path + '" download style="color:var(--accent2)">⬇ unduh</a>';
            break;
          }
          if (st.status === 'error') { el().textContent = '❌ gagal'; break; }
          el().textContent = (st.status === 'queued' ? 'antri ' : 'render ') + st.percent + '%';
        }
      } catch (err) { if (el()) el().textContent = '❌ ' + err.message; }
    }
  });

  // ---------- events ----------
  $('btnGo').addEventListener('click', startProject);
  $('ytUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') startProject(); });
  $('fileUpload').addEventListener('change', (e) => {
    uploadFile = e.target.files[0] || null;
    $('uploadName').textContent = uploadFile ? '📎 ' + uploadFile.name + ' (klik Get clips untuk mulai)' : '';
    if (uploadFile) $('ytUrl').value = '';
  });
  $('ytUrl').addEventListener('input', () => {
    if ($('ytUrl').value) { uploadFile = null; $('fileUpload').value = ''; $('uploadName').textContent = ''; }
  });
  $('btnBackInput').addEventListener('click', () => { clearInterval(pollTimer); show('viewInput'); loadHistory(); });
  $('btnBackInput2').addEventListener('click', () => { show('viewInput'); loadHistory(); });

  // ---------- init ----------
  loadTemplates();
  loadHistory();

  // Buka kembali project dari URL (?project=...)
  const params = new URLSearchParams(window.location.search);
  if (params.get('project')) {
    const pid = params.get('project');
    api('/projects/' + pid + '/status').then((st) => {
      if (st.status === 'done') openResults(pid);
      else if (st.status !== 'error') watchProgress(pid);
    }).catch(() => {});
  }
})();
