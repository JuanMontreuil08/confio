"use strict";

import {BrowserCall} from "./telephony.js";

const $ = (id) => document.getElementById(id);
const start = $("start"), stop = $("stop"), mute = $("mute");
const canvas = $("wave"), ctx = canvas.getContext("2d");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
document.documentElement.dataset.voiceSdk = typeof window.TelnyxWebRTC?.TelnyxRTC === "function" ? "ready" : "missing";
let active = false, muted = false, startedAt = 0, callState = "idle";
let live = false, phone = null, audioContext = null, analyser = null, remoteSource = null;
let audioSamples = null, remoteLevel = 0;
let waveWidth = 0, waveHeight = 0, frame = 0, lastTime = 0;
let intensity = .07, phase = 0;

function render() {
  document.body.dataset.call = active ? "active" : "idle";
  document.body.dataset.muted = String(muted);
  stop.disabled = !active || callState === "ending";
  mute.disabled = callState !== "active";
  start.disabled = active;
  start.querySelector("span").textContent = active ? "Call in progress" : "Talk with your agent";
  mute.setAttribute("aria-pressed", String(muted));
  mute.setAttribute("aria-label", muted ? "Unmute microphone" : "Mute microphone");
  $("mute-label").textContent = muted ? "Unmute" : "Mute";
  const labels = {idle: "Call ended", connecting: "Connecting", dialing: "Calling", active: "Connected", ending: "Ending call"};
  $("connection-label").textContent = labels[callState];
  $("call-status").textContent = labels[callState];
  requestDraw();
}

function logEvent(source, message) {
  const entry = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString("en-GB", {hour12: false});
  const label = document.createElement("strong"); label.textContent = source;
  const detail = document.createElement("span"); detail.textContent = message;
  entry.append(time, label, detail);
  if (source === "Error") entry.classList.add("log-error");
  $("logs").append(entry);
  while ($("logs").children.length > 80) $("logs").firstElementChild.remove();
  $("logs").scrollTop = $("logs").scrollHeight;
}

function workspace() {
  const card = document.querySelector(".call-card");
  const before = card.getBoundingClientRect();
  document.body.dataset.workspace = "true";
  $("debug-panel").hidden = false;
  const after = card.getBoundingClientRect();
  if (!reducedMotion.matches && before.width !== after.width) {
    card.animate([{transformOrigin: "top left", transform: `translate(${before.x-after.x}px, ${before.y-after.y}px) scale(${before.width/after.width}, ${before.height/after.height})`},
      {transformOrigin: "top left", transform: "none"}], {duration: 280, easing: "cubic-bezier(.4,0,.2,1)"});
  }
}

function stateChanged(state) {
  callState = state;
  active = state !== "idle";
  if (state === "active") startedAt = performance.now();
  if (!active) { muted = false; start.focus({preventScroll: true}); }
  render();
}

function remoteAudio(stream) {
  remoteSource?.disconnect(); analyser?.disconnect();
  remoteSource = analyser = audioSamples = null;
  remoteLevel = 0;
  if (!stream || !audioContext) return;
  remoteSource = audioContext.createMediaStreamSource(stream);
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 1024;
  audioSamples = new Float32Array(analyser.fftSize);
  // One playback path only. The SDK's audio element remains muted.
  remoteSource.connect(analyser);
  analyser.connect(audioContext.destination);
  audioContext.resume().catch(() => logEvent("Error", "Audio is suspended. Click the page to resume playback."));
}

