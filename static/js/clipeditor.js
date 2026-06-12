/* ============================================================================
   Editor Klip — Auto Klip VIP (clone Opus Clip)
   - Preview: <video> + CSS transform crop (keyframes) + caption overlay rAF
   - Text-based editing: hapus kata = potong video (cut_ranges)
   - Timeline canvas: sprite, waveform, playhead, zoom, cut shading, blok overlay
   - Undo/redo 50 langkah (snapshot), autosave, shortcut keyboard
   ========================================================================== */
(function () {
  'use strict';

  const API = '/api/v1/clipstudio';
  const $ = (id) => document.getElementById(id);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function token() { return localStorage.getItem('token'); }
  if (!token()) { window.location.href = '/?relogin=1'; return; }
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Authorization': 'Bearer ' + token() }, opts.headers || {});
    const r = await fetch(API + path, opts);
    if (r.status === 401) { localStorage.removeItem('token'); window.location.href = '/?relogin=1'; throw new Error('401'); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || d.error || 'HTTP ' + r.status);
    return d;
  }
  const jbody = (o) => ({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(o) });

  // ---------------- state ----------------
  const clipId = new URLSearchParams(location.search).get('clip');
  if (!clipId) { document.body.innerHTML = '<p style="padding:30px">clip id tidak ada.</p>'; return; }

  let DATA = null;          // respons /clips/{id}
  let WORDS = [];           // transkrip seluruh video [{word,start,end,conf,is_filler}]
  let ES = {};              // edit_state (kerja)
  let STYLE = {};           // caption_style (kerja)
  let TPLS = [];            // templates dari server
  let clip = null, project = null;
  let aspect = '9:16', layout = 'fill', trackerOn = true;
  let title = '';

  // playback
  const vid = $('vid'), bgVid = $('bgVideo');
  let playing = false;
  let selWords = new Set();      // index kata terpilih
  let selSegment = null;         // [s,e] segmen timeline terpilih
  let extendMode = false;

  // timeline
  let pxPerSec = 60, tlW = 0;
  let spriteImg = null, wavePeaks = null;
  const tlCanvas = $('tlCanvas'), tctx = tlCanvas.getContext('2d');

  // undo/redo
  let hist = [], histPos = -1;
  const HIST_MAX = 50;

  // autosave
  let saveTimer = null, dirty = false;

  // ---------------- util waktu ----------------
  const cs = () => (ES.extend && ES.extend.start != null) ? ES.extend.start : clip.start;
  const ce = () => (ES.extend && ES.extend.end != null) ? ES.extend.end : clip.end;
  function fmtT(t) {
    t = Math.max(0, t);
    const m = Math.floor(t / 60), s = Math.floor(t % 60), c = Math.floor((t % 1) * 100);
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0') + '.' + String(c).padStart(2, '0');
  }
  function keptSegments() {
    const start = cs(), end = ce();
    const cuts = (ES.cut_ranges || []).map((c) => [Math.max(start, c[0]), Math.min(end, c[1])])
      .filter((c) => c[1] > c[0]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const c of cuts) {
      if (merged.length && c[0] <= merged[merged.length - 1][1]) {
        merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], c[1]);
      } else merged.push([c[0], c[1]]);
    }
    const segs = []; let cur = start;
    for (const [a, b] of merged) { if (a > cur + 0.01) segs.push([cur, a]); cur = Math.max(cur, b); }
    if (end > cur + 0.01) segs.push([cur, end]);
    return segs;
  }
  function inCut(t) {
    for (const c of (ES.cut_ranges || [])) if (t >= c[0] && t < c[1]) return c;
    return null;
  }
  function outDuration() { return keptSegments().reduce((a, s) => a + s[1] - s[0], 0); }
  function srcToOut(t) {
    let acc = 0;
    for (const [a, b] of keptSegments()) {
      if (t < a) return acc;
      if (t <= b) return acc + (t - a);
      acc += b - a;
    }
    return acc;
  }

  // ---------------- undo / redo ----------------
  function snapshot() {
    return JSON.stringify({ ES, STYLE, title, aspect, layout, trackerOn });
  }
  function pushHist() {
    hist = hist.slice(0, histPos + 1);
    hist.push(snapshot());
    if (hist.length > HIST_MAX) hist.shift();
    histPos = hist.length - 1;
    updateUndoBtns();
  }
  function applySnap(s) {
    const o = JSON.parse(s);
    ES = o.ES; STYLE = o.STYLE; title = o.title; aspect = o.aspect; layout = o.layout; trackerOn = o.trackerOn;
    $('clipTitle').value = title;
    $('selAspect').value = aspect; $('selLayout').value = layout;
    $('btnTracker').textContent = '🎯 Tracker: ' + (trackerOn ? 'ON' : 'OFF');
    renderTranscript(); layoutStage(); renderTimeline(); refreshOpenPanel();
    markDirty(false);
  }
  function undo() { if (histPos > 0) { histPos--; applySnap(hist[histPos]); markDirty(true); } }
  function redo() { if (histPos < hist.length - 1) { histPos++; applySnap(hist[histPos]); markDirty(true); } }
  function updateUndoBtns() {
    $('btnUndo').disabled = histPos <= 0;
    $('btnRedo').disabled = histPos >= hist.length - 1;
  }
  function commit() { pushHist(); markDirty(true); }

  // ---------------- autosave ----------------
  function markDirty(saveSoon) {
    dirty = true;
    $('saveState').textContent = 'Belum tersimpan…';
    if (saveSoon !== false) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveNow, 900);
    }
  }
  async function saveNow() {
    clearTimeout(saveTimer);
    if (!dirty) return;
    try {
      await api('/clips/' + clipId, jbody({
        title, aspect_ratio: aspect, layout_mode: layout, tracker_on: trackerOn,
        caption_style: STYLE, edit_state: ES,
      }));
      dirty = false;
      $('saveState').textContent = 'Tersimpan ✓';
    } catch (e) {
      $('saveState').textContent = 'Gagal simpan!';
    }
  }

  // ---------------- kata-kata klip ----------------
  function clipWordEntries() {
    // [{i, w}] kata dlm rentang klip (termasuk extend)
    const a = cs(), b = ce(), out = [];
    for (let i = 0; i < WORDS.length; i++) {
      const w = WORDS[i];
      if (w.end > a && w.start < b) out.push({ i, w });
    }
    return out;
  }
  function wordText(i) {
    const we = (ES.word_edits || {})[String(i)];
    return (we != null ? we : WORDS[i].word).trim();
  }
  function visibleCaptionWords() {
    const del = new Set(ES.deleted_words || []);
    return clipWordEntries().filter(({ i, w }) => {
      if (del.has(i)) return false;
      const mid = (w.start + w.end) / 2;
      return !inCut(mid);
    }).map(({ i, w }) => ({ i, word: wordText(i), start: w.start, end: w.end }));
  }

  // ---------------- caption pages (sinkron dgn backend group_pages) ----------------
  function effStyle() {
    const tpl = TPLS.find((t) => t.id === (STYLE.template || 'opus-green')) || TPLS[0] || {};
    return Object.assign({}, tpl, STYLE);
  }
  function buildPages() {
    const st = effStyle();
    const maxW = Math.max(1, st.max_words || 4);
    const words = visibleCaptionWords();
    const pages = []; let cur = [];
    for (let j = 0; j < words.length; j++) {
      cur.push(words[j]);
      const gap = j + 1 < words.length ? words[j + 1].start - words[j].end : 99;
      if (cur.length >= maxW || gap > 1.2 || /[.!?…]$/.test(words[j].word)) { pages.push(cur); cur = []; }
    }
    if (cur.length) pages.push(cur);
    return pages;
  }
  let pagesCache = null;
  function invalidatePages() { pagesCache = null; }
  function getPages() { if (!pagesCache) pagesCache = buildPages(); return pagesCache; }

  // ---------------- stage / preview ----------------
  const stage = $('stage'), capOv = $('capOverlay');
  let stageW = 0, stageH = 0;
  function aspectWH() {
    return aspect === '1:1' ? [1, 1] : aspect === '16:9' ? [16, 9] : [9, 16];
  }
  function layoutStage() {
    const [aw, ah] = aspectWH();
    const panel = stage.parentElement;
    const availW = panel.clientWidth - 24, availH = panel.clientHeight - 70;
    let h = availH, w = h * aw / ah;
    if (w > availW) { w = availW; h = w * ah / aw; }
    stageW = Math.max(120, Math.floor(w)); stageH = Math.max(120, Math.floor(h));
    stage.style.width = stageW + 'px'; stage.style.height = stageH + 'px';
    bgVid.style.display = layout === 'fit' ? 'block' : 'none';
    invalidatePages();
  }
  function interpCenter(t) {
    let kfs = (ES.crop_keyframes && ES.crop_keyframes.length ? ES.crop_keyframes : clip.crop_keyframes) || [];
    if (!trackerOn) {
      kfs = kfs.length ? [kfs[0]] : [];
    }
    if (!kfs.length) return [project.width / 2, project.height / 2];
    if (t <= kfs[0].t) return [kfs[0].cx, kfs[0].cy];
    for (let i = 1; i < kfs.length; i++) {
      if (t <= kfs[i].t) {
        const a = kfs[i - 1], b = kfs[i], f = (t - a.t) / Math.max(1e-6, b.t - a.t);
        return [a.cx + (b.cx - a.cx) * f, a.cy + (b.cy - a.cy) * f];
      }
    }
    return [kfs[kfs.length - 1].cx, kfs[kfs.length - 1].cy];
  }
  function cropWindow(cx, cy) {
    const [aw, ah] = aspectWH();
    const r = aw / ah, sw = project.width, sh = project.height;
    let w, h;
    if (sw / sh > r) { h = sh; w = h * r; } else { w = sw; h = w / r; }
    const x = clamp(cx - w / 2, 0, sw - w), y = clamp(cy - h / 2, 0, sh - h);
    return [x, y, w, h];
  }
  function applyCropTransform() {
    const t = vid.currentTime;
    if (layout === 'fit') {
      // video utuh + blur background
      vid.style.width = '100%'; vid.style.height = '100%';
      vid.style.left = '0'; vid.style.top = '0'; vid.style.objectFit = 'contain';
      vid.style.position = 'absolute'; vid.style.transform = '';
      return;
    }
    if (layout === 'split') {
      const vh = stageH * 0.55;
      vid.style.objectFit = 'cover';
      vid.style.width = stageW + 'px'; vid.style.height = vh + 'px';
      vid.style.left = '0'; vid.style.top = '0'; vid.style.transform = '';
      return;
    }
    vid.style.objectFit = '';
    const [cx, cy] = interpCenter(t);
    const [x, y, w, h] = cropWindow(cx, cy);
    const scale = stageW / w;
    vid.style.width = (project.width * scale) + 'px';
    vid.style.height = 'auto';
    vid.style.left = (-x * scale) + 'px';
    vid.style.top = (-y * scale) + 'px';
  }

  // drag crop manual (override AI per keyframe)
  let dragCrop = null;
  stage.addEventListener('pointerdown', (e) => {
    if (e.target.closest('#capOverlay')) return;
    if (layout !== 'fill') return;
    const [cx, cy] = interpCenter(vid.currentTime);
    dragCrop = { x0: e.clientX, y0: e.clientY, cx, cy };
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener('pointermove', (e) => {
    if (!dragCrop) return;
    const [, , w] = cropWindow(dragCrop.cx, dragCrop.cy);
    const scale = stageW / w;
    const ncx = clamp(dragCrop.cx - (e.clientX - dragCrop.x0) / scale, 0, project.width);
    const ncy = clamp(dragCrop.cy - (e.clientY - dragCrop.y0) / scale, 0, project.height);
    setKeyframeAt(vid.currentTime, ncx, ncy, false);
  });
  stage.addEventListener('pointerup', () => { if (dragCrop) { dragCrop = null; commit(); } });
  function setKeyframeAt(t, cx, cy, doCommit) {
    let kfs = (ES.crop_keyframes && ES.crop_keyframes.length ? ES.crop_keyframes
      : JSON.parse(JSON.stringify(clip.crop_keyframes || []))) || [];
    if (!trackerOn) kfs = [{ t: cs(), cx, cy }];
    else {
      const near = kfs.find((k) => Math.abs(k.t - t) < 0.5);
      if (near) { near.cx = cx; near.cy = cy; }
      else { kfs.push({ t: Math.round(t * 2) / 2, cx, cy }); kfs.sort((a, b) => a.t - b.t); }
    }
    ES.crop_keyframes = kfs;
    if (doCommit !== false) commit();
  }

  // drag posisi caption (vertikal)
  let dragCap = null;
  capOv.addEventListener('pointerdown', (e) => {
    dragCap = { y0: e.clientY, p0: effStyle().pos_pct || 72 };
    capOv.setPointerCapture(e.pointerId);
    e.stopPropagation();
  });
  capOv.addEventListener('pointermove', (e) => {
    if (!dragCap) return;
    const dpct = (e.clientY - dragCap.y0) / stageH * 100;
    STYLE.pos_pct = clamp(Math.round(dragCap.p0 - dpct), 5, 95);
  });
  capOv.addEventListener('pointerup', () => { if (dragCap) { dragCap = null; commit(); refreshOpenPanel(); } });

  // ---------------- caption overlay render (rAF) ----------------
  function cssFont(f) { return "'" + (f || 'Impact') + "', Impact, Arial, sans-serif"; }
  let lastCapKey = '';
  function renderCaption() {
    const st = effStyle();
    if (ES.captions_on === false) { capOv.innerHTML = ''; return; }
    const t = vid.currentTime;
    const pages = getPages();
    let page = null;
    for (const p of pages) {
      if (t >= p[0].start - 0.05 && t <= p[p.length - 1].end + 0.15) { page = p; break; }
    }
    if (!page) { if (lastCapKey !== '') { capOv.innerHTML = ''; lastCapKey = ''; } return; }
    let activeIdx = -1;
    for (let j = 0; j < page.length; j++) {
      if (t >= page[j].start && t < page[j].end) { activeIdx = j; break; }
    }
    const key = pages.indexOf(page) + ':' + activeIdx + ':' + JSON.stringify([st.template, st.size, st.pos_pct, st.font, st.uppercase, st.text_color, st.highlight_color, st.mode]);
    if (key === lastCapKey) return;
    lastCapKey = key;

    const sizePx = (st.size || 60) * (stageH / 1920) * (aspect === '16:9' ? 1.6 : 1);
    capOv.style.top = '';
    capOv.style.bottom = (100 - (st.pos_pct || 72)) + '%';
    capOv.style.fontFamily = cssFont(st.font);
    capOv.style.fontSize = sizePx + 'px';
    capOv.style.fontWeight = '800';

    const mode = st.mode || 'highlight';
    const useBox = (mode === 'box' || mode === 'news') && st.box_color;
    const kw = ES.keyword_colors || {};
    let html = '';
    const items = mode === 'pop' ? (activeIdx >= 0 ? [page[activeIdx]] : []) : page;
    items.forEach((w, j) => {
      const isActive = mode === 'pop' ? true : j === activeIdx;
      let txt = w.word;
      if (st.uppercase) txt = txt.toUpperCase();
      txt = txt.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      let css = 'color:' + (st.text_color || '#fff') + ';';
      if (!useBox) {
        const oc = st.outline_color || '#000';
        const ow = Math.max(1, (st.outline || 3) * stageH / 1920 * 1.6);
        css += 'text-shadow:' + [-1, 1].map((a) => [-1, 1].map((b) =>
          (a * ow) + 'px ' + (b * ow) + 'px 0 ' + oc).join(',')).join(',') +
          ',0 ' + (ow * 1.4) + 'px ' + (ow * 1.5) + 'px rgba(0,0,0,.55);';
      } else {
        const bc = (st.box_color || '#000000B0');
        css += 'background:' + (bc.length === 9 ? bc.slice(0, 7) : bc) + 'CC'.slice(0, 0) + ';';
        css += 'background:' + bc + ';padding:.06em .25em;border-radius:.14em;';
      }
      if (isActive) {
        css += 'color:' + (st.highlight_color || '#39FF14') + ';';
        if (mode === 'pop') css += 'transform:scale(1.12);';
      } else if (kw[String(w.i)]) {
        css += 'color:' + kw[String(w.i)] + ';';
      }
      html += '<span style="' + css + '">' + txt + '</span> ';
    });
    capOv.innerHTML = html;
  }

  // ---------------- text & media overlay preview ----------------
  function renderOverlays() {
    const t = srcToOut(vid.currentTime);
    // teks
    const tl = $('textLayer');
    let h = '';
    (ES.texts || []).forEach((x) => {
      if (t >= (x.start || 0) && t <= (x.end || 3)) {
        const size = (x.size || 56) * stageH / 1920;
        h += '<div style="position:absolute;left:0;right:0;top:' + (x.y_pct || 12) + '%;text-align:center;' +
          'font-family:' + cssFont(x.font || 'Arial') + ';font-weight:800;font-size:' + size + 'px;color:' + (x.color || '#fff') +
          ';text-shadow:0 2px 6px rgba(0,0,0,.8)">' + String(x.text || '').replace(/</g, '&lt;') + '</div>';
      }
    });
    if (tl._last !== h) { tl.innerHTML = h; tl._last = h; }
    // media & broll
    const ml = $('mediaLayer');
    let mh = '';
    (ES.broll || []).forEach((b) => {
      if (t >= (b.start || 0) && t <= (b.end || 3)) {
        const tag = b.type === 'video' ? 'video' : 'img';
        mh += '<' + tag + ' src="' + (b.url || '') + '" ' + (tag === 'video' ? 'muted autoplay loop playsinline' : '') +
          ' style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></' + tag + '>';
      }
    });
    (ES.overlays || []).forEach((o) => {
      if (o.type === 'audio') return;
      if (t >= (o.start || 0) && t <= (o.end || 3)) {
        const tag = o.type === 'video' ? 'video' : 'img';
        const w = (o.w_pct || 40);
        mh += '<' + tag + ' src="' + (o.url || '') + '" ' + (tag === 'video' ? 'muted autoplay loop playsinline' : '') +
          ' style="position:absolute;left:' + (o.x_pct || 50) + '%;top:' + (o.y_pct || 50) + '%;width:' + w + '%;transform:translate(-50%,-50%);border-radius:6px"></' + tag + '>';
      }
    });
    if (ml._last !== mh) { ml.innerHTML = mh; ml._last = mh; }
  }

  // ---------------- playback engine ----------------
  function play() {
    if (vid.currentTime < cs() || vid.currentTime >= ce() - 0.05) vid.currentTime = keptSegments()[0] ? keptSegments()[0][0] : cs();
    vid.play(); if (layout === 'fit') bgVid.play().catch(() => {});
    playing = true; $('btnPlay').textContent = '⏸';
  }
  function pause() {
    vid.pause(); bgVid.pause();
    playing = false; $('btnPlay').textContent = '▶';
  }
  function seek(t) {
    t = clamp(t, cs(), ce() - 0.03);
    const c = inCut(t);
    if (c) t = Math.min(c[1] + 0.01, ce() - 0.03);
    vid.currentTime = t;
    if (layout === 'fit') bgVid.currentTime = t;
    lastCapKey = '';
  }
  function rafLoop() {
    if (playing) {
      const t = vid.currentTime;
      const c = inCut(t);
      if (c) { // skip otomatis cut range
        const segs = keptSegments();
        const nxt = segs.find((s) => s[0] >= c[1] - 0.01);
        if (nxt) { vid.currentTime = nxt[0] + 0.01; if (layout === 'fit') bgVid.currentTime = nxt[0]; }
        else { pause(); seek(segs[0] ? segs[0][0] : cs()); }
      } else if (t >= ce() - 0.03) {
        pause(); const segs = keptSegments(); seek(segs[0] ? segs[0][0] : cs());
      }
      if (layout === 'fit' && Math.abs(bgVid.currentTime - vid.currentTime) > 0.3) bgVid.currentTime = vid.currentTime;
    }
    applyCropTransform();
    renderCaption();
    renderOverlays();
    highlightActiveWord();
    drawPlayhead();
    $('tCur').textContent = fmtT(srcToOut(vid.currentTime));
    requestAnimationFrame(rafLoop);
  }

  // ---------------- transcript panel ----------------
  const tbox = $('transcriptBox');
  function renderTranscript() {
    invalidatePages();
    const del = new Set(ES.deleted_words || []);
    const kw = ES.keyword_colors || {};
    const frag = document.createDocumentFragment();
    const a = cs(), b = ce();

    // Mode extend: tampilkan kata redup sebelum/sesudah batas klip
    let entries;
    if (extendMode) {
      const lo = Math.max(0, a - 90), hi = Math.min(project.duration, b + 90);
      entries = [];
      for (let i = 0; i < WORDS.length; i++) {
        const w = WORDS[i];
        if (w.end > lo && w.start < hi) entries.push({ i, w });
      }
    } else entries = clipWordEntries();

    entries.forEach(({ i, w }, k) => {
      const span = document.createElement('span');
      span.className = 'w';
      span.dataset.i = i;
      span.textContent = wordText(i);
      const inRange = w.end > a && w.start < b;
      if (!inRange) span.classList.add('dim');
      if (del.has(i)) span.classList.add('del');
      if (w.is_filler) span.classList.add('filler');
      if (kw[String(i)]) { span.classList.add('kw'); span.style.color = kw[String(i)]; }
      if (selWords.has(i)) span.classList.add('sel');
      const mid = (w.start + w.end) / 2;
      if (inRange && inCut(mid) && !del.has(i)) span.classList.add('del');
      frag.appendChild(span);

      // badge jeda antar kata
      const nxt = entries[k + 1];
      if (nxt) {
        const gap = nxt.w.start - w.end;
        if (gap >= 0.25) {
          const g = document.createElement('span');
          g.className = 'gap';
          g.textContent = gap.toFixed(2) + 's';
          g.title = 'Klik untuk hapus jeda ini (remove pause)';
          g.dataset.s = w.end; g.dataset.e = nxt.w.start;
          frag.appendChild(g);
        }
      }
      frag.appendChild(document.createTextNode(' '));
    });
    tbox.innerHTML = '';
    tbox.appendChild(frag);
  }

  let lastActiveEl = null;
  function highlightActiveWord() {
    const t = vid.currentTime;
    let el = null;
    // cari kata aktif via dataset (linear cukup — DOM kecil per klip)
    const spans = tbox.children;
    for (let k = 0; k < spans.length; k++) {
      const s = spans[k];
      if (!s.dataset || s.dataset.i == null) continue;
      const w = WORDS[+s.dataset.i];
      if (w && t >= w.start && t < w.end) { el = s; break; }
    }
    if (el !== lastActiveEl) {
      if (lastActiveEl) lastActiveEl.classList.remove('active');
      if (el) {
        el.classList.add('active');
        if (playing) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      lastActiveEl = el;
    }
  }

  let lastClickIdx = null;
  tbox.addEventListener('click', (e) => {
    const g = e.target.closest('.gap');
    if (g) {
      if (confirm('Hapus jeda ' + (+g.dataset.e - +g.dataset.s).toFixed(2) + ' detik ini dari video?')) {
        addCut(+g.dataset.s, +g.dataset.e);
        commit(); renderTranscript(); renderTimeline();
      }
      return;
    }
    const el = e.target.closest('.w');
    if (!el) return;
    const i = +el.dataset.i;
    const w = WORDS[i];

    if (extendMode) {
      // klik kata redup -> perluas batas klip ke kata itu
      if (w.start < cs()) { ES.extend = ES.extend || {}; ES.extend.start = w.start; }
      else if (w.end > ce()) { ES.extend = ES.extend || {}; ES.extend.end = w.end; }
      commit(); renderTranscript(); renderTimeline(); updateTotals();
      return;
    }
    if (el.classList.contains('del')) {
      // restore kata yang dicoret
      restoreWord(i);
      commit(); renderTranscript(); renderTimeline();
      return;
    }
    // seleksi: shift = rentang, ctrl = multi, klik biasa = pilih + seek
    if (e.shiftKey && lastClickIdx != null) {
      const [lo, hi] = [Math.min(lastClickIdx, i), Math.max(lastClickIdx, i)];
      for (let k = lo; k <= hi; k++) selWords.add(k);
    } else if (e.ctrlKey || e.metaKey) {
      selWords.has(i) ? selWords.delete(i) : selWords.add(i);
    } else {
      selWords.clear(); selWords.add(i);
      seek(w.start + 0.01);
    }
    lastClickIdx = i;
    renderTranscript();
  });
  tbox.addEventListener('dblclick', (e) => {
    const el = e.target.closest('.w');
    if (!el || el.classList.contains('dim')) return;
    const i = +el.dataset.i;
    const inp = document.createElement('input');
    inp.className = 'wedit';
    inp.value = wordText(i);
    el.replaceWith(inp);
    inp.focus(); inp.select();
    const done = (apply) => {
      if (apply && inp.value.trim() !== WORDS[i].word.trim()) {
        ES.word_edits = ES.word_edits || {};
        ES.word_edits[String(i)] = inp.value.trim();
        commit();
      }
      renderTranscript();
    };
    inp.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') done(true);
      if (ev.key === 'Escape') done(false);
      ev.stopPropagation();
    });
    inp.addEventListener('blur', () => done(true));
  });

  function addCut(s, e) {
    ES.cut_ranges = ES.cut_ranges || [];
    ES.cut_ranges.push([Math.round(s * 1000) / 1000, Math.round(e * 1000) / 1000]);
    invalidatePages();
  }
  function deleteWordIdx(i) {
    const w = WORDS[i];
    ES.deleted_words = ES.deleted_words || [];
    if (!ES.deleted_words.includes(i)) ES.deleted_words.push(i);
    ES.word_cut_map = ES.word_cut_map || {};
    const cut = [Math.round(w.start * 1000) / 1000, Math.round(w.end * 1000) / 1000];
    ES.word_cut_map[String(i)] = cut;
    addCut(cut[0], cut[1]);
  }
  function restoreWord(i) {
    ES.deleted_words = (ES.deleted_words || []).filter((x) => x !== i);
    const cut = (ES.word_cut_map || {})[String(i)];
    if (cut) {
      ES.cut_ranges = (ES.cut_ranges || []).filter((c) => !(Math.abs(c[0] - cut[0]) < 0.002 && Math.abs(c[1] - cut[1]) < 0.002));
      delete ES.word_cut_map[String(i)];
    }
    invalidatePages();
  }
  function deleteSelected() {
    if (!selWords.size) return;
    selWords.forEach((i) => deleteWordIdx(i));
    selWords.clear();
    commit(); renderTranscript(); renderTimeline(); updateTotals();
  }
  $('btnDelWord').addEventListener('click', deleteSelected);

  // Speech cleanup: filler + jeda > 0.8s, preview dulu
  $('btnCleanup').addEventListener('click', () => {
    const entries = clipWordEntries();
    const del = new Set(ES.deleted_words || []);
    const fillers = entries.filter(({ i, w }) => w.is_filler && !del.has(i) && !inCut((w.start + w.end) / 2));
    const pauses = [];
    for (let k = 0; k < entries.length - 1; k++) {
      const gap = entries[k + 1].w.start - entries[k].w.end;
      if (gap > 0.8 && !inCut(entries[k].w.end + gap / 2)) {
        pauses.push([entries[k].w.end, entries[k + 1].w.start]);
      }
    }
    if (!fillers.length && !pauses.length) { alert('Tidak ada filler words atau jeda > 0.8s yang bisa dibersihkan. 👍'); return; }
    let listHtml = '';
    fillers.forEach(({ i }) => { listHtml += '🗣 filler: <b>' + wordText(i) + '</b><br>'; });
    pauses.forEach((p) => { listHtml += '⏸ jeda ' + (p[1] - p[0]).toFixed(2) + 's @ ' + fmtT(srcToOut(p[0])) + '<br>'; });
    openDialog(
      '<h3>🧹 Speech cleanup</h3>' +
      '<p style="font-size:.85rem;color:var(--muted)">' + fillers.length + ' filler + ' + pauses.length + ' jeda panjang akan dipotong:</p>' +
      '<div class="cleanlist">' + listHtml + '</div>' +
      '<div class="row2"><button class="btn btn-g" data-x>Batal</button><button class="btn btn-p" id="dlgApply">Potong semua</button></div>'
    );
    $('dlgApply').addEventListener('click', () => {
      fillers.forEach(({ i }) => deleteWordIdx(i));
      pauses.forEach((p) => addCut(p[0], p[1]));
      commit(); renderTranscript(); renderTimeline(); updateTotals();
      closeDialog();
    });
  });

  // Extend a clip
  $('btnExtend').addEventListener('click', () => {
    extendMode = !extendMode;
    $('btnExtend').classList.toggle('on', extendMode);
    renderTranscript();
    if (extendMode) alert('Mode Extend: klik kata REDUP di luar batas klip untuk memperluas awal/akhir klip. Klik tombol lagi untuk selesai.');
  });

  // ---------------- timeline ----------------
  const LANE = { ruler: [0, 18], sprite: [20, 46], wave: [68, 34], cap: [106, 18], media: [126, 18], text: [146, 18] };
  function renderTimeline() {
    invalidatePages();
    const dur = ce() - cs();
    tlW = Math.max($('tlScroll').clientWidth, Math.ceil(dur * pxPerSec) + 40);
    tlCanvas.width = tlW * devicePixelRatio;
    tlCanvas.height = 170 * devicePixelRatio;
    tlCanvas.style.width = tlW + 'px';
    tlCanvas.style.height = '170px';
    tctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    tctx.clearRect(0, 0, tlW, 170);
    const X = (t) => (t - cs()) * pxPerSec + 8;

    // ruler
    tctx.fillStyle = '#9A9AAB'; tctx.font = '9px Inter'; tctx.strokeStyle = '#2A2A36';
    const step = pxPerSec >= 120 ? 1 : pxPerSec >= 50 ? 5 : 10;
    for (let s = 0; s <= dur + step; s += step) {
      const x = X(cs() + s);
      tctx.fillText(fmtT(s).slice(0, 5), x + 2, 12);
      tctx.beginPath(); tctx.moveTo(x, 14); tctx.lineTo(x, 170); tctx.stroke();
    }

    // sprite strip
    if (spriteImg && clip.sprite_meta && clip.sprite_meta.cols) {
      const m = clip.sprite_meta;
      const n = m.count;
      for (let k = 0; k < n; k++) {
        const tSec = clip.start + k * m.interval; // sprite dibuat dari rentang asli klip
        if (tSec < cs() || tSec > ce()) continue;
        const sx = (k % m.cols) * m.tile_w, sy = Math.floor(k / m.cols) * m.tile_h;
        const dw = m.interval * pxPerSec;
        tctx.drawImage(spriteImg, sx, sy, m.tile_w, m.tile_h, X(tSec), LANE.sprite[0], dw + 0.5, LANE.sprite[1]);
      }
    } else {
      tctx.fillStyle = '#17171F';
      tctx.fillRect(8, LANE.sprite[0], tlW - 16, LANE.sprite[1]);
    }

    // waveform
    tctx.fillStyle = 'rgba(6,182,212,.75)';
    if (wavePeaks) {
      const mid = LANE.wave[0] + LANE.wave[1] / 2;
      for (let x = 8; x < tlW - 8; x += 2) {
        const t = cs() + (x - 8) / pxPerSec;
        if (t > ce()) break;
        const p = wavePeaks[Math.floor(t * 50)] || 0; // peaks per 20ms
        const h = Math.max(1, p * LANE.wave[1]);
        tctx.fillRect(x, mid - h / 2, 1.4, h);
      }
    }

    // caption pages (blok per grup)
    tctx.fillStyle = 'rgba(139,92,246,.5)';
    getPages().forEach((p) => {
      const x = X(p[0].start), w = Math.max(3, (p[p.length - 1].end - p[0].start) * pxPerSec);
      tctx.fillRect(x, LANE.cap[0] + 3, w - 1, LANE.cap[1] - 6);
    });

    // blok media/broll
    (ES.broll || []).forEach((b, bi) => drawBlock(b, LANE.media, '#F59E0B', 'B' + (bi + 1)));
    (ES.overlays || []).forEach((o, oi) => { if (o.type !== 'audio') drawBlock(o, LANE.media, '#06B6D4', 'M' + (oi + 1)); });
    (ES.texts || []).forEach((x, xi) => drawBlock(x, LANE.text, '#EC4899', 'T' + (xi + 1)));
    function drawBlock(item, lane, color, label) {
      // item start/end dlm waktu OUTPUT -> mapping kasar ke sumber utk digambar
      const segs = keptSegments();
      let s = item.start || 0, e = item.end || (s + 3);
      const o2s = (t) => { let acc = 0; for (const [a, b] of segs) { const d = b - a; if (t <= acc + d) return a + (t - acc); acc += d; } return segs.length ? segs[segs.length - 1][1] : cs(); };
      const x = X(o2s(s)), w = Math.max(6, (e - s) * pxPerSec);
      tctx.fillStyle = color + '66'; tctx.strokeStyle = color;
      tctx.fillRect(x, lane[0] + 2, w, lane[1] - 4);
      tctx.strokeRect(x, lane[0] + 2, w, lane[1] - 4);
      tctx.fillStyle = '#fff'; tctx.font = '9px Inter';
      tctx.fillText(label, x + 3, lane[0] + 13);
    }

    // cut ranges diarsir gelap
    tctx.fillStyle = 'rgba(0,0,0,.72)';
    (ES.cut_ranges || []).forEach((c) => {
      const x = X(Math.max(cs(), c[0])), w = (Math.min(ce(), c[1]) - Math.max(cs(), c[0])) * pxPerSec;
      if (w > 0) {
        tctx.fillRect(x, LANE.sprite[0], w, LANE.wave[0] + LANE.wave[1] - LANE.sprite[0]);
        tctx.strokeStyle = '#F43F5E';
        tctx.strokeRect(x, LANE.sprite[0], w, LANE.wave[0] + LANE.wave[1] - LANE.sprite[0]);
      }
    });

    // segmen terpilih
    if (selSegment) {
      const x = X(selSegment[0]), w = (selSegment[1] - selSegment[0]) * pxPerSec;
      tctx.strokeStyle = '#06B6D4'; tctx.lineWidth = 2;
      tctx.strokeRect(x, LANE.sprite[0] - 1, w, LANE.wave[0] + LANE.wave[1] - LANE.sprite[0] + 2);
      tctx.lineWidth = 1;
    }

    // trim handles ujung klip
    tctx.fillStyle = '#8B5CF6';
    tctx.fillRect(X(cs()) - 5, LANE.sprite[0], 5, 82);
    tctx.fillRect(X(ce()), LANE.sprite[0], 5, 82);

    drawPlayhead(true);
    updateTotals();
  }
  let phX = -1;
  function drawPlayhead(force) {
    const x = (vid.currentTime - cs()) * pxPerSec + 8;
    if (!force && Math.abs(x - phX) < 0.5) return;
    // redraw ringan: gunakan overlay? sederhananya redraw penuh saat playing tiap ~100ms
    if (!force) {
      if (!drawPlayhead._tick || performance.now() - drawPlayhead._tick > 100) {
        drawPlayhead._tick = performance.now();
        renderTimelineStatic();
      }
      return;
    }
    phX = x;
    tctx.strokeStyle = '#fff'; tctx.lineWidth = 1.6;
    tctx.beginPath(); tctx.moveTo(x, 0); tctx.lineTo(x, 170); tctx.stroke();
    tctx.fillStyle = '#fff';
    tctx.beginPath(); tctx.moveTo(x - 5, 0); tctx.lineTo(x + 5, 0); tctx.lineTo(x, 8); tctx.fill();
    tctx.lineWidth = 1;
  }
  function renderTimelineStatic() { renderTimeline(); }

  function updateTotals() { $('tTot').textContent = fmtT(outDuration()); }

  // interaksi timeline
  let tlDrag = null; // {type:'seek'|'trimL'|'trimR'|'block', ...}
  tlCanvas.addEventListener('pointerdown', (e) => {
    const rect = tlCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const t = cs() + (x - 8) / pxPerSec;
    const XL = (cs() - cs()) * pxPerSec + 8, XR = (ce() - cs()) * pxPerSec + 8;
    if (Math.abs(x - XL) < 8 && y > LANE.sprite[0]) { tlDrag = { type: 'trimL' }; }
    else if (Math.abs(x - XR) < 8 && y > LANE.sprite[0]) { tlDrag = { type: 'trimR' }; }
    else {
      // blok overlay?
      const hitBlock = hitOverlayBlock(t, y);
      if (hitBlock) { tlDrag = hitBlock; }
      else {
        tlDrag = { type: 'seek' };
        // pilih segmen bila klik area sprite
        if (y >= LANE.sprite[0] && y <= LANE.wave[0] + LANE.wave[1]) {
          selSegment = keptSegments().find((s) => t >= s[0] && t <= s[1]) || null;
        }
        seek(t);
        renderTimeline();
      }
    }
    tlCanvas.setPointerCapture(e.pointerId);
  });
  function hitOverlayBlock(t, y) {
    const segs = keptSegments();
    const s2o = (tt) => srcToOut(tt);
    const lists = [
      { arr: ES.broll || [], lane: LANE.media, key: 'broll' },
      { arr: (ES.overlays || []).filter((o) => o.type !== 'audio'), lane: LANE.media, key: 'overlays' },
      { arr: ES.texts || [], lane: LANE.text, key: 'texts' },
    ];
    const to = s2o(t);
    for (const L of lists) {
      if (y < L.lane[0] || y > L.lane[0] + L.lane[1]) continue;
      for (let i = 0; i < L.arr.length; i++) {
        const it = L.arr[i];
        const s = it.start || 0, e = it.end || s + 3;
        if (to >= s - 0.15 && to <= e + 0.15) {
          const edge = (to <= s + 0.25) ? 'L' : (to >= e - 0.25) ? 'R' : 'mid';
          return { type: 'block', key: L.key, item: it, edge, grabbed: to };
        }
      }
    }
    return null;
  }
  tlCanvas.addEventListener('pointermove', (e) => {
    if (!tlDrag) return;
    const rect = tlCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = clamp(cs() + (x - 8) / pxPerSec, 0, project.duration);
    if (tlDrag.type === 'seek') { seek(t); drawPlayhead(true); }
    else if (tlDrag.type === 'trimL') {
      ES.extend = ES.extend || {};
      ES.extend.start = clamp(t, 0, ce() - 1);
      renderTimeline(); renderTranscript();
    } else if (tlDrag.type === 'trimR') {
      ES.extend = ES.extend || {};
      ES.extend.end = clamp(t, cs() + 1, project.duration);
      renderTimeline(); renderTranscript();
    } else if (tlDrag.type === 'block') {
      const to = srcToOut(clamp(t, cs(), ce()));
      const it = tlDrag.item;
      const dur = (it.end || it.start + 3) - (it.start || 0);
      if (tlDrag.edge === 'L') it.start = clamp(to, 0, (it.end || 3) - 0.3);
      else if (tlDrag.edge === 'R') it.end = Math.max((it.start || 0) + 0.3, to);
      else { it.start = clamp(to - dur / 2, 0, outDuration()); it.end = it.start + dur; }
      renderTimeline();
    }
  });
  tlCanvas.addEventListener('pointerup', () => {
    if (tlDrag && (tlDrag.type === 'trimL' || tlDrag.type === 'trimR' || tlDrag.type === 'block')) {
      commit(); updateTotals();
    }
    tlDrag = null;
  });

  // toolbar timeline
  $('btnPlay').addEventListener('click', () => playing ? pause() : play());
  $('playBig').addEventListener('click', () => playing ? pause() : play());
  $('btnPrevF').addEventListener('click', () => seek(vid.currentTime - 1 / (project.fps || 30)));
  $('btnNextF').addEventListener('click', () => seek(vid.currentTime + 1 / (project.fps || 30)));
  $('btnSplit').addEventListener('click', () => {
    const t = vid.currentTime;
    if (t <= cs() + 0.1 || t >= ce() - 0.1) return;
    addCut(t, t + 0.04); // cut tipis = titik split (segmen bisa dipilih & dihapus terpisah)
    commit(); renderTimeline(); renderTranscript();
  });
  $('btnDelSeg').addEventListener('click', () => {
    if (!selSegment) { alert('Klik segmen di timeline dulu untuk memilihnya.'); return; }
    addCut(selSegment[0], selSegment[1]);
    selSegment = null;
    commit(); renderTimeline(); renderTranscript(); updateTotals();
  });
  $('btnMute').addEventListener('click', () => {
    vid.muted = !vid.muted;
    $('btnMute').textContent = vid.muted ? '🔇' : '🔊';
  });
  $('volRange').addEventListener('input', () => {
    const v = +$('volRange').value / 100;
    vid.volume = v; ES.volume = v;
    markDirty();
  });
  $('zoomRange').addEventListener('input', () => { pxPerSec = +$('zoomRange').value; renderTimeline(); });
  $('zoomIn').addEventListener('click', () => { pxPerSec = clamp(pxPerSec * 1.3, 20, 400); $('zoomRange').value = pxPerSec; renderTimeline(); });
  $('zoomOut').addEventListener('click', () => { pxPerSec = clamp(pxPerSec / 1.3, 20, 400); $('zoomRange').value = pxPerSec; renderTimeline(); });
  $('btnHideTl').addEventListener('click', () => {
    $('tlRoot').classList.toggle('tlhidden');
    $('btnHideTl').textContent = $('tlRoot').classList.contains('tlhidden') ? '▴ Timeline' : '▾ Timeline';
    setTimeout(layoutStage, 50);
  });

  // ---------------- sidebar ----------------
  const sbody = $('sbody');
  let openMenu = null;
  document.querySelectorAll('#srail button').forEach((b) => {
    b.addEventListener('click', () => {
      const m = b.dataset.menu;
      if (openMenu === m) { openMenu = null; sbody.classList.remove('open'); }
      else { openMenu = m; sbody.classList.add('open'); renderPanel(m); }
      document.querySelectorAll('#srail button').forEach((x) => x.classList.toggle('on', x.dataset.menu === openMenu));
      setTimeout(layoutStage, 50);
    });
  });
  function refreshOpenPanel() { if (openMenu) renderPanel(openMenu); }

  function renderPanel(m) {
    if (m === 'ai') return panelAI();
    if (m === 'captions') return panelCaptions();
    if (m === 'media') return panelMedia();
    if (m === 'brand') return panelBrand();
    if (m === 'broll') return panelBroll();
    if (m === 'trans') return panelTransitions();
    if (m === 'text') return panelText();
    if (m === 'audio') return panelAudio();
    if (m === 'hook') return panelHook();
  }

  // --- AI enhance ---
  function panelAI() {
    sbody.innerHTML = '<h3>✨ AI Enhance</h3>' +
      '<p class="note" style="margin-bottom:12px">Satu klik: speech cleanup + auto emoji + keyword highlight + pastikan tracker wajah aktif.</p>' +
      '<button class="actionbtn primary" id="aiEnhanceBtn">🚀 Jalankan AI Enhance</button>' +
      '<div class="note" id="aiEnhanceLog"></div>';
    $('aiEnhanceBtn').addEventListener('click', async () => {
      const log = $('aiEnhanceLog');
      const btn = $('aiEnhanceBtn'); btn.disabled = true; btn.textContent = '⏳ Memproses...';
      try {
        // 1) cleanup otomatis (tanpa konfirmasi)
        const entries = clipWordEntries();
        const del = new Set(ES.deleted_words || []);
        let n = 0;
        entries.forEach(({ i, w }) => { if (w.is_filler && !del.has(i)) { deleteWordIdx(i); n++; } });
        for (let k = 0; k < entries.length - 1; k++) {
          const gap = entries[k + 1].w.start - entries[k].w.end;
          if (gap > 0.8) { addCut(entries[k].w.end, entries[k + 1].w.start); n++; }
        }
        log.innerHTML = '🧹 ' + n + ' filler/jeda dipotong<br>⏳ minta emoji & keyword ke AI...';
        // 2) emoji + keywords paralel
        const [em, kw] = await Promise.all([
          api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'emoji' }) }),
          api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'keywords' }) }),
        ]);
        ES.word_edits = Object.assign({}, ES.word_edits || {}, em.word_edits || {});
        ES.keyword_colors = Object.assign({}, ES.keyword_colors || {}, kw.keyword_colors || {});
        trackerOn = true; $('btnTracker').textContent = '🎯 Tracker: ON';
        commit(); renderTranscript(); renderTimeline();
        log.innerHTML += '<br>✅ Selesai! Emoji: ' + Object.keys(em.word_edits || {}).length + ', keyword: ' + Object.keys(kw.keyword_colors || {}).length;
      } catch (e) { log.textContent = '❌ ' + e.message; }
      btn.disabled = false; btn.textContent = '🚀 Jalankan AI Enhance';
    });
  }

  // --- Captions ---
  function panelCaptions() {
    const st = effStyle();
    let tplHtml = '';
    TPLS.forEach((t) => {
      tplHtml += '<div class="tplcard' + ((STYLE.template || 'opus-green') === t.id ? ' on' : '') + '" data-tpl="' + t.id + '">' +
        '<div class="demo" style="font-family:' + cssFont(t.font) + '"><span style="color:' + t.text_color + '">KATA </span>' +
        '<span style="color:' + t.highlight_color + '">AKTIF</span></div>' + t.name + '</div>';
    });
    let fontOpts = '';
    (window.__fonts || []).forEach((f) => { fontOpts += '<option' + (st.font === f ? ' selected' : '') + '>' + f + '</option>'; });
    sbody.innerHTML = '<h3>💬 Captions</h3>' +
      '<div class="fgroup"><label><input type="checkbox" id="capOn" ' + (ES.captions_on !== false ? 'checked' : '') + ' style="width:auto;margin-right:6px">Tampilkan captions</label></div>' +
      '<div class="fgroup"><label>Template</label><div class="tplgrid">' + tplHtml + '</div></div>' +
      '<div class="fgroup"><label>Font</label><select id="capFont">' + fontOpts + '</select></div>' +
      '<div class="fgroup"><label>Ukuran: <span id="capSizeVal">' + (st.size || 60) + '</span></label><input type="range" id="capSize" min="30" max="120" value="' + (st.size || 60) + '"></div>' +
      '<div class="fgroup"><label>Posisi vertikal: <span id="capPosVal">' + (st.pos_pct || 72) + '%</span> <span style="text-transform:none;font-weight:400">(bisa drag di preview)</span></label><input type="range" id="capPos" min="5" max="95" value="' + (st.pos_pct || 72) + '"></div>' +
      '<div class="fgroup colorrow"><span>Teks <input type="color" id="cText" value="' + (st.text_color || '#FFFFFF') + '"></span>' +
      '<span>Aktif <input type="color" id="cHi" value="' + (st.highlight_color || '#39FF14') + '"></span>' +
      '<span>Outline <input type="color" id="cOut" value="' + (st.outline_color || '#000000') + '"></span></div>' +
      '<div class="fgroup"><label><input type="checkbox" id="capUpper" ' + (st.uppercase ? 'checked' : '') + ' style="width:auto;margin-right:6px">HURUF BESAR semua</label></div>' +
      '<div class="fgroup"><label>Max kata per baris: <span id="capMaxVal">' + (st.max_words || 4) + '</span></label><input type="range" id="capMax" min="1" max="8" value="' + (st.max_words || 4) + '"></div>' +
      '<button class="actionbtn" id="btnEmoji">😀 Auto emoji (AI)</button>' +
      '<button class="actionbtn" id="btnKeyword">🖍 Keyword highlight (AI)</button>';
    sbody.querySelectorAll('.tplcard').forEach((c) => c.addEventListener('click', () => {
      STYLE = { template: c.dataset.tpl }; // reset override saat ganti template
      commit(); panelCaptions(); lastCapKey = '';
    }));
    const bind = (id, key, ev, map) => {
      $(id).addEventListener(ev || 'change', () => {
        STYLE[key] = map ? map($(id)) : $(id).value;
        lastCapKey = ''; markDirty();
        const valEl = $(id.replace(/^cap/, 'cap') + 'Val');
        if (valEl) valEl.textContent = $(id).value + (key === 'pos_pct' ? '%' : '');
      });
      $(id).addEventListener('pointerup', commit);
    };
    bind('capFont', 'font');
    bind('capSize', 'size', 'input', (el) => +el.value);
    bind('capPos', 'pos_pct', 'input', (el) => +el.value);
    bind('cText', 'text_color', 'input');
    bind('cHi', 'highlight_color', 'input');
    bind('cOut', 'outline_color', 'input');
    $('capUpper').addEventListener('change', () => { STYLE.uppercase = $('capUpper').checked; lastCapKey = ''; commit(); });
    $('capMax').addEventListener('input', () => { STYLE.max_words = +$('capMax').value; $('capMaxVal').textContent = $('capMax').value; invalidatePages(); lastCapKey = ''; markDirty(); });
    $('capOn').addEventListener('change', () => { ES.captions_on = $('capOn').checked; lastCapKey = ''; commit(); });
    $('btnEmoji').addEventListener('click', async () => {
      $('btnEmoji').textContent = '⏳ ...';
      try {
        const d = await api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'emoji' }) });
        ES.word_edits = Object.assign({}, ES.word_edits || {}, d.word_edits || {});
        commit(); renderTranscript();
        $('btnEmoji').textContent = '✅ ' + Object.keys(d.word_edits || {}).length + ' emoji ditambah';
      } catch (e) { $('btnEmoji').textContent = '❌ ' + e.message; }
    });
    $('btnKeyword').addEventListener('click', async () => {
      $('btnKeyword').textContent = '⏳ ...';
      try {
        const d = await api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'keywords' }) });
        ES.keyword_colors = Object.assign({}, ES.keyword_colors || {}, d.keyword_colors || {});
        commit(); renderTranscript();
        $('btnKeyword').textContent = '✅ ' + Object.keys(d.keyword_colors || {}).length + ' keyword ditandai';
      } catch (e) { $('btnKeyword').textContent = '❌ ' + e.message; }
    });
  }

  // --- Media ---
  function panelMedia(tab) {
    tab = tab || 'all';
    sbody.innerHTML = '<h3>🖼️ Media</h3>' +
      '<button class="actionbtn primary" id="mediaUpBtn">⬆️ Upload gambar/video/audio</button>' +
      '<input type="file" id="mediaUpInput" accept="image/*,video/*,audio/*" style="display:none">' +
      '<div class="tabs2">' + ['all', 'image', 'video', 'audio'].map((k) =>
        '<button data-mt="' + k + '" class="' + (tab === k ? 'on' : '') + '">' + ({ all: 'All', image: 'Images', video: 'Videos', audio: 'Audio' })[k] + '</button>').join('') + '</div>' +
      '<div class="mediagrid" id="mediaGrid">Memuat…</div>' +
      '<h3 style="margin-top:16px;font-size:.82rem">Di timeline</h3><div id="mediaUsed"></div>' +
      '<p class="note">Klik media untuk menambah sebagai overlay 3 detik di posisi playhead. File media disimpan selama project ada (auto terhapus saat project dihapus).</p>';
    sbody.querySelectorAll('[data-mt]').forEach((b) => b.addEventListener('click', () => panelMedia(b.dataset.mt)));
    $('mediaUpBtn').addEventListener('click', () => $('mediaUpInput').click());
    $('mediaUpInput').addEventListener('change', async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      $('mediaUpBtn').textContent = '⏳ Mengupload...';
      const fd = new FormData(); fd.append('file', f);
      try {
        await api('/projects/' + project.id + '/media', { method: 'POST', body: fd });
        panelMedia(tab);
      } catch (err) { alert(err.message); $('mediaUpBtn').textContent = '⬆️ Upload gambar/video/audio'; }
    });
    api('/projects/' + project.id + '/media').then((d) => {
      const grid = $('mediaGrid'); grid.innerHTML = '';
      const items = d.media.filter((m) => tab === 'all' || m.type === tab);
      if (!items.length) { grid.innerHTML = '<p class="note">Belum ada media.</p>'; }
      items.forEach((m) => {
        const div = document.createElement('div');
        div.className = 'mi';
        div.innerHTML = (m.type === 'image' ? '<img src="' + m.url + '">' :
          m.type === 'video' ? '<video src="' + m.url + '" muted></video>' : '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:1.4rem">🎵</div>') +
          '<div class="nm">' + m.name + '</div>';
        div.addEventListener('click', () => {
          const t0 = srcToOut(vid.currentTime);
          if (m.type === 'audio') {
            ES.music = { path: m.url, volume: 0.25, duck: true, fade: true, name: m.name };
          } else {
            ES.overlays = ES.overlays || [];
            ES.overlays.push({ url: m.url, type: m.type, start: t0, end: Math.min(outDuration(), t0 + 3), x_pct: 50, y_pct: 50, w_pct: 45 });
          }
          commit(); renderTimeline(); panelMedia(tab);
        });
        grid.appendChild(div);
      });
      const used = $('mediaUsed'); used.innerHTML = '';
      (ES.overlays || []).forEach((o, i) => {
        const r = document.createElement('div');
        r.className = 'itemrow';
        r.innerHTML = '<span class="nm">' + (o.type === 'video' ? '🎞' : '🖼') + ' overlay ' + (i + 1) + ' (' + fmtT(o.start) + '–' + fmtT(o.end) + ')</span><button>✕</button>';
        r.querySelector('button').addEventListener('click', () => { ES.overlays.splice(i, 1); commit(); renderTimeline(); panelMedia(tab); });
        used.appendChild(r);
      });
    }).catch(() => { $('mediaGrid').innerHTML = '<p class="note">Gagal memuat media.</p>'; });
  }

  // --- Brand template ---
  function panelBrand() {
    const presets = JSON.parse(localStorage.getItem('clip_brand_presets') || '[]');
    let list = '';
    presets.forEach((p, i) => {
      list += '<div class="itemrow"><span class="nm">🏷 ' + p.name + '</span>' +
        '<button data-apply="' + i + '" title="Terapkan ke klip ini">✓</button>' +
        '<button data-all="' + i + '" title="Terapkan ke SEMUA klip project">⇶</button>' +
        '<button data-del="' + i + '">✕</button></div>';
    });
    sbody.innerHTML = '<h3>🏷️ Brand Template</h3>' +
      '<p class="note" style="margin-bottom:10px">Simpan preset caption + watermark, lalu terapkan 1 klik ke semua klip.</p>' +
      '<div class="fgroup"><label>Nama preset</label><input type="text" id="brandName" placeholder="Brand saya"></div>' +
      '<div class="fgroup"><label>Teks watermark (opsional)</label><input type="text" id="brandWm" value="' + (ES.watermark_text || '') + '" placeholder="@username"></div>' +
      '<button class="actionbtn primary" id="brandSave">💾 Simpan preset dari style sekarang</button>' +
      (list || '<p class="note">Belum ada preset.</p>') + (list ? '<div id="brandList"></div>' : '');
    $('brandSave').addEventListener('click', () => {
      const name = $('brandName').value.trim() || 'Preset ' + (presets.length + 1);
      presets.push({ name, style: effStyle(), watermark_text: $('brandWm').value.trim() });
      localStorage.setItem('clip_brand_presets', JSON.stringify(presets));
      panelBrand();
    });
    sbody.querySelectorAll('[data-apply]').forEach((b) => b.addEventListener('click', () => {
      const p = presets[+b.dataset.apply];
      STYLE = JSON.parse(JSON.stringify(p.style));
      if (p.watermark_text) { ES.texts = ES.texts || []; }
      ES.watermark_text = p.watermark_text;
      lastCapKey = ''; commit(); refreshOpenPanel();
    }));
    sbody.querySelectorAll('[data-all]').forEach((b) => b.addEventListener('click', async () => {
      const p = presets[+b.dataset.all];
      if (!confirm('Terapkan preset "' + p.name + '" ke SEMUA klip project ini?')) return;
      const d = await api('/projects/' + project.id);
      for (const c of d.clips) {
        await api('/clips/' + c.id, jbody({ caption_style: p.style }));
      }
      STYLE = JSON.parse(JSON.stringify(p.style));
      lastCapKey = ''; commit();
      alert('✅ Diterapkan ke ' + d.clips.length + ' klip.');
    }));
    sbody.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
      presets.splice(+b.dataset.del, 1);
      localStorage.setItem('clip_brand_presets', JSON.stringify(presets));
      panelBrand();
    }));
  }

  // --- B-Roll ---
  function panelBroll() {
    sbody.innerHTML = '<h3>🎞️ B-Roll (Pexels)</h3>' +
      '<button class="actionbtn" id="brKw">🧠 Saran kata kunci dari AI</button>' +
      '<div class="kwchips" id="brChips"></div>' +
      '<div class="fgroup"><input type="search" id="brQ" placeholder="Cari stock footage… (English)"></div>' +
      '<div class="tabs2"><button class="on" data-bt="videos">Video</button><button data-bt="photos">Foto</button></div>' +
      '<div class="mediagrid" id="brGrid"></div>' +
      '<h3 style="margin-top:14px;font-size:.82rem">Di timeline</h3><div id="brUsed"></div>';
    let btype = 'videos';
    sbody.querySelectorAll('[data-bt]').forEach((b) => b.addEventListener('click', () => {
      btype = b.dataset.bt;
      sbody.querySelectorAll('[data-bt]').forEach((x) => x.classList.toggle('on', x === b));
      if ($('brQ').value) doSearch();
    }));
    const doSearch = async () => {
      const q = $('brQ').value.trim();
      if (!q) return;
      $('brGrid').innerHTML = '⏳ mencari…';
      try {
        const d = await api('/broll?q=' + encodeURIComponent(q) + '&media_type=' + btype);
        const grid = $('brGrid'); grid.innerHTML = '';
        if (d.error) { grid.innerHTML = '<p class="note">' + d.error + '</p>'; return; }
        if (!d.items.length) grid.innerHTML = '<p class="note">Tidak ada hasil.</p>';
        d.items.forEach((it) => {
          const div = document.createElement('div');
          div.className = 'mi';
          div.innerHTML = '<img src="' + it.thumb + '"><div class="nm">' + (it.by || '') + '</div>';
          div.addEventListener('click', () => {
            const t0 = srcToOut(vid.currentTime);
            ES.broll = ES.broll || [];
            ES.broll.push({ url: it.url, type: it.type === 'video' ? 'video' : 'image', start: t0, end: Math.min(outDuration(), t0 + 2) });
            commit(); renderTimeline(); renderUsed();
          });
          grid.appendChild(div);
        });
      } catch (e) { $('brGrid').innerHTML = '<p class="note">❌ ' + e.message + '</p>'; }
    };
    $('brQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
    $('brKw').addEventListener('click', async () => {
      $('brKw').textContent = '⏳ ...';
      try {
        const d = await api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'broll_keywords' }) });
        const chips = $('brChips'); chips.innerHTML = '';
        d.keywords.forEach((k) => {
          const s = document.createElement('span'); s.textContent = k;
          s.addEventListener('click', () => { $('brQ').value = k; doSearch(); });
          chips.appendChild(s);
        });
        $('brKw').textContent = '🧠 Saran kata kunci dari AI';
      } catch (e) { $('brKw').textContent = '❌ ' + e.message; }
    });
    const renderUsed = () => {
      const used = $('brUsed'); used.innerHTML = '';
      (ES.broll || []).forEach((b, i) => {
        const r = document.createElement('div');
        r.className = 'itemrow';
        r.innerHTML = '<span class="nm">🎞 B-roll ' + (i + 1) + ' (' + fmtT(b.start) + '–' + fmtT(b.end) + ')</span><button>✕</button>';
        r.querySelector('button').addEventListener('click', () => { ES.broll.splice(i, 1); commit(); renderTimeline(); renderUsed(); });
        used.appendChild(r);
      });
    };
    renderUsed();
  }

  // --- Transitions ---
  function panelTransitions() {
    const cur = (ES.transitions && ES.transitions[0] && ES.transitions[0].type) || 'cut';
    sbody.innerHTML = '<h3>🔀 Transisi</h3>' +
      '<p class="note" style="margin-bottom:10px">Dipasang di titik sambungan hasil pemotongan (cut/hapus kata).</p>' +
      ['cut', 'fade', 'zoom', 'slide'].map((t) =>
        '<button class="actionbtn" data-tr="' + t + '" style="' + (cur === t ? 'border-color:var(--accent)' : '') + '">' +
        ({ cut: '✂️ Cut (langsung)', fade: '🌫 Fade', zoom: '🔍 Zoom', slide: '➡️ Slide' })[t] + (cur === t ? ' ✓' : '') + '</button>').join('') +
      '<p class="note">Catatan: zoom & slide dirender sebagai fade halus pada versi ini (TODO upgrade xfade).</p>';
    sbody.querySelectorAll('[data-tr]').forEach((b) => b.addEventListener('click', () => {
      ES.transitions = [{ type: b.dataset.tr }];
      commit(); panelTransitions();
    }));
  }

  // --- Text ---
  function panelText() {
    let list = '';
    (ES.texts || []).forEach((x, i) => {
      list += '<div class="itemrow"><span class="nm">🔤 ' + (x.text || '') + '</span><button data-i="' + i + '">✕</button></div>';
    });
    sbody.innerHTML = '<h3>🔤 Text</h3>' +
      '<div class="fgroup"><label>Teks</label><input type="text" id="txtVal" placeholder="Judul / hook keren…"></div>' +
      '<div class="fgroup colorrow"><span>Warna <input type="color" id="txtColor" value="#FFFFFF"></span>' +
      '<span style="flex:1">Ukuran <input type="number" id="txtSize" value="56" min="20" max="140" style="width:64px"></span></div>' +
      '<div class="fgroup"><label>Posisi vertikal % (0 atas)</label><input type="range" id="txtY" min="2" max="90" value="12"></div>' +
      '<button class="actionbtn primary" id="txtAdd">＋ Tambah di playhead (3 detik)</button>' + list;
    $('txtAdd').addEventListener('click', () => {
      const v = $('txtVal').value.trim();
      if (!v) return;
      const t0 = srcToOut(vid.currentTime);
      ES.texts = ES.texts || [];
      ES.texts.push({ text: v, start: t0, end: Math.min(outDuration(), t0 + 3), color: $('txtColor').value, size: +$('txtSize').value, y_pct: +$('txtY').value, font: 'Arial' });
      commit(); renderTimeline(); panelText();
    });
    sbody.querySelectorAll('[data-i]').forEach((b) => b.addEventListener('click', () => {
      ES.texts.splice(+b.dataset.i, 1);
      commit(); renderTimeline(); panelText();
    }));
  }

  // --- Audio ---
  function panelAudio() {
    sbody.innerHTML = '<h3>🎵 Audio</h3>' +
      '<div class="fgroup"><label>Volume suara asli: <span id="aVolVal">' + Math.round((ES.volume != null ? ES.volume : 1) * 100) + '%</span></label>' +
      '<input type="range" id="aVol" min="0" max="150" value="' + Math.round((ES.volume != null ? ES.volume : 1) * 100) + '"></div>' +
      '<h3 style="font-size:.85rem">Background music</h3>' +
      (ES.music ? '<div class="itemrow"><span class="nm">🎶 ' + (ES.music.name || 'musik') + '</span><button id="musDel">✕</button></div>' : '') +
      '<div id="musList">⏳</div>' +
      (ES.music ?
        '<div class="fgroup"><label>Volume musik: <span id="mVolVal">' + Math.round((ES.music.volume || 0.25) * 100) + '%</span></label>' +
        '<input type="range" id="mVol" min="0" max="100" value="' + Math.round((ES.music.volume || 0.25) * 100) + '"></div>' +
        '<div class="fgroup"><label><input type="checkbox" id="mDuck" ' + (ES.music.duck !== false ? 'checked' : '') + ' style="width:auto;margin-right:6px">Auto-duck saat ada suara bicara</label>' +
        '<label><input type="checkbox" id="mFade" ' + (ES.music.fade !== false ? 'checked' : '') + ' style="width:auto;margin-right:6px">Fade in/out</label></div>' : '') +
      '<p class="note">Musik & ducking diterapkan saat Export (preview belum memutar musik).</p>';
    $('aVol').addEventListener('input', () => {
      ES.volume = +$('aVol').value / 100;
      $('aVolVal').textContent = $('aVol').value + '%';
      vid.volume = clamp(ES.volume, 0, 1);
      markDirty();
    });
    $('aVol').addEventListener('pointerup', commit);
    api('/music').then((d) => {
      const box = $('musList'); box.innerHTML = '';
      d.tracks.forEach((tr) => {
        const r = document.createElement('div');
        r.className = 'itemrow';
        r.innerHTML = '<span class="nm">🎵 ' + tr.name + '</span><button title="Preview">▶</button><button title="Pakai">＋</button>';
        const [pv, add] = r.querySelectorAll('button');
        let au = null;
        pv.addEventListener('click', () => {
          if (au) { au.pause(); au = null; pv.textContent = '▶'; return; }
          au = new Audio(tr.url); au.volume = 0.5; au.play();
          pv.textContent = '⏸';
          au.addEventListener('ended', () => { pv.textContent = '▶'; au = null; });
        });
        add.addEventListener('click', () => {
          if (au) au.pause();
          ES.music = { path: tr.url, volume: 0.25, duck: true, fade: true, name: tr.name };
          commit(); panelAudio();
        });
        box.appendChild(r);
      });
      if (!d.tracks.length) box.innerHTML = '<p class="note">Library kosong.</p>';
    });
    if ($('musDel')) $('musDel').addEventListener('click', () => { ES.music = null; commit(); panelAudio(); });
    if ($('mVol')) {
      $('mVol').addEventListener('input', () => { ES.music.volume = +$('mVol').value / 100; $('mVolVal').textContent = $('mVol').value + '%'; markDirty(); });
      $('mVol').addEventListener('pointerup', commit);
      $('mDuck').addEventListener('change', () => { ES.music.duck = $('mDuck').checked; commit(); });
      $('mFade').addEventListener('change', () => { ES.music.fade = $('mFade').checked; commit(); });
    }
  }

  // --- AI hook ---
  function panelHook() {
    sbody.innerHTML = '<h3>🪝 AI Hook</h3>' +
      '<p class="note" style="margin-bottom:10px">AI membuat 3 alternatif kalimat hook — pasang sebagai text overlay di 3 detik pertama.</p>' +
      '<button class="actionbtn primary" id="hookGen">🧠 Buatkan hook</button><div id="hookList"></div>';
    $('hookGen').addEventListener('click', async () => {
      $('hookGen').textContent = '⏳ AI menulis hook...';
      try {
        const d = await api('/clips/' + clipId + '/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'hooks' }) });
        const box = $('hookList'); box.innerHTML = '';
        d.hooks.forEach((h) => {
          const b = document.createElement('button');
          b.className = 'actionbtn';
          b.textContent = '“' + h + '”';
          b.addEventListener('click', () => {
            ES.texts = ES.texts || [];
            ES.texts.push({ text: h, start: 0, end: 3, color: '#FFE600', size: 64, y_pct: 10, font: 'Arial' });
            commit(); renderTimeline();
            b.textContent = '✅ Terpasang 0–3 detik';
          });
          box.appendChild(b);
        });
        $('hookGen').textContent = '🧠 Buatkan hook';
      } catch (e) { $('hookGen').textContent = '❌ ' + e.message; }
    });
  }

  // ---------------- export ----------------
  function openDialog(html) {
    $('dlgRoot').innerHTML = '<div class="dlg"><div class="box">' + html + '</div></div>';
    $('dlgRoot').querySelectorAll('[data-x]').forEach((b) => b.addEventListener('click', closeDialog));
    $('dlgRoot').querySelector('.dlg').addEventListener('click', (e) => { if (e.target.classList.contains('dlg')) closeDialog(); });
  }
  function closeDialog() { $('dlgRoot').innerHTML = ''; }

  $('btnExport').addEventListener('click', async () => {
    await saveNow();
    let histHtml = '';
    try {
      const h = await api('/clips/' + clipId + '/exports');
      h.exports.filter((e) => e.status === 'done').slice(0, 4).forEach((e) => {
        histHtml += '<div class="itemrow"><span class="nm">📼 ' + e.resolution + ' • ' + new Date(e.created_at).toLocaleString('id-ID') + '</span>' +
          '<a href="' + e.file_path + '" download style="color:var(--accent2)">⬇</a></div>';
      });
    } catch (e) {}
    openDialog(
      '<h3>📤 Export klip</h3>' +
      '<div class="fgroup"><label>Resolusi</label><select id="expRes"><option value="1080p">1080p (kualitas terbaik)</option><option value="720p">720p (file kecil)</option></select></div>' +
      '<div class="fgroup"><label><input type="checkbox" id="expWm" style="width:auto;margin-right:6px">Tambahkan watermark</label></div>' +
      '<div id="expProg" class="hidden"><div class="exp-pbar"><div id="expBar"></div></div><p style="font-size:.8rem;color:var(--muted)" id="expStat">Menyiapkan render…</p></div>' +
      '<div class="row2"><button class="btn btn-g" data-x>Batal</button><button class="btn btn-p" id="expGo">🚀 Render</button></div>' +
      (histHtml ? '<h3 style="font-size:.85rem;margin-top:16px">Riwayat export</h3>' + histHtml : '')
    );
    $('expGo').addEventListener('click', async () => {
      $('expGo').disabled = true;
      $('expProg').classList.remove('hidden');
      try {
        const d = await api('/clips/' + clipId + '/export', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ resolution: $('expRes').value, watermark: $('expWm').checked }),
        });
        const poll = setInterval(async () => {
          try {
            const st = await api('/exports/' + d.export_id);
            $('expBar').style.width = st.percent + '%';
            $('expStat').textContent = st.status === 'queued' ? 'Menunggu antrian…' : 'Rendering ' + st.percent + '%';
            if (st.status === 'done') {
              clearInterval(poll);
              $('expBar').style.width = '100%';
              $('expStat').innerHTML = '✅ Selesai! (' + (st.file_size / 1048576).toFixed(1) + ' MB)';
              $('expGo').outerHTML = '<a class="btn btn-p" style="text-align:center;text-decoration:none" href="' + st.file_path + '" download>⬇️ Download MP4</a>';
            } else if (st.status === 'error') {
              clearInterval(poll);
              $('expStat').textContent = '❌ ' + (st.error_message || 'Render gagal');
              $('expGo').disabled = false;
            }
          } catch (e) {}
        }, 1500);
      } catch (e) {
        $('expStat').textContent = '❌ ' + e.message;
        $('expGo').disabled = false;
      }
    });
  });

  // ---------------- header & shortcuts ----------------
  $('btnBack').addEventListener('click', async () => {
    await saveNow();
    location.href = '/clipstudio?project=' + (project ? project.id : '');
  });
  $('clipTitle').addEventListener('change', () => { title = $('clipTitle').value; commit(); });
  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);
  $('btnSave').addEventListener('click', () => { dirty = true; saveNow(); });
  $('selAspect').addEventListener('change', () => { aspect = $('selAspect').value; layoutStage(); commit(); });
  $('selLayout').addEventListener('change', () => { layout = $('selLayout').value; layoutStage(); applyCropTransform(); commit(); });
  $('btnTracker').addEventListener('click', () => {
    trackerOn = !trackerOn;
    $('btnTracker').textContent = '🎯 Tracker: ' + (trackerOn ? 'ON' : 'OFF');
    commit();
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
    if (e.code === 'Space') { e.preventDefault(); playing ? pause() : play(); }
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelected(); }
    else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
    else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z') { e.preventDefault(); redo(); }
    else if (e.key === '+' || e.key === '=') { pxPerSec = clamp(pxPerSec * 1.25, 20, 400); $('zoomRange').value = pxPerSec; renderTimeline(); }
    else if (e.key === '-') { pxPerSec = clamp(pxPerSec / 1.25, 20, 400); $('zoomRange').value = pxPerSec; renderTimeline(); }
  });
  window.addEventListener('beforeunload', () => { if (dirty) saveNow(); });
  window.addEventListener('resize', () => { layoutStage(); renderTimeline(); });

  // tab mobile
  document.querySelectorAll('.mtabs button').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.mtabs button').forEach((x) => x.classList.toggle('on', x === b));
      document.body.className = 'mtab-' + b.dataset.mtab;
      setTimeout(() => { layoutStage(); renderTimeline(); }, 60);
    });
  });

  // ---------------- waveform decode ----------------
  async function loadWaveform() {
    try {
      const r = await fetch(project.waveform_audio, { headers: { 'Authorization': 'Bearer ' + token() } });
      const buf = await r.arrayBuffer();
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      const audio = await ac.decodeAudioData(buf);
      const ch = audio.getChannelData(0);
      const per = Math.floor(audio.sampleRate / 50); // peak per 20ms
      const peaks = new Float32Array(Math.ceil(ch.length / per));
      for (let i = 0; i < peaks.length; i++) {
        let mx = 0;
        const s0 = i * per, s1 = Math.min(ch.length, s0 + per);
        for (let s = s0; s < s1; s += 4) { const v = Math.abs(ch[s]); if (v > mx) mx = v; }
        peaks[i] = mx;
      }
      wavePeaks = peaks;
      ac.close();
      renderTimeline();
    } catch (e) { console.warn('waveform gagal', e); }
  }

  // ---------------- init ----------------
  async function init() {
    DATA = await api('/clips/' + clipId);
    clip = DATA.clip; project = DATA.project; WORDS = DATA.words || [];
    ES = clip.edit_state || {};
    STYLE = clip.caption_style || { template: 'opus-green' };
    title = clip.title || 'Klip';
    aspect = clip.aspect_ratio || '9:16';
    layout = clip.layout_mode || 'fill';
    trackerOn = clip.tracker_on !== false;
    clip.sprite_meta = clip.sprite_meta || {};

    $('clipTitle').value = title;
    $('selAspect').value = aspect;
    $('selLayout').value = layout;
    $('btnTracker').textContent = '🎯 Tracker: ' + (trackerOn ? 'ON' : 'OFF');
    if (ES.volume != null) { vid.volume = clamp(ES.volume, 0, 1); $('volRange').value = ES.volume * 100; }

    try {
      const t = await api('/templates');
      TPLS = t.templates; window.__fonts = t.fonts;
    } catch (e) { TPLS = []; }

    vid.src = project.source;
    bgVid.src = project.source;
    vid.addEventListener('loadedmetadata', () => {
      seek(cs());
      layoutStage(); applyCropTransform();
    });

    if (clip.sprite) {
      spriteImg = new Image();
      spriteImg.onload = () => renderTimeline();
      spriteImg.src = clip.sprite;
    }
    loadWaveform();

    // credits header
    try {
      const pl = await api('/projects');
      let cr = 0; pl.projects.forEach((p) => cr += p.credits_used || 0);
      $('creditsVal').textContent = cr;
    } catch (e) {}

    renderTranscript();
    layoutStage();
    renderTimeline();
    updateTotals();
    pushHist();                       // snapshot awal
    dirty = false; $('saveState').textContent = 'Tersimpan';
    requestAnimationFrame(rafLoop);

    if (window.innerWidth <= 900) document.body.className = 'mtab-prev';
  }

  init().catch((e) => {
    document.body.innerHTML = '<div style="padding:40px;text-align:center"><h2>😵 ' + e.message + '</h2>' +
      '<p style="margin-top:10px"><a href="/clipstudio" style="color:#8B5CF6">← Kembali ke Clip Studio</a></p></div>';
  });
})();
