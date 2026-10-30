import {readFile} from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../../web/call/telephony.js', import.meta.url), 'utf8');
const {BrowserCall} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

function fixture(overrides = {}) {
  const track = {enabled: true, stopped: false, stop() { this.stopped = true; }};
  const local = {getTracks: () => [track], getAudioTracks: () => [track]};
  const remote = {getAudioTracks: () => [{}]};
  const states = [], logs = [], remotes = [];
  class SDK {
    static instance;
    constructor() { SDK.instance = this; this.events = {}; this.calls = []; }
    on(name, fn) { this.events[name] = fn; }
    off(name) { delete this.events[name]; }
    emit(name, value) { this.events[name]?.(value); }
    connect() {}
    disconnect() { this.disconnected = true; }
    newCall(options) {
      this.calls.push(options);
      return this.call = {id: 'one', direction: 'outbound', localStream: local, remoteStream: remote,
        hangups: 0, hangup() { this.hangups++; },
        muteAudio() { track.enabled = false; }, unmuteAudio() { track.enabled = true; }};
    }
    update(state) { this.call.state = state; this.emit('telnyx.notification', {type: 'callUpdate', call: this.call}); }
  }
  const phone = new BrowserCall({sdk: SDK, media: {getUserMedia: async () => local},
    request: async () => ({ok: true, json: async () => ({login_token: 'fake', from: '+16064318045', to: '+51999999999'})}),
    remoteElement: {srcObject: null}, onState: state => states.push(state),
    onLog: (...log) => logs.push(log), onRemote: stream => remotes.push(stream), ...overrides});
  return {phone, SDK, track, local, remote, states, logs, remotes};
}

test('one click dials once, local mute preserves remote audio, stop hangs up and releases microphone', async () => {
  const f = fixture();
  try {
    await f.phone.start(); await f.phone.start();
    const client = f.SDK.instance;
    client.emit('telnyx.ready'); client.emit('telnyx.ready');
    assert.equal(client.calls.length, 1);
    assert.equal(client.calls[0].destinationNumber, '+51999999999');
    assert.equal(client.calls[0].localStream, f.local);
    assert.equal(client.calls[0].video, false);
    client.update('active');
    await new Promise(resolve => setTimeout(resolve, 230));
    assert.equal(f.remotes[0], f.remote);
    f.phone.mute(true); assert.equal(f.track.enabled, false);
    assert.equal(f.phone.remote, f.remote);
    f.phone.mute(false); assert.equal(f.track.enabled, true);
    const call = client.call;
    await f.phone.stop();
    assert.equal(call.hangups, 1);
    assert.equal(f.track.stopped, true);
    assert.equal(client.disconnected, true);
    assert.equal(f.states.at(-1), 'idle');
  } finally { f.phone.cleanup(); }
});

test('remote hangup returns idle without redial and releases microphone', async () => {
  const f = fixture();
  await f.phone.start();
  const client = f.SDK.instance;
  client.emit('telnyx.ready'); client.update('active'); client.update('hangup');
  assert.equal(client.calls.length, 1);
  assert.equal(f.states.at(-1), 'idle');
  assert.equal(f.track.stopped, true);
});

test('cancel during microphone permission cannot dial later', async () => {
  let resolve;
  const permission = new Promise(done => { resolve = done; });
  const f = fixture({media: {getUserMedia: () => permission}});
  const pending = f.phone.start();
  await f.phone.stop(); resolve(f.local); await pending;
  assert.equal(f.SDK.instance, undefined);
  assert.equal(f.track.stopped, true);
});

test('failed authentication stops microphone and does not leak provider data', async () => {
  const f = fixture({request: async () => ({ok: false})});
  await f.phone.start();
  assert.equal(f.track.stopped, true);
  assert.equal(f.SDK.instance, undefined);
  assert.equal(f.states.at(-1), 'idle');
});

test('socket loss does not redial', async () => {
  const f = fixture(); await f.phone.start();
  const client = f.SDK.instance; client.emit('telnyx.ready'); client.update('active');
  client.emit('telnyx.socket.close');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(client.calls.length, 1);
  assert.equal(client.call.hangups, 1);
  assert.equal(f.track.stopped, true);
});
