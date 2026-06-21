// Browser Translator - Offscreen Document
// Captures tab audio via getUserMedia + WebAudio API, streams raw PCM16 (16kHz mono)
// to the backend WebSocket. Moonshine STT expects PCM Float32 — we send Int16 over the
// wire and let the backend convert (np.frombuffer(..., dtype=np.int16)). This avoids
// the pydub/pyaudioop decode path that's broken on Python 3.13.

const BACKEND_URL = 'ws://localhost:8765/ws/audio';
const SAMPLE_RATE = 16000;

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let capturedStream = null;
let isCapturing = false;
let chunkSeq = 0;

// ========== WebSocket connection ==========

function connectWs(sourceLang, targetLang, translationModel) {
  return new Promise((resolve, reject) => {
    try {
      ws = new WebSocket(BACKEND_URL);
    } catch (e) {
      reject(new Error('WebSocket creation failed: ' + e.message));
      return;
    }

    ws.onopen = () => {
      // Send start_capture metadata
      ws.send(JSON.stringify({
        type: 'start_capture',
        sourceLang: sourceLang,
        targetLang: targetLang,
        translationModel: translationModel
      }));
      resolve();
    };

    ws.onclose = () => {
      ws = null;
      chrome.runtime.sendMessage({ type: 'ws_closed' });
    };

    ws.onerror = () => reject(new Error('WebSocket error'));

    ws.onmessage = (event) => {
      const msg = JSON.parse(typeof event.data === 'string' ? event.data : new TextDecoder().decode(event.data));
      switch (msg.type) {
        case 'tts_audio':
          // TTS audio plays in the offscreen doc (it has DOM/AudioContext).
          // The service worker has no DOM — can't instantiate AudioContext.
          playTranslatedAudio(msg.data, msg.sampleRate);
          break;

        case 'translation':
        case 'transcription':
        case 'ocr_result':
        case 'status':
        case 'error':
          // Forward non-audio messages to the service worker for routing
          // to popup/content script.
          chrome.runtime.sendMessage({ type: 'backend_msg', data: event.data })
            .catch(() => { /* service worker may be gone */ });
          break;
      }
    };
  });
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

// ========== Audio Playback (translated TTS) ==========
// Reuses the shared `audioContext` declared above (line ~11) for capture.
// AudioContext can only live in a DOM context — which offscreen.html provides.

function playTranslatedAudio(base64Data, sampleRate = 22050) {
  try {
    const binaryStr = atob(base64Data);
    const len = binaryStr.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryStr.charCodeAt(i);
    }

    // If the shared context has a different sample rate, recreate.
    if (!audioContext || audioContext.sampleRate !== sampleRate) {
      if (audioContext) {
        audioContext.close().catch(() => {});
        audioContext = null;
      }
      audioContext = new AudioContext({ sampleRate });
    }

    const pcmData = new Int16Array(bytes.buffer);
    const floatData = new Float32Array(pcmData.length);
    for (let i = 0; i < pcmData.length; i++) {
      floatData[i] = pcmData[i] / 32768.0;
    }

    const dest = audioContext.destination;
    const audioBuffer = audioContext.createBuffer(1, floatData.length, sampleRate);
    audioBuffer.getChannelData(0).set(floatData);
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(dest);
    source.start();
  } catch (e) {
    console.error('[BT-Offscreen] TTS playback failed:', e);
  }
}

function stopCapture() {
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

async function startCapture(streamId, sourceLang, targetLang, translationModel) {
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
    // 4096-frame buffer ≈ 256ms at 16kHz, fired often enough for STT chunking
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);

    processorNode.onaudioprocess = (event) => {
      if (!isCapturing) return;
      const channelData = event.inputBuffer.getChannelData(0); // Float32 [-1, 1]
      // Convert Float32 [-1, 1] → Int16 [-32768, 32767]
      const int16 = new Int16Array(channelData.length);
      for (let i = 0; i < channelData.length; i++) {
        const s = Math.max(-1, Math.min(1, channelData[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      sendPcmChunk(int16);
    };

    sourceNode.connect(processorNode);
    // ScriptProcessor needs to connect to destination to fire onaudioprocess
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
      sendResponse({ isCapturing });
      break;
  }
  return true; // Keep channel open for async
});

// Signal ready immediately — listener registration completes sync after this script runs
chrome.runtime.sendMessage({ type: 'offscreen_ready' });
