// Browser Translator - Content Script
// Handles overlay translations on web pages

let overlayContainer = null;
let overlayVisible = false;

// Listen for messages from background
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case 'translation':
      // Show a small toast-like notification at top of page
      showToast(`${msg.source || ''} → ${msg.target}: ${msg.translated}`);
      break;

    case 'ocr_translation_overlay':
      // Apply OCR overlay translations
      if (msg.elements && msg.elements.length > 0) {
        applyOcrOverlay(msg.elements);
      }
      break;

    case 'toggle_overlay':
      toggleOverlay();
      break;
  }
});

// ========== Toast Notification ==========

function showToast(text, duration = 4000) {
  let toast = document.getElementById('bt-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'bt-toast';
    toast.style.cssText = `
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 2147483647;
      background: #1a1a2e;
      color: #e0e0e0;
      padding: 10px 16px;
      border-radius: 8px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 13px;
      line-height: 1.4;
      max-width: 400px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      border: 1px solid #2a2a4a;
      transition: opacity 0.3s ease;
    `;
    document.body.appendChild(toast);
  }

  toast.textContent = text;
  toast.style.opacity = '1';

  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => {
    toast.style.opacity = '0';
  }, duration);
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
