// Browser Translator - Popup Script

let state = {
  isCapturing: false,
  wsConnected: false,
  backendUrl: 'ws://localhost:8765/ws/audio'
};

// DOM references
const $ = (id) => document.getElementById(id);

const statusDot = $('connection-status');
const backendStatus = $('backend-status');
const audioStatus = $('audio-status');
const ocrStatus = $('ocr-status');
const btnStartAudio = $('btn-start-audio');
const btnStopAudio = $('btn-stop-audio');
const btnOcr = $('btn-ocr');
const btnClearResults = $('btn-clear-results');
const resultsPanel = $('results-panel');
const sourceLang = $('source-lang');
const targetLang = $('target-lang');
const modelSelect = $('model-select');
const swapBtn = $('swap-langs');

// ========== State Updates ==========

function updateUI() {
  // Connection dot
  statusDot.className = 'status-dot ' + (state.wsConnected ? 'connected' : 'disconnected');
  backendStatus.textContent = state.wsConnected ? 'connected' : 'disconnected';

  // Audio buttons
  btnStartAudio.disabled = state.isCapturing || !state.wsConnected;
  btnStopAudio.disabled = !state.isCapturing;

  if (state.isCapturing) {
    audioStatus.textContent = '🎙️ Capturing audio...';
    audioStatus.className = 'status-text active';
  } else {
    audioStatus.textContent = 'Idle';
    audioStatus.className = 'status-text idle';
  }
}

// Listen for messages from background
chrome.runtime.onMessage.addListener((msg) => {
  switch (msg.type) {
    case 'state':
      state = { ...state, ...msg };
      updateUI();
      break;

    case 'backend_status':
      state.wsConnected = msg.status === 'connected';
      updateUI();
      break;

    case 'transcription':
      addResult('transcript', '📝 ' + msg.text);
      break;

    case 'translation':
      addResult('translation',
        `🗣️ <span class="lang-tag">${msg.source || '?'} → ${msg.target || '?'}</span><br>` +
        `<div class="orig-text">${escapeHtml(msg.original)}</div>` +
        `<div class="trans-text">${escapeHtml(msg.translated)}</div>`
      );
      break;

    case 'ocr_result':
      ocrStatus.textContent = '✅ OCR complete';
      ocrStatus.className = 'status-text success';
      addResult('ocr',
        '📄 <span class="lang-tag">OCR Translation</span><br>' +
        `<div class="orig-text">${escapeHtml(msg.texts || '')}</div>` +
        `<div class="trans-text">${escapeHtml(msg.translated || '')}</div>`
      );
      break;

    case 'error':
      addResult('error', '❌ ' + escapeHtml(msg.message));
      break;
  }
});

// ========== Actions ==========

btnStartAudio.addEventListener('click', async () => {
  audioStatus.textContent = '⏳ Starting capture...';
  audioStatus.className = 'status-text active';

  const resp = await chrome.runtime.sendMessage({
    action: 'start_capture',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    translationModel: modelSelect.value
  });

  if (resp?.success) {
    audioStatus.textContent = '🎙️ Capturing audio...';
    audioStatus.className = 'status-text active';
  } else {
    audioStatus.textContent = '❌ Failed: ' + (resp?.error || 'unknown');
    audioStatus.className = 'status-text error';
  }
});

btnStopAudio.addEventListener('click', async () => {
  const resp = await chrome.runtime.sendMessage({ action: 'stop_capture' });
  if (resp?.success) {
    audioStatus.textContent = '⏹ Stopped';
    audioStatus.className = 'status-text idle';
  }
});

