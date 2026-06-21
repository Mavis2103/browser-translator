// Browser Translator - Background Service Worker
// Handles audio capture (via offscreen document), WebSocket connection to Python backend,
// and message routing between popup, content scripts, and backend.

const BACKEND_URL = 'ws://localhost:8765/ws/audio';

let ws = null;
let wsReconnectTimer = null;
let isCapturing = false;
let offscreenCaptureTabId = null;

// ========== WebSocket connection ==========

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    ws = new WebSocket(BACKEND_URL);
  } catch (e) {
    console.error('[BT] WebSocket creation failed:', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('[BT] WebSocket connected');
    wsReconnectTimer = null;
    broadcastState();
  };

  ws.onclose = (e) => {
    console.log('[BT] WebSocket closed:', e.code, e.reason);
    ws = null;
    scheduleReconnect();
    broadcastState();
  };

  ws.onerror = (e) => {
    console.error('[BT] WebSocket error');
  };

  ws.onmessage = (event) => {
    handleBackendMessage(event.data);
  };
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWebSocket();
  }, 5000);
}

function sendToBackend(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(data);
    return true;
  }
  console.warn('[BT] Cannot send: WebSocket not open');
  return false;
}

function handleBackendMessage(data) {
  try {
    // Try to parse as JSON first
    const msg = JSON.parse(typeof data === 'string' ? data : new TextDecoder().decode(data));
    
    switch (msg.type) {
      case 'translation':
        // Send translation result to active tab
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, {
              type: 'translation',
              original: msg.original,
              translated: msg.translated,
              source: msg.source,
              target: msg.target
            });
          }
        });
        // Also forward to popup if open
        broadcastToPopup(msg);
        break;

      case 'transcription':
        broadcastToPopup({ type: 'transcription', text: msg.text });
        break;

      case 'ocr_result':
        broadcastToPopup({
          type: 'ocr_result',
          texts: msg.texts,
          translated: msg.translated
        });
        break;

      case 'status':
        broadcastToPopup({ type: 'backend_status', status: msg.status });
        break;

      case 'error':
        broadcastToPopup({ type: 'error', message: msg.message });
        break;
    }
  } catch (e) {
    // Binary audio data
    console.log('[BT] Received binary data, playing...');
  }
}

// ========== Audio Capture (via Offscreen Document) ==========
// Uses chrome.tabCapture.getMediaStreamId() + offscreen document for
// MV3-compatible tab audio capture. Auto-reconnects on WS drop.

let captureConfig = null;  // remember settings for auto-reconnect { sourceLang, targetLang, translationModel }

async function ensureOffscreenDocument() {
  const existing = await chrome.offscreen.hasDocument();
  if (!existing) {
    await chrome.offscreen.createDocument({
      url: chrome.runtime.getURL('offscreen.html'),
      reasons: ['USER_MEDIA'],
      justification: 'Capture tab audio for speech-to-speech translation'
    });
    // Wait for offscreen.js to signal ready
    await waitForOffscreenReady();
  }
}

function waitForOffscreenReady() {
  return new Promise((resolve) => {
    const handler = (msg) => {
      if (msg.type === 'offscreen_ready') {
        chrome.runtime.onMessage.removeListener(handler);
        resolve();
      }
    };
    chrome.runtime.onMessage.addListener(handler);
    // Safety: resolve anyway after 5s
    setTimeout(() => {
      chrome.runtime.onMessage.removeListener(handler);
      resolve();
    }, 5000);
  });
}

async function closeOffscreenDocument() {
  const existing = await chrome.offscreen.hasDocument();
  if (existing) {
    await chrome.offscreen.closeDocument();
  }
}