start.addEventListener("click", async () => {
  if (active) return;
  workspace();
  $("logs").replaceChildren();
  $("timer").textContent = "00:00";
  muted = false;
  if (!live) {
    $("connection-label").textContent = "Preview";
    logEvent("Setup", "Preview mode. Start the server with --live to make a real call.");
    return;
  }
  try {
    audioContext ||= new AudioContext();
    await audioContext.resume();
    phone ||= new BrowserCall({onState: stateChanged, onLog: logEvent, onRemote: remoteAudio,
      remoteElement: $("remote-audio")});
    await phone.start();
  } catch { logEvent("Error", "Audio could not start. Check browser support and microphone permissions."); stateChanged("idle"); }
});
stop.addEventListener("click", () => phone?.stop());
mute.addEventListener("click", () => { muted = !muted; phone?.mute(muted); render(); });
document.addEventListener("click", () => {
  if (audioContext?.state === "suspended") audioContext.resume().catch(() => {});
});
window.addEventListener("pagehide", () => { phone?.stop(); });
setInterval(() => {
  if (callState !== "active") return;
  const seconds = Math.floor((performance.now() - startedAt) / 1000);
  $("timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}, 250);

function resize() {
  const rect = canvas.getBoundingClientRect();
  waveWidth = rect.width; waveHeight = rect.height;
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(waveWidth * ratio);
  canvas.height = Math.round(waveHeight * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  requestDraw();
}

function requestDraw() {
  cancelAnimationFrame(frame);
  lastTime = performance.now();
  draw(lastTime);
}

// Remote audio RMS drives intensity; microphone mute never changes these waves.
function draw(now) {
  const dt = Math.min((now - lastTime) / 1000, .05);
  lastTime = now;
  if (analyser && audioSamples) {
    analyser.getFloatTimeDomainData(audioSamples);
    remoteLevel = Math.sqrt(audioSamples.reduce((sum, value) => sum + value * value, 0) / audioSamples.length);
  } else remoteLevel = 0;
  const target = reducedMotion.matches ? .02 : Math.min(1, remoteLevel * 7);
  intensity = reducedMotion.matches ? target : intensity + (target - intensity) * (1 - Math.exp(-dt * 7));
  phase += dt * (.45 + intensity * 1.8);
  const time = reducedMotion.matches ? 1.2 : phase;
  const amplitude = .7 + intensity * 50;
  const center = waveHeight / 2;
  const colors = ["88,90,238", "47,142,248", "130,91,241", "63,170,239", "161,125,248", "100,116,246"];
  ctx.clearRect(0, 0, waveWidth, waveHeight);
  for (let layer = 0; layer < colors.length; layer++) {
    const points = [];
    for (let step = 0; step <= 160; step++) {
      const x = step / 160;
      const envelope = Math.pow(Math.sin(x * Math.PI), 2.6);
      const carrier = Math.sin(x * Math.PI * (3.3 + layer * .27) - time * (1.1 + layer * .07) + layer * .65);
      points.push([x * waveWidth, carrier * envelope * amplitude * (1 - layer * .065)]);
    }
    const gradient = ctx.createLinearGradient(0, 0, waveWidth, 0);
    const opacity = .25 + intensity * .55;
    gradient.addColorStop(0, `rgba(${colors[layer]},0)`);
    gradient.addColorStop(.28, `rgba(${colors[layer]},${opacity * .6})`);
    gradient.addColorStop(.5, `rgba(${colors[layer]},${opacity})`);
    gradient.addColorStop(.72, `rgba(${colors[layer]},${opacity * .6})`);
    gradient.addColorStop(1, `rgba(${colors[layer]},0)`);
    // A second contour gives each ribbon a soft, translucent body.
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x, center + y) : ctx.moveTo(x, center + y));
    ctx.strokeStyle = gradient; ctx.lineWidth = 1.2 + intensity * .5;
    ctx.stroke();
    for (let i = points.length - 1; i >= 0; i--) {
      const [x, y] = points[i];
      ctx.lineTo(x, center + y * -.45);
    }
    ctx.closePath(); ctx.fillStyle = gradient;
    ctx.globalAlpha = .08 + intensity * .14; ctx.fill(); ctx.globalAlpha = 1;
  }
  if (!reducedMotion.matches && !document.hidden) frame = requestAnimationFrame(draw);
}
new ResizeObserver(resize).observe(canvas);
reducedMotion.addEventListener("change", requestDraw);
document.addEventListener("visibilitychange", requestDraw);

let previousEvent = null;
async function pollDevice() {
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) throw new Error("Preview server unavailable");
    const status = await response.json();
    if (previousEvent !== null && status.call_event > previousEvent) {
      $("device-status").textContent = "Call button received. Select Talk with your agent to begin.";
    }
    previousEvent = status.call_event;
  } catch {
    // The frontend remains usable without the serial bridge.
  } finally { setTimeout(pollDevice, 1500); }
}
pollDevice();

fetch("/api/config", {cache: "no-store"}).then(response => response.json()).then(config => {
  live = config.live;
}).catch(() => {});
