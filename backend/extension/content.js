// Browser Translator - Content Script
// Handles overlay translations on web pages

let translationPanel = null;  // persistent panel for audio translations
let overlayContainer = null;
let overlayVisible = false;

// Listen for messages from background
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case 'translation':
      showTranslation(msg.original, msg.translated, msg.source, msg.target);
      break;

    case 'ocr_translation_overlay':
      if (msg.elements && msg.elements.length > 0) {
        applyOcrOverlay(msg.elements);
      }
      break;

    case 'show_ocr_result':
      showOcrPanel(msg.original, msg.translated);
      break;
  }
});

// ========== Persistent Translation Overlay (top-right) ==========

function showTranslation(original, translated, sourceLang, targetLang) {
  if (!translationPanel) {
    translationPanel = document.createElement('div');
    translationPanel.id = 'bt-translation-panel';
    translationPanel.style.cssText = `
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 2147483647;
      background: #1a1a2e;
      color: #e0e0e0;
      padding: 0;
      border-radius: 8px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 13px;
      line-height: 1.5;
      max-width: 420px;
      max-height: 70vh;
      overflow-y: auto;
      box-shadow: 0 4px 24px rgba(0,0,0,0.5);
      border: 1px solid #2a2a4a;
      min-width: 240px;
    `;
    document.body.appendChild(translationPanel);
    renderTranslationHeader();
  }

  addTranslationEntry(original, translated, sourceLang, targetLang);
  translationPanel.style.display = 'block';
}

function renderTranslationHeader() {
  // Header + close button
  const header = document.createElement('div');
  header.style.cssText = `
    padding: 8px 12px;
    background: #2a2a4a;
    border-radius: 8px 8px 0 0;
    font-weight: 600;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
  `;
  header.innerHTML = `
    <span>🔊 Audio Translation</span>
    <span id="bt-tts-close" style="cursor:pointer;opacity:0.6;font-size:16px;">✕</span>
  `;
  translationPanel.appendChild(header);

  // Content container
  const body = document.createElement('div');
  body.id = 'bt-tts-body';
  body.style.cssText = `padding: 0;`;
  translationPanel.appendChild(body);

  // Close handler
  header.querySelector('#bt-tts-close').addEventListener('click', () => {
    translationPanel.style.display = 'none';
  });

  // Esc to close
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && translationPanel && translationPanel.style.display !== 'none') {
      translationPanel.style.display = 'none';
    }
  });
}

function addTranslationEntry(original, translated, sourceLang, targetLang) {
  const body = translationPanel.querySelector('#bt-tts-body');
  if (!body) return;

  function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  const entry = document.createElement('div');
  // Alternate background for readability
  const isOdd = body.children.length % 2 === 0;
  entry.style.cssText = `
    padding: 8px 12px;
    border-bottom: 1px solid #2a2a4a;
    background: ${isOdd ? 'transparent' : 'rgba(42,42,74,0.3)'};
  `;
  entry.innerHTML = `
    <div style="font-size:11px;color:#888;margin-bottom:2px;">
      ${esc(sourceLang || '?')} → ${esc(targetLang || '?')}
    </div>
    <div style="color:#aaa;font-size:12px;margin-bottom:2px;">${esc(original).slice(0, 200)}</div>
    <div style="color:#4fc3f7;">${esc(translated).slice(0, 200)}</div>
  `;
  body.prepend(entry);

  // Keep max 20 entries
  while (body.children.length > 20) {
    body.lastChild.remove();
  }
}

// ========== OCR Overlay ==========

function createOverlayContainer() {
  if (overlayContainer) return overlayContainer;

  overlayContainer = document.createElement('div');
  overlayContainer.id = 'bt-overlay-container';
  overlayContainer.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 2147483646;
    pointer-events: none;
  `;
  document.body.appendChild(overlayContainer);
  return overlayContainer;
}

function applyOcrOverlay(elements) {
  const container = createOverlayContainer();

  // Remove old overlays
  container.innerHTML = '';

  for (const el of elements) {
    const overlay = document.createElement('div');
    overlay.style.cssText = `
      position: absolute;
      left: ${el.x}px;
      top: ${el.y}px;
      background: rgba(26, 26, 46, 0.9);
      color: #4fc3f7;
      padding: 2px 6px;
      border-radius: 3px;
      font-size: ${Math.max(el.fontSize || 14, 10)}px;
      font-family: monospace;
      pointer-events: auto;
      border: 1px solid rgba(79, 195, 247, 0.3);
      white-space: nowrap;
    `;
    overlay.textContent = el.translated;
    container.appendChild(overlay);
  }

  overlayVisible = true;
}

function toggleOverlay() {
  if (overlayContainer) {
    overlayContainer.style.display = overlayVisible ? 'none' : 'block';
    overlayVisible = !overlayVisible;
  }
}

// Handle escape key to hide overlay
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && overlayVisible && overlayContainer) {
    overlayContainer.style.display = 'none';
    overlayVisible = false;
  }
});

// ========== OCR Result Panel ==========

function showOcrPanel(original, translated) {
  // Escape HTML to prevent injection
  function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
  // Remove existing panel if present
  const existing = document.getElementById('bt-ocr-panel');
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.id = 'bt-ocr-panel';
  panel.style.cssText = `
    position: fixed;
    top: 60px;
    right: 16px;
    z-index: 2147483647;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 0;
    border-radius: 8px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    max-width: 420px;
    max-height: 70vh;
    overflow-y: auto;
    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
    border: 1px solid #2a2a4a;
  `;

  panel.innerHTML = `
    <div style="padding:8px 12px;background:#2a2a4a;border-radius:8px 8px 0 0;font-weight:600;display:flex;justify-content:space-between;align-items:center;">
      <span>📄 OCR Translation</span>
      <span id="bt-ocr-close" style="cursor:pointer;opacity:0.6;font-size:16px;">✕</span>
    </div>
    <div style="padding:8px 12px;border-bottom:1px solid #2a2a4a;color:#aaa;">
      <div style="font-size:11px;color:#666;margin-bottom:2px;">Original</div>
      <div>${esc(original).slice(0, 2000)}</div>
    </div>
    <div style="padding:8px 12px;color:#4fc3f7;">
      <div style="font-size:11px;color:#666;margin-bottom:2px;">Translation</div>
      <div>${esc(translated).slice(0, 2000)}</div>
    </div>
  `;

  document.body.appendChild(panel);

  // Close handler
  document.getElementById('bt-ocr-close').addEventListener('click', () => {
    panel.remove();
  });

  // Esc to close
  const escHandler = (e) => {
    if (e.key === 'Escape' && document.getElementById('bt-ocr-panel')) {
      panel.remove();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);
}
