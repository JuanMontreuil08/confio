const get = id => document.getElementById(id);
const talk = get('talk');
let busy = false, held = false, recording = null, webcamStream = null;

function wav(chunks, rate) {
  const frames = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const count = Math.floor(frames * 16000 / rate);
  const input = new Float32Array(frames);
  let offset = 0;
  for (const chunk of chunks) { input.set(chunk, offset); offset += chunk.length; }
  const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const tag = (pos, text) => [...text].forEach((c, i) => view.setUint8(pos + i, c.charCodeAt(0)));
  tag(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); tag(8, 'WAVE');
  tag(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); tag(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const sample = Math.max(-1, Math.min(1, input[Math.floor(i * rate / 16000)]));
    view.setInt16(44 + i * 2, Math.round(sample * 32767), true);
  }
  return buffer;
}

async function submit(body, type = 'text/plain') {
  if (busy || held || !body) return;
  busy = true; talk.disabled = true;
  get('status').textContent = '';
  const turn = document.createElement('div');
  turn.className = 'conversation-turn';
  const request = document.createElement('p');
  request.className = 'conversation-user';
  request.textContent = 'You · ' + (type === 'audio/wav' ? 'Voice message' : type === 'image/jpeg' ? 'Capture webcam photo' : body);
  const responseLine = document.createElement('p');
  responseLine.className = 'conversation-agent typing';
  responseLine.setAttribute('aria-label', 'Confio is preparing a response');
  responseLine.innerHTML = 'Confio <span class="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
  turn.append(request, responseLine);
  get('history').append(turn);
  turn.scrollIntoView({block:'nearest'});
  get('audio').pause();
  try {
    const response = await fetch('/turn', {method:'POST', headers:{'Content-Type':type}, body});
    if (!response.ok) throw Error('The demo is busy or the request was too large. Try again.');
    const result = await response.json();
    if (result.error) throw Error(result.error);
    request.textContent = 'You · ' + result.transcript;
    responseLine.textContent = 'Confio · ' + result.reply;
    if (result.restored) {
      const before = get('before-image');
      const after = get('after').querySelector('img');
      before.src = result.webcam_original ? 'data:image/jpeg;base64,' + result.webcam_original : '/sample.jpg';
      get('before-caption').textContent = result.webcam_original ? 'Before · webcam' : 'Before · camera sample';
      after.src = 'data:image/jpeg;base64,' + result.restored;
      after.classList.toggle('webcam-image', Boolean(result.webcam_original));
      get('after').hidden = false;
    }
    for (const event of result.events) {
      if (event.kind === 'take_photo') get('photo-status').textContent = 'Restored with Muse Image.';
      if (event.kind === 'save_photo') get('photo-status').textContent = 'Restored photo saved in this demo session.';
      if (event.kind === 'call') {
        get('phone-status').textContent = 'Incoming call for ' + event.text;
        get('incoming-call').hidden = false;
        get('hangup').disabled = false;
        document.body.dataset.call = 'active';
      }
      if (event.kind === 'sms') {
        const bubble = document.createElement('div');
        bubble.className = 'bubble'; bubble.textContent = event.text;
        if (event.image) {
          const image = document.createElement('img');
          image.src = 'data:image/jpeg;base64,' + event.image; image.alt = 'Shared photo';
          image.classList.toggle('webcam-image', Boolean(event.webcam)); bubble.append(image);
        }
        get('inbox').prepend(bubble);
      }
    }
    const player = get('audio');
    player.hidden = !result.audio;
    if (result.audio) {
      player.src = 'data:audio/wav;base64,' + result.audio;
      player.play().catch(() => {});
    }
    get('status').textContent = result.audio_error ? 'Voice unavailable · read the response below' :
      '';
  } catch (error) {
    responseLine.textContent = 'Confio · ' + error.message;
  } finally {
    responseLine.classList.remove('typing');
    responseLine.removeAttribute('aria-label');
    busy = false; talk.disabled = false;
  }
}

async function start() {
  if (busy || held || recording) return;
  held = true;
  get('status').textContent = 'Opening microphone…';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    if (!held) {
      stream.getTracks().forEach(track => track.stop());
      get('status').textContent = 'Microphone ready · hold to talk again';
      return;
    }
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    recording = {context, stream, processor, chunks, timer:setTimeout(stop, 20000)};
    processor.onaudioprocess = event => {
      const samples = event.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(samples));
      const rms = Math.sqrt(samples.reduce((sum,x) => sum + x*x, 0) / samples.length);
      get('meter').firstElementChild.style.width = Math.min(100, rms * 600) + '%';
    };
    source.connect(processor); processor.connect(context.destination);
    talk.classList.add('listening'); get('status').textContent = 'Listening…';
  } catch {
    held = false;
    get('status').textContent = 'Microphone unavailable · type or choose an example';
  }
}