async function startAudioCapture(sourceLang = 'auto', targetLang = 'vi', translationModel = 'qwen3.5:0.8b') {
  if (isCapturing) {
    return { success: false, error: 'Already capturing' };
  }

  // Save config for auto-recovery
  captureConfig = { sourceLang, targetLang, translationModel };

  try {
    // Get current tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]) throw new Error('No active tab');
    offscreenCaptureTabId = tabs[0].id;

    // Ensure offscreen document exists
    await ensureOffscreenDocument();

    // Get stream ID (MV3-compatible — omit consumerTabId so offscreen
    // document can consume it. Chrome 116+ allows cross-context consumption.)
    const streamId = await chrome.tabCapture.getMediaStreamId({
      targetTabId: offscreenCaptureTabId
    });

    // Send stream ID to offscreen doc to start capture
    const resp = await chrome.runtime.sendMessage({
      action: 'start_capture',
      streamId: streamId,
      sourceLang: sourceLang,
      targetLang: targetLang,
      translationModel: translationModel
    });

    if (!resp || !resp.received) {
      throw new Error('Offscreen document did not acknowledge capture start');
    }

    isCapturing = true;
    broadcastState();
    return { success: true };
  } catch (e) {
    console.error('[BT] Audio capture failed:', e);
    return { success: false, error: e.message };
  }
}

async function stopAudioCapture() {
  if (!isCapturing) return { success: false, error: 'Not capturing' };

  captureConfig = null;  // clear auto-recovery

  try {
    // Tell offscreen doc to stop
    await chrome.runtime.sendMessage({ action: 'stop_capture' });
  } catch (e) {
    // Offscreen may already be gone
    console.warn('[BT] Error stopping capture:', e.message);
  }

  isCapturing = false;
  offscreenCaptureTabId = null;
  broadcastState();
  return { success: true };
}

// ========== OCR via Backend HTTP ==========

async function capturePageForOcr() {
  try {
    // Send request to backend (it will use CDP to screenshot)
    const resp = await fetch('http://localhost:8765/api/ocr/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sourceLang: 'auto',
        targetLang: 'vi',
        fullPage: true
      })
    });
    const result = await resp.json();
    return result;
  } catch (e) {
    console.error('[BT] OCR capture failed:', e);
    return { success: false, error: e.message };
  }
}

// ========== Keepalive — prevent Chrome from killing service worker ==========

let keepaliveInterval = null;

function startKeepalive() {
  stopKeepalive();
  // Use chrome.alarms to wake the SW every 20s (more reliable than setTimeout in MV3)
  chrome.alarms.create('bt-keepalive', { periodInMinutes: 0.33 });
}

function stopKeepalive() {
  try { chrome.alarms.clear('bt-keepalive'); } catch (e) { /* ignore */ }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'bt-keepalive') {
    // Keep SW alive — no-op, just the alarm event wakes us
    checkOffscreenHealth();
  }
});

// ========== Offscreen Recovery ==========

async function checkOffscreenHealth() {
  // If we think we're capturing but offscreen is gone, recover
  if (isCapturing && captureConfig) {
    const hasDoc = await chrome.offscreen.hasDocument().catch(() => false);
    if (!hasDoc) {
      console.warn('[BT] Offscreen document missing during capture — recovering...');
      recoverOffscreenCapture();
    }
  }
}

async function recoverOffscreenCapture() {
  if (!captureConfig) return;
  const { sourceLang, targetLang, translationModel } = captureConfig;

  try {
    // Close any stale offscreen doc
    const hasDoc = await chrome.offscreen.hasDocument().catch(() => false);
    if (hasDoc) {
      await chrome.offscreen.closeDocument().catch(() => {});
    }

    // Get current tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]) throw new Error('No active tab');

    // Create fresh offscreen doc
    await ensureOffscreenDocument();

    // Get new stream ID
    const streamId = await chrome.tabCapture.getMediaStreamId({
      targetTabId: tabs[0].id
    });

    // Start capture with stored config
    await chrome.runtime.sendMessage({
      action: 'start_capture',
      streamId: streamId,
      sourceLang: sourceLang,
      targetLang: targetLang,
      translationModel: translationModel
    });

    offscreenCaptureTabId = tabs[0].id;
    isCapturing = true;
    broadcastState();
    console.log('[BT] Offscreen capture recovered successfully');
  } catch (e) {
    console.error('[BT] Offscreen recovery failed:', e);
    // Give up — user needs to click Start again
    isCapturing = false;
    captureConfig = null;
    broadcastState();
  }
}

