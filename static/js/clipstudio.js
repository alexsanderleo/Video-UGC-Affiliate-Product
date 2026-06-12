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
        card.innerHTML =
          '<div class="thumbwrap">' +
            '<img loading="lazy" src="' + (c.thumbnail || '') + '" onerror="this.style.visibility=\'hidden\'">' +
            '<span class="scorebadge">🔥 ' + c.score + '</span>' +
            '<span class="durbadge">' + fmtDur(c.duration) + '</span>' +
          '</div>' +
          '<div class="body"><h3></h3>' +
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
      loadHistory();
    } catch (e) { alert(e.message); }
  }

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
