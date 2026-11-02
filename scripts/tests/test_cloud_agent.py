"""Cloud transport regressions. Fake keys/providers only; never reads .env."""
import asyncio
import base64
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud_agent as cloud


def frame(kind, data=b""):
    return struct.pack("<4sBI", b"MIC1", ord(kind), len(data)) + data


def config():
    values = {k: "fake" for k in cloud.REQUIRED}
    values.update(CONFIO_DEVICE_TOKEN="d" * 40, CONFIO_CAMERA_TOKEN="c" * 40,
                  CONFIO_ADMIN_TOKEN="a" * 40, CONTACTS_JSON=json.dumps({"Example": "+51999999999"}),
                  SUPABASE_URL="https://example.supabase.co", TELNYX_FROM_NUMBER="+12025550123",
                  CONFIO_PUBLIC_URL="https://example.modal.run")
    key = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    values["TELNYX_PUBLIC_KEY"] = base64.b64encode(key).decode()
    return values


class FakeSocket:
    closed = False
    def __init__(self):
        self.sent = []
    async def send_bytes(self, data):
        self.sent.append(data)
    async def send_str(self, data):
        self.sent.append(data)
    async def close(self, **kwargs):
        self.closed = True


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_press_release_preserves_channel_and_plays_with_acks(self):
        socket = FakeSocket()
        link = cloud.WifiVoiceLink(socket, asyncio.get_running_loop())
        link.feed(frame("S"))
        link.feed(frame("D", struct.pack("<4h", 100, 0, 200, 0)))
        link.feed(frame("F"))
        self.assertEqual(await asyncio.to_thread(link.record), struct.pack("<2h", 100, 200))
        self.assertEqual(socket.sent[0], cloud.pack("B"))
        playing = asyncio.create_task(asyncio.to_thread(link.play, b"\x01\x00" * 20000))
        seen = 1
        maximum = 0
        while not playing.done():
            await asyncio.sleep(0.001)
            maximum = max(maximum, link.sent - link.acked)
            while seen < len(socket.sent):
                link.feed(frame("A"))
                seen += 1
        await playing
        self.assertLessEqual(maximum, cloud.WINDOW)
        self.assertEqual(socket.sent[-1], cloud.pack("E"))
        self.assertEqual(b"".join(p[9:] for p in socket.sent[1:-1]), b"\x01\x00" * 20000)

    async def test_disconnect_aborts_recording_without_replaying_partial_audio(self):
        link = cloud.WifiVoiceLink(FakeSocket(), asyncio.get_running_loop())
        link.feed(frame("S"))
        task = asyncio.create_task(asyncio.to_thread(link.record))
        await asyncio.sleep(0.01)
        link.close()
        with self.assertRaises(ConnectionError):
            await task

    async def test_rejects_malformed_frames_and_bounds_live_audio(self):
        link = cloud.WifiVoiceLink(FakeSocket(), asyncio.get_running_loop())
        for data in [b"short", frame("S", b"xx"), frame("D", b"xx"), frame("Q"), frame("D", b"x" * 1028)]:
            with self.assertRaises(ValueError):
                link.feed(data)
        link.sent = cloud.WINDOW
        await asyncio.to_thread(link.stream, b"\x00\x00" * 512)
        self.assertFalse(link.socket.sent)
        link.feed(frame("H"))
        self.assertEqual(link.poll()[0], "H")

    async def test_camera_correlates_capture_and_ignores_late_response(self):
        camera = cloud.CameraLink(asyncio.get_running_loop())
        camera.socket = FakeSocket()
        shot = asyncio.create_task(camera.capture_async())
        await asyncio.sleep(0)
        camera.receive(b"0" * 32 + b"stale")
        self.assertFalse(shot.done())
        camera.receive(camera.request_id.encode() + b"jpeg")
        self.assertEqual(await shot, b"jpeg")
        camera.receive(b"0" * 32 + b"late")
        next_shot = asyncio.create_task(camera.capture_async())
        await asyncio.sleep(0)
        camera.disconnect()
        with self.assertRaisesRegex(ValueError, "disconnected"):
            await next_shot


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = cloud.CloudAgent(config())
        self.client = TestClient(TestServer(self.gateway.application()))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close()

    async def test_private_routes_reject_missing_and_wrong_role_tokens(self):
        for path in ["/device", "/camera", "/admin/status"]:
            response = await self.client.get(path)
            self.assertEqual(response.status, 401)
        response = await self.client.get("/camera", headers={"Authorization": "Bearer " + "d" * 40})
        self.assertEqual(response.status, 401)
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)

    async def test_deployment_waits_for_recording_turn_or_call(self):
        headers = {"Authorization": "Bearer " + "a" * 40}
        link = cloud.WifiVoiceLink(FakeSocket(), asyncio.get_running_loop())
        self.gateway.device = link
        link.busy = True
        response = await self.client.post("/admin/drain", headers=headers)
        self.assertEqual(response.status, 409)
        self.assertFalse(self.gateway.draining)
        link.busy = False
        response = await self.client.post("/admin/drain", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertTrue(link.closed.is_set())
        self.assertTrue(self.gateway.draining)
        response = await self.client.post("/admin/undrain", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertFalse(self.gateway.draining)

    async def test_unsigned_telnyx_event_is_rejected(self):
        response = await self.client.post("/webhooks/telnyx", json={"data": {}})
        self.assertEqual(response.status, 403)

    async def test_drain_rejects_work_continuing_after_disconnect(self):
        self.gateway.interaction_active = True
        self.gateway.device = None
        headers = {"Authorization": "Bearer " + "a" * 40}
        response = await self.client.post("/admin/drain", headers=headers)
        self.assertEqual(response.status, 409)
        response = await self.client.get("/admin/status", headers=headers)
        self.assertTrue((await response.json())["busy"])

    async def test_only_one_camera_socket_and_drain_forces_reconnect(self):
        headers = {"Authorization": "Bearer " + "c" * 40}
        camera = await self.client.ws_connect("/camera", headers=headers)
        with self.assertRaises(Exception) as rejected:
            await self.client.ws_connect("/camera", headers=headers)
        self.assertEqual(rejected.exception.status, 409)
        response = await self.client.post("/admin/drain", headers={"Authorization": "Bearer " + "a" * 40})
        self.assertEqual(response.status, 200)
        await camera.receive(timeout=2)
        await camera.close()

    async def test_provider_keys_are_required_and_tokens_must_be_distinct(self):
        values = config()
        del values["META_API_KEY"]
        with self.assertRaisesRegex(ValueError, "META_API_KEY"):
            cloud.CloudAgent(values)
        values = config()
        values["CONFIO_CAMERA_TOKEN"] = values["CONFIO_DEVICE_TOKEN"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            cloud.CloudAgent(values)

    async def test_idle_wifi_loss_does_not_restart_the_memory_expiration_clock(self):
        self.gateway.agent = SimpleNamespace(pending_call=None, mark_idle=Mock())
        link = cloud.WifiVoiceLink(FakeSocket(), asyncio.get_running_loop())
        link.record = Mock(side_effect=ConnectionError("Idle socket lost"))
        await asyncio.to_thread(self.gateway.run, link)
        self.gateway.agent.mark_idle.assert_not_called()

    async def test_finishing_previous_turn_keeps_a_new_press_busy(self):
        link = cloud.WifiVoiceLink(FakeSocket(), asyncio.get_running_loop())
        self.gateway.device = link
        link.feed(frame("S"))
        previous_press = link.latest_press
        link.feed(frame("S"))
        self.gateway.finish_turn(link, previous_press)
        self.assertTrue(self.gateway.interaction_active)
        self.gateway.finish_turn(link, link.latest_press)
        self.assertFalse(self.gateway.interaction_active)


if __name__ == "__main__":
    unittest.main()
