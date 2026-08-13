// ============================================
// Lilly Voice Engine for VibeCode
// Browser-based VAD + Whisper STT (via Lilly server) + Web Speech TTS
// ============================================

/*
  This replaces the old stub that depended on window.SpeechRecognition
  (unavailable in most desktop browsers) and pointed at an OpenLive agent
  WebSocket that never received audio (OpenLive's protocol carries no audio).

  Pipeline:
    mic → MediaRecorder(WebM/Opus) → /api/transcribe (Whisper STT on Lilly server)
        → _doChat(transcript)  (reuses the existing VibeCode chat path)
        → speechSynthesis TTS  (browser-native, zero download)

  Push-to-talk: hold SPACE (when not typing) to record, release to send.
  Hotword/VAD mode: toggle the mic button to listen continuously via a
  silence-threshold auto-send.

  The UI elements are injected into #chat-input-row so they sit next to the
  send button, reusing the same styling hooks as before.
*/

class LillyVoiceEngine {
  constructor(options = {}) {
    this.onTranscript = options.onTranscript || (() => {});
    this.onResponse = options.onResponse || (() => {});
    this.onError = options.onError || console.error;
    this.onStatus = options.onStatus || (() => {});

    this.mediaStream = null;
    this.mediaRecorder = null;
    this.audioContext = null;
    this.analyser = null;
    this.dataArray = null;
    this.source = null;

    this.isListening = false;
    this.isSpeaking = false;
    this.isRecording = false;
    this.pttActive = false;

    this.chunks = [];
    this.silenceStart = null;
    this.silenceTimer = null;
    this.noiseFloor = 0.0;
    this.rmsSampleCount = 0;
    this.rmsSum = 0;

    this.speechSynthesis = window.speechSynthesis;
    this.currentUtterance = null;

    // VAD thresholds (tunable)
    this.SPEECH_RMS = 0.015;       // amplitude above this = "speech"
    this.SILENCE_MS = 1200;        // silence duration before auto-send
    this.SILENCE_SAMPLE_MS = 100;  // how often to check the gate

    this.init();
  }

  async init() {
    try {
      await this.requestMic();
      console.log('[Lilly Voice] initialized, mic available');
    } catch (err) {
      console.warn('[Lilly Voice] init warning:', err.message);
    }
  }