function stop() {
  held = false;
  if (!recording) return;
  const {context, stream, processor, chunks, timer} = recording;
  recording = null; clearTimeout(timer);
  stream.getTracks().forEach(track => track.stop());
  processor.disconnect(); context.close().catch(() => {});
  talk.classList.remove('listening');
  get('meter').firstElementChild.style.width = '0';
  if (chunks.length) submit(wav(chunks, context.sampleRate), 'audio/wav');
  else get('status').textContent = 'Hold the button a little longer';
}

talk.addEventListener('pointerdown', event => {
  talk.setPointerCapture(event.pointerId); start();
});
talk.addEventListener('pointerup', stop);
talk.addEventListener('pointercancel', stop);
talk.addEventListener('keydown', event => {
  if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); if (!event.repeat) start(); }
});
talk.addEventListener('keyup', event => { if (event.key === ' ' || event.key === 'Enter') stop(); });
window.addEventListener('blur', stop);
get('typed').addEventListener('submit', event => { event.preventDefault(); submit(get('request').value.trim()); });
document.querySelectorAll('[data-request]').forEach(button => button.addEventListener('click', () => submit(button.dataset.request)));
async function callAction(path, outcome) {
  const buttons = ['accept-call', 'decline-call', 'hangup'].map(get);
  buttons.forEach(button => { button.disabled = true; });
  try {
    const response = await fetch(path, {method:'POST'});
    if (!response.ok) throw Error('Call update failed');
    const state = await response.json();
    get('incoming-call').hidden = true;
    get('phone-status').textContent = state.accepted || state.cancelled ? outcome : 'Call no longer active';
    if (state.accepted) get('hangup').disabled = false;
    else delete document.body.dataset.call;
  } catch {
    get('phone-status').textContent = 'Unable to update the call · try again';
    get('hangup').disabled = false;
  } finally {
    buttons.slice(0, 2).forEach(button => { button.disabled = false; });
  }
}

get('hangup').addEventListener('click', () => callAction('/cancel', 'Simulated call ended'));
get('decline-call').addEventListener('click', () => callAction('/cancel', 'Simulated call declined'));
get('accept-call').addEventListener('click', () => callAction('/accept-call', 'Simulated call connected'));

function stopWebcam() {
  if (webcamStream) webcamStream.getTracks().forEach(track => track.stop());
  webcamStream = null;
  const preview = get('camera-preview');
  preview.srcObject = null;
  preview.hidden = true;
  get('capture-webcam').hidden = true;
  get('capture-webcam').disabled = true;
  get('webcam').textContent = 'Use webcam';
}

get('webcam').addEventListener('click', async () => {
  if (webcamStream) { stopWebcam(); return; }
  const toggle = get('webcam');
  const preview = get('camera-preview');
  toggle.disabled = true;
  try {
    webcamStream = await navigator.mediaDevices.getUserMedia({video:true, audio:false});
    preview.srcObject = webcamStream;
    preview.hidden = false;
    get('capture-webcam').hidden = false;
    await preview.play();
    get('capture-webcam').disabled = false;
    toggle.textContent = 'Stop webcam';
  } catch {
    stopWebcam();
    get('photo-status').textContent = 'Webcam unavailable. You can still use the sample photo.';
  } finally {
    toggle.disabled = false;
  }
});

get('capture-webcam').addEventListener('click', () => {
  const preview = get('camera-preview');
  if (busy || held || !preview.videoWidth) return;
  const canvas = document.createElement('canvas');
  const scale = Math.min(1, 1280 / Math.max(preview.videoWidth, preview.videoHeight));
  canvas.width = Math.round(preview.videoWidth * scale);
  canvas.height = Math.round(preview.videoHeight * scale);
  canvas.getContext('2d').drawImage(preview, 0, 0, canvas.width, canvas.height);
  stopWebcam();
  canvas.toBlob(photo => {
    if (photo) submit(photo, 'image/jpeg');
    else get('photo-status').textContent = 'Could not capture the photo. Try again.';
  }, 'image/jpeg', .88);
});
window.addEventListener('pagehide', stopWebcam);
