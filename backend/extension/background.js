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

      case 'tts_audio':
        // Received translated audio from backend
        playTranslatedAudio(msg.data, msg.sampleRate);
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
// MV3-compatible tab audio capture (works in Chrome, Brave, Edge).
// See: https://developer.chrome.com/docs/extensions/reference/api/tabCapture

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

// ========== Audio Playback (TTS output) ==========

let audioContext = null;
let gainNode = null;

function playTranslatedAudio(base64Data, sampleRate = 22050) {
  try {
    // Decode base64 to ArrayBuffer
    const binaryStr = atob(base64Data);
    const len = binaryStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryStr.charCodeAt(i);
    }

    // Create audio context
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      gainNode = audioContext.createGain();
      gainNode.gain.value = 1.0;
      gainNode.connect(audioContext.destination);
    }

    // Decode PCM16 data
    const pcmData = new Int16Array(bytes.buffer);
    const floatData = new Float32Array(pcmData.length);
    for (let i = 0; i < pcmData.length; i++) {
      floatData[i] = pcmData[i] / 32768.0;
    }

    // Create audio buffer and play
    const audioBuffer = audioContext.createBuffer(1, floatData.length, sampleRate);
    audioBuffer.getChannelData(0).set(floatData);

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(gainNode);
    source.start();
  } catch (e) {
    console.error('[BT] Audio playback failed:', e);
  }
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

// Connect on install/startup
chrome.runtime.onInstalled.addListener(() => {
  connectWebSocket();
});
chrome.runtime.onStartup.addListener(() => {
  connectWebSocket();
});

// Attempt initial connection
connectWebSocket();
