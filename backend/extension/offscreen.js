// Browser Translator - Offscreen Document
// Captures tab audio via getUserMedia + WebAudio API, streams raw PCM16 (16kHz mono)
// to the backend WebSocket. Includes auto-reconnect on WS drop and keepalive pings.

const BACKEND_URL = 'ws://localhost:8765/ws/audio';
const SAMPLE_RATE = 16000;
const KEEPALIVE_INTERVAL_MS = 10000; // ping every 10s to keep WS alive
const MAX_RECONNECT_ATTEMPTS = 20;

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let capturedStream = null;
let isCapturing = false;
let isStopped = false;  // set true on user stop — skip reconnect
let chunkSeq = 0;
let reconnectAttempts = 0;
let reconnectTimer = null;
// Generation counter prevents stale ws.onclose/onopen handlers from
// overwriting a newer connection when connectWs() races with a reconnect timer.
let wsGen = 0;
let keepaliveTimer = null;

// ========== WebSocket connection ==========

function connectWs(sourceLang, targetLang, translationModel) {
  const gen = ++wsGen;
  return new Promise((resolve, reject) => {
    if (ws) {
      try { ws.close(); } catch (e) { /* ignore */ }
      ws = null;
    }
    try {
      ws = new WebSocket(BACKEND_URL);
    } catch (e) {
      reject(new Error('WebSocket creation failed: ' + e.message));
      return;
    }

    ws.onopen = () => {
      if (gen !== wsGen) return;  // stale — a newer connectWs() replaced us
      reconnectAttempts = 0;
      // Send start_capture metadata (idempotent — backend handles re-sends)
      ws.send(JSON.stringify({
        type: 'start_capture',
        sourceLang: sourceLang,
        targetLang: targetLang,
        translationModel: translationModel
      }));
      startKeepalive();
      resolve();
    };

    ws.onclose = () => {
      if (gen !== wsGen) return;  // stale — ignore, newer connection is active
      ws = null;
      stopKeepalive();
      chrome.runtime.sendMessage({ type: 'ws_closed' });
      // Auto-reconnect if not stopped by user
      if (!isStopped && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts), 10000);
        reconnectAttempts++;
        console.log('[BT-Offscreen] WS closed, reconnecting in ' + delay + 'ms (attempt ' + reconnectAttempts + ')');
        reconnectTimer = setTimeout(() => {
          connectWs(sourceLang, targetLang, translationModel)
            .then(() => {
              // Reconnected — if the audio stream is still alive, resume sending
              if (isCapturing && capturedStream) {
                restartAudioPipeline(sourceLang, targetLang, translationModel);
              }
            })
            .catch(() => {
              // Will retry via onclose again
            });
        }, delay);
      } else if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        console.error('[BT-Offscreen] Max reconnection attempts reached. Giving up.');
        chrome.runtime.sendMessage({ type: 'ws_dead' });
      }
    };

    ws.onerror = () => {
      if (gen !== wsGen) return;  // stale
      // onclose will fire after onerror, reconnect logic handles it
    };

    ws.onmessage = (event) => {
      if (gen !== wsGen) return;  // stale
      const msg = JSON.parse(typeof event.data === 'string' ? event.data : new TextDecoder().decode(event.data));
      // Forward backend messages to service worker (translation results, etc.)
      chrome.runtime.sendMessage({ type: 'backend_msg', data: event.data })
        .catch(() => { /* service worker may be gone */ });
    };
  });
}

function startKeepalive() {
  stopKeepalive();
  keepaliveTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }));
    }
  }, KEEPALIVE_INTERVAL_MS);
}

function stopKeepalive() {
  if (keepaliveTimer) {
    clearInterval(keepaliveTimer);
    keepaliveTimer = null;
  }
}

function sendPcmChunk(int16Array) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  chunkSeq++;
  // 4-byte big-endian sequence prefix + PCM16 LE bytes
  const seqPrefix = new ArrayBuffer(4);
  new DataView(seqPrefix).setUint32(0, chunkSeq, false);
  const audioBytes = new Uint8Array(int16Array.buffer, int16Array.byteOffset, int16Array.byteLength);
  const combined = new Blob([seqPrefix, audioBytes], { type: 'application/octet-stream' });
  ws.send(combined);
}

