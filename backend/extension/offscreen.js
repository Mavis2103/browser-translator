// Browser Translator - Offscreen Document
// Handles getUserMedia + MediaRecorder for tab audio capture.
// Created by the service worker via chrome.offscreen.createDocument().
// Communicates audio chunks back to the service worker via chrome.runtime.

const BACKEND_URL = 'ws://localhost:8765/ws/audio';

let mediaRecorder = null;
let capturedStream = null;
let ws = null;
let isCapturing = false;
let chunkSeq = 0;

// ========== WebSocket for streaming audio ==========

function connectWs(sourceLang, targetLang, translationModel) {
  return new Promise((resolve, reject) => {
    try {
      ws = new WebSocket(BACKEND_URL);
    } catch (e) {
      reject(new Error('WebSocket creation failed: ' + e.message));
      return;
    }

    ws.onopen = () => {
      console.log('[BT-Offscreen] WS connected');
      // Send start_capture metadata
      ws.send(JSON.stringify({
        type: 'start_capture',
        sourceLang: sourceLang,
        targetLang: targetLang,
        translationModel: translationModel
      }));
      resolve();
    };

    ws.onclose = (e) => {
      console.log('[BT-Offscreen] WS closed:', e.code, e.reason);
      ws = null;
      chrome.runtime.sendMessage({ type: 'ws_closed' });
    };

    ws.onerror = (e) => {
      console.error('[BT-Offscreen] WS error');
      reject(new Error('WebSocket error'));
    };

    ws.onmessage = (event) => {
      // Forward translation results back to service worker
      chrome.runtime.sendMessage({ type: 'backend_msg', data: event.data });
    };
  });
}

function sendAudioChunk(blob) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn('[BT-Offscreen] WS not open, dropping chunk');
    return;
  }
  chunkSeq++;
  const seqPrefix = new ArrayBuffer(4);
  new DataView(seqPrefix).setUint32(0, chunkSeq, false);
  const combined = new Blob([seqPrefix, blob], { type: 'application/octet-stream' });
  ws.send(combined);
}

function stopCapture() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  if (capturedStream) {
    capturedStream.getTracks().forEach(t => t.stop());
    capturedStream = null;
  }
  if (ws) {
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
    // chromeMediaSource and chromeMediaSourceId MUST be in `mandatory`.
    // sampleRate is requested in `optional` — backend transcodes to 16kHz anyway.
    capturedStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        },
        optional: [{ sampleRate: 16000 }]
      }
    });

    // MediaRecorder WebM/Opus — backend decodes via pydub/audio_pipeline.py
    // (see _decode_audio_chunk: detects EBML magic 0x1A45DFA3)
    const options = {
      mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm',
      audioBitsPerSecond: 16000
    };

    mediaRecorder = new MediaRecorder(capturedStream, options);

    mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        sendAudioChunk(event.data);
      }
    };

    mediaRecorder.onstop = () => {
      console.log('[BT-Offscreen] Capture stopped');
      stopCapture();
    };

    mediaRecorder.start(1000); // Chunks every 1s
    isCapturing = true;
    console.log('[BT-Offscreen] Audio capture started');

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

// Signal ready
chrome.runtime.sendMessage({ type: 'offscreen_ready' });