  // ── Microphone Access ─────────
  async requestMic() {
    if (this.mediaStream) return true;
    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        },
      });
      return true;
    } catch (err) {
      console.warn('[Lilly Voice] microphone access denied:', err.message);
      this.onError('Microphone access denied: ' + err.message);
      return false;
    }
  }

  // ── Push-to-Talk (hold SPACE) ─────────
  startPtt() {
    if (!this.mediaStream) { this.onError('No microphone access'); return false; }
    this.pttActive = true;
    this.isRecording = true;
    this.chunks = [];
    this.updateStatus('recording');

    this.mediaRecorder = new MediaRecorder(this.mediaStream, { mimeType: 'audio/webm;codecs=opus' });
    this.mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) this.chunks.push(e.data); };
    this.mediaRecorder.start();
    return true;
  }

  async endPtt() {
    if (!this.pttActive || !this.mediaRecorder) return;
    this.pttActive = false;
    this.isRecording = false;

    await new Promise((resolve) => {
      if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') { resolve(); return; }
      this.mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) this.chunks.push(e.data); };
      this.mediaRecorder.stop();
      this.mediaRecorder.onstop = resolve;
    });

    if (this.chunks.length === 0) {
      this.updateStatus('idle');
      return;
    }

    const blob = new Blob(this.chunks, { type: 'audio/webm' });
    this.chunks = [];
    this.updateStatus('thinking');
    await this.transcribeAndSend(blob);
  }

  // ── Continuous Mode (auto VAD send) ─────────
  startContinuous() {
    if (!this.mediaStream) { this.onError('No microphone access'); return false; }
    if (this.isListening) return true;

    this.isListening = true;
    this.isRecording = true;
    this.chunks = [];
    this.silenceStart = null;
    this.updateStatus('listening');

    this.mediaRecorder = new MediaRecorder(this.mediaStream, { mimeType: 'audio/webm;codecs=opus' });
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
      this.checkSilence();
    };
    this.mediaRecorder.start(500); // emit a chunk every 500ms for VAD analysis

    // Set up audio analyser for amplitude-based VAD
    this.setupAnalyser();

    // Fallback: periodic silence check even if chunks stall
    this.silenceTimer = setInterval(() => this.checkSilence(), this.SILENCE_SAMPLE_MS);

    return true;
  }

  stopContinuous() {
    this.isListening = false;
    this.pttActive = false;
    this.isRecording = false;

    if (this.silenceTimer) { clearInterval(this.silenceTimer); this.silenceTimer = null; }

    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try { this.mediaRecorder.stop(); } catch (e) { /* already stopped */ }
    }
    this.mediaRecorder = null;
    this.cleanupAnalyser();
    this.updateStatus('idle');
  }

  // ── Audio analyser for VAD ─────────
  setupAnalyser() {
    if (!this.mediaStream) return;
    try {
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 256;
      this.analyser.smoothingTimeConstant = 0.8;
      this.source = this.audioContext.createMediaStreamSource(this.mediaStream);
      this.source.connect(this.analyser);
      this.dataArray = new Uint8Array(this.analyser.frequencyBinCount);
      this.startVadLoop();
    } catch (e) { /* best-effort */ }
  }

  cleanupAnalyser() {
    if (this.source) { try { this.source.disconnect(); } catch (e) {} this.source = null; }
    if (this.analyser) { this.analyser = null; }
    if (this.audioContext) { try { this.audioContext.close(); } catch (e) {} this.audioContext = null; }
    this.dataArray = null;
  }

  startVadLoop() {
    const loop = () => {
      if (!this.isListening || !this.analyser || !this.dataArray) return;
      this.analyser.getByteFrequencyData(this.dataArray);
      let sum = 0;
      for (let i = 0; i < this.dataArray.length; i++) sum += this.dataArray[i] * this.dataArray[i];
      const rms = Math.sqrt(sum / this.dataArray.length) / 255;

      // Update running average for noise floor calibration
      this.rmsSum += rms;
      this.rmsSampleCount++;

      if (this.isRecording) {
        const gate = Math.max(this.SPEECH_RMS, this.noiseFloor * 1.5);
        if (rms > gate) {
          this.silenceStart = Date.now();
        } else if (this.silenceStart && Date.now() - this.silenceStart > this.SILENCE_MS) {
          // Silence detected — send what we have
          this.silenceStart = null;
          this.flushRecordedAudio();
        }
      }

      // Recalibrate noise floor while idle
      if (!this.isListening && this.rmsSampleCount > 0) {
        this.noiseFloor = this.rmsSum / this.rmsSampleCount;
        this.rmsSum = 0;
        this.rmsSampleCount = 0;
      }

      requestAnimationFrame(loop);
    };
    loop();
  }

  async flushRecordedAudio() {
    if (this.chunks.length === 0) return;
    const chunks = this.chunks;
    this.chunks = [];
    const blob = new Blob(chunks, { type: 'audio/webm' });

    this.isRecording = false;
    this.updateStatus('thinking');
    await this.transcribeAndSend(blob);
    // Resume listening if still in continuous mode
    if (this.isListening) {
      this.isRecording = true;
      this.updateStatus('listening');
    }
  }

  checkSilence() {
    if (!this.isListening || !this.isRecording) return;
    if (this.silenceStart && Date.now() - this.silenceStart > this.SILENCE_MS) {
      this.silenceStart = null;
      this.flushRecordedAudio();
    }
  }

  // ── Transcribe via Lilly server Whisper STT ─────────
  async transcribeAndSend(blob) {
    // Only send if the blob has meaningful audio (>= 0.3s)
    if (!blob || blob.size < 1000) {
      this.updateStatus(this.isListening ? 'listening' : 'idle');
      return;
    }

    try {
      const formData = new FormData();
      formData.append('file', blob, 'mic_audio.webm');

      this.updateStatus('thinking');
      const r = await fetch('/api/transcribe', {
        method: 'POST',
        body: formData,
      });
      const d = await r.json();
      const text = (d.text || '').trim();

      if (!text) {
        this.updateStatus(this.pttActive || this.isListening ? 'listening' : 'idle');
        return;
      }

      console.log('[Lilly Voice] transcript:', text);
      this.onTranscript(text);

      // Feed into the existing VibeCode chat path — reuses all rendering
      if (typeof _doChat === 'function') {
        await _doChat(text);
      } else if (typeof sendChat === 'function') {
        // Fallback: type into the input and send
        const input = document.getElementById('chat-input');
        if (input) { input.value = text; autoResize(input); }
        await sendChat();
      }

      // Speak the reply — find the last assistant message
      const msgs = document.querySelectorAll('.cmsg.assistant');
      if (msgs.length > 0) {
        const lastMsg = msgs[msgs.length - 1];
        const replyText = lastMsg.textContent || lastMsg.innerText || '';
        if (replyText) {
          this.speak(replyText);
        }
      }
    } catch (err) {
      console.error('[Lilly Voice] transcribe error:', err);
      this.onError('Transcription error: ' + err.message);
      this.updateStatus(this.pttActive || this.isListening ? 'listening' : 'idle');
    }
  }

  // ── Text-to-Speech (browser Web Speech API) ─────────
  speak(text) {
    if (!text || !this.speechSynthesis) return;

    // Cancel any ongoing speech
    this.speechSynthesis.cancel();

    this.isSpeaking = true;
    this.updateStatus('speaking');

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.1;
    utterance.volume = 1.0;

    // Find a good voice
    const voices = this.speechSynthesis.getVoices();
    const preferred =
      voices.find((v) => v.name.includes('Samantha')) ||
      voices.find((v) => v.name.includes('Google')) ||
      voices.find((v) => v.lang.startsWith('en')) ||
      voices[0];
    if (preferred) utterance.voice = preferred;

    utterance.onend = () => {
      this.isSpeaking = false;
      this.updateStatus(this.isListening ? 'listening' : 'idle');
    };

    utterance.onerror = (e) => {
      this.isSpeaking = false;
      console.error('[Lilly Voice] TTS error:', e.error);
      this.updateStatus(this.isListening ? 'listening' : 'idle');
    };

    this.currentUtterance = utterance;
    this.speechSynthesis.speak(utterance);
  }

  stopSpeaking() {
    if (this.speechSynthesis) this.speechSynthesis.cancel();
    this.isSpeaking = false;
    this.updateStatus(this.isListening ? 'listening' : 'idle');
  }

  // ── UI Status ─────────
  updateStatus(status) {
    this.onStatus(status);
    const statusEl = document.getElementById('voice-status');
    if (statusEl) {
      statusEl.className = 'voice-status ' + status;
      const labels = {
        listening: '🎙 Listening…',
        recording: '🔴 Recording…',
        thinking: '🧠 Thinking…',
        speaking: '🔊 Speaking…',
        idle: 'Ready',
        disconnected: '🔴 Mic unavailable',
        error: '❌ Error',
      };
      statusEl.textContent = labels[status] || status;
    }
  }

  // ── Cleanup ─────────
  destroy() {
    this.stopSpeaking();
    this.stopContinuous();

    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      try { this.mediaRecorder.stop(); } catch (e) {}
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((t) => t.stop());
      this.mediaStream = null;
    }
  }
}