// ========== Messaging ==========

function broadcastToPopup(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {
    // Popup may not be open - ignore
  });
}

function broadcastState() {
  broadcastToPopup({
    type: 'state',
    isCapturing: isCapturing,
    wsConnected: ws && ws.readyState === WebSocket.OPEN,
    backendUrl: BACKEND_URL
  });
}

// Handle messages from popup, content script, or offscreen document
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Messages from offscreen document
  if (msg.type) {
    switch (msg.type) {
      case 'capture_started':
        isCapturing = true;
        broadcastState();
        return false;

      case 'capture_error':
        console.error('[BT] Offscreen capture error:', msg.error);
        isCapturing = false;
        broadcastState();
        return false;

      case 'offscreen_ready':
        console.log('[BT] Offscreen document ready');
        return false;

      case 'ws_closed':
        // Offscreen's backend WS closed
        console.log('[BT] Offscreen WS closed, capture ended');
        isCapturing = false;
        offscreenCaptureTabId = null;
        broadcastState();
        return false;

      case 'ws_dead':
        // Offscreen's WS gave up reconnecting — recreate offscreen doc
        console.warn('[BT] Offscreen WS dead, attempting recovery...');
        if (captureConfig) {
          recoverOffscreenCapture();
        }
        return false;

      case 'capture_reconnected':
        // Offscreen reconnected its WS and resumed
        console.log('[BT] Offscreen capture reconnected');
        isCapturing = true;
        broadcastState();
        return false;

      case 'backend_msg':
        // Forward backend message (translation result) to other contexts
        try {
          const parsed = JSON.parse(typeof msg.data === 'string' ? msg.data : '');
          handleBackendMessage(parsed);
        } catch {
          handleBackendMessage(msg.data);
        }
        return false;
    }
  }

  // Messages from popup or content script
  switch (msg.action) {
    case 'get_state':
      sendResponse({
        isCapturing,
        wsConnected: ws && ws.readyState === WebSocket.OPEN
      });
      return true;

    case 'start_capture':
      startAudioCapture(msg.sourceLang, msg.targetLang, msg.translationModel).then(sendResponse);
      return true;

    case 'stop_capture':
      stopAudioCapture().then(sendResponse);
      return true;

    case 'ocr_capture':
      capturePageForOcr().then(sendResponse);
      return true;

    case 'set_language':
      sendToBackend(JSON.stringify({
        type: 'set_language',
        sourceLang: msg.sourceLang,
        targetLang: msg.targetLang
      }));
      sendResponse({ success: true });
      return true;

    case 'reconnect':
      connectWebSocket();
      sendResponse({ success: true });
      return true;

    default:
      sendResponse({ success: false, error: 'Unknown action' });
      return true;
  }
});

// ========== Init ==========

// Create right-click context menu to hide/show translation panel
chrome.runtime.onInstalled.addListener(() => {
  connectWebSocket();
  startKeepalive();
  chrome.contextMenus.create({
    id: 'bt-toggle-panel',
    title: 'Ẩn / Hiện bảng dịch (Browser Translator)',
    contexts: ['page', 'selection', 'editable'],
  });
});

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'bt-toggle-panel' && tab?.id) {
    chrome.tabs.sendMessage(tab.id, { type: 'toggle_panel' })
      .catch(() => { /* content script may not be ready */ });
  }
});

chrome.runtime.onStartup.addListener(() => {
  connectWebSocket();
  startKeepalive();
});

// Attempt initial connection
connectWebSocket();
startKeepalive();
