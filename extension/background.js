// Browser Translator - Background Service Worker
// Handles tabCapture audio capture, WebSocket connection to Python backend,
// and message routing between popup, content scripts, and backend.

const BACKEND_URL = 'ws://localhost:8765/ws/audio';

let ws = null;
let wsReconnectTimer = null;
let mediaRecorder = null;
let recordedChunks = [];
let isCapturing = false;
let capturedStream = null;

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

// ========== Audio Capture (tabCapture) ==========

async function startAudioCapture(sourceLang = 'auto', targetLang = 'vi') {
  if (isCapturing) {
    console.warn('[BT] Already capturing audio');
    return { success: false, error: 'Already capturing' };
  }

  try {
    // Get current tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]) throw new Error('No active tab');

    // Capture tab audio
    capturedStream = await chrome.tabCapture.capture({
      audio: true,
      video: false,
      audioConstraints: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false
      }
    });

    // Notify backend about new capture
    sendToBackend(JSON.stringify({
      type: 'start_capture',
      sourceLang: sourceLang,
      targetLang: targetLang,
      tabTitle: tabs[0].title || '',
      tabUrl: tabs[0].url || ''
    }));

    // Set up MediaRecorder for streaming
    const options = {
      mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm',
      audioBitsPerSecond: 16000
    };

    recordedChunks = [];
    mediaRecorder = new MediaRecorder(capturedStream, options);

    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        sendToBackend(event.data);
      }
    };

    mediaRecorder.onstop = () => {
      console.log('[BT] Audio capture stopped');
      sendToBackend(JSON.stringify({ type: 'stop_capture' }));
      if (capturedStream) {
        capturedStream.getTracks().forEach(t => t.stop());
        capturedStream = null;
      }
    };

    mediaRecorder.start(1000); // Send chunks every 1s
    isCapturing = true;
    broadcastState();

    return { success: true };
  } catch (e) {
    console.error('[BT] Audio capture failed:', e);
    return { success: false, error: e.message };
  }
}

function stopAudioCapture() {
  if (!isCapturing) return { success: false, error: 'Not capturing' };

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  isCapturing = false;
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

// Handle messages from popup or content script
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'get_state':
      sendResponse({
        isCapturing,
        wsConnected: ws && ws.readyState === WebSocket.OPEN
      });
      return true;

    case 'start_capture':
      startAudioCapture(msg.sourceLang, msg.targetLang).then(sendResponse);
      return true;

    case 'stop_capture':
      sendResponse(stopAudioCapture());
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