// ========== Audio Capture ==========

function stopCapture() {
  isStopped = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  stopKeepalive();
  if (processorNode) {
    try { processorNode.disconnect(); } catch (e) { /* may already be disconnected */ }
    processorNode.onaudioprocess = null;
    processorNode = null;
  }
  if (sourceNode) {
    try { sourceNode.disconnect(); } catch (e) { /* may already be disconnected */ }
    sourceNode = null;
  }
  if (capturedStream) {
    capturedStream.getTracks().forEach(t => t.stop());
    capturedStream = null;
  }
  if (audioContext && audioContext.state !== 'closed') {
    audioContext.close().catch(() => { /* ignore */ });
    audioContext = null;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'stop_capture' }));
    ws.close();
    ws = null;
  }
  isCapturing = false;
  chunkSeq = 0;
}

function restartAudioPipeline(sourceLang, targetLang, translationModel) {
  // Rebuild the WebAudio pipeline with existing stream
  if (!capturedStream || !ws || ws.readyState !== WebSocket.OPEN) return;

  // Close old pipeline
  if (processorNode) {
    try { processorNode.disconnect(); } catch (e) { /* ignore */ }
    processorNode = null;
  }
  if (sourceNode) {
    try { sourceNode.disconnect(); } catch (e) { /* ignore */ }
    sourceNode = null;
  }
  if (audioContext && audioContext.state !== 'closed') {
    audioContext.close().catch(() => { /* ignore */ });
  }

  // Rebuild
  audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
  sourceNode = audioContext.createMediaStreamSource(capturedStream);
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {
    if (!isCapturing) return;
    const channelData = event.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(channelData.length);
    for (let i = 0; i < channelData.length; i++) {
      const s = Math.max(-1, Math.min(1, channelData[i]));
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    sendPcmChunk(int16);
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);

  console.log('[BT-Offscreen] Audio pipeline restarted after WS reconnect');
  chrome.runtime.sendMessage({ type: 'capture_reconnected' });
}

async function startCapture(streamId, sourceLang, targetLang, translationModel) {
  isStopped = false;
  reconnectAttempts = 0;
  try {
    // Connect WebSocket first
    await connectWs(sourceLang, targetLang, translationModel);

    // Get audio stream using the stream ID
    capturedStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        },
        optional: [{ sampleRate: SAMPLE_RATE }]
      }
    });

    // WebAudio pipeline: MediaStream → AudioContext(16kHz) → ScriptProcessor → Int16
    audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
    sourceNode = audioContext.createMediaStreamSource(capturedStream);
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);

    processorNode.onaudioprocess = (event) => {
      if (!isCapturing) return;
      const channelData = event.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(channelData.length);
      for (let i = 0; i < channelData.length; i++) {
        const s = Math.max(-1, Math.min(1, channelData[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      sendPcmChunk(int16);
    };

    sourceNode.connect(processorNode);
    processorNode.connect(audioContext.destination);

    isCapturing = true;
    chrome.runtime.sendMessage({ type: 'capture_started' });
  } catch (e) {
    console.error('[BT-Offscreen] Capture failed:', e);
    chrome.runtime.sendMessage({ type: 'capture_error', error: e.message });
    stopCapture();
  }
}

// ========== Message listener (from service worker) ==========

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'start_capture':
      startCapture(msg.streamId, msg.sourceLang, msg.targetLang, msg.translationModel);
      sendResponse({ received: true });
      break;

    case 'stop_capture':
      stopCapture();
      sendResponse({ success: true });
      break;

    case 'get_status':
      sendResponse({ isCapturing, wsActive: ws && ws.readyState === WebSocket.OPEN });
      break;
  }
  return true;
});

// Signal ready immediately
chrome.runtime.sendMessage({ type: 'offscreen_ready' });
