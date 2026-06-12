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
    voSyncReset();
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
  function maskWord(t) {
    let core = t, tail = '';
    while (core && !/[a-z0-9]/i.test(core[core.length - 1])) { tail = core[core.length - 1] + tail; core = core.slice(0, -1); }
    if (core.length <= 1) return '*' + tail;
    return core[0] + '*'.repeat(core.length - 1) + tail;
  }
  function isCensored(i) { return (ES.censored_words || []).indexOf(i) !== -1; }
  function visibleCaptionWords() {
    const del = new Set(ES.deleted_words || []);
    return clipWordEntries().filter(({ i, w }) => {
      if (del.has(i)) return false;
      const mid = (w.start + w.end) / 2;
      return !inCut(mid);
    }).map(({ i, w }) => ({
      i, word: isCensored(i) ? maskWord(wordText(i)) : wordText(i),
      start: w.start, end: w.end,
    }));
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
    const [shx, shy] = effectShakeOffset(srcToOut(t));   // efek shake
    if (layout === 'fit') {
      // video utuh + blur background
      vid.style.width = '100%'; vid.style.height = '100%';
      vid.style.left = '0'; vid.style.top = '0'; vid.style.objectFit = 'contain';
      vid.style.position = 'absolute';
      vid.style.transform = (shx || shy) ? `translate(${shx}px,${shy}px)` : '';
      return;
    }
    if (layout === 'split') {
      const vh = stageH * 0.55;
      vid.style.objectFit = 'cover';
      vid.style.width = stageW + 'px'; vid.style.height = vh + 'px';
      vid.style.left = '0'; vid.style.top = '0';
      vid.style.transform = (shx || shy) ? `translate(${shx}px,${shy}px)` : '';
      return;
    }
    vid.style.objectFit = '';
    vid.style.transform = '';
    const [cx, cy] = interpCenter(t);
    const [x, y, w, h] = cropWindow(cx, cy);
    const scale = stageW / w;
    vid.style.width = (project.width * scale) + 'px';
    vid.style.height = 'auto';
    vid.style.left = (-x * scale + shx) + 'px';
    vid.style.top = (-y * scale + shy) + 'px';
  }

  // Interaksi stage: KLIK (tanpa geser) = play/pause; GESER = pindah crop manual.
  // (pola CapCut/YouTube — tombol play besar hanya visual, pointer-events none)
  let dragCrop = null;
  stage.addEventListener('pointerdown', (e) => {
    if (e.target.closest('#capOverlay')) return;          // caption: drag posisi / klik edit
    if (e.target.closest('[data-oi]')) return;            // overlay media: drag sendiri
    const [cx, cy] = interpCenter(vid.currentTime);
    dragCrop = { x0: e.clientX, y0: e.clientY, cx, cy, moved: false };
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener('pointermove', (e) => {
    if (!dragCrop) return;
    if (!dragCrop.moved &&
        Math.abs(e.clientX - dragCrop.x0) < 5 && Math.abs(e.clientY - dragCrop.y0) < 5) return;
    dragCrop.moved = true;
    if (layout !== 'fill') return;                        // crop drag hanya mode Fill
    const [, , w] = cropWindow(dragCrop.cx, dragCrop.cy);
    const scale = stageW / w;
    const ncx = clamp(dragCrop.cx - (e.clientX - dragCrop.x0) / scale, 0, project.width);
    const ncy = clamp(dragCrop.cy - (e.clientY - dragCrop.y0) / scale, 0, project.height);
    setKeyframeAt(vid.currentTime, ncx, ncy, false);
  });
  stage.addEventListener('pointerup', () => {
    if (!dragCrop) return;
    const wasClick = !dragCrop.moved;
    const didCrop = dragCrop.moved && layout === 'fill';
    dragCrop = null;
    if (wasClick) { playing ? pause() : play(); }         // klik = play/pause
    else if (didCrop) { commit(); }
  });
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

  // drag posisi caption (vertikal); klik tanpa geser = buka editor caption (sorot & edit)
  let dragCap = null;
  capOv.addEventListener('pointerdown', (e) => {
    dragCap = { y0: e.clientY, x0: e.clientX, p0: effStyle().pos_pct || 72, moved: false };
    capOv.setPointerCapture(e.pointerId);
    e.stopPropagation();
  });
  capOv.addEventListener('pointermove', (e) => {
    if (!dragCap) return;
    if (Math.abs(e.clientY - dragCap.y0) > 4 || Math.abs(e.clientX - dragCap.x0) > 4) dragCap.moved = true;
    if (!dragCap.moved) return;
    const dpct = (e.clientY - dragCap.y0) / stageH * 100;
    STYLE.pos_pct = clamp(Math.round(dragCap.p0 - dpct), 5, 95);
  });
  capOv.addEventListener('pointerup', () => {
    if (!dragCap) return;
    const wasClick = !dragCap.moved;
    dragCap = null;
    if (wasClick) {
      // klik caption -> edit page yang sedang tampil
      const t = vid.currentTime;
      const pages = getPages();
      const pi = pages.findIndex((p) => t >= p[0].start - 0.05 && t <= p[p.length - 1].end + 0.15);
      if (pi >= 0) openCaptionEditor(pi);
    } else { commit(); refreshOpenPanel(); }
  });

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

  // ---------------- EFEK (CapCut-style) ----------------
  // Preview pakai CSS filter/transform; export pakai filter ffmpeg yang setara.
  const EFFECTS = [
    { type: 'bw', name: 'Hitam Putih', css: 'grayscale(1)' },
    { type: 'vintage', name: 'Vintage', css: 'sepia(.5) contrast(1.08) brightness(1.02)' },
    { type: 'blur', name: 'Blur', css: 'blur(7px)' },
    { type: 'glow', name: 'Glow Terang', css: 'brightness(1.18) saturate(1.35)' },
    { type: 'grain', name: 'Film Grain', css: 'contrast(1.07) brightness(.98)' },
    { type: 'shake', name: 'Goyang (Shake)', css: '' },
    { type: 'invert', name: 'Negatif', css: 'invert(1)' },
  ];
  function activeEffects(tOut) {
    return (ES.effects || []).filter((fx) => tOut >= (fx.start || 0) && tOut <= (fx.end || 3));
  }
  function effectShakeOffset(tOut) {
    const on = activeEffects(tOut).some((fx) => fx.type === 'shake');
    if (!on) return [0, 0];
    const amp = stageW * 0.012;
    return [Math.sin(tOut * 53) * amp, Math.cos(tOut * 47) * amp];
  }

  // ---------------- ANIMASI & MASK (buatan sendiri, tanpa API eksternal) ----------------
  const ANIM_DUR = 0.45;
  const TEXT_ANIM_IN = [['none', 'Tanpa'], ['fade', 'Fade In'], ['slide-up', 'Naik'], ['slide-down', 'Turun'], ['slide-left', 'Geser Kiri']];
  const TEXT_ANIM_OUT = [['none', 'Tanpa'], ['fade', 'Fade Out'], ['slide-down', 'Turun']];
  const TEXT_ANIM_LOOP = [['none', 'Tanpa'], ['pulse', 'Pulse (denyut)']];
  const MASKS = [['none', 'Tanpa'], ['circle', 'Lingkaran'], ['rounded', 'Rounded']];

  function animState(it, t) {
    // hitung {opacity, dx, dy} dari animasi masuk/keluar item pada waktu output t
    const s = it.start || 0, e = it.end || s + 3;
    let op = 1, dx = 0, dy = 0;
    const din = Math.min(ANIM_DUR, (e - s) / 2);
    if (it.anim_in && it.anim_in !== 'none' && t < s + din) {
      const p = clamp((t - s) / din, 0, 1);
      op = p;
      if (it.anim_in === 'slide-up') dy = (1 - p) * stageH * 0.07;
      if (it.anim_in === 'slide-down') dy = -(1 - p) * stageH * 0.07;
      if (it.anim_in === 'slide-left') dx = (1 - p) * stageW * 0.12;
      if (it.anim_in === 'fade') { dx = 0; dy = 0; }
    }
    if (it.anim_out && it.anim_out !== 'none' && t > e - din) {
      const p = clamp((e - t) / din, 0, 1);
      op = Math.min(op, p);
      if (it.anim_out === 'slide-down') dy = (1 - p) * stageH * 0.07;
    }
    if (it.anim_loop === 'pulse') op *= 0.82 + 0.18 * Math.sin(t * 6);
    return { op, dx, dy };
  }
  function maskCss(it) {
    if (it.mask === 'circle') return 'clip-path:circle(50% at 50% 50%);';
    if (it.mask === 'rounded') return 'border-radius:14%;overflow:hidden;';
    return '';
  }

  // ---------------- TEXT OVERLAY ber-style ala Opus (bg rounded, align, italic) ----------------
  // Preview = CSS live; untuk export, teks dirender PNG LOKAL via canvas (tanpa API eksternal)
  // lalu diupload sbg media project — ffmpeg drawtext tidak bisa bg rounded/wrap/align.
  function roundRectPath(c, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, h / 2, w / 2));
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
  function textFontCss(it, px) {
    return (it.italic ? 'italic ' : '') + '800 ' + px + 'px ' + cssFont(it.font || 'Arial');
  }
  function needsPngRender(it) {
    return !!(it.bg || it.italic || it.underline || (it.align && it.align !== 'center'));
  }
  async function renderTextPng(it) {
    // basis 1080x1920 — identik dgn resolusi export
    const W = 1080;
    const boxW = Math.round(W * (it.width_pct || 86) / 100);
    const fs = it.size || 56;
    const padX = Math.round(fs * 0.45), padY = Math.round(fs * 0.22);
    const cv = document.createElement('canvas');
    let c = cv.getContext('2d');
    c.font = textFontCss(it, fs);
    const words = String(it.text || '').trim().split(/\s+/);
    const lines = []; let cur = '';
    for (const w of words) {
      const t = cur ? cur + ' ' + w : w;
      if (c.measureText(t).width > boxW - padX * 2 && cur) { lines.push(cur); cur = w; } else cur = t;
    }
    if (cur) lines.push(cur);
    const lh = Math.round(fs * 1.3);
    cv.width = boxW;
    cv.height = lines.length * lh + padY * 2 + 8;
    c = cv.getContext('2d');
    c.font = textFontCss(it, fs);
    c.textBaseline = 'middle';
    const radius = it.bg_radius != null ? +it.bg_radius : 10;
    lines.forEach((ln, i) => {
      const tw = c.measureText(ln).width;
      const x = it.align === 'left' ? padX : it.align === 'right' ? boxW - padX - tw : (boxW - tw) / 2;
      const yC = padY + 4 + i * lh + lh / 2;
      if (it.bg) {   // background rounded per BARIS (gaya "word's background" Opus)
        c.fillStyle = it.bg;
        roundRectPath(c, x - padX * 0.7, yC - lh / 2, tw + padX * 1.4, lh, radius);
        c.fill();
      } else {
        c.shadowColor = 'rgba(0,0,0,.8)'; c.shadowBlur = 8; c.shadowOffsetY = 2;
      }
      c.fillStyle = it.color || '#fff';
      c.fillText(ln, x, yC);
      c.shadowColor = 'transparent'; c.shadowBlur = 0; c.shadowOffsetY = 0;
      if (it.underline) {
        c.strokeStyle = it.color || '#fff';
        c.lineWidth = Math.max(2, fs / 14);
        c.beginPath();
        c.moveTo(x, yC + fs * 0.42);
        c.lineTo(x + tw, yC + fs * 0.42);
        c.stroke();
      }
    });
    return new Promise((res) => cv.toBlob(res, 'image/png'));
  }
  const txtRenderTimers = new Map();
  function scheduleTextRender(it) {
    // regen PNG export (debounce per item) — preview tetap CSS live
    if (!needsPngRender(it)) {
      if (it.render_url) { delete it.render_url; markDirty(); }
      return;
    }
    clearTimeout(txtRenderTimers.get(it));
    txtRenderTimers.set(it, setTimeout(async () => {
      try {
        const blob = await renderTextPng(it);
        const fd = new FormData();
        fd.append('file', blob, '_txt_' + Date.now() + '.png');
        const d = await api('/projects/' + project.id + '/media', { method: 'POST', body: fd });
        it.render_url = d.url;
        commit();
      } catch (e) { console.warn('Render teks PNG gagal:', e); }
    }, 700));
  }

  // ---------------- overlay preview terpadu (z-order mengikuti track) ----------------
  function renderOverlays() {
    const t = srcToOut(vid.currentTime);

    // efek -> filter di elemen video
    const fxCss = activeEffects(t).map((fx) => {
      const def = EFFECTS.find((d) => d.type === fx.type);
      return def ? def.css : '';
    }).filter(Boolean).join(' ');
    if (vid._fx !== fxCss) { vid.style.filter = fxCss; bgVid.style.filter = fxCss + ' blur(22px) brightness(.55)'; vid._fx = fxCss; }

    const ml = $('mediaLayer');
    if (ovDrag) return;   // jangan rebuild DOM saat user sedang menggeser overlay

    // 1) bangun ulang DOM hanya saat STRUKTUR berubah (video tidak restart saat animasi)
    const items = visualItems().slice().sort((a2, b2) => trackOf(a2.kind, a2.it) - trackOf(b2.kind, b2.it));
    const vis = items.filter((v) => t >= (v.it.start || 0) && t <= (v.it.end || 3));
    const sig = JSON.stringify(vis.map((v) => {
      const it = v.it;
      return [v.kind, v.idx, it.url, it.x_pct, it.y_pct, it.w_pct, it.rot, it.mask,
              it.border, it.border_color, it.text, it.size, it.color, it.font, trackOf(v.kind, it),
              it.bg, it.bg_radius, it.align, it.italic, it.underline, it.width_pct];
    }));
    if (ml._sig !== sig) {
      ml._sig = sig;
      let mh = '';
      vis.forEach((v) => {
        const it = v.it;
        const z = 4 + trackOf(v.kind, it);
        const dk = ' data-k="' + v.kind + ':' + v.idx + '"';
        if (v.kind === 'broll') {
          const tag = it.type === 'video' ? 'video' : 'img';
          mh += '<' + tag + dk + ' src="' + (it.url || '') + '" ' + (tag === 'video' ? 'muted autoplay loop playsinline' : '') +
            ' style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;z-index:' + z + '"></' + tag + '>';
        } else if (v.kind === 'overlays') {
          const tag = it.type === 'video' ? 'video' : 'img';
          const border = it.border ? 'border:3px solid ' + (it.border_color || '#fff') + ';' : '';
          mh += '<' + tag + dk + ' data-oi="' + v.idx + '" src="' + (it.url || '') + '" ' + (tag === 'video' ? 'muted autoplay loop playsinline' : '') +
            ' style="position:absolute;left:' + (it.x_pct || 50) + '%;top:' + (it.y_pct || 50) + '%;width:' + (it.w_pct || 40) +
            '%;border-radius:6px;pointer-events:auto;cursor:move;touch-action:none;z-index:' + z + ';' +
            maskCss(it) + border + '"></' + tag + '>';
        } else if (v.kind === 'texts') {
          const size = (it.size || 56) * stageH / 1920;
          const wp = it.width_pct || 86;
          const align = it.align || 'center';
          const rad = (it.bg_radius != null ? +it.bg_radius : 10) * stageH / 1920;
          const deco = (it.underline ? 'text-decoration:underline;' : '') + (it.italic ? 'font-style:italic;' : '');
          const bg = it.bg
            ? 'background:' + it.bg + ';border-radius:' + rad.toFixed(1) + 'px;padding:.1em .35em;box-decoration-break:clone;-webkit-box-decoration-break:clone;'
            : 'text-shadow:0 2px 6px rgba(0,0,0,.8);';
          mh += '<div' + dk + ' style="position:absolute;left:' + (50 - wp / 2) + '%;width:' + wp + '%;top:' + (it.y_pct || 12) + '%;text-align:' + align + ';z-index:' + z + ';line-height:1.42;pointer-events:none">' +
            '<span style="font-family:' + cssFont(it.font || 'Arial') + ';font-weight:800;font-size:' + size + 'px;color:' + (it.color || '#fff') + ';' + deco + bg + '">' +
            String(it.text || '').replace(/</g, '&lt;') + '</span></div>';
        }
        // efek tidak digambar sebagai DOM (filter video di atas)
      });
      ml.innerHTML = mh;
    }

    // 2) tiap frame: terapkan nilai animasi (opacity + offset) tanpa rebuild
    for (const el of ml.children) {
      const k = (el.dataset.k || '').split(':');
      const v = vis.find((v2) => v2.kind === k[0] && v2.idx === +k[1]);
      if (!v) continue;
      const it = v.it;
      const an = animState(it, t);
      el.style.opacity = an.op.toFixed(3);
      if (v.kind === 'overlays') {
        el.style.transform = 'translate(calc(-50% + ' + an.dx + 'px),calc(-50% + ' + an.dy + 'px))' +
          (it.rot ? ' rotate(' + it.rot + 'deg)' : '');
      } else if (v.kind === 'texts') {
        el.style.transform = 'translate(' + an.dx + 'px,' + an.dy + 'px)';
      }
    }
    const tl2 = $('textLayer');
    if (tl2 && tl2.innerHTML) tl2.innerHTML = '';   // teks kini ikut layer terpadu
  }

  // drag overlay media di preview (pindah posisi) + scroll mouse utk resize
  let ovDrag = null;
  $('mediaLayer').addEventListener('pointerdown', (e) => {
    const el = e.target.closest('[data-oi]');
    if (!el) return;
    const oi = +el.dataset.oi;
    const o = (ES.overlays || [])[oi];
    if (!o) return;
    ovDrag = { oi, el, x0: e.clientX, y0: e.clientY, px: o.x_pct || 50, py: o.y_pct || 50 };
    el.setPointerCapture(e.pointerId);
    el.style.outline = '2px solid #06B6D4';
    e.stopPropagation(); e.preventDefault();
  }, true);
  $('mediaLayer').addEventListener('pointermove', (e) => {
    if (!ovDrag) return;
    const o = ES.overlays[ovDrag.oi];
    o.x_pct = clamp(Math.round(ovDrag.px + (e.clientX - ovDrag.x0) / stageW * 100), 0, 100);
    o.y_pct = clamp(Math.round(ovDrag.py + (e.clientY - ovDrag.y0) / stageH * 100), 0, 100);
    ovDrag.el.style.left = o.x_pct + '%';
    ovDrag.el.style.top = o.y_pct + '%';
  }, true);
  $('mediaLayer').addEventListener('pointerup', () => {
    if (!ovDrag) return;
    ovDrag.el.style.outline = '';
    ovDrag = null;
    $('mediaLayer')._last = '';   // paksa rebuild bersih
    commit(); renderTimeline();
  }, true);
  $('mediaLayer').addEventListener('wheel', (e) => {
    const el = e.target.closest('[data-oi]');
    if (!el) return;
    const o = (ES.overlays || [])[+el.dataset.oi];
    if (!o) return;
    e.preventDefault();
    o.w_pct = clamp(Math.round((o.w_pct || 40) + (e.deltaY < 0 ? 3 : -3)), 8, 100);
    el.style.width = o.w_pct + '%';
    markDirty();
  }, { passive: false, capture: true });

  // ---------------- playback engine ----------------
  function play() {
    if (vid.currentTime < cs() || vid.currentTime >= ce() - 0.05) vid.currentTime = keptSegments()[0] ? keptSegments()[0][0] : cs();
    vid.play(); if (layout === 'fit') bgVid.play().catch(() => {});
    playing = true; $('btnPlay').textContent = '⏸';
    $('playBig').style.display = 'none';
  }
  function pause() {
    vid.pause(); bgVid.pause();
    voAudios.forEach((a) => { try { a.el.pause(); } catch (e) {} });
    playing = false; $('btnPlay').textContent = '▶';
    $('playBig').style.display = 'flex';
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
    voSync();
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
      span.textContent = isCensored(i) ? maskWord(wordText(i)) : wordText(i);
      if (isCensored(i)) span.style.cssText += 'color:#F59E0B;border-bottom:1px dashed #F59E0B';
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

  // ================= TIMELINE ala CAPCUT =================
  // Sumbu X = WAKTU OUTPUT (potongan cut benar-benar hilang, segmen menyambung).
  // Track: 3 baris overlay (atas = layer teratas) -> video utama -> waveform ->
  // caption -> audio (voice-over & musik). Blok bisa digeser horizontal & VERTIKAL
  // (pindah track), tepi blok utk resize, tepi segmen video utk trim cut.
  const LANE = {
    ruler: [0, 16],
    ov: [[18, 16], [36, 16], [54, 16]],   // baris 0 (paling atas) = track 2
    video: [72, 46],
    wave: [120, 22],
    cap: [144, 16],
    audio: [162, 16],
  };
  const TL_H = 182;
  const tlStatic = document.createElement('canvas');   // lapisan konten (tanpa playhead)
  let selItem = null;       // {kind:'broll'|'overlays'|'texts'|'effects'|'voiceovers'|'cap'|'music', idx}
  let capRects = [];        // rect blok caption utk hit-test

  const X = (tOut) => tOut * pxPerSec + 8;
  const o2s = (t) => { // waktu output -> sumber
    const segs = keptSegments();
    let acc = 0;
    for (const [a, b] of segs) { const d = b - a; if (t <= acc + d) return a + (t - acc); acc += d; }
    return segs.length ? segs[segs.length - 1][1] : cs();
  };
  const trackOf = (kind, item) =>
    (item.track != null) ? item.track : (kind === 'texts' || kind === 'effects' ? 2 : kind === 'broll' ? 0 : 1);
  const ovLaneForTrack = (tr) => LANE.ov[2 - clamp(tr, 0, 2)];
  const trackForY = (y) => {
    for (let r = 0; r < 3; r++) {
      if (y >= LANE.ov[r][0] - 2 && y <= LANE.ov[r][0] + LANE.ov[r][1] + 2) return 2 - r;
    }
    return null;
  };
  function visualItems() { // semua blok visual (utk render & hit-test), urut track
    const out = [];
    (ES.broll || []).forEach((it, idx) => out.push({ kind: 'broll', idx, it, color: '#F59E0B', label: '🎞B' + (idx + 1) }));
    (ES.overlays || []).forEach((it, idx) => { if (it.type !== 'audio') out.push({ kind: 'overlays', idx, it, color: '#06B6D4', label: (it.sticker ? '😀' : '🖼') + 'M' + (idx + 1) }); });
    (ES.texts || []).forEach((it, idx) => out.push({ kind: 'texts', idx, it, color: '#EC4899', label: '🔤' + (it.text || '').slice(0, 10) }));
    (ES.effects || []).forEach((it, idx) => out.push({ kind: 'effects', idx, it, color: '#A78BFA', label: '🎇' + (it.name || it.type) }));
    return out;
  }

  function renderTimeline() {
    invalidatePages();
    const outDur = outDuration();
    tlW = Math.max($('tlScroll').clientWidth, Math.ceil(outDur * pxPerSec) + 40);
    tlStatic.width = tlW * devicePixelRatio;
    tlStatic.height = TL_H * devicePixelRatio;
    tlCanvas.width = tlW * devicePixelRatio;
    tlCanvas.height = TL_H * devicePixelRatio;
    tlCanvas.style.width = tlW + 'px';
    tlCanvas.style.height = TL_H + 'px';
    const c = tlStatic.getContext('2d');
    c.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    c.clearRect(0, 0, tlW, TL_H);

    // latar baris track (ala CapCut)
    LANE.ov.forEach((l) => { c.fillStyle = 'rgba(255,255,255,.025)'; c.fillRect(8, l[0], tlW - 16, l[1]); });
    c.fillStyle = 'rgba(255,255,255,.03)';
    c.fillRect(8, LANE.audio[0], tlW - 16, LANE.audio[1]);

    // ruler (waktu OUTPUT)
    c.fillStyle = '#9A9AAB'; c.font = '9px Inter'; c.strokeStyle = '#22222C';
    const step = pxPerSec >= 120 ? 1 : pxPerSec >= 50 ? 5 : 10;
    for (let s = 0; s <= outDur + step; s += step) {
      const x = X(s);
      c.fillText(fmtT(s).slice(0, 5), x + 2, 11);
      c.beginPath(); c.moveTo(x, 13); c.lineTo(x, TL_H); c.stroke();
    }

    // VIDEO utama: segmen menyambung (cut benar-benar hilang dari timeline)
    const segs = keptSegments();
    const m = (clip.sprite_meta && clip.sprite_meta.cols) ? clip.sprite_meta : null;
    let acc = 0;
    segs.forEach((seg, si) => {
      const segOutX = X(acc);
      const segW = (seg[1] - seg[0]) * pxPerSec;
      c.fillStyle = '#101016';
      c.fillRect(segOutX, LANE.video[0], segW, LANE.video[1]);
      if (m && spriteImg) {
        const k0 = Math.max(0, Math.floor((seg[0] - clip.start) / m.interval));
        for (let k = k0; k < m.count; k++) {
          const tileSrc = clip.start + k * m.interval;
          if (tileSrc >= seg[1]) break;
          if (tileSrc + m.interval <= seg[0]) continue;
          const sx = (k % m.cols) * m.tile_w, sy = Math.floor(k / m.cols) * m.tile_h;
          const dx = segOutX + Math.max(0, (tileSrc - seg[0])) * pxPerSec;
          const dw = Math.min(m.interval, seg[1] - Math.max(tileSrc, seg[0])) * pxPerSec;
          c.drawImage(spriteImg, sx, sy, m.tile_w, m.tile_h, dx, LANE.video[0], dw + 0.5, LANE.video[1]);
        }
      }
      // bingkai segmen + garis sambungan (titik split/cut)
      const isSel = selSegment && Math.abs(selSegment[0] - seg[0]) < 0.01 && Math.abs(selSegment[1] - seg[1]) < 0.01;
      c.strokeStyle = isSel ? '#06B6D4' : '#2A2A36';
      c.lineWidth = isSel ? 2.5 : 1;
      c.strokeRect(segOutX, LANE.video[0], segW, LANE.video[1]);
      c.lineWidth = 1;
      if (si > 0) { // penanda sambungan cut
        c.fillStyle = '#F43F5E';
        c.fillRect(segOutX - 1.5, LANE.video[0] - 2, 3, LANE.video[1] + 4);
      }
      acc += seg[1] - seg[0];
    });

    // waveform (mengikuti waktu output)
    c.fillStyle = 'rgba(6,182,212,.75)';
    if (wavePeaks) {
      const mid = LANE.wave[0] + LANE.wave[1] / 2;
      for (let x = 8; x < Math.min(tlW - 8, X(outDur)); x += 2) {
        const src = o2s((x - 8) / pxPerSec);
        const p = wavePeaks[Math.floor(src * 50)] || 0; // peaks per 20ms
        const h = Math.max(1, p * LANE.wave[1]);
        c.fillRect(x, mid - h / 2, 1.4, h);
      }
    }

    // caption pages (klik = pilih, dblklik = edit)
    capRects = [];
    getPages().forEach((p, pi) => {
      const x0 = srcToOut(p[0].start), x1 = srcToOut(p[p.length - 1].end);
      const x = X(x0), w = Math.max(4, (x1 - x0) * pxPerSec);
      const isSel = selItem && selItem.kind === 'cap' && selItem.idx === pi;
      c.fillStyle = isSel ? 'rgba(139,92,246,.95)' : 'rgba(139,92,246,.5)';
      c.fillRect(x, LANE.cap[0] + 2, w - 1, LANE.cap[1] - 4);
      if (isSel) { c.strokeStyle = '#fff'; c.strokeRect(x, LANE.cap[0] + 2, w - 1, LANE.cap[1] - 4); }
      capRects.push({ x, w, pi });
    });

    // blok overlay di 3 track (geser vertikal = pindah track/layer)
    const drawBlock = (lane, item, color, label, isSel) => {
      const s = item.start || 0, e = item.end || (s + 3);
      const x = X(s), w = Math.max(8, (e - s) * pxPerSec);
      c.fillStyle = color + (isSel ? 'CC' : '70');
      c.strokeStyle = isSel ? '#FFFFFF' : color;
      c.lineWidth = isSel ? 2 : 1;
      c.fillRect(x, lane[0] + 1, w, lane[1] - 2);
      c.strokeRect(x, lane[0] + 1, w, lane[1] - 2);
      if (isSel) {
        c.fillStyle = '#fff';
        c.fillRect(x - 2, lane[0], 4, lane[1]);
        c.fillRect(x + w - 2, lane[0], 4, lane[1]);
      }
      c.lineWidth = 1;
      c.fillStyle = '#fff'; c.font = '9px Inter';
      c.fillText(label, x + 4, lane[0] + 12);
    };
    visualItems().forEach((v) => {
      const isSel = selItem && selItem.kind === v.kind && selItem.idx === v.idx;
      drawBlock(ovLaneForTrack(trackOf(v.kind, v.it)), v.it, v.color, v.label, isSel);
    });

    // lane AUDIO: voice-over + audio tambahan + musik
    (ES.voiceovers || []).forEach((v, i) => {
      const isSel = selItem && selItem.kind === 'voiceovers' && selItem.idx === i;
      drawBlock(LANE.audio, v, '#22C55E', '🎙' + ((v.text || 'VO').slice(0, 14)), isSel);
    });
    (ES.audios || []).forEach((a, i) => {
      const isSel = selItem && selItem.kind === 'audios' && selItem.idx === i;
      drawBlock(LANE.audio, a, '#38BDF8', '🎵' + ((a.name || 'audio').slice(0, 12)), isSel);
    });
    if (ES.music) {
      const isSel = selItem && selItem.kind === 'music';
      drawBlock(LANE.audio, { start: ES.music.start || 0, end: (ES.music.end != null ? ES.music.end : outDur) },
        '#0EA5E9', '🎵 ' + (ES.music.name || 'musik'), isSel);
    }

    // trim handles ujung klip (di waktu output: 0 dan outDur)
    c.fillStyle = '#8B5CF6';
    c.fillRect(X(0) - 5, LANE.video[0], 5, LANE.video[1]);
    c.fillRect(X(outDur), LANE.video[0], 5, LANE.video[1]);

    compositeTimeline(true);
    updateTotals();
  }

  let phX = -1;
  function compositeTimeline(force) {
    const x = X(srcToOut(vid.currentTime));
    if (!force && Math.abs(x - phX) < 0.4) return;
    phX = x;
    tctx.setTransform(1, 0, 0, 1, 0, 0);
    tctx.clearRect(0, 0, tlCanvas.width, tlCanvas.height);
    tctx.drawImage(tlStatic, 0, 0);
    tctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    tctx.strokeStyle = '#fff'; tctx.lineWidth = 1.6;
    tctx.beginPath(); tctx.moveTo(x, 0); tctx.lineTo(x, TL_H); tctx.stroke();
    tctx.fillStyle = '#fff';
    tctx.beginPath(); tctx.moveTo(x - 5, 0); tctx.lineTo(x + 5, 0); tctx.lineTo(x, 8); tctx.fill();
    tctx.lineWidth = 1;
  }
  function drawPlayhead() { compositeTimeline(false); }

  function updateTotals() { $('tTot').textContent = fmtT(outDuration()); }

  // ---- interaksi timeline: seleksi, drag blok (H+V), trim segmen, seek ----
  let tlDrag = null;
  const EDGE_PX = 7;

  function segBoundaries() {
    // [{outX, segIdxLeft, segIdxRight}] posisi sambungan antar segmen (waktu output)
    const segs = keptSegments();
    const out = [];
    let acc = 0;
    for (let i = 0; i < segs.length; i++) {
      if (i > 0) out.push({ tOut: acc, left: i - 1, right: i });
      acc += segs[i][1] - segs[i][0];
    }
    return out;
  }

  function hitTest(x, y) {
    const outDur = outDuration();
    const tOut = (x - 8) / pxPerSec;
    // 1) trim handle ujung klip
    if (Math.abs(x - X(0)) < EDGE_PX + 2 && y >= LANE.video[0] && y <= LANE.video[0] + LANE.video[1]) return { type: 'trimL' };
    if (Math.abs(x - X(outDur)) < EDGE_PX + 2 && y >= LANE.video[0] && y <= LANE.video[0] + LANE.video[1]) return { type: 'trimR' };
    // 2) tepi sambungan segmen (trim cut ala CapCut) di lane video
    if (y >= LANE.video[0] && y <= LANE.video[0] + LANE.video[1]) {
      for (const b of segBoundaries()) {
        if (Math.abs(x - X(b.tOut)) <= EDGE_PX) return { type: 'segedge', boundary: b };
      }
    }
    // 3) blok caption
    if (y >= LANE.cap[0] && y <= LANE.cap[0] + LANE.cap[1]) {
      const r = capRects.find((r2) => x >= r2.x - 2 && x <= r2.x + r2.w + 2);
      if (r) return { type: 'cap', idx: r.pi };
    }
    // 4) blok overlay (3 track) — cek dari track teratas
    const tr = trackForY(y);
    if (tr != null) {
      const items = visualItems().filter((v) => trackOf(v.kind, v.it) === tr);
      for (let k = items.length - 1; k >= 0; k--) {
        const v = items[k];
        const s = v.it.start || 0, e = v.it.end || s + 3;
        const x1 = X(s), x2 = x1 + Math.max(8, (e - s) * pxPerSec);
        if (x < x1 - EDGE_PX || x > x2 + EDGE_PX) continue;
        const edge = (Math.abs(x - x1) <= EDGE_PX) ? 'L' : (Math.abs(x - x2) <= EDGE_PX) ? 'R' : 'mid';
        return { type: 'block', key: v.kind, idx: v.idx, item: v.it, edge };
      }
    }
    // 5) lane audio: voice-over / audio tambahan / musik
    if (y >= LANE.audio[0] && y <= LANE.audio[0] + LANE.audio[1]) {
      for (const [key, arr] of [['voiceovers', ES.voiceovers || []], ['audios', ES.audios || []]]) {
        for (let i = arr.length - 1; i >= 0; i--) {
          const it = arr[i];
          const s = it.start || 0, e = it.end || s + 3;
          const x1 = X(s), x2 = x1 + Math.max(8, (e - s) * pxPerSec);
          if (x < x1 - EDGE_PX || x > x2 + EDGE_PX) continue;
          const edge = (Math.abs(x - x1) <= EDGE_PX) ? 'L' : (Math.abs(x - x2) <= EDGE_PX) ? 'R' : 'mid';
          return { type: 'block', key, idx: i, item: it, edge, audioLane: true };
        }
      }
      if (ES.music) {
        // blok musik: bisa digeser & di-resize seperti blok lain
        if (ES.music.start == null) ES.music.start = 0;
        if (ES.music.end == null) ES.music.end = outDur;
        const x1 = X(ES.music.start), x2 = X(ES.music.end);
        if (x >= x1 - EDGE_PX && x <= x2 + EDGE_PX) {
          const edge = (Math.abs(x - x1) <= EDGE_PX) ? 'L' : (Math.abs(x - x2) <= EDGE_PX) ? 'R' : 'mid';
          return { type: 'block', key: 'music', idx: 0, item: ES.music, edge, audioLane: true };
        }
      }
    }
    // 6) lane video tengah = pilih segmen
    if (y >= LANE.video[0] && y <= LANE.wave[0] + LANE.wave[1]) return { type: 'video' };
    return { type: 'seek' };
  }

  tlCanvas.addEventListener('pointerdown', (e) => {
    const rect = tlCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const tOut = clamp((x - 8) / pxPerSec, 0, outDuration());
    const hit = hitTest(x, y);

    if (hit.type === 'cap') {
      selItem = { kind: 'cap', idx: hit.idx };
      selSegment = null;
      const page = getPages()[hit.idx];
      if (page) seek(page[0].start + 0.01);
      renderTimeline();
    } else if (hit.type === 'block') {
      selItem = { kind: hit.key, idx: hit.idx };
      selSegment = null;
      tlDrag = { type: 'block', key: hit.key, item: hit.item, edge: hit.edge,
                 audioLane: !!hit.audioLane, grabOff: tOut - (hit.item.start || 0) };
      renderTimeline();
    } else if (hit.type === 'music') {
      selItem = { kind: 'music', idx: 0 };
      selSegment = null;
      renderTimeline();
    } else if (hit.type === 'segedge') {
      // siapkan drag tepi sambungan: satukan cut yang menyusun boundary ini
      const segs = keptSegments();
      const a = segs[hit.boundary.left][1], b = segs[hit.boundary.right][0];
      ES.cut_ranges = (ES.cut_ranges || []).filter((cu) => cu[1] <= a + 0.001 || cu[0] >= b - 0.001);
      const merged = [a, b];
      ES.cut_ranges.push(merged);
      let leftOut = 0;
      for (const [s0, s1] of segs) { if (s1 <= a + 0.001) leftOut += s1 - s0; else break; }
      tlDrag = { type: 'segedge', cut: merged, orig: [a, b], leftOut };
      selItem = null; selSegment = null;
    } else if (hit.type === 'trimL' || hit.type === 'trimR') {
      tlDrag = { type: hit.type };
    } else {
      selItem = null;
      tlDrag = { type: 'seek' };
      if (hit.type === 'video') {
        const src = o2s(tOut);
        selSegment = keptSegments().find((s) => src >= s[0] && src <= s[1]) || null;
      } else selSegment = null;
      seek(o2s(tOut));
      renderTimeline();
    }
    tlCanvas.setPointerCapture(e.pointerId);
  });

  tlCanvas.addEventListener('pointermove', (e) => {
    const rect = tlCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (!tlDrag) {
      const hit = hitTest(x, y);
      tlCanvas.style.cursor =
        (hit.type === 'trimL' || hit.type === 'trimR' || hit.type === 'segedge') ? 'ew-resize' :
        (hit.type === 'block' && hit.edge !== 'mid') ? 'ew-resize' :
        (hit.type === 'block' || hit.type === 'cap' || hit.type === 'music') ? 'grab' :
        (hit.type === 'video') ? 'pointer' : 'crosshair';
      return;
    }
    const tOut = clamp((x - 8) / pxPerSec, 0, outDuration());
    if (tlDrag.type === 'seek') { seek(o2s(tOut)); compositeTimeline(true); }
    else if (tlDrag.type === 'trimL') {
      const src = clamp(o2s(tOut), 0, ce() - 1);
      ES.extend = ES.extend || {};
      ES.extend.start = Math.round(src * 100) / 100;
      renderTimeline(); renderTranscript();
    } else if (tlDrag.type === 'trimR') {
      // tarik ujung kanan: pakai posisi mouse relatif thd ujung
      const delta = ((x - 8) / pxPerSec) - outDuration();
      ES.extend = ES.extend || {};
      ES.extend.end = clamp(Math.round((ce() + delta) * 100) / 100, cs() + 1, project.duration);
      renderTimeline(); renderTranscript();
    } else if (tlDrag.type === 'segedge') {
      // geser sambungan = atur lebar cut bebas (trim ala CapCut).
      // delta dihitung dari posisi awal drag: kiri = makin memotong segmen kiri,
      // kanan = makin memotong segmen kanan.
      const cut = tlDrag.cut;
      const rawX = (x - 8) / pxPerSec;          // out-time mouse TANPA clamp
      const delta = rawX - tlDrag.leftOut;
      if (delta < 0) {
        cut[0] = clamp(Math.round((tlDrag.orig[0] + delta) * 100) / 100, cs(), tlDrag.orig[1] - 0.02);
        cut[1] = tlDrag.orig[1];
      } else {
        cut[0] = tlDrag.orig[0];
        cut[1] = clamp(Math.round((tlDrag.orig[1] + delta) * 100) / 100, tlDrag.orig[0] + 0.02, ce());
      }
      invalidatePages();
      renderTimeline();
    } else if (tlDrag.type === 'block') {
      const it = tlDrag.item;
      const dur = (it.end || (it.start || 0) + 3) - (it.start || 0);
      if (tlDrag.edge === 'L') it.start = clamp(tOut, 0, (it.end || 3) - 0.2);
      else if (tlDrag.edge === 'R') it.end = clamp(tOut, (it.start || 0) + 0.2, outDuration());
      else {
        it.start = clamp(tOut - tlDrag.grabOff, 0, Math.max(0, outDuration() - dur));
        it.end = it.start + dur;
        // drag VERTIKAL: pindah track/layer (khusus blok visual, bukan audio)
        if (!tlDrag.audioLane) {
          const tr = trackForY(y);
          if (tr != null && tr !== trackOf(tlDrag.key, it)) it.track = tr;
        }
      }
      renderTimeline();
    }
  });

  tlCanvas.addEventListener('pointerup', () => {
    if (tlDrag && tlDrag.type !== 'seek') {
      if (tlDrag.type === 'segedge') {
        // bersihkan cut yang menyusut jadi nol
        ES.cut_ranges = (ES.cut_ranges || []).filter((cu) => cu[1] - cu[0] > 0.03);
        renderTranscript();
      }
      if (tlDrag.type === 'trimL' || tlDrag.type === 'trimR') renderTranscript();
      if (tlDrag.type === 'block' && ['voiceovers', 'audios', 'music'].indexOf(tlDrag.key) !== -1) voSyncReset();
      commit(); updateTotals();
    }
    tlDrag = null;
  });

  // dblclick blok caption / text -> edit langsung
  tlCanvas.addEventListener('dblclick', (e) => {
    const rect = tlCanvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const hit = hitTest(x, y);
    if (hit.type === 'cap') openCaptionEditor(hit.idx);
    else if (hit.type === 'block' && hit.key === 'texts') {
      const t2 = ES.texts[hit.idx];
      const nv = prompt('Edit teks overlay:', t2.text || '');
      if (nv != null) { t2.text = nv; commit(); renderTimeline(); }
    }
  });

  // hapus item terpilih (blok / segmen / caption page) — dipakai tombol & tombol Del
  function deleteSelectedItem() {
    if (!selItem && selSegment) {
      // segmen video terpilih: Del = potong segmen itu (cut beneran hilang ala CapCut)
      addCut(selSegment[0], selSegment[1]);
      selSegment = null;
      commit(); renderTranscript(); renderTimeline(); updateTotals();
      return true;
    }
    if (!selItem) return false;
    if (selItem.kind === 'music') {
      ES.music = null;
      voSyncReset();
    } else if (selItem.kind === 'cap') {
      // hapus caption page = hapus kata-kata page itu (video ikut terpotong)
      const page = getPages()[selItem.idx];
      if (page && confirm('Hapus ' + page.length + ' kata di caption ini? (video ikut terpotong)')) {
        page.forEach((w) => deleteWordIdx(w.i));
      } else { return true; }
    } else if (ES[selItem.kind]) {
      ES[selItem.kind].splice(selItem.idx, 1);
      if (selItem.kind === 'voiceovers' || selItem.kind === 'audios') voSyncReset();
    }
    selItem = null;
    commit(); renderTranscript(); renderTimeline(); updateTotals();
    return true;
  }

  // ---- editor properti item overlay: animasi / mask / rotasi / border ----
  function openItemEditor(it, after) {
    const sel = (label, key, opts) =>
      '<div class="fgroup"><label>' + label + '</label><select data-pk="' + key + '">' +
      opts.map((o) => '<option value="' + o[0] + '"' + ((it[key] || 'none') === o[0] ? ' selected' : '') + '>' + o[1] + '</option>').join('') +
      '</select></div>';
    openDialog(
      '<h3>✏️ Properti Item</h3>' +
      sel('Animasi masuk', 'anim_in', TEXT_ANIM_IN) +
      sel('Animasi keluar', 'anim_out', TEXT_ANIM_OUT) +
      sel('Animasi loop', 'anim_loop', TEXT_ANIM_LOOP) +
      sel('Mask bentuk', 'mask', MASKS) +
      '<div class="fgroup"><label>Rotasi: <span id="ieRotV">' + (it.rot || 0) + '°</span></label>' +
      '<input type="range" id="ieRot" min="-180" max="180" value="' + (it.rot || 0) + '"></div>' +
      '<div class="fgroup colorrow"><label><input type="checkbox" id="ieBorder" ' + (it.border ? 'checked' : '') + ' style="width:auto;margin-right:6px">Border</label>' +
      '<input type="color" id="ieBorderC" value="' + (it.border_color || '#FFFFFF') + '"></div>' +
      '<div class="row2"><button class="btn btn-g" data-x>Tutup</button><button class="btn btn-p" id="ieSave">💾 Terapkan</button></div>'
    );
    $('ieRot').addEventListener('input', () => { $('ieRotV').textContent = $('ieRot').value + '°'; });
    $('ieSave').addEventListener('click', () => {
      document.querySelectorAll('#dlgRoot [data-pk]').forEach((s) => { it[s.dataset.pk] = s.value; });
      it.rot = +$('ieRot').value || 0;
      it.border = $('ieBorder').checked;
      it.border_color = $('ieBorderC').value;
      $('mediaLayer')._sig = '';
      commit(); renderTimeline();
      closeDialog();
      if (after) after();
    });
  }

  // ---- editor caption per page (klik caption di preview / dblklik blok timeline) ----
  function openCaptionEditor(pageIdx) {
    const page = getPages()[pageIdx];
    if (!page) return;
    let rows = '';
    page.forEach((w, j) => {
      rows += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">' +
        '<input data-wi="' + w.i + '" value="' + String(wordText(w.i)).replace(/"/g, '&quot;') + '"' +
        ' style="flex:1;background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;color:var(--text);font-size:.9rem;outline:none">' +
        '<button data-delw="' + w.i + '" title="Hapus kata (video ikut terpotong)" style="background:none;border:1px solid var(--border);border-radius:8px;color:var(--muted);padding:7px 10px;cursor:pointer">🗑</button></div>';
    });
    openDialog(
      '<h3>💬 Edit Caption</h3>' +
      '<p style="font-size:.78rem;color:var(--muted);margin-bottom:10px">Ubah teks per kata (audio tidak berubah). Tombol 🗑 menghapus kata sekaligus memotong videonya.</p>' +
      rows +
      '<div class="row2"><button class="btn btn-g" data-x>Batal</button><button class="btn btn-p" id="capEdSave">💾 Simpan</button></div>'
    );
    document.querySelectorAll('#dlgRoot [data-delw]').forEach((b) => b.addEventListener('click', () => {
      deleteWordIdx(+b.dataset.delw);
      commit(); renderTranscript(); renderTimeline();
      closeDialog();
    }));
    $('capEdSave').addEventListener('click', () => {
      document.querySelectorAll('#dlgRoot [data-wi]').forEach((inp) => {
        const i = +inp.dataset.wi;
        const v = inp.value.trim();
        if (v && v !== WORDS[i].word.trim()) {
          ES.word_edits = ES.word_edits || {};
          ES.word_edits[String(i)] = v;
        }
      });
      lastCapKey = '';
      commit(); renderTranscript(); renderTimeline();
      closeDialog();
    });
  }

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
    if (m === 'voiceover') return panelVoiceover();
    if (m === 'post') return panelPost();
    if (m === 'thumb') return panelThumb();
    if (m === 'fx') return panelEffects();
    if (m === 'sticker') return panelSticker();
  }

  // --- Efek (CapCut-style) ---
  function panelEffects() {
    let used = '';
    (ES.effects || []).forEach((fx, i) => {
      used += '<div class="itemrow"><span class="nm">🎇 ' + (fx.name || fx.type) + ' (' + fmtT(fx.start) + '–' + fmtT(fx.end) + ')</span><button data-fxd="' + i + '">✕</button></div>';
    });
    sbody.innerHTML = '<h3>🎇 Efek</h3>' +
      '<p class="note" style="margin-bottom:10px">Klik efek = pasang 3 detik di playhead (track atas). Atur durasi/posisi dengan drag blok di timeline — bisa juga dipindah track.</p>' +
      '<div class="tplgrid">' + EFFECTS.map((d) =>
        '<div class="tplcard" data-fx="' + d.type + '"><div class="demo" style="filter:' + (d.css || 'none') + '">🎬</div>' + d.name + '</div>'
      ).join('') + '</div>' +
      (used ? '<h3 style="font-size:.82rem;margin-top:14px">Terpasang</h3>' + used : '');
    sbody.querySelectorAll('[data-fx]').forEach((b) => b.addEventListener('click', () => {
      const def = EFFECTS.find((d) => d.type === b.dataset.fx);
      const t0 = srcToOut(vid.currentTime);
      ES.effects = ES.effects || [];
      ES.effects.push({ type: def.type, name: def.name, start: t0, end: Math.min(outDuration(), t0 + 3), track: 2 });
      commit(); renderTimeline(); panelEffects();
    }));
    sbody.querySelectorAll('[data-fxd]').forEach((b) => b.addEventListener('click', () => {
      ES.effects.splice(+b.dataset.fxd, 1);
      commit(); renderTimeline(); panelEffects();
    }));
  }

  // --- Stiker (emoji -> PNG transparan, jadi overlay gambar biasa) ---
  const STICKER_EMOJIS = ['🔥', '😂', '😱', '❤️', '💯', '👍', '👏', '🎉', '✨', '⭐', '💰', '🤑', '😍', '🥶', '💀', '🤯', '😎', '🙏', '👀', '⚡', '🚀', '🏆', '🎯', '💡', '❗', '❓', '✅', '❌', '➡️', '⬇️', '🤣', '😭', '🫵', '💪', '🧠', '🗣️'];
  function panelSticker() {
    sbody.innerHTML = '<h3>😀 Stiker</h3>' +
      '<p class="note" style="margin-bottom:8px">Klik stiker = pasang 3 detik di playhead. Geser & resize langsung di preview, pindah track di timeline.</p>' +
      '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:6px">' +
      STICKER_EMOJIS.map((em) => '<button data-em="' + em + '" style="background:var(--card);border:1px solid var(--border);border-radius:9px;font-size:1.35rem;padding:6px;cursor:pointer">' + em + '</button>').join('') +
      '</div>' +
      '<div class="fgroup" style="margin-top:12px"><label>Atau ketik emoji/teks stiker sendiri</label>' +
      '<div style="display:flex;gap:6px"><input type="text" id="stCustom" maxlength="6" placeholder="🤡" style="flex:1;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px;font-size:1.1rem;outline:none">' +
      '<button class="iconbtn" id="stAdd">＋</button></div></div>' +
      '<p class="note">Stiker dirender jadi PNG transparan secara LOKAL (tanpa API eksternal apa pun) — tampil identik di export, aman untuk jangka panjang. Bisa juga upload PNG stiker sendiri lewat menu Media.</p>';
    const addSticker = async (em) => {
      if (!em) return;
      // render emoji -> PNG transparan via canvas
      const cv = document.createElement('canvas');
      cv.width = cv.height = 256;
      const ctx = cv.getContext('2d');
      ctx.font = '200px "Segoe UI Emoji", "Apple Color Emoji", sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(em, 128, 140);
      const blob = await new Promise((res) => cv.toBlob(res, 'image/png'));
      const fd = new FormData();
      fd.append('file', new File([blob], 'sticker_' + Date.now() + '.png', { type: 'image/png' }));
      try {
        const d = await api('/projects/' + project.id + '/media', { method: 'POST', body: fd });
        const t0 = srcToOut(vid.currentTime);
        ES.overlays = ES.overlays || [];
        ES.overlays.push({ url: d.url, type: 'image', sticker: true, start: t0, end: Math.min(outDuration(), t0 + 3), x_pct: 75, y_pct: 25, w_pct: 22, track: 2 });
        commit(); renderTimeline();
      } catch (e) { alert(e.message); }
    };
    sbody.querySelectorAll('[data-em]').forEach((b) => b.addEventListener('click', () => addSticker(b.dataset.em)));
    $('stAdd').addEventListener('click', () => addSticker($('stCustom').value.trim()));
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
      '<button class="actionbtn" id="btnKeyword">🖍 Keyword highlight (AI)</button>' +
      '<button class="actionbtn" id="btnFontUp">🔠 Upload font custom (.ttf/.otf)</button>' +
      '<input type="file" id="fontUpInput" accept=".ttf,.otf" style="display:none">';
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
    $('btnFontUp').addEventListener('click', () => $('fontUpInput').click());
    $('fontUpInput').addEventListener('change', async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      $('btnFontUp').textContent = '⏳ mengupload...';
      const fd = new FormData(); fd.append('file', f);
      try {
        const d = await api('/fonts', { method: 'POST', body: fd });
        injectCustomFont(d.family, d.url);
        if (window.__fonts.indexOf(d.family) === -1) window.__fonts.push(d.family);
        STYLE.font = d.family;
        lastCapKey = ''; commit(); panelCaptions();
      } catch (err) { alert(err.message); $('btnFontUp').textContent = '🔠 Upload font custom (.ttf/.otf)'; }
    });
  }
  function injectCustomFont(family, url) {
    const st = document.createElement('style');
    st.textContent = '@font-face{font-family:"' + family + '";src:url("' + url + '")}';
    document.head.appendChild(st);
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
      const items = d.media.filter((m) => (tab === 'all' || m.type === tab) &&
        (m.name || '').indexOf('_txt_') === -1);   // sembunyikan PNG render text overlay internal
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
            // multi-track audio (ala add_audios): volume & fade per item
            ES.audios = ES.audios || [];
            ES.audios.push({ url: m.url, name: m.name, start: t0, end: Math.min(outDuration(), t0 + 8), volume: 1.0, fade: false });
            voSyncReset();
          } else {
            ES.overlays = ES.overlays || [];
            ES.overlays.push({ url: m.url, type: m.type, start: t0, end: Math.min(outDuration(), t0 + 3), x_pct: 50, y_pct: 50, w_pct: 45, track: 1 });
          }
          commit(); renderTimeline(); panelMedia(tab);
        });
        grid.appendChild(div);
      });
      const used = $('mediaUsed'); used.innerHTML = '';
      (ES.overlays || []).forEach((o, i) => {
        const r = document.createElement('div');
        r.className = 'itemrow';
        r.innerHTML = '<span class="nm">' + (o.sticker ? '😀' : o.type === 'video' ? '🎞' : '🖼') + ' overlay ' + (i + 1) + ' (' + fmtT(o.start) + '–' + fmtT(o.end) + ')</span>' +
          '<button data-edit title="Animasi / mask / rotasi">✏️</button><button data-del>✕</button>';
        r.querySelector('[data-del]').addEventListener('click', () => { ES.overlays.splice(i, 1); commit(); renderTimeline(); panelMedia(tab); });
        r.querySelector('[data-edit]').addEventListener('click', () => openItemEditor(o, () => panelMedia(tab)));
        used.appendChild(r);
      });
      (ES.audios || []).forEach((a, i) => {
        const r = document.createElement('div');
        r.className = 'itemrow';
        r.innerHTML = '<span class="nm">🎵 ' + (a.name || 'audio') + ' (' + fmtT(a.start) + ')</span>' +
          '<input type="range" min="0" max="150" value="' + Math.round((a.volume || 1) * 100) + '" style="width:60px" title="Volume">' +
          '<label style="font-size:.65rem"><input type="checkbox" ' + (a.fade ? 'checked' : '') + ' style="width:auto"> fade</label>' +
          '<button data-del>✕</button>';
        r.querySelector('input[type=range]').addEventListener('input', (ev) => { a.volume = +ev.target.value / 100; markDirty(); });
        r.querySelector('input[type=range]').addEventListener('change', commit);
        r.querySelector('input[type=checkbox]').addEventListener('change', (ev) => { a.fade = ev.target.checked; commit(); });
        r.querySelector('[data-del]').addEventListener('click', () => { ES.audios.splice(i, 1); voSyncReset(); commit(); renderTimeline(); panelMedia(tab); });
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
    const brand = ES.brand || {};
    sbody.innerHTML = '<h3>🏷️ Brand Template</h3>' +
      '<p class="note" style="margin-bottom:10px">Simpan preset caption + watermark, lalu terapkan 1 klik ke semua klip.</p>' +
      '<div class="fgroup"><label>Nama preset</label><input type="text" id="brandName" placeholder="Brand saya"></div>' +
      '<div class="fgroup"><label>Teks watermark (opsional)</label><input type="text" id="brandWm" value="' + (ES.watermark_text || '') + '" placeholder="@username"></div>' +
      '<button class="actionbtn primary" id="brandSave">💾 Simpan preset dari style sekarang</button>' +
      (list || '<p class="note">Belum ada preset.</p>') + (list ? '<div id="brandList"></div>' : '') +
      '<h3 style="font-size:.85rem;margin-top:16px">🎬 Intro / Outro card</h3>' +
      '<p class="note">Pilih gambar/video dari menu Media (upload dulu di sana) — dipasang otomatis di awal/akhir saat export.</p>' +
      '<div class="fgroup"><label>Intro</label><select id="brandIntro"><option value="">— tidak ada —</option></select></div>' +
      '<div class="fgroup"><label>Outro</label><select id="brandOutro"><option value="">— tidak ada —</option></select></div>';
    api('/projects/' + project.id + '/media').then((d) => {
      const opts = (d.media || []).filter((m) => m.type !== 'audio' && (m.name || '').indexOf('_txt_') === -1)
        .map((m) => '<option value="' + m.url + '">' + m.name + '</option>').join('');
      $('brandIntro').innerHTML = '<option value="">— tidak ada —</option>' + opts;
      $('brandOutro').innerHTML = '<option value="">— tidak ada —</option>' + opts;
      if (brand.intro) $('brandIntro').value = brand.intro;
      if (brand.outro) $('brandOutro').value = brand.outro;
      const upd = () => {
        ES.brand = { intro: $('brandIntro').value || null, outro: $('brandOutro').value || null };
        commit();
      };
      $('brandIntro').addEventListener('change', upd);
      $('brandOutro').addEventListener('change', upd);
    });
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
  // Kalimat caption (utk AI B-Roll per caption): gabung kata sampai tanda baca/jeda
  function captionSentences() {
    const words = visibleCaptionWords();
    const sents = []; let cur = [];
    for (let j = 0; j < words.length; j++) {
      cur.push(words[j]);
      const gap = j + 1 < words.length ? words[j + 1].start - words[j].end : 99;
      if (/[.!?…]$/.test(words[j].word.trim()) || gap > 1.2 || cur.length >= 18) { sents.push(cur); cur = []; }
    }
    if (cur.length) sents.push(cur);
    return sents.map((ws) => ({
      text: ws.map((w) => w.word.trim()).join(' '),
      start: srcToOut(ws[0].start),
      end: srcToOut(ws[ws.length - 1].end),
    })).filter((s2) => s2.text && s2.end > s2.start);
  }
  function panelBroll() {
    // === AI B-Roll (Gemini akun sendiri, ala Opus "Auto Generate AI B-Roll") ===
    let aiRows = '';
    captionSentences().forEach((s2, i) => {
      aiRows += '<div class="itemrow"><span class="nm" title="' + s2.text.replace(/"/g, '&quot;') + '">' +
        fmtT(s2.start) + ' · ' + s2.text.slice(0, 42) + (s2.text.length > 42 ? '…' : '') + '</span>' +
        '<button data-aib="' + i + '" title="AI generate gambar utk caption ini">✨</button></div>';
    });
    sbody.innerHTML = '<h3>🎞️ B-Roll</h3>' +
      '<h3 style="font-size:.82rem">✨ AI B-Roll (Gemini — generate gambar)</h3>' +
      '<p class="note">Pilih caption → AI membuat GAMBAR sesuai isinya → otomatis tampil di timeline tepat pada rentang caption itu (±40 dtk per gambar). Cookie akun Gemini diatur admin di /mimin.</p>' +
      '<div style="max-height:170px;overflow:auto;border:1px solid var(--border);border-radius:9px;padding:4px" id="aibList">' +
      (aiRows || '<p class="note">Belum ada caption (transkrip kosong).</p>') + '</div>' +
      '<div class="fgroup" style="margin-top:8px"><label>Prompt B-Roll manual (gambar bebas)</label>' +
      '<textarea id="aibPrompt" rows="2" placeholder="mis: ilustrasi orang lari di pantai saat matahari terbit, sinematik" style="width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px 10px;font-size:.8rem;outline:none;font-family:inherit"></textarea></div>' +
      '<button class="actionbtn" id="aibGen">✨ Generate & taruh di playhead (3 dtk)</button>' +
      '<h3 style="margin-top:14px;font-size:.82rem">📚 Stock library (Pexels)</h3>' +
      '<button class="actionbtn" id="brKw">🧠 Saran kata kunci dari AI</button>' +
      '<div class="kwchips" id="brChips"></div>' +
      '<div class="fgroup"><input type="search" id="brQ" placeholder="Cari stock footage… (English)"></div>' +
      '<div class="tabs2"><button class="on" data-bt="videos">Video</button><button data-bt="photos">Foto</button></div>' +
      '<div class="mediagrid" id="brGrid"></div>' +
      '<h3 style="margin-top:14px;font-size:.82rem">Di timeline</h3><div id="brUsed"></div>';
    // generate per caption
    const sents = captionSentences();
    sbody.querySelectorAll('[data-aib]').forEach((b) => b.addEventListener('click', async () => {
      const s2 = sents[+b.dataset.aib];
      if (!s2 || b.disabled) return;
      b.disabled = true; b.textContent = '⏳';
      try {
        const d = await api('/clips/' + clipId + '/broll-image', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: s2.text }),
        });
        ES.broll = ES.broll || [];
        ES.broll.push({ url: d.url, type: 'image', start: s2.start,
                        end: Math.min(outDuration(), Math.max(s2.end, s2.start + 1.5)), ai: true });
        commit(); renderTimeline(); renderUsed();
        b.textContent = '✅';
      } catch (e) { b.textContent = '❌'; b.title = e.message; alert('AI B-Roll gagal: ' + e.message); b.disabled = false; }
    }));
    // generate dari prompt manual di playhead
    $('aibGen').addEventListener('click', async () => {
      const p = $('aibPrompt').value.trim();
      if (!p) { alert('Tulis prompt gambarnya dulu.'); return; }
      $('aibGen').disabled = true; $('aibGen').textContent = '⏳ AI menggambar (±40 dtk)…';
      try {
        const d = await api('/clips/' + clipId + '/broll-image', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: p }),
        });
        const t0 = srcToOut(vid.currentTime);
        ES.broll = ES.broll || [];
        ES.broll.push({ url: d.url, type: 'image', start: t0, end: Math.min(outDuration(), t0 + 3), ai: true });
        commit(); renderTimeline(); renderUsed();
        $('aibGen').textContent = '✅ Terpasang — generate lagi?';
      } catch (e) { $('aibGen').textContent = '❌ ' + e.message; }
      $('aibGen').disabled = false;
    });
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
        r.innerHTML = '<span class="nm">' + (b.ai ? '✨ AI' : '🎞') + ' B-roll ' + (i + 1) + ' (' + fmtT(b.start) + '–' + fmtT(b.end) + ')</span><button>✕</button>';
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

  // --- Text (dengan animasi masuk/keluar/loop — buatan sendiri) ---
  // Panel "Text overlay settings" ala Opus: font, dekorasi, alignment, bg color, radius, width
  function openTextSettings(it, back) {
    const fonts = ['Arial', 'Impact', 'Georgia', 'Verdana', 'Tahoma', 'Trebuchet MS', 'Courier New', 'Times New Roman']
      .concat((window.__fonts || []).filter((f) => ['Arial', 'Impact'].indexOf(f) === -1));
    const fontOpts = fonts.map((f) => '<option' + ((it.font || 'Arial') === f ? ' selected' : '') + '>' + f + '</option>').join('');
    const alignBtn = (a, icon) => '<button data-al="' + a + '" class="' + ((it.align || 'center') === a ? 'on' : '') + '" style="flex:1">' + icon + '</button>';
    sbody.innerHTML = '<h3>🎨 Text overlay settings</h3>' +
      '<div class="fgroup"><label>Input</label><textarea id="tsText" rows="2" style="width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px 10px;font-size:.85rem;outline:none;font-family:inherit">' + String(it.text || '').replace(/</g, '&lt;') + '</textarea></div>' +
      '<div class="fgroup"><label>Font</label><select id="tsFont">' + fontOpts + '</select></div>' +
      '<div class="fgroup colorrow"><span>Warna <input type="color" id="tsColor" value="' + (it.color || '#FFFFFF') + '"></span>' +
      '<span style="flex:1">Ukuran <input type="number" id="tsSize" value="' + (it.size || 56) + '" min="20" max="140" style="width:64px"> px</span></div>' +
      '<div class="fgroup"><label>Decoration</label><div class="tabs2" style="margin-bottom:0">' +
      '<button id="tsItalic" class="' + (it.italic ? 'on' : '') + '" style="font-style:italic">I</button>' +
      '<button id="tsUnder" class="' + (it.underline ? 'on' : '') + '" style="text-decoration:underline">U</button></div></div>' +
      '<div class="fgroup"><label>Text alignment</label><div class="tabs2" style="margin-bottom:0">' +
      alignBtn('left', '⬅') + alignBtn('center', '⬌') + alignBtn('right', '➡') + '</div></div>' +
      '<div class="fgroup colorrow"><span><input type="checkbox" id="tsBgOn" ' + (it.bg ? 'checked' : '') + ' style="width:auto;margin-right:4px">Word’s background</span>' +
      '<input type="color" id="tsBg" value="' + (it.bg || '#FFFFFF') + '"></div>' +
      '<div class="fgroup colorrow"><span style="flex:1">Radius <input type="number" id="tsRad" value="' + (it.bg_radius != null ? it.bg_radius : 10) + '" min="0" max="60" style="width:60px"> px</span>' +
      '<span style="flex:1">Width <input type="number" id="tsWidth" value="' + (it.width_pct || 86) + '" min="20" max="100" style="width:60px"> %</span></div>' +
      '<div class="fgroup"><label>Posisi vertikal % (0 atas)</label><input type="range" id="tsY" min="2" max="90" value="' + (it.y_pct || 12) + '"></div>' +
      '<button class="actionbtn primary" id="tsDone">✓ Selesai</button>';
    const apply = () => {
      it.text = $('tsText').value;
      it.font = $('tsFont').value;
      it.color = $('tsColor').value;
      it.size = +$('tsSize').value || 56;
      it.bg = $('tsBgOn').checked ? $('tsBg').value : null;
      it.bg_radius = +$('tsRad').value || 0;
      it.width_pct = clamp(+$('tsWidth').value || 86, 20, 100);
      it.y_pct = +$('tsY').value;
      $('mediaLayer')._sig = '';            // paksa preview rebuild
      markDirty(); renderTimeline(); scheduleTextRender(it);
    };
    ['tsText', 'tsFont', 'tsColor', 'tsSize', 'tsBgOn', 'tsBg', 'tsRad', 'tsWidth', 'tsY'].forEach((id) => {
      $(id).addEventListener('input', apply);
      $(id).addEventListener('change', apply);
    });
    $('tsItalic').addEventListener('click', () => { it.italic = !it.italic; $('tsItalic').classList.toggle('on', it.italic); apply(); });
    $('tsUnder').addEventListener('click', () => { it.underline = !it.underline; $('tsUnder').classList.toggle('on', it.underline); apply(); });
    sbody.querySelectorAll('[data-al]').forEach((b) => b.addEventListener('click', () => {
      it.align = b.dataset.al;
      sbody.querySelectorAll('[data-al]').forEach((b2) => b2.classList.toggle('on', b2.dataset.al === it.align));
      apply();
    }));
    $('tsDone').addEventListener('click', () => { commit(); back(); });
  }

  function panelText() {
    let list = '';
    (ES.texts || []).forEach((x, i) => {
      list += '<div class="itemrow"><span class="nm">🔤 ' + (x.text || '') + '</span>' +
        '<button data-style="' + i + '" title="Style (font, bg, alignment)">🎨</button>' +
        '<button data-edit="' + i + '" title="Animasi">✏️</button><button data-i="' + i + '">✕</button></div>';
    });
    const selOpts = (opts) => opts.map((o) => '<option value="' + o[0] + '">' + o[1] + '</option>').join('');
    sbody.innerHTML = '<h3>🔤 Text</h3>' +
      '<div class="fgroup"><label>Teks</label><input type="text" id="txtVal" placeholder="Judul / hook keren…"></div>' +
      '<div class="fgroup colorrow"><span>Warna <input type="color" id="txtColor" value="#FFFFFF"></span>' +
      '<span style="flex:1">Ukuran <input type="number" id="txtSize" value="56" min="20" max="140" style="width:64px"></span></div>' +
      '<div class="fgroup"><label>Posisi vertikal % (0 atas)</label><input type="range" id="txtY" min="2" max="90" value="12"></div>' +
      '<div class="fgroup"><label>Animasi masuk / keluar / loop</label>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">' +
      '<select id="txtAnimIn">' + selOpts(TEXT_ANIM_IN) + '</select>' +
      '<select id="txtAnimOut">' + selOpts(TEXT_ANIM_OUT) + '</select>' +
      '<select id="txtAnimLoop">' + selOpts(TEXT_ANIM_LOOP) + '</select></div></div>' +
      '<button class="actionbtn primary" id="txtAdd">＋ Tambah di playhead (3 detik)</button>' + list;
    $('txtAnimIn').value = 'fade';
    $('txtAdd').addEventListener('click', () => {
      const v = $('txtVal').value.trim();
      if (!v) return;
      const t0 = srcToOut(vid.currentTime);
      ES.texts = ES.texts || [];
      ES.texts.push({
        text: v, start: t0, end: Math.min(outDuration(), t0 + 3),
        color: $('txtColor').value, size: +$('txtSize').value, y_pct: +$('txtY').value, font: 'Arial', track: 2,
        anim_in: $('txtAnimIn').value, anim_out: $('txtAnimOut').value, anim_loop: $('txtAnimLoop').value,
      });
      commit(); renderTimeline(); panelText();
    });
    sbody.querySelectorAll('[data-style]').forEach((b) => b.addEventListener('click', () => {
      openTextSettings(ES.texts[+b.dataset.style], panelText);
    }));
    sbody.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => {
      openItemEditor(ES.texts[+b.dataset.edit], panelText);
    }));
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
      '<div class="fgroup"><label><input type="checkbox" id="aEnhance" ' + (ES.audio_enhance ? 'checked' : '') + ' style="width:auto;margin-right:6px">✨ AI Speech enhancement (denoise + perjernih suara saat export)</label></div>' +
      '<button class="actionbtn" id="aCensor">🤬 Auto censor kata kasar' + ((ES.censored_words || []).length ? ' (' + ES.censored_words.length + ' aktif)' : '') + '</button>' +
      '<h3 style="font-size:.85rem">Background music</h3>' +
      (ES.music ? '<div class="itemrow"><span class="nm">🎶 ' + (ES.music.name || 'musik') + '</span><button id="musDel">✕</button></div>' : '') +
      '<div id="musList">⏳</div>' +
      (ES.music ?
        '<div class="fgroup"><label>Volume musik: <span id="mVolVal">' + Math.round((ES.music.volume || 0.25) * 100) + '%</span></label>' +
        '<input type="range" id="mVol" min="0" max="100" value="' + Math.round((ES.music.volume || 0.25) * 100) + '"></div>' +
        '<div class="fgroup"><label><input type="checkbox" id="mDuck" ' + (ES.music.duck !== false ? 'checked' : '') + ' style="width:auto;margin-right:6px">Auto-duck saat ada suara bicara</label>' +
        '<label><input type="checkbox" id="mFade" ' + (ES.music.fade !== false ? 'checked' : '') + ' style="width:auto;margin-right:6px">Fade in/out</label></div>' : '') +
      '<p class="note">🎧 Musik langsung bunyi di preview & jadi blok biru di lane audio timeline — geser/resize bebas. Auto-duck (musik mengecil saat ada suara bicara) diterapkan presisi saat Export.</p>';
    $('aVol').addEventListener('input', () => {
      ES.volume = +$('aVol').value / 100;
      $('aVolVal').textContent = $('aVol').value + '%';
      vid.volume = clamp(ES.volume, 0, 1);
      markDirty();
    });
    $('aVol').addEventListener('pointerup', commit);
    $('aEnhance').addEventListener('change', () => { ES.audio_enhance = $('aEnhance').checked; commit(); });
    $('aCensor').addEventListener('click', async () => {
      $('aCensor').textContent = '⏳ memindai...';
      try {
        const d = await api('/clips/' + clipId + '/ai', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'censor' }),
        });
        const found = d.censored_words || [];
        if (!found.length) { $('aCensor').textContent = '✅ Tidak ada kata kasar terdeteksi'; return; }
        const names = Object.values(d.preview || {}).join(', ');
        if (confirm('Ditemukan ' + found.length + ' kata kasar: ' + names + '\n\nSensor semuanya? (audio di-mute + caption tersensor saat export)')) {
          ES.censored_words = found;
          commit(); renderTranscript(); panelAudio();
        } else $('aCensor').textContent = '🤬 Auto censor kata kasar';
      } catch (e) { $('aCensor').textContent = '❌ ' + e.message; }
    });
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
          ES.music = { path: tr.url, volume: 0.25, duck: true, fade: true, name: tr.name,
                       start: 0, end: outDuration() };
          voSyncReset();
          commit(); renderTimeline(); panelAudio();
        });
        box.appendChild(r);
      });
      if (!d.tracks.length) box.innerHTML = '<p class="note">Library kosong.</p>';
    });
    if ($('musDel')) $('musDel').addEventListener('click', () => { ES.music = null; voSyncReset(); commit(); renderTimeline(); panelAudio(); });
    if ($('mVol')) {
      $('mVol').addEventListener('input', () => { ES.music.volume = +$('mVol').value / 100; $('mVolVal').textContent = $('mVol').value + '%'; markDirty(); });
      $('mVol').addEventListener('pointerup', commit);
      $('mDuck').addEventListener('change', () => { ES.music.duck = $('mDuck').checked; commit(); });
      $('mFade').addEventListener('change', () => { ES.music.fade = $('mFade').checked; commit(); });
    }
  }

  // --- AI hook / CTA ala Opus: script (manual/AI) -> TTS baca script + text overlay + duck audio asli ---
  function fillVoiceSelect(sel) {
    api('/voices').then((d) => {
      if (d.groups) {
        sel.innerHTML = d.groups.map((g) =>
          '<optgroup label="' + g.label + '">' +
          g.voices.map((v) => '<option value="' + v.id + '">' + v.name + '</option>').join('') +
          '</optgroup>').join('');
      } else {
        sel.innerHTML = d.voices.map((v) => '<option value="' + v.id + '">' + v.name + '</option>').join('');
      }
    }).catch(() => { sel.innerHTML = '<option value="edge:id-ID-ArdiNeural">Ardi (Pria, Indonesia)</option>'; });
  }
  function panelHook() {
    let list = '';
    (ES.voiceovers || []).forEach((v, i) => {
      if (!v.is_hook) return;
      list += '<div class="itemrow"><span class="nm">🪝 ' + (v.text || 'hook') + ' @ ' + fmtT(v.start || 0) + '</span>' +
        '<button data-hd="' + i + '" title="Hapus (beserta text overlay-nya)">✕</button></div>';
    });
    sbody.innerHTML = '<h3>🪝 AI Hook</h3>' +
      '<p class="note" style="margin-bottom:10px">Persis fitur AI hook Opus: tulis script (atau AI yang menulis) → AI voice-over MEMBACAKAN script di awal/akhir video + tampil sebagai text overlay, volume audio asli otomatis diturunkan selama hook.</p>' +
      '<div class="fgroup"><label>Script</label><textarea id="hkText" rows="3" placeholder="Type your script here… (kosongkan lalu klik AI buatkan)" style="width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px 10px;font-size:.85rem;outline:none;font-family:inherit"></textarea></div>' +
      '<div class="fgroup" style="display:grid;grid-template-columns:1fr 1fr;gap:6px">' +
      '<span><label>Gaya script</label><select id="hkStyle"><option value="serius">Serius</option><option value="semangat">Semangat</option><option value="lucu">Lucu</option><option value="santai">Santai</option><option value="misterius">Misterius</option></select></span>' +
      '<span><label>Posisi</label><select id="hkPos"><option value="hook">Awal video (hook)</option><option value="cta">Akhir video (CTA)</option></select></span></div>' +
      '<div class="fgroup"><label>Kata kunci utk AI (opsional)</label><input type="text" id="hkKw" placeholder="mis: diskon 50%, motivasi pagi"></div>' +
      '<button class="actionbtn" id="hkGen">🧠 AI buatkan 3 alternatif script</button><div id="hkAlt"></div>' +
      '<div class="fgroup"><label>Speaker voice</label><select id="hkVoice"><option value="">memuat…</option></select></div>' +
      '<div class="fgroup"><label>Volume audio asli saat hook: <span id="hkDuckVal">20%</span></label>' +
      '<input type="range" id="hkDuck" min="0" max="100" value="20"></div>' +
      '<div class="fgroup"><label><input type="checkbox" id="hkShowText" checked style="width:auto;margin-right:6px">Tampilkan sebagai text overlay (bisa diedit di menu Text 🎨)</label></div>' +
      '<button class="actionbtn primary" id="hkMake">🪝 Generate AI hook</button>' + list;
    fillVoiceSelect($('hkVoice'));
    $('hkDuck').addEventListener('input', () => { $('hkDuckVal').textContent = $('hkDuck').value + '%'; });
    $('hkGen').addEventListener('click', async () => {
      $('hkGen').textContent = '⏳ AI menulis script...';
      try {
        const d = await api('/clips/' + clipId + '/ai', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'hooks', style: $('hkStyle').value, position: $('hkPos').value, keywords: $('hkKw').value.trim() }),
        });
        const box = $('hkAlt'); box.innerHTML = '';
        d.hooks.forEach((h) => {
          const b = document.createElement('button');
          b.className = 'actionbtn';
          b.textContent = '“' + h + '”';
          b.addEventListener('click', () => { $('hkText').value = h; });
          box.appendChild(b);
        });
        $('hkGen').textContent = '🧠 AI buatkan 3 alternatif script';
      } catch (e) { $('hkGen').textContent = '❌ ' + e.message; }
    });
    $('hkMake').addEventListener('click', async () => {
      const script = $('hkText').value.trim();
      if (!script) { alert('Tulis script dulu, atau klik "AI buatkan 3 alternatif" lalu pilih salah satu.'); return; }
      $('hkMake').textContent = '⏳ membuat suara AI...';
      try {
        const d = await generateVO(script, $('hkVoice').value);
        const dur = d.duration || 3;
        const pos = $('hkPos').value;
        const start = pos === 'cta' ? Math.max(0, outDuration() - dur - 0.2) : 0;
        const end = Math.min(outDuration(), start + dur);
        const hookId = 'hk' + Date.now();
        ES.voiceovers = ES.voiceovers || [];
        ES.voiceovers.push({
          url: d.url, start, end, duration: d.duration, volume: 1.0, text: script,
          voice: d.voice, is_hook: true, hook_id: hookId,
          duck_original: +$('hkDuck').value / 100,
        });
        if ($('hkShowText').checked) {
          ES.texts = ES.texts || [];
          const t = {
            text: script, start, end: Math.min(outDuration(), end + 0.3),
            color: '#111111', size: 48, y_pct: 12, font: 'Arial', track: 2,
            align: 'center', bg: '#FFFFFF', bg_radius: 10, width_pct: 75,
            anim_in: 'fade', anim_out: 'fade', hook_id: hookId,
          };
          ES.texts.push(t);
          scheduleTextRender(t);
        }
        voSyncReset(); commit(); renderTimeline(); panelHook();
      } catch (e) { alert(e.message); panelHook(); }
    });
    sbody.querySelectorAll('[data-hd]').forEach((b) => b.addEventListener('click', () => {
      const v = ES.voiceovers[+b.dataset.hd];
      if (v && v.hook_id) ES.texts = (ES.texts || []).filter((t) => t.hook_id !== v.hook_id);
      ES.voiceovers.splice(+b.dataset.hd, 1);
      voSyncReset(); commit(); renderTimeline(); panelHook();
    }));
  }

  // --- AI Voice-over + Dubbing ---
  async function generateVO(text, voice) {
    return api('/clips/' + clipId + '/voiceover', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice }),
    });
  }
  function panelVoiceover() {
    let list = '';
    (ES.voiceovers || []).forEach((v, i) => {
      list += '<div class="itemrow"><span class="nm">🎙 ' + (v.text || 'voiceover') + ' @ ' + fmtT(v.start || 0) + '</span>' +
        '<button data-revoice="' + i + '" title="Ganti pengisi suara (regenerate dgn suara terpilih)">🔁</button>' +
        '<button data-vi="' + i + '" title="Hapus">✕</button></div>';
    });
    const dubbed = ES.volume === 0;
    sbody.innerHTML = '<h3>🎙️ AI Voice-over</h3>' +
      '<div class="fgroup"><label>Suara</label><select id="voVoice"><option value="">memuat…</option></select></div>' +
      '<div class="fgroup"><label>Teks narasi</label><textarea id="voText" rows="3" placeholder="Tulis narasi yang ingin diucapkan AI..." style="width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:9px;padding:8px 10px;font-size:.85rem;outline:none;font-family:inherit"></textarea></div>' +
      '<button class="actionbtn primary" id="voGen">🎙 Generate & taruh di playhead</button>' +
      '<button class="actionbtn" id="voDub">🎬 Dubbing AI: ganti SELURUH suara video dgn suara terpilih' + (dubbed ? ' ✓ (aktif)' : '') + '</button>' +
      (dubbed ? '<button class="actionbtn" id="voDubOff">↩ Kembalikan suara asli</button>' : '') +
      list +
      '<p class="note">🔁 = ganti pengisi suara item itu. Dubbing AI membaca seluruh teks caption klip dengan suara pilihan lalu MEME-MUTE suara asli — caption tetap sinkron dengan teks.</p>';
    api('/voices').then((d) => {
      // dropdown berkelompok per engine (Edge / Supertonic 3 OFFLINE / Piper / gTTS)
      if (d.groups) {
        $('voVoice').innerHTML = d.groups.map((g) =>
          '<optgroup label="' + (g.offline ? '📦 ' : '🌐 ') + g.label + '">' +
          g.voices.map((v) => '<option value="' + v.id + '">' + v.name + '</option>').join('') +
          '</optgroup>').join('');
      } else {
        $('voVoice').innerHTML = d.voices.map((v) => '<option value="' + v.id + '">' + v.name + '</option>').join('');
      }
    });
    $('voGen').addEventListener('click', async () => {
      const txt = $('voText').value.trim();
      if (!txt) return;
      $('voGen').disabled = true; $('voGen').textContent = '⏳ AI merekam suara...';
      try {
        const d = await generateVO(txt, $('voVoice').value);
        const t0 = srcToOut(vid.currentTime);
        ES.voiceovers = ES.voiceovers || [];
        ES.voiceovers.push({ url: d.url, start: t0, end: Math.min(outDuration(), t0 + (d.duration || 3)), duration: d.duration, volume: 1.0, text: d.text, voice: d.voice });
        voSyncReset();
        commit(); renderTimeline(); panelVoiceover();
      } catch (e) { alert(e.message); panelVoiceover(); }
    });
    // Dubbing AI penuh: TTS seluruh teks caption -> mute suara asli
    $('voDub').addEventListener('click', async () => {
      const words = visibleCaptionWords();
      if (!words.length) { alert('Tidak ada teks caption di klip ini.'); return; }
      const fullText = words.map((w) => w.word).join(' ');
      if (!confirm('Dubbing AI akan membaca seluruh teks klip (' + words.length + ' kata) dengan suara terpilih dan ME-MUTE suara asli. Lanjut?')) return;
      $('voDub').disabled = true; $('voDub').textContent = '⏳ AI merekam dubbing penuh...';
      try {
        const d = await generateVO(fullText, $('voVoice').value);
        ES.voiceovers = (ES.voiceovers || []).filter((v) => !v.is_dub);
        ES.voiceovers.push({ url: d.url, start: 0, end: Math.min(outDuration(), d.duration || outDuration()), duration: d.duration, volume: 1.0, text: '[DUBBING] ' + d.text, voice: d.voice, is_dub: true });
        ES.volume = 0;            // mute suara asli
        vid.volume = 0; $('volRange').value = 0;
        voSyncReset();
        commit(); renderTimeline(); panelVoiceover();
      } catch (e) { alert(e.message); panelVoiceover(); }
    });
    if ($('voDubOff')) $('voDubOff').addEventListener('click', () => {
      ES.voiceovers = (ES.voiceovers || []).filter((v) => !v.is_dub);
      ES.volume = 1; vid.volume = 1; $('volRange').value = 100;
      voSyncReset();
      commit(); renderTimeline(); panelVoiceover();
    });
    // ganti pengisi suara item (regenerate teks yang sama dgn suara terpilih)
    sbody.querySelectorAll('[data-revoice]').forEach((b) => b.addEventListener('click', async () => {
      const i = +b.dataset.revoice;
      const v = ES.voiceovers[i];
      if (!v) return;
      b.textContent = '⏳';
      try {
        const d = await generateVO((v.text || '').replace(/^\[DUBBING\] /, ''), $('voVoice').value);
        v.url = d.url; v.duration = d.duration; v.voice = d.voice;
        v.end = Math.min(outDuration(), (v.start || 0) + (d.duration || 3));
        voSyncReset();
        commit(); renderTimeline(); panelVoiceover();
      } catch (e) { alert(e.message); panelVoiceover(); }
    }));
    sbody.querySelectorAll('[data-vi]').forEach((b) => b.addEventListener('click', () => {
      const v = ES.voiceovers[+b.dataset.vi];
      if (v && v.is_dub) { ES.volume = 1; vid.volume = 1; $('volRange').value = 100; }
      ES.voiceovers.splice(+b.dataset.vi, 1);
      voSyncReset();
      commit(); renderTimeline(); panelVoiceover();
    }));
  }

  // preview playback voice-over + audio tambahan + MUSIK (sinkron dgn waktu output)
  let voAudios = [];
  function voSyncReset() {
    voAudios.forEach((a) => { try { a.el.pause(); } catch (e) {} });
    const items = [...(ES.voiceovers || []), ...(ES.audios || [])].map((v) => ({ v, el: new Audio(v.url) }));
    if (ES.music && (ES.music.path || ES.music.url)) {
      const mEl = new Audio(ES.music.path || ES.music.url);
      mEl.loop = true;   // musik diulang dalam jendela bloknya (sama dgn export)
      items.push({ v: ES.music, el: mEl, isMusic: true });
    }
    voAudios = items;
    voAudios.forEach((a) => { a.el.preload = 'auto'; });
  }
  function voSync() {
    if (!voAudios.length) return;
    const t = srcToOut(vid.currentTime);
    let duck = 1;   // duck suara asli saat AI hook bunyi (sama dgn export)
    voAudios.forEach(({ v, el, isMusic }) => {
      const start = v.start || 0;
      const end = (v.end != null) ? v.end : (start + (v.duration || 3));
      const within = playing && t >= start && t < (isMusic ? Math.min(end, outDuration()) : end);
      if (within && !isMusic && v.duck_original != null) duck = Math.min(duck, +v.duck_original);
      if (within) {
        const vol = Math.min(1, v.volume != null ? v.volume : (isMusic ? 0.25 : 1));
        if (el.paused) {
          const want = t - start;
          el.currentTime = (isMusic && el.duration) ? (want % el.duration) : want;
          el.volume = vol;
          el.play().catch(() => {});
        } else {
          if (Math.abs(el.volume - vol) > 0.02) el.volume = vol;
          if (!isMusic) {
            const want = t - start;
            if (Math.abs(el.currentTime - want) > 0.35) el.currentTime = want;
          }
        }
      } else if (!el.paused) el.pause();
    });
    const wantV = clamp((ES.volume != null ? ES.volume : 1) * duck, 0, 1);
    if (Math.abs(vid.volume - wantV) > 0.02) vid.volume = wantV;
  }

  // --- Post sosial (Customize Your Post ala Opus) ---
  function panelPost() {
    sbody.innerHTML = '<h3>📣 Post ke Sosial Media</h3>' +
      '<button class="actionbtn primary" id="postGen">🧠 AI buatkan caption per platform</button>' +
      '<div id="postOut"></div>' +
      '<h3 style="font-size:.85rem;margin-top:14px">🔗 Share link</h3>' +
      '<button class="actionbtn" id="shareBtn">Salin link export terakhir</button>' +
      '<h3 style="font-size:.85rem;margin-top:14px">🗓 Jadwalkan posting</h3>' +
      '<p class="note">Auto-post & scheduler ke TikTok/YouTube/IG membutuhkan koneksi akun resmi (OAuth) — '
      + 'segera hadir (TODO: butuh app credentials tiap platform). Sementara: download MP4 + salin caption di atas.</p>';
    $('postGen').addEventListener('click', async () => {
      $('postGen').disabled = true; $('postGen').textContent = '⏳ AI menulis...';
      try {
        const d = await api('/clips/' + clipId + '/ai', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'post_copy' }),
        });
        const pc = d.post_copy || {};
        const yt = pc.youtube || {};
        const block = (label, text) =>
          '<div class="fgroup" style="margin-top:10px"><label>' + label + '</label>' +
          '<div style="background:var(--card);border:1px solid var(--border);border-radius:9px;padding:9px;font-size:.78rem;line-height:1.5;white-space:pre-wrap">' + (text || '-') + '</div>' +
          '<button class="actionbtn" style="margin-top:6px" data-copy>📋 Salin</button></div>';
        $('postOut').innerHTML =
          block('TikTok', pc.tiktok) +
          block('YouTube Shorts', (yt.title ? yt.title + '\n\n' : '') + (yt.description || '')) +
          block('Instagram Reels', pc.instagram);
        $('postOut').querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => {
          navigator.clipboard.writeText(b.previousElementSibling.textContent).then(() => { b.textContent = '✅ Tersalin'; });
        }));
      } catch (e) { alert(e.message); }
      $('postGen').disabled = false; $('postGen').textContent = '🧠 AI buatkan caption per platform';
    });
    $('shareBtn').addEventListener('click', async () => {
      try {
        const h = await api('/clips/' + clipId + '/exports');
        const done = (h.exports || []).find((e) => e.status === 'done' && e.file_path);
        if (!done) { alert('Belum ada export selesai. Render dulu lewat tombol Export.'); return; }
        const url = location.origin + done.file_path;
        await navigator.clipboard.writeText(url);
        $('shareBtn').textContent = '✅ Link tersalin!';
      } catch (e) { alert(e.message); }
    });
  }

  // --- Thumbnail generator ---
  function panelThumb() {
    sbody.innerHTML = '<h3>📸 Thumbnail Generator</h3>' +
      '<p class="note" style="margin-bottom:10px">Geser playhead ke frame terbaik, tulis judul, lalu unduh thumbnail siap pakai.</p>' +
      '<div class="fgroup"><label>Teks judul di thumbnail</label><input type="text" id="thTitle" value="' + (title || '').replace(/"/g, '&quot;') + '"></div>' +
      '<div class="fgroup colorrow"><span>Warna <input type="color" id="thColor" value="#FFE600"></span>' +
      '<span style="flex:1">Ukuran <input type="range" id="thSize" min="40" max="140" value="84" style="width:100%"></span></div>' +
      '<button class="actionbtn primary" id="thGen">📥 Buat & unduh thumbnail (frame saat ini)</button>' +
      '<canvas id="thCanvas" style="width:100%;border-radius:10px;margin-top:10px;border:1px solid var(--border)"></canvas>';
    const draw = () => {
      const cv = $('thCanvas');
      const [aw, ah] = aspectWH();
      cv.width = aw >= ah ? 1280 : 1080;
      cv.height = aw >= ah ? 720 : 1920;
      if (aspect === '1:1') { cv.width = 1080; cv.height = 1080; }
      const ctx = cv.getContext('2d');
      // gambar frame video dengan crop aktif
      const [cx, cy] = interpCenter(vid.currentTime);
      const [x, y, w, h] = cropWindow(cx, cy);
      try { ctx.drawImage(vid, x, y, w, h, 0, 0, cv.width, cv.height); }
      catch (e) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, cv.width, cv.height); }
      // judul
      const txt = ($('thTitle').value || '').toUpperCase();
      if (txt) {
        const size = +$('thSize').value * cv.height / 1000;
        ctx.font = '900 ' + size + 'px Impact, Arial Black, sans-serif';
        ctx.textAlign = 'center';
        ctx.lineWidth = size / 7;
        ctx.strokeStyle = '#000';
        ctx.fillStyle = $('thColor').value;
        const words = txt.split(' ');
        const lines = [];
        let cur = '';
        words.forEach((w2) => {
          if (ctx.measureText(cur + ' ' + w2).width > cv.width * 0.9 && cur) { lines.push(cur); cur = w2; }
          else cur = cur ? cur + ' ' + w2 : w2;
        });
        if (cur) lines.push(cur);
        const y0 = cv.height * 0.82 - (lines.length - 1) * size * 1.1;
        lines.forEach((ln, i) => {
          ctx.strokeText(ln, cv.width / 2, y0 + i * size * 1.1);
          ctx.fillText(ln, cv.width / 2, y0 + i * size * 1.1);
        });
      }
      return cv;
    };
    ['thTitle', 'thColor', 'thSize'].forEach((id) => $(id).addEventListener('input', draw));
    draw();
    $('thGen').addEventListener('click', () => {
      const cv = draw();
      const a = document.createElement('a');
      a.download = 'thumbnail.jpg';
      a.href = cv.toDataURL('image/jpeg', 0.92);
      a.click();
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
      '<button class="actionbtn" id="expXml" style="margin-top:12px">📐 Export to XML — Adobe Premiere / DaVinci Resolve</button>' +
      (histHtml ? '<h3 style="font-size:.85rem;margin-top:16px">Riwayat export</h3>' + histHtml : '')
    );
    $('expXml').addEventListener('click', async () => {
      $('expXml').textContent = '⏳ membuat XML...';
      try {
        const r = await fetch(API + '/clips/' + clipId + '/xml', { headers: { 'Authorization': 'Bearer ' + token() } });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (title || 'klip') + '.xml';
        a.click();
        URL.revokeObjectURL(a.href);
        $('expXml').textContent = '✅ XML terunduh — import di Premiere/Resolve';
      } catch (e) { $('expXml').textContent = '❌ ' + e.message; }
    });
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
    else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      if (!deleteSelectedItem()) deleteSelected();   // blok timeline terpilih dulu, lalu kata
    }
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
      (t.custom_fonts || []).forEach((f) => injectCustomFont(f.family, f.url));
    } catch (e) { TPLS = []; }
    voSyncReset();   // siapkan preview audio voice-over yang sudah tersimpan

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
