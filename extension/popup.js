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
    targetLang: targetLang.value
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
  ocrStatus.textContent = '⏳ Processing OCR...';
  ocrStatus.className = 'status-text active';

  // First set language
  await chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value
  });

  const resp = await chrome.runtime.sendMessage({ action: 'ocr_capture' });

  if (resp?.success) {
    ocrStatus.textContent = '✅ OCR complete';
    ocrStatus.className = 'status-text success';
  } else {
    ocrStatus.textContent = '❌ Failed: ' + (resp?.error || 'connection refused');
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
    targetLang: targetLang.value
  });
});

sourceLang.addEventListener('change', () => {
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value
  });
});

targetLang.addEventListener('change', () => {
  chrome.runtime.sendMessage({
    action: 'set_language',
    sourceLang: sourceLang.value,
    targetLang: targetLang.value
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

// ========== Init ==========

// Request initial state
chrome.runtime.sendMessage({ action: 'get_state' }, (resp) => {
  if (resp) {
    state = { ...state, ...resp };
    updateUI();
  }
});
