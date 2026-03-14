/**
 * app.js – Animato Studio v2 — all logic inside DOMContentLoaded
 *
 * Fixes:
 *  - All DOM queries happen after DOM is ready → no null element errors
 *  - showView / router consolidated here (no duplicate in inline script)
 *  - Stages initialised here, inside DOMContentLoaded
 *  - All functions that HTML onclick="" uses are exposed on window
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Bootstrap: wait for full DOM before touching anything
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // ── DOM refs (safe after DOMContentLoaded) ──────────────────────────────
  const get = (id) => document.getElementById(id);

  const statusText       = get('statusText');
  const progressBar      = get('progressBar');
  const progressPct      = get('progressPercent');
  const statusBadge      = get('statusBadge');
  const audioInput       = get('audioInput');
  const fileNameEl       = get('fileName');
  const generateBtn      = get('generateBtn');
  const logEl            = get('log');
  const videoPlayer      = get('videoPlayer');
  const videoPlaceholder = get('videoPlaceholder');
  const videoGenerating  = get('videoGenerating');
  const videoActions     = get('videoActions');
  const downloadBtn      = get('downloadBtn');
  const dropZone         = get('dropZone');
  const errorAlert       = get('errorAlert');
  const errorMessage     = get('errorMessage');
  const storyboardPanel  = get('storyboardPanel');
  const slidesGrid       = get('slidesGrid');
  const slideCount       = get('slideCount');
  const audioPreview     = get('audioPreview');
  const audioPlayerPreview = get('audioPlayerPreview');
  const uploadBar        = get('uploadBar');
  const uploadProgress   = get('uploadProgress');
  const studioLabel      = get('studioProjectLabel');
  const projectIdDisplay = get('projectIdDisplay');
  const generatingMsg    = get('generatingMessage');
  const stagesList       = get('stagesList');

  // ── State ────────────────────────────────────────────────────────────────
  let projectId        = null;
  let selectedFile     = null;
  let sseSource        = null;
  let pollInterval     = null;
  let activeStage      = null;
  let editingSlideIdx  = null;
  let storyboardData   = [];
  let selectedTheme    = localStorage.getItem('animato_theme') || 'cinematic_pro';

  // ── Unified design themes – one per art style (mirrors DESIGN_THEMES in ai_providers.py) ────
  const DESIGN_THEMES = {
    // Photography & Realism
    photo:           { name: 'Hyper-Real Photo',    description: 'Lifelike DSLR photography with natural light and texture',         art: 'Hyper-Real Photo',      design: 'Modern',   colors: ['#1a1a1a', '#38bdf8', '#d4d4d4'] },
    cinematic_pro:   { name: 'Cinematic Pro',       description: 'Dramatic widescreen cinema with deep shadow grading',              art: 'Epic Cinematic',        design: 'Modern',   colors: ['#0d1929', '#38bdf8', '#c8922a'] },
    // Sci-Fi & Futurism
    neon_pulse:      { name: 'Neon Pulse',          description: 'Cyberpunk city nights with electrifying neon glow',                art: 'Surreal Cyberpunk',     design: 'Neon',     colors: ['#050510', '#00ffcc', '#ff00cc'] },
    futuristic_ui:   { name: 'Futuristic HUD',      description: 'Glowing holographic interface with sci-fi precision',             art: 'Futuristic HUD',        design: 'Neon',     colors: ['#030d1a', '#00d4ff', '#0066ff'] },
    retro_synthwave: { name: 'Retro Synthwave',     description: '80s nostalgia with chrome grids and neon sunsets',                art: 'Retro Synthwave',       design: 'Neon',     colors: ['#08001a', '#ff2d78', '#b24bff'] },
    // Illustration & Animation
    cartoon_pop:     { name: 'Cartoon Pop',         description: 'Bold outlines and saturated flat colours with energy',            art: 'Vibrant Cartoon',       design: 'Kawaii',   colors: ['#1a0a2e', '#ff6eb4', '#ffe066'] },
    pixar_3d:        { name: 'Pixar 3D',            description: 'Polished 3D render with cinematic studio lighting',               art: 'Pixar 3D Render',       design: 'Kawaii',   colors: ['#0a1a30', '#ff6eb4', '#f5a623'] },
    corporate_pro:   { name: 'Corporate Pro',       description: 'Clean flat design with confident blue accents',                   art: 'Flat Illustration',     design: 'Modern',   colors: ['#061428', '#0ea5e9', '#38bdf8'] },
    isometric_tech:  { name: 'Isometric Tech',      description: 'Crisp vector geometry with a tech-forward palette',              art: 'Isometric Vector',      design: 'Modern',   colors: ['#080f20', '#38bdf8', '#34d399'] },
    kawaii_dream:    { name: 'Kawaii Dream',        description: 'Ultra-cute pastel world with soft rounded aesthetics',            art: 'Cute Kawaii',           design: 'Kawaii',   colors: ['#1a0830', '#ff6eb4', '#ffcce8'] },
    claymation:      { name: 'Claymation',          description: 'Handmade clay stop-motion with warm practical light',             art: 'Claymation Style',      design: 'Kawaii',   colors: ['#1a0f0a', '#ff6eb4', '#f5c07a'] },
    lego_build:      { name: 'LEGO Build',          description: 'Plastic brick geometry with playful toy-box colours',            art: 'LEGO Brick Build',      design: 'Kawaii',   colors: ['#0a1a2e', '#ff2d2d', '#ffe600'] },
    lowpoly_geo:     { name: 'Low-Poly Geo',        description: 'Faceted 3D geometry with crisp hard-shaded surfaces',            art: 'Low-Poly Render',       design: 'Modern',   colors: ['#0a1520', '#34d399', '#818cf8'] },
    // Traditional & Fine Art
    watercolor_soft: { name: 'Watercolor Soft',     description: 'Delicate pastel brushwork with soft bleeding washes',            art: 'Watercolour Pastel',    design: 'Vintage',  colors: ['#150d0a', '#c9a02a', '#e8c8b0'] },
    baroque_gold:    { name: 'Baroque Gold',        description: 'Rich oil painting with ornate gold and theatrical shadow',       art: 'Baroque Painting',      design: 'Vintage',  colors: ['#1a0a00', '#c9a02a', '#8b4513'] },
    impressionist:   { name: 'Impressionist',       description: 'Loose painterly brushwork with vibrant broken colour',           art: 'Impressionist Painting', design: 'Vintage', colors: ['#15100a', '#c9a02a', '#a78bca'] },
    ukiyoe:          { name: 'Ukiyo-e Print',       description: 'Japanese woodblock with flat colour and flowing outlines',       art: 'Ukiyo-e Woodblock',     design: 'Vintage',  colors: ['#0d1520', '#c9a02a', '#c0392b'] },
    stained_glass:   { name: 'Stained Glass',       description: 'Jewel-toned cathedral light through bold lead frames',          art: 'Stained Glass',         design: 'Vintage',  colors: ['#0a0a1a', '#c9a02a', '#0d6b3a'] },
    charcoal_sketch: { name: 'Charcoal Sketch',     description: 'Expressive hand-drawn linework on rough paper grain',           art: 'Charcoal Sketch',       design: 'Minimal',  colors: ['#0a0a0a', '#666666', '#d4c8b0'] },
    // Monochrome & Minimal
    minimal_clean:   { name: 'Minimal Clean',       description: 'Pure editorial monochrome with generous negative space',         art: 'Minimalist Monochrome', design: 'Minimal',  colors: ['#0a0a0a', '#666666', '#f0f0f0'] },
    dark_noir:       { name: 'Dark Noir',           description: 'Moody chiaroscuro shadows with rain-slick mystery',             art: 'Neo-Noir Grit',         design: 'Neon',     colors: ['#050505', '#1a1a2e', '#b0b0b0'] },
    manga_ink:       { name: 'Manga Ink',           description: 'Classic B&W manga with screentone and motion lines',            art: 'B&W Manga',             design: 'Minimal',  colors: ['#0a0a0a', '#555555', '#e8e8e8'] },
    brutalist:       { name: 'Brutalist',           description: 'Raw concrete monoliths with stark geometric shadow',            art: 'Brutalist Architecture', design: 'Minimal', colors: ['#111111', '#555555', '#cccccc'] },
    // Genre & Retro
    golden_vintage:  { name: 'Golden Vintage',      description: 'Retro pulp magazine with bold ink and aged paper',              art: 'Vintage Pulp Cover',    design: 'Vintage',  colors: ['#1a0a00', '#c9a02a', '#f5e6c8'] },
    industrial_steam:{ name: 'Industrial Steam',    description: 'Brass gears and Victorian machinery in warm metallic haze',     art: 'Industrial Steampunk',  design: 'Vintage',  colors: ['#1a0e00', '#c9a02a', '#7a4a1a'] },
    dieselpunk:      { name: 'Dieselpunk',          description: 'WWI-era riveted industry with muted metal and smoke',           art: 'Dieselpunk Retro-Tech', design: 'Vintage',  colors: ['#0f0e0a', '#c9a02a', '#5a5040'] },
    // Dark & Atmospheric
    fantasy_epic:    { name: 'Fantasy Epic',        description: 'Grand magical vistas with heroic golden light',                 art: 'High-Fantasy Epic',     design: 'Modern',   colors: ['#0a1a2e', '#4a8fd4', '#d4a820'] },
    gothic_dark:     { name: 'Gothic Dark',         description: 'Brooding cathedral shadows with candlelit melancholy',          art: 'Gothic Dark Art',       design: 'Neon',     colors: ['#080510', '#00ffcc', '#4a3060'] },
    // Japanese Animation
    anime_vivid:     { name: 'Anime Vivid',         description: 'Polished anime art with crisp linework and rich gradients',    art: 'High-Detail Anime',     design: 'Kawaii',   colors: ['#0a0820', '#ff6eb4', '#38bdf8'] },
    // Professional Illustration
    concept_art:     { name: 'AAA Concept Art',     description: 'Matte-painting precision with dramatic narrative mood',        art: 'AAA Concept Art',       design: 'Modern',   colors: ['#0a1520', '#38bdf8', '#c8922a'] },
  };

  // ── View router (exposed globally for onclick="showView(...)") ────────────
  const VIEWS = ['home', 'studio', 'projects'];

  function showView(name) {
    if (!VIEWS.includes(name)) name = 'home';
    
    // Update active class on views
    VIEWS.forEach(v => {
      const el = get(`view-${v}`);
      if (el) el.classList.toggle('active', v === name);
    });

    // Update URL hash without triggering scroll if already there
    if (window.location.hash !== `#${name}`) {
      window.location.hash = name;
    }

    if (name === 'projects') loadProjects();
    window.scrollTo(0, 0);
    lucide.createIcons();
  }
  window.showView = showView;

  // Hash routing — persists on reload
  function hashView() { return location.hash.replace('#', '') || 'home'; }
  window.addEventListener('hashchange', () => {
    const view = hashView();
    showView(view);
  });
  
  // Initial render
  showView(hashView());

  // ── Pipeline stages initialisation ──────────────────────────────────────
  const STAGES = [
    { key: 'asr',        label: 'ASR Transcription',  icon: 'waveform',       color: 'text-sky-400'    },
    { key: 'script',     label: 'Scene Planning',      icon: 'layout-list',    color: 'text-indigo-400' },
    { key: 'storyboard', label: 'Storyboard',          icon: 'film',           color: 'text-violet-400' },
    { key: 'images',     label: 'Image Generation',    icon: 'image',          color: 'text-rose-400'   },
    { key: 'render',     label: 'Video Rendering',     icon: 'clapperboard',   color: 'text-amber-400'  },
    { key: 'done',       label: 'Production Complete', icon: 'check-circle-2', color: 'text-emerald-400'},
  ];

  if (stagesList) {
    STAGES.forEach(s => {
      const div = document.createElement('div');
      div.id = `stage-${s.key}`;
      div.className = 'flex items-center gap-3 p-3 rounded-xl transition-all opacity-30';
      div.innerHTML = `
        <div class="w-8 h-8 rounded-xl bg-white/5 border border-white/5 flex items-center justify-center flex-shrink-0">
          <i data-lucide="${s.icon}" class="w-4 h-4 ${s.color}"></i>
        </div>
        <div class="flex-1 min-w-0">
          <p class="text-xs font-bold text-slate-400 truncate">${s.label}</p>
        </div>
        <div class="stage-indicator w-4 h-4 flex items-center justify-center">
          <div class="w-1.5 h-1.5 rounded-full bg-slate-700"></div>
        </div>
      `;
      stagesList.appendChild(div);
    });
    lucide.createIcons();
  }

  // ── Unified theme selector ───────────────────────────────────────────────
  function buildThemeGrid() {
    const grid = get('themeGrid');
    if (!grid) return;
    grid.innerHTML = '';
    Object.entries(DESIGN_THEMES).forEach(([key, cfg]) => {
      const isSelected = selectedTheme === key;
      const card = document.createElement('button');
      card.className = 'theme-card relative w-full text-left rounded-2xl p-3 transition-all';
      card.style.cssText = isSelected
        ? 'border:1px solid rgba(56,189,248,0.65);background:rgba(56,189,248,0.10);box-shadow:0 4px 24px rgba(56,189,248,0.08)'
        : 'border:1px solid rgba(255,255,255,0.07);background:rgba(255,255,255,0.03)';

      // Colour swatches
      const swatches = cfg.colors.map(c =>
        `<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${c};border:1px solid rgba(255,255,255,0.12);flex-shrink:0"></span>`
      ).join('');

      const checkMark = isSelected
        ? `<span style="width:16px;height:16px;border-radius:50%;background:#38bdf8;display:flex;align-items:center;justify-content:center;flex-shrink:0">
             <i data-lucide="check" style="width:10px;height:10px;color:white"></i>
           </span>`
        : '';

      card.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:5px">${swatches}</div>
          ${checkMark}
        </div>
        <p style="font-size:12px;font-weight:900;color:#e2e8f0;margin:0 0 2px;line-height:1.3">${cfg.name}</p>
        <p style="font-size:10px;color:#64748b;margin:0 0 8px;line-height:1.4">${cfg.description}</p>
        <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,0.15);color:rgba(165,180,252,0.8)">${cfg.art}</span>
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:2px 6px;border-radius:4px;background:rgba(168,85,247,0.15);color:rgba(216,180,254,0.8)">${cfg.design}</span>
        </div>
      `;

      card.addEventListener('mouseenter', () => {
        if (!isSelected) card.style.background = 'rgba(255,255,255,0.06)';
      });
      card.addEventListener('mouseleave', () => {
        if (!isSelected) card.style.background = 'rgba(255,255,255,0.03)';
      });
      card.addEventListener('click', () => {
        selectedTheme = key;
        localStorage.setItem('animato_theme', key);
        buildThemeGrid();
      });
      grid.appendChild(card);
    });
    lucide.createIcons();
  }

  buildThemeGrid();

  // ── Helpers ──────────────────────────────────────────────────────────────
  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // ── Log ──────────────────────────────────────────────────────────────────
  function logLine(msg, type = 'info') {
    if (!logEl) return;
    const colors = { info: 'text-slate-500', success: 'text-emerald-400', error: 'text-rose-400', system: 'text-sky-400', warn: 'text-amber-400' };
    const icons  = { info: '›', success: '✓', error: '✗', system: '⚡', warn: '⚠' };
    const el = document.createElement('div');
    el.className = 'flex items-start gap-1.5 py-0.5';
    el.innerHTML = `
      <span class="${colors[type] || colors.info} flex-shrink-0 font-black">${icons[type] || '›'}</span>
      <span class="${colors[type] || colors.info}">${escHtml(msg)}</span>
    `;
    logEl.prepend(el);
    while (logEl.childElementCount > 150) logEl.lastElementChild?.remove();
  }

  function clearLog() {
    if (!logEl) return;
    logEl.innerHTML = '<div class="text-slate-700 flex items-center gap-1.5"><span>$</span> Log cleared.</div>';
  }
  window.clearLog = clearLog;

  // ── Custom Modals ────────────────────────────────────────────────────────
  const customModal = get('customModal');
  const modalIcon   = get('modalIcon');
  const modalTitle  = get('modalTitle');
  const modalMsg    = get('modalMessage');
  const modalConfirm= get('modalConfirmBtn');
  const modalCancel = get('modalCancelBtn');

  function showModal({ title, message, icon = 'info', confirmText = 'Confirm', cancelText = 'Cancel', showCancel = true }) {
    return new Promise((resolve) => {
      if (!customModal) return resolve(false);

      modalTitle.textContent = title;
      modalMsg.textContent   = message;
      modalConfirm.textContent = confirmText;
      modalCancel.textContent  = cancelText;
      modalCancel.style.display = showCancel ? 'block' : 'none';
      
      const iconContainer = get('modalIconContainer');
      if (iconContainer) {
        iconContainer.innerHTML = `<i data-lucide="${icon}" class="w-8 h-8 text-sky-400"></i>`;
        lucide.createIcons();
      }

      customModal.classList.remove('hidden');
      setTimeout(() => customModal.classList.add('visible'), 10);

      const cleanup = (val) => {
        customModal.classList.remove('visible');
        setTimeout(() => customModal.classList.add('hidden'), 300);
        modalConfirm.removeEventListener('click', onConfirm);
        modalCancel.removeEventListener('click', onCancel);
        resolve(val);
      };

      const onConfirm = () => cleanup(true);
      const onCancel  = () => cleanup(false);

      modalConfirm.addEventListener('click', onConfirm);
      modalCancel.addEventListener('click', onCancel);
    });
  }

  // Override / Extend window functions (optional, but let's provide nice helpers)
  window.animato = {
    alert:   (msg, title = 'Notice') => showModal({ title, message: msg, showCancel: false, confirmText: 'OK' }),
    confirm: (msg, title = 'Confirm Action') => showModal({ title, message: msg, icon: 'help-circle' }),
    error:   (msg, title = 'Error')  => showModal({ title, message: msg, icon: 'alert-triangle', confirmText: 'I understand', showCancel: false })
  };

  // ── Stage UI ─────────────────────────────────────────────────────────────
  const STAGE_ORDER = STAGES.map(s => s.key);

  function activateStage(stageName) {
    if (stageName === activeStage) return;
    activeStage = stageName;
    const idx = STAGE_ORDER.indexOf(stageName);
    if (idx < 0) return;

    STAGE_ORDER.forEach((s, i) => {
      const el = get(`stage-${s}`);
      if (!el) return;
      const indicator = el.querySelector('.stage-indicator');
      if (i < idx) {
        el.classList.remove('opacity-30', 'bg-sky-500/5', 'border', 'border-sky-500/10');
        el.classList.add('bg-emerald-500/5');
        if (indicator) indicator.innerHTML = '<i data-lucide="check" class="w-3 h-3 text-emerald-400"></i>';
      } else if (i === idx) {
        el.classList.remove('opacity-30', 'bg-emerald-500/5');
        el.classList.add('bg-sky-500/5', 'border', 'border-sky-500/10');
        if (indicator) indicator.innerHTML = '<div class="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></div>';
      } else {
        el.classList.add('opacity-30');
        el.classList.remove('bg-emerald-500/5', 'bg-sky-500/5', 'border', 'border-sky-500/10');
        if (indicator) indicator.innerHTML = '<div class="w-1.5 h-1.5 rounded-full bg-slate-700"></div>';
      }
    });

    document.querySelectorAll('.stage-item').forEach(item => {
      const dot   = item.querySelector('.stage-dot');
      const stage = item.dataset.stage;
      const i     = STAGE_ORDER.indexOf(stage);
      if (!dot) return;
      if (i < idx)       { dot.style.background = '#34d399'; dot.style.boxShadow = 'none'; }
      else if (i === idx){ dot.style.background = '#38bdf8'; dot.style.boxShadow = '0 0 0 4px rgba(56,189,248,0.2)'; }
      else               { dot.style.background = '#374151'; dot.style.boxShadow = 'none'; }
    });

    lucide.createIcons();
  }

  // ── Progress UI ──────────────────────────────────────────────────────────
  function updateProgress(data) {
    const progress = data.progress ?? 0;
    const msg      = data.message  || '';

    if (statusText)   statusText.textContent   = msg;
    if (generatingMsg) generatingMsg.textContent = msg;
    if (progressBar)  progressBar.style.width  = `${progress}%`;
    if (progressPct)  progressPct.textContent  = `${progress}%`;

    if (data.state) activateStage(data.state);

    // Show storyboard panel once storyboard stage starts
    if (data.state === 'storyboard' || data.state === 'images') {
      if (storyboardData.length === 0) loadStoryboard();
    }

    if (data.state === 'done')   onComplete();
    if (data.state === 'failed') onError(msg);
  }

  // ── Completion ───────────────────────────────────────────────────────────
  function onComplete() {
    stopStreaming();

    if (statusBadge) {
      statusBadge.textContent  = 'DONE';
      statusBadge.className    = 'px-3 py-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-[10px] font-black tracking-widest uppercase text-emerald-400';
    }

    if (videoGenerating)  { videoGenerating.classList.add('hidden'); videoGenerating.style.display = ''; }
    if (videoPlaceholder)   videoPlaceholder.classList.add('hidden');
    if (videoPlayer)      { videoPlayer.classList.remove('hidden'); }
    if (videoActions)       videoActions.classList.remove('hidden');

    const url = `/api/projects/${projectId}/video`;
    if (videoPlayer)  { videoPlayer.src = url; videoPlayer.load(); videoPlayer.play().catch(() => {}); }
    if (downloadBtn)    downloadBtn.href = url;

    logLine('Video master ready! 🎬', 'success');
    setBusy(false);
    loadStoryboard();
  }

  function onError(msg) {
    stopStreaming();

    if (statusBadge) {
      statusBadge.textContent = 'FAULT';
      statusBadge.className   = 'px-3 py-1 rounded-full bg-rose-500/15 border border-rose-500/30 text-[10px] font-black tracking-widest uppercase text-rose-400';
    }

    if (videoGenerating) { videoGenerating.classList.add('hidden'); videoGenerating.style.display = ''; }
    if (videoPlaceholder)  videoPlaceholder.classList.remove('hidden');
    if (errorAlert)        errorAlert.classList.remove('hidden');
    if (errorMessage)      errorMessage.textContent = msg;

    logLine(msg, 'error');
    setBusy(false);
  }

  // ── SSE streaming ─────────────────────────────────────────────────────────
  function startStreaming() {
    if (sseSource) sseSource.close();
    sseSource = new EventSource(`/api/projects/${projectId}/stream`);
    sseSource.onmessage = (e) => {
      try { updateProgress(JSON.parse(e.data)); } catch (_) {}
    };
    sseSource.onerror = () => {
      sseSource.close(); sseSource = null;
      if (!pollInterval) pollInterval = setInterval(pollStatus, 2000);
    };
  }

  function stopStreaming() {
    if (sseSource)   { sseSource.close(); sseSource = null; }
    if (pollInterval){ clearInterval(pollInterval); pollInterval = null; }
  }

  async function pollStatus() {
    if (!projectId) return;
    try {
      const r = await fetch(`/api/projects/${projectId}/status`);
      if (r.ok) updateProgress(await r.json());
    } catch (_) {}
  }

  // ── Busy state ────────────────────────────────────────────────────────────
  function setBusy(busy) {
    if (!generateBtn) return;
    generateBtn.disabled = busy || !selectedFile;
    if (busy) {
      generateBtn.innerHTML = `<i data-lucide="loader-2" class="w-5 h-5 animate-spin"></i> Processing…`;
      if (videoPlaceholder)    videoPlaceholder.classList.add('hidden');
      if (videoGenerating) {
        videoGenerating.classList.remove('hidden');
        videoGenerating.style.display = 'flex';
      }
      if (videoPlayer)   videoPlayer.classList.add('hidden');
      if (videoActions)  videoActions.classList.add('hidden');
    } else {
      generateBtn.innerHTML = `<i data-lucide="zap" class="w-5 h-5 fill-current"></i> Generate Video`;
    }
    lucide.createIcons();
  }

  // ── File handling ─────────────────────────────────────────────────────────
  if (dropZone) {
    dropZone.addEventListener('click', () => audioInput && audioInput.click());
    dropZone.addEventListener('dragover',  (e) => { e.preventDefault(); dropZone.classList.add('drag-active'); });
    dropZone.addEventListener('dragleave', ()  => dropZone.classList.remove('drag-active'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('drag-active');
      const f = e.dataTransfer.files[0];
      if (f) selectFile(f);
    });
  }
  if (audioInput) {
    audioInput.addEventListener('change', (e) => { if (e.target.files[0]) selectFile(e.target.files[0]); });
  }

  function selectFile(f) {
    selectedFile = f;
    const sizeMB = (f.size / 1048576).toFixed(1);
    if (fileNameEl) {
      fileNameEl.innerHTML = `
        <span class="text-sky-400 font-bold">${escHtml(f.name)}</span>
        <span class="text-slate-600 ml-2 text-xs">${sizeMB} MB</span>
      `;
    }
    if (generateBtn) generateBtn.disabled = false;
    logLine(`Audio loaded: ${f.name} (${sizeMB} MB)`);

    if (audioPlayerPreview && audioPreview) {
      audioPlayerPreview.src = URL.createObjectURL(f);
      audioPreview.classList.remove('hidden');
    }
  }

  // ── Generate button ───────────────────────────────────────────────────────
  if (generateBtn) {
    generateBtn.addEventListener('click', async () => {
      if (!selectedFile) return;

      // Reset UI state
      if (errorAlert)       errorAlert.classList.add('hidden');
      if (storyboardPanel)  storyboardPanel.classList.add('hidden');
      if (slidesGrid)       slidesGrid.innerHTML = '';
      storyboardData = [];
      activeStage    = null;
      clearLog();
      setBusy(true);

      updateProgress({ state: 'queued', progress: 0, message: 'Initializing…' });
      if (statusBadge) {
        statusBadge.textContent = 'RUNNING';
        statusBadge.className   = 'px-3 py-1 rounded-full bg-sky-500/15 border border-sky-500/30 text-[10px] font-black tracking-widest uppercase text-sky-400 animate-pulse';
      }

      try {
        // 1. Create project
        logLine('Creating project…', 'system');
        const cr = await fetch('/api/projects', { method: 'POST' });
        if (!cr.ok) throw new Error('Failed to create project');
        const { project_id } = await cr.json();
        projectId = project_id;
        if (studioLabel)      studioLabel.classList.remove('hidden');
        if (projectIdDisplay) projectIdDisplay.textContent = project_id.substring(0, 8) + '…';

        // 2. Upload audio (XHR with progress)
        logLine('Uploading audio…', 'system');
        if (uploadProgress) uploadProgress.classList.remove('hidden');
        await uploadAudioXHR(project_id, selectedFile);
        if (uploadProgress) uploadProgress.classList.add('hidden');
        logLine('Audio uploaded ✓', 'success');

        // 3. Start pipeline
        const reset = get('forceReset')?.checked ?? false;
        logLine(`Launching AI pipeline… theme=${selectedTheme}`, 'system');
        const gr = await fetch(
          `/api/projects/${project_id}/generate?reset=${reset}&theme=${selectedTheme}`,
          { method: 'POST' }
        );
        if (!gr.ok) {
          const err = await gr.json().catch(() => ({}));
          throw new Error(err.detail || 'Failed to start pipeline');
        }
        logLine('Pipeline started ✓', 'success');

        // 4. Stream progress
        startStreaming();

      } catch (err) {
        onError(err.message);
      }
    });
  }

  function uploadAudioXHR(pid, file) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('file', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `/api/projects/${pid}/upload`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && uploadBar) {
          uploadBar.style.width = `${Math.round(e.loaded / e.total * 100)}%`;
        }
      };
      xhr.onload  = () => xhr.status < 300 ? resolve(JSON.parse(xhr.responseText)) : reject(new Error((JSON.parse(xhr.responseText)||{}).detail || 'Upload failed'));
      xhr.onerror = () => reject(new Error('Network error during upload'));
      xhr.send(form);
    });
  }

  // ── Storyboard ────────────────────────────────────────────────────────────
  async function loadStoryboard() {
    if (!projectId) return;
    try {
      const r = await fetch(`/api/projects/${projectId}/storyboard`);
      if (!r.ok) return;
      storyboardData = await r.json();
      renderStoryboard();
    } catch (_) {}
  }

  function renderStoryboard() {
    if (!storyboardData.length || !slidesGrid) return;
    if (storyboardPanel) storyboardPanel.classList.remove('hidden');
    if (slideCount)      slideCount.textContent = `${storyboardData.length} slides`;
    slidesGrid.innerHTML = '';

    storyboardData.forEach((slide, i) => {
      const idx  = i + 1;
      const card = document.createElement('div');
      card.className = 'slide-card glass rounded-2xl p-4 border border-white/5 cursor-pointer group card-enter';
      card.style.animationDelay = `${i * 55}ms`;
      card.innerHTML = `
        <div class="aspect-video rounded-xl overflow-hidden bg-slate-900 border border-white/5 mb-3 relative">
          <img
            src="/api/projects/${projectId}/slides/${idx}/image"
            class="w-full h-full object-cover opacity-80 group-hover:opacity-100 transition"
            onerror="this.style.display='none'"
            loading="lazy"
          />
          <div class="absolute inset-0 flex items-end p-2">
            <span class="text-[9px] font-black text-white/60 bg-black/50 px-2 py-0.5 rounded-full uppercase tracking-widest">S${idx}</span>
          </div>
        </div>
        <h4 class="text-xs font-bold text-slate-300 mb-1 truncate">${escHtml(slide.title)}</h4>
        <div class="flex items-center gap-1.5 mb-1.5">
          ${slide.layout ? `<span class="text-[9px] font-black uppercase tracking-widest text-indigo-400/70 bg-indigo-500/10 px-1.5 py-0.5 rounded">${escHtml(slide.layout)}</span>` : ''}
          ${slide.style  ? `<span class="text-[9px] font-black uppercase tracking-widest text-purple-400/70 bg-purple-500/10 px-1.5 py-0.5 rounded">${escHtml(slide.style)}</span>` : ''}
        </div>
        <div class="flex items-center justify-between mt-2">
          <span class="text-[10px] font-mono text-slate-600">${(slide.duration || 0).toFixed(1)}s</span>
          <button class="text-[10px] font-bold uppercase tracking-widest text-sky-500 hover:text-sky-300 transition open-slide-btn" data-idx="${idx}">Edit →</button>
        </div>
      `;
      card.addEventListener('click', () => window.openSlideModal(idx));
      card.querySelector('.open-slide-btn').addEventListener('click', (e) => { e.stopPropagation(); window.openSlideModal(idx); });
      slidesGrid.appendChild(card);
    });
  }

  // Exposed for onclick in HTML (slide cards use addEventListener, but HTML buttons in modal use window.*)
  window.refreshStoryboard = () => loadStoryboard();

  // ── Slide Modal ───────────────────────────────────────────────────────────
  function openSlideModal(idx) {
    editingSlideIdx = idx;
    const slide = storyboardData[idx - 1];
    if (!slide) return;

    const modalNum = get('modalSlideNum');
    const editTitle = get('editTitle');
    const editBullets = get('editBullets');
    const editPrompt = get('editImagePrompt');
    const editDur    = get('editDuration');
    const preview    = get('modalSlidePreview');
    const modal      = get('slideModal');

    if (modalNum)   modalNum.textContent    = `#${idx}`;
    if (editTitle)  editTitle.value         = slide.title  || '';
    if (editBullets)editBullets.value       = (slide.bullets || []).join('\n');
    if (editPrompt) editPrompt.value        = slide.image_prompt || '';
    if (editDur)    editDur.value           = slide.duration || 8;

    if (preview) {
      preview.innerHTML = `
        <img src="/api/projects/${projectId}/slides/${idx}/image"
             class="w-full h-full object-cover"
             onerror="this.parentElement.innerHTML='<div class=\\'w-full h-full flex items-center justify-center text-slate-700\\'>No image yet</div>'"
        />`;
    }

    if (modal) modal.classList.remove('hidden');
    lucide.createIcons();
  }
  window.openSlideModal = openSlideModal;

  function closeSlideModal() {
    const modal = get('slideModal');
    if (modal) modal.classList.add('hidden');
    editingSlideIdx = null;
  }
  window.closeSlideModal = closeSlideModal;

  async function saveSlide() {
    if (!editingSlideIdx || !projectId) return;
    const btn = get('saveSlideBtn');
    if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }

    const body = {
      title:        get('editTitle')?.value        || '',
      bullets:      (get('editBullets')?.value || '').split('\n').map(l => l.trim()).filter(Boolean),
      image_prompt: get('editImagePrompt')?.value  || '',
      duration:     parseFloat(get('editDuration')?.value || '8') || 8,
    };

    try {
      const r = await fetch(`/api/projects/${projectId}/storyboard/${editingSlideIdx}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error('Save failed');
      storyboardData[editingSlideIdx - 1] = { ...storyboardData[editingSlideIdx - 1], ...body };
      logLine(`Slide ${editingSlideIdx} saved ✓`, 'success');
      renderStoryboard();
    } catch (err) {
      logLine(`Save error: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.textContent = 'Save Changes'; btn.disabled = false; }
    }
  }
  window.saveSlide = saveSlide;

  async function rerenderSlide(regenImage) {
    if (!editingSlideIdx || !projectId) return;
    await saveSlide();

    const label = regenImage ? 'Regenerating image + re-rendering…' : 'Re-rendering slide…';
    logLine(label, 'system');
    closeSlideModal();
    setBusy(true);
    updateProgress({ state: 'render', progress: 50, message: label });
    if (statusBadge) {
      statusBadge.textContent = 'RUNNING';
      statusBadge.className   = 'px-3 py-1 rounded-full bg-sky-500/15 border border-sky-500/30 text-[10px] font-black tracking-widest uppercase text-sky-400 animate-pulse';
    }

    try {
      const r = await fetch(
        `/api/projects/${projectId}/storyboard/${editingSlideIdx}/rerender?regen_image=${regenImage}`,
        { method: 'POST' }
      );
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || 'Re-render failed');
      }
      startStreaming();
    } catch (err) {
      onError(err.message);
    }
  }
  window.rerenderSlide = rerenderSlide;

  // ── Projects view ─────────────────────────────────────────────────────────
  async function loadProjects() {
    const loading = get('projectsLoading');
    const grid    = get('projectsGrid');
    const empty   = get('projectsEmpty');
    const errEl   = get('projectsError');

    if (!grid) return;

    // Show loading skeleton, hide everything else
    if (loading) loading.classList.remove('hidden');
    grid.classList.add('hidden');
    grid.innerHTML = '';
    if (empty)  empty.classList.add('hidden');
    if (errEl)  errEl.classList.add('hidden');

    try {
      const r = await fetch('/api/projects');
      if (!r.ok) throw new Error(`Server error ${r.status}`);
      const projects = await r.json();

      // Hide loading skeleton now that we have data
      if (loading) loading.classList.add('hidden');

      if (!Array.isArray(projects) || !projects.length) {
        if (empty) empty.classList.remove('hidden');
        return;
      }

      grid.classList.remove('hidden');

      projects.forEach(p => {
        const state    = p.status?.state    || 'unknown';
        const progress = p.status?.progress || 0;
        const created  = new Date(p.created_at * 1000).toLocaleDateString();

        const stateStyle = {
          done:    'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
          failed:  'text-rose-400 bg-rose-500/10 border-rose-500/20',
          running: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
        }[state] || 'text-slate-500 bg-slate-900/80 border-slate-800';

        const card = document.createElement('div');
        card.className = 'glass rounded-3xl p-5 border border-white/5 hover:border-sky-500/20 transition-all group';
        card.innerHTML = `
          <div class="flex items-center justify-between mb-4">
            <span class="text-xs font-mono text-slate-600">${p.project_id.substring(0, 8)}…</span>
            <span class="text-[10px] font-black uppercase tracking-widest px-2.5 py-1 rounded-full border ${stateStyle}">${state}</span>
          </div>
          <p class="text-sm font-bold text-slate-300 mb-1 truncate">${escHtml(p.audio_name || 'Unknown audio')}</p>
          <p class="text-xs text-slate-600 mb-4">Created ${created}</p>
          ${p.has_video ? `
            <div class="aspect-video rounded-xl bg-slate-900 border border-white/5 overflow-hidden mb-4">
              <video src="/api/projects/${p.project_id}/video" class="w-full h-full object-cover opacity-60 group-hover:opacity-90 transition" muted loop></video>
            </div>
          ` : `
            <div class="h-1.5 bg-slate-900 rounded-full overflow-hidden mb-4">
              <div class="h-full bg-sky-500/50 rounded-full" style="width:${progress}%"></div>
            </div>
          `}
          <div class="flex gap-2">
            ${p.has_video ? `
              <a href="/api/projects/${p.project_id}/video" download class="flex-1 py-2 bg-sky-500/10 hover:bg-sky-500/20 border border-sky-500/20 text-sky-400 rounded-xl text-xs font-bold text-center transition">Download</a>
              <button class="flex-1 py-2 bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 rounded-xl text-xs font-bold transition open-proj-btn" data-pid="${p.project_id}">Open</button>
            ` : `
              <button class="flex-1 py-2 bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 rounded-xl text-xs font-bold transition open-proj-btn" data-pid="${p.project_id}">Resume</button>
            `}
            <button class="px-3 py-2 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/10 text-rose-400 rounded-xl text-xs font-bold transition del-proj-btn" data-pid="${p.project_id}" title="Delete">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        `;

        card.querySelectorAll('.open-proj-btn').forEach(btn =>
          btn.addEventListener('click', () => openProject(btn.dataset.pid))
        );
        card.querySelectorAll('.del-proj-btn').forEach(btn =>
          btn.addEventListener('click', (e) => deleteProject(btn.dataset.pid, e.currentTarget))
        );

        grid.appendChild(card);
      });

      lucide.createIcons();
    } catch (err) {
      if (loading) loading.classList.add('hidden');
      if (errEl) {
        errEl.textContent = `Failed to load projects: ${err.message}`;
        errEl.classList.remove('hidden');
      }
    }
  }
  window.loadProjects = loadProjects;

  function openProject(pid) {
    projectId = pid;
    if (studioLabel)      studioLabel.classList.remove('hidden');
    if (projectIdDisplay) projectIdDisplay.textContent = pid.substring(0, 8) + '…';
    showView('studio');
    loadStoryboard();
    pollStatus();
    startStreaming();
  }

  async function deleteProject(pid, btn) {
    const ok = await window.animato.confirm('Are you sure you want to delete this project? This action cannot be undone.', 'Delete Project');
    if (!ok) return;
    btn.disabled = true;
    try {
      await fetch(`/api/projects/${pid}`, { method: 'DELETE' });
      loadProjects();
    } catch (_) {
      btn.disabled = false;
    }
  }

}); // end DOMContentLoaded