// ── Inject Voice UI into VibeCode ─────────
function injectVoiceUI() {
  const style = document.createElement('style');
  style.textContent = `
    #voice-controls {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
    }
    .voice-btn {
      background: var(--frost-surface, rgba(255,255,255,0.08));
      border: 1px solid var(--frost-border, rgba(167,139,250,0.15));
      color: var(--text-dim, #aaa);
      border-radius: 10px;
      width: 36px;
      height: 36px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 16px;
      transition: all .2s;
      position: relative;
    }
    .voice-btn:hover {
      background: rgba(167,139,250,.15);
      color: var(--accent, #a78bfa);
      border-color: rgba(167,139,250,.3);
    }
    .voice-btn.active {
      background: rgba(239,68,68,.15);
      color: #ef4444;
      border-color: rgba(239,68,68,.3);
      animation: pulse 1s infinite;
    }
    .voice-btn.speaking {
      background: rgba(59,130,246,.15);
      color: #3b82f6;
      border-color: rgba(59,130,246,.3);
    }
    #voice-status {
      font-size: 10px;
      color: var(--text-dim, #aaa);
      white-space: nowrap;
      min-width: 80px;
    }
    #voice-status.listening { color: #ef4444; }
    #voice-status.speaking { color: #3b82f6; }
    #voice-status.thinking { color: #f59e0b; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  `;
  document.head.appendChild(style);

  // Only inject if not already present
  if (document.getElementById('voice-controls')) return;

  const chatInputRow = document.getElementById('chat-input-row');
  if (chatInputRow) {
    const voiceControls = document.createElement('div');
    voiceControls.id = 'voice-controls';
    voiceControls.innerHTML = `
      <button id="voice-mic" class="voice-btn" onclick="toggleMic()" title="Voice input — hold SPACE for push-to-talk, click to toggle listening">
        🎤
      </button>
      <div id="voice-visualizer">
        <div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div>
      </div>
      <div id="voice-status" class="voice-status">Ready</div>
    `;

    // Insert before the send button
    const sendBtn = document.getElementById('chat-send');
    if (sendBtn) {
      chatInputRow.insertBefore(voiceControls, sendBtn);
    } else {
      chatInputRow.appendChild(voiceControls);
    }
  }
}