btnOcr.addEventListener('click', async () => {
  ocrStatus.textContent = '⏳ Capturing page screenshot...';
  ocrStatus.className = 'status-text active';

  try {
    // Capture screenshot browser-side via chrome.tabs
    const dataUrl = await chrome.tabs.captureVisibleTab({ format: 'png' });
    const base64 = dataUrl.split(',')[1];

    ocrStatus.textContent = '⏳ Running OCR...';
    ocrStatus.className = 'status-text active';

    // Send directly to backend (no CDP round-trip)
    const resp = await fetch('http://localhost:8765/api/ocr/image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: base64,
        sourceLang: sourceLang.value,
        targetLang: targetLang.value
      })
    });
    const result = await resp.json();

    if (result.success) {
      ocrStatus.textContent = '✅ OCR complete';
      ocrStatus.className = 'status-text success';
      addResult('ocr',
        '📄 <span class="lang-tag">OCR Translation</span><br>' +
        `<div class="orig-text">${escapeHtml(result.texts || '')}</div>` +
        `<div class="trans-text">${escapeHtml(result.translated || '')}</div>`
      );

      // Send overlay data to content script for on-page display
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]) {
          chrome.tabs.sendMessage(tabs[0].id, {
            type: 'show_ocr_result',
            original: result.texts,
            translated: result.translated
          }).catch(() => { /* content script not ready */ });
        }
      });
    } else {
      ocrStatus.textContent = '❌ Failed: ' + (result.error || 'unknown');
      ocrStatus.className = 'status-text error';
    }
  } catch (e) {
    ocrStatus.textContent = '❌ Error: ' + e.message;
    ocrStatus.className = 'status-text error';
  }
});

swapBtn.addEventListener('click', () => {
  const tmp = sourceLang.value;
  sourceLang.value = targetLang.value;
  targetLang.value = tmp;
  // Update backend
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    translationModel: modelSelect.value
  });
});

sourceLang.addEventListener('change', () => {
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    translationModel: modelSelect.value
  });
});

targetLang.addEventListener('change', () => {
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    translationModel: modelSelect.value
  });
});

modelSelect.addEventListener('change', () => {
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value,
    translationModel: modelSelect.value
  });
});

btnClearResults.addEventListener('click', () => {
  resultsPanel.innerHTML = `<div class="empty-state">No translations yet.<br>Start audio capture or OCR a page.</div>`;
});

// ========== Helpers ==========

function addResult(type, html) {
  // Remove empty state
  const empty = resultsPanel.querySelector('.empty-state');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = 'result-item result-' + type;
  el.innerHTML = html;
  resultsPanel.prepend(el);

  // Keep max 20 items
  while (resultsPanel.children.length > 20) {
    resultsPanel.lastChild.remove();
  }
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ========== Health Polling ==========

let healthInterval = null;

async function pollHealth() {
  try {
    const resp = await fetch('http://localhost:8765/api/health');
    const data = await resp.json();
    const healthRow = document.getElementById('health-row');
    if (!healthRow) return;

    if (data.status === 'ok') {
      healthRow.style.display = 'flex';
      updateHealth('health-stt', data.models?.stt);
      updateHealth('health-ocr', data.models?.ocr);
      updateHealthLabel('health-llm', data.models?.translation || '?');
      // TTS is intentionally disabled by default (STT-only mode)
    } else {
      healthRow.style.display = 'none';
    }
  } catch {
    const hr = document.getElementById('health-row');
    if (hr) hr.style.display = 'none';
  }
}

function updateHealth(id, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  const span = el.querySelector('span');
  if (ok) {
    span.textContent = 'ok';
    span.className = 'hl-ok';
  } else if (ok === false) {
    span.textContent = 'fail';
    span.className = 'hl-fail';
  } else {
    span.textContent = '...';
    span.className = 'hl-blank';
  }
}

function updateHealthLabel(id, label) {
  const el = document.getElementById(id);
  if (!el) return;
  const span = el.querySelector('span');
  span.textContent = label || '?';
  span.className = label ? 'hl-ok' : 'hl-blank';
}

// ========== Init ==========

// Start health polling
healthInterval = setInterval(pollHealth, 5000);
pollHealth(); // immediate first poll

// Request initial state
chrome.runtime.sendMessage({ action: 'get_state' }, (resp) => {
  if (resp) {
    state = { ...state, ...resp };
    updateUI();
  }
});
