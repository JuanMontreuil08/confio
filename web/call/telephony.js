// One browser session and one outbound call. No automatic redial.
export class BrowserCall {
  constructor({onState, onLog, onRemote, remoteElement, sdk = window.TelnyxWebRTC.TelnyxRTC,
    media = navigator.mediaDevices, request = window.fetch.bind(window)}) {
    Object.assign(this, {onState, onLog, onRemote, remoteElement, sdk, media, request});
    this.generation = 0;
    this.busy = false;
  }

  async start() {
    if (this.busy) return;
    this.busy = true;
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    this.abort = new AbortController();
    this.onState("connecting");
    this.onLog("Microphone", "Requesting access");
    try {
      const stream = await this.media.getUserMedia({audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      }, video: false});
      if (!current()) { stream.getTracks().forEach(track => track.stop()); return; }
      this.local = stream;
      this.onLog("Microphone", "Ready · laptop input");
      const response = await this.request("/api/session", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: "{}", signal: this.abort.signal, cache: "no-store",
      });
      if (!current()) return;
      if (!response.ok) throw new Error("session");
      const session = await response.json();
      if (!current()) return;
      this.onLog("Telnyx", "Authenticating browser session");
      const client = this.client = new this.sdk({login_token: session.login_token});
      client.remoteElement = this.remoteElement;
      let dialed = false;
      client.on("telnyx.ready", () => {
        if (!current() || dialed) return;
        dialed = true;
        clearTimeout(this.connectTimeout);
        this.onLog("Telnyx", `Dialing ${session.to}`);
        this.onState("dialing");
        try {
          this.call = client.newCall({destinationNumber: session.to,
            callerNumber: session.from, callerName: "Confio",
            audio: true, video: false, localStream: this.local,
            remoteElement: this.remoteElement});
          this.ringTimeout = setTimeout(() => {
            this.onLog("Call", "No answer within 45 seconds");
            this.stop();
          }, 45000);
        } catch { this.fail("Could not place the call. Check Telnyx configuration."); }
      });
      client.on("telnyx.notification", notification => {
        if (!current() || notification.type !== "callUpdate") return;
        const call = notification.call;
        if (call.direction === "inbound") { call.hangup(); return; }
        if (this.call && call.id !== this.call.id) return;
        this.call = call;
        if (call.state !== this.lastState) {
          this.onLog("Telnyx", call.state);
          this.lastState = call.state;
        }
        if (call.state === "active") {
          clearTimeout(this.ringTimeout);
          if (!this.answered) {
            this.answered = true;
            this.onState("active");
            this.onLog("Audio", "Connected · microphone → phone · phone → browser");
            this.callTimeout = setTimeout(() => {
              this.onLog("Call", "10-minute test limit reached"); this.stop();
            }, 600000);
          }
        }
        if (["hangup", "destroy", "purge"].includes(call.state)) {
          this.onLog("Call", "Ended by phone or network");
          this.cleanup();
        }
      });
      client.on("telnyx.error", () => {
        if (current()) this.fail("Telnyx connection failed. Check the portal before retrying.");
      });
      client.on("telnyx.socket.close", () => {
        if (current()) this.fail("Connection lost. Check Telnyx before retrying.");
      });
      this.connectTimeout = setTimeout(() => {
        if (current()) this.fail("Telnyx authentication timed out.");
      }, 20000);
      this.mediaPoll = setInterval(() => {
        const stream = this.call?.remoteStream || this.remoteElement.srcObject;
        if (current() && stream?.getAudioTracks().length && stream !== this.remote) {
          this.remote = stream;
          this.onRemote(stream);
          this.onLog("Audio", "Remote voice connected to waveform");
        }
      }, 200);
      client.connect();
    } catch (error) {
      if (!current()) return;
      this.fail(error.name === "NotAllowedError" ? "Microphone access denied. Allow it in your browser."
        : error.name === "NotFoundError" ? "No microphone found on this laptop."
        : "Could not start the call. Check the local server and microphone.");
    }
  }

  mute(value) {
    if (!this.busy) return;
    // Keep the desired state in the SDK as well as the current local tracks.
    if (value) this.call?.muteAudio(); else this.call?.unmuteAudio();
    this.local?.getAudioTracks().forEach(track => { track.enabled = !value; });
    this.onLog("Microphone", value ? "Muted · you can still hear the caller" : "Unmuted");
  }

  fail(message) {
    this.onLog("Error", message);
    this.stop(false);
  }

  async stop(log = true) {
    if (!this.busy) return;
    ++this.generation; // Invalidate pending permission/token requests and SDK callbacks.
    this.onState("ending");
    if (log) this.onLog("Call", "Ending call");
    try { await this.call?.hangup(); } catch {
      this.onLog("Error", "Hangup could not be confirmed. Check Telnyx.");
    } finally { this.cleanup(); }
  }

  cleanup() {
    ++this.generation;
    this.abort?.abort();
    clearTimeout(this.connectTimeout); clearTimeout(this.ringTimeout); clearTimeout(this.callTimeout);
    clearInterval(this.mediaPoll);
    this.local?.getTracks().forEach(track => track.stop());
    this.call?.localStream?.getTracks().forEach(track => track.stop());
    const client = this.client;
    this.client = this.call = this.local = this.remote = null;
    this.busy = this.answered = false;
    this.lastState = null;
    if (client) {
      for (const event of ["telnyx.ready", "telnyx.notification", "telnyx.error", "telnyx.socket.close"]) client.off(event);
      client.disconnect();
    }
    this.remoteElement.srcObject = null;
    this.onRemote(null);
    this.onState("idle");
  }
}