// ── Global Voice Functions ─────────
let lillyVoice = null;
let voiceActive = false; // continuous listening mode
let pttHeld = false;     // push-to-talk held

function initVoice() {
  injectVoiceUI();

  lillyVoice = new LillyVoiceEngine({
    onTranscript: (text) => {
      // Type the transcript into the chat input for visibility
      const input = document.getElementById('chat-input');
      if (input) { input.value = text; autoResize(input); }
      // Clear after feeding to _doChat
      setTimeout(() => { if (input) input.value = ''; autoResize(input); }, 100);
    },
    onResponse: (text, done) => {
      if (done && text) { lillyVoice.speak(text); }
    },
    onStatus: (status) => {
      // visualizer feedback
      const visualizer = document.getElementById('voice-visualizer');
      if (status === 'listening' || status === 'recording') {
        if (visualizer) visualizer.style.opacity = '1';
      } else {
        if (visualizer) visualizer.style.opacity = '0.3';
      }
    },
    onError: (err) => {
      console.error('[Lilly Voice] error:', err);
      const statusEl = document.getElementById('voice-status');
      if (statusEl) statusEl.textContent = '❌ ' + err;
    },
  });

  // Keyboard: hold SPACE for push-to-talk when not in an input
  document.addEventListener('keydown', (e) => {
    // Ignore if typing in an input/textarea
    const tag = e.target.tagName;
    if (tag === 'TEXTAREA' || tag === 'INPUT') return;
    if (e.code === 'Space') {
      e.preventDefault();
      if (!lillyVoice) return;
      if (pttHeld) return; // already held
      pttHeld = true;
      // If currently speaking, stop speech (barge-in)
      if (lillyVoice.isSpeaking) {
        lillyVoice.stopSpeaking();
      }
      lillyVoice.startPtt();
    }
    // Escape to stop speech
    if (e.code === 'Escape' && lillyVoice.isSpeaking) {
      lillyVoice.stopSpeaking();
    }
  });

  document.addEventListener('keyup', (e) => {
    const tag = e.target.tagName;
    if (tag === 'TEXTAREA' || tag === 'INPUT') return;
    if (e.code === 'Space' && pttHeld) {
      e.preventDefault();
      pttHeld = false;
      if (lillyVoice) lillyVoice.endPtt();
    }
  });

  console.log('[Lilly Voice] engine ready — hold SPACE to speak, click mic to toggle listening');
}

function toggleMic() {
  if (!lillyVoice) { initVoice(); return; }

  voiceActive = !voiceActive;
  const btn = document.getElementById('voice-mic');
  const visualizer = document.getElementById('voice-visualizer');

  if (voiceActive) {
    btn.classList.add('active');
    btn.title = 'Click to stop listening';
    if (visualizer) visualizer.style.display = 'flex';
    // Stop any ongoing speech first
    lillyVoice.stopSpeaking();
    if (lillyVoice.isListening || lillyVoice.pttActive) {
      lillyVoice.stopContinuous();
    } else {
      if (!lillyVoice.mediaStream) {
        lillyVoice.requestMic().then(() => { if (voiceActive) lillyVoice.startContinuous(); });
      } else {
        lillyVoice.startContinuous();
      }
    }
  } else {
    btn.classList.remove('active');
    btn.title = 'Voice input — hold SPACE for push-to-talk';
    lillyVoice.stopContinuous();
    if (visualizer) visualizer.style.display = 'flex';
  }
}

// Auto-initialize when page loads
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVoice);
} else {
  initVoice();
}

// Export for use in other scripts
window.LillyVoiceEngine = LillyVoiceEngine;
window.initVoice = initVoice;
window.toggleMic = toggleMic;
