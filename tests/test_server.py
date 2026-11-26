"""
Server-level tests: slot/double-buffer invariants, concurrency, LAN auth guards, scheduler,
on-device Flash schedule, updater integrity and graceful BLE shutdown.

Runs fully offline against MockTransport + aiohttp's TestClient (no BLE, no network).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Isolate all user data BEFORE importing the server (DATA_DIR is resolved at import time)
_DATA_DIR = Path(tempfile.mkdtemp(prefix="loy_test_data_"))
os.environ["LOY_DATA_DIR"] = str(_DATA_DIR)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import server as S  # noqa: E402
from app import update_signing as U  # noqa: E402
from app.security import LanAuthStore, LoginRateLimiter, host_allowed, same_origin  # noqa: E402
from app.storage import read_json  # noqa: E402
from protocol import TLV, MockTransport, PacketParser  # noqa: E402
from protocol.framing import decode_tlv_length  # noqa: E402

LIVE = set(S.LIVE_SLOTS)


# ---------------------------------------------------------------------------
# A mock panel that models what is VISIBLE and flags writes to the visible program
# ---------------------------------------------------------------------------

def _tlvs(payload: bytes) -> List[tuple]:
    out, i = [], 0
    while i < len(payload):
        tag = payload[i]
        length, used = decode_tlv_length(payload, i + 1)
        start = i + 1 + used
        out.append((tag, payload[start:start + length]))
        i = start + length
    return out


class PanelMock(MockTransport):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.visible: Any = None          # 1-based program id, "sequence", or None
        self.writes: List[Dict[str, Any]] = []
        self.violations: List[str] = []
        self.deleted: List[int] = []

    async def send_frame(self, frame: bytes, *, wait_ack: bool = True, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        pkt = PacketParser().feed(frame)[0]
        tags = dict(_tlvs(pkt.payload))
        if TLV.ID_PRO in tags:
            prog = tags[TLV.ID_PRO][0] + 1
            ram = tags.get(TLV.DATA_SAVE, b"\x01")[0] == 0
            if prog == self.visible:
                self.violations.append(f"wrote to VISIBLE program {prog}")
            if not self.writes or self.writes[-1]["prog"] != prog or self.writes[-1]["sno_end"] != pkt.sno - 1:
                self.writes.append({"prog": prog, "ram": ram, "sno_end": pkt.sno})
            else:
                self.writes[-1]["sno_end"] = pkt.sno
        if TLV.DISPATCH in tags and tags[TLV.DISPATCH][0] == 2 and TLV.ID_PRO not in tags:
            self.visible = tags[TLV.DISPATCH][1] + 1
        if TLV.PGM_PLAY in tags:
            model, index = tags[TLV.PGM_PLAY][0], tags[TLV.PGM_PLAY][1]
            self.visible = index + 1 if model == 1 else ("sequence" if model == 0 else self.visible)
        if TLV.DELETE in tags:
            ids = [b + 1 for b in tags[TLV.DELETE][1:]]
            self.deleted.extend(ids)
            if self.visible in ids:
                self.violations.append(f"deleted VISIBLE program {self.visible}")
        return await super().send_frame(frame, wait_ack=wait_ack, timeout=timeout)


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch: pytest.MonkeyPatch):
    """Fresh data dir + fresh controller state for every test."""
    for p in _DATA_DIR.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    monkeypatch.setattr(S, "state", S.ControllerState())
    monkeypatch.setattr(S, "LAN_AUTH", LanAuthStore(S.LAN_AUTH_FILE))
    monkeypatch.setattr(S, "LOGIN_LIMITER", LoginRateLimiter())
    monkeypatch.setattr(S, "MockTransport", PanelMock)
    S._schedule_cache.update(mtime=None, data=None)
    yield


def run(coro_fn):
    """Run `coro_fn(client)` against a live test server."""
    async def _main():
        app = S.create_app()
        app.on_startup.clear()  # no background loops in tests
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(_main())


async def _connect(client) -> PanelMock:
    r = await client.post("/api/connect", json={"mock": True})
    assert r.status == 200, await r.text()
    return S.state.transport


FRAME = base64.b64encode(bytes(64 * 64 * 3)).decode()


# ---------------------------------------------------------------------------
# 1. Slot invariants across uploads, alerts and schedule rules
# ---------------------------------------------------------------------------

def test_slot_invariants_uploads_alerts_and_rules():
    async def body(c):
        panel = await _connect(c)
        steps = [
            ("/api/gif_pack/send", {"filename": "10_mode_coding.gif"}),
            ("/api/gif_pack/send", {"filename": "11_mode_on_air_meeting.gif"}),
            ("/api/upload_frames", {"frames": [FRAME], "width": 64, "height": 64}),
            ("/api/hw_text_clock", {"shape": "hw_neon_ring"}),
            ("/api/send_text", {"text": "HELLO"}),
            ("/api/work_schedule/test_rule", {"rule": {"name": "T", "kind": "gif", "target": "13_mode_coffee_break.gif",
                                                       "days": ["mon"], "start": "09:00", "end": "10:00"}}),
        ]
        seen_slots = []
        for path, payload in steps:
            r = await c.post(path, json=payload)
            data = await r.json()
            assert r.status == 200, (path, data)
            assert panel.visible == S.state.active_slot, path
            seen_slots.append(S.state.active_slot)
        assert set(seen_slots) <= LIVE
        assert all(a != b for a, b in zip(seen_slots, seen_slots[1:])), "must ping-pong 1<->2"

        # Alert: RAM slot 3, then automatic restore to the live slot and slot 3 deleted
        live_before = S.state.active_slot
        r = await c.post("/api/notify", json={"title": "BUILD", "duration_s": 1})
        assert r.status == 200
        assert panel.visible == S.ALERT_SLOT and S.state.temp_alert_active
        assert panel.writes[-1] == {**panel.writes[-1], "prog": S.ALERT_SLOT, "ram": True}
        await asyncio.sleep(1.4)
        assert panel.visible == live_before and not S.state.temp_alert_active
        assert S.ALERT_SLOT in panel.deleted

        # Upload while an alert is showing: new content appears, alert removed, no flicker
        await c.post("/api/notify", json={"title": "X", "duration_s": 30})
        r = await c.post("/api/gif_pack/send", json={"filename": "10_mode_coding.gif"})
        assert r.status == 200
        assert panel.visible == S.state.active_slot in LIVE and not S.state.temp_alert_active
        assert S.state.notify_restore_task is None

        assert panel.violations == []
        assert all(w["prog"] in LIVE | {S.ALERT_SLOT} for w in panel.writes)
        # mirror kept in sync for every path
        st = await (await c.get("/api/status")).json()
        assert st["now_playing"]["title"] == "10 Mode Coding"
    run(body)


def test_automatic_refreshes_go_to_ram_and_manual_to_flash():
    async def body(c):
        panel = await _connect(c)
        await c.post("/api/gif_pack/send", json={"filename": "10_mode_coding.gif"})
        assert panel.writes[-1]["ram"] is False  # user action persists
        await S._apply_schedule_rule(
            {"id": "a", "name": "auto", "kind": "gif", "target": "13_mode_coffee_break.gif"},
            persist=S.AUTO_REFRESH_PERSIST,
        )
        assert panel.writes[-1]["ram"] is (not S.AUTO_REFRESH_PERSIST)
        assert panel.violations == []
    run(body)


# ---------------------------------------------------------------------------
# 2. Concurrency: simultaneous clients never collide on a slot
# ---------------------------------------------------------------------------

def test_concurrent_uploads_never_hit_the_visible_slot():
    async def body(c):
        panel = await _connect(c)
        reqs = [
            c.post("/api/gif_pack/send", json={"filename": "10_mode_coding.gif"}),
            c.post("/api/gif_pack/send", json={"filename": "11_mode_on_air_meeting.gif"}),
            c.post("/api/upload_frames", json={"frames": [FRAME], "width": 64, "height": 64}),
        ]
        results = await asyncio.gather(*reqs)
        assert all(r.status == 200 for r in results)
        assert panel.violations == []
        assert panel.visible == S.state.active_slot
    run(body)


# ---------------------------------------------------------------------------
# 3. LAN auth / CSRF / DNS-rebinding guards
# ---------------------------------------------------------------------------

@pytest.fixture
def as_lan_client(monkeypatch: pytest.MonkeyPatch):
    """Requests carrying X-Test-LAN: 1 are treated as coming from a phone on Wi-Fi."""
    monkeypatch.setattr(S, "_is_loopback_req", lambda req: req.headers.get("X-Test-LAN") != "1")
    return {"X-Test-LAN": "1"}


def test_request_guards_block_cross_site_and_rebinding():
    async def body(c):
        assert (await c.get("/api/status", headers={"Host": "evil.example:18420"})).status == 403
        r = await c.post("/api/control", json={"action": "sync_time"}, headers={"Origin": "https://evil.example"})
        assert r.status == 403
        r = await c.post("/api/auth/set_password", data='{"password":""}', headers={"Content-Type": "text/plain"})
        assert r.status == 415
        ws_bad = await c.get("/ws", headers={"Origin": "https://evil.example"})
        assert ws_bad.status == 403
        ok = await c.get("/api/status")
        assert ok.status == 200
        assert "script-src 'self'" in ok.headers["Content-Security-Policy"]
    run(body)
    assert host_allowed("127.0.0.1:18420") and host_allowed("[::1]:1") and host_allowed("my-mac.local:18420")
    assert host_allowed("192.168.1.20:18420") and not host_allowed("attacker.com:18420")
    assert same_origin("http://192.168.1.20:18420", "192.168.1.20:18420")
    assert not same_origin("null", "127.0.0.1:18420")


def test_lan_locked_until_password_then_sessions(as_lan_client):
    lan = as_lan_client

    async def body(c):
        r = await c.post("/api/auth/login", json={"password": "1234"}, headers=lan)
        assert r.status == 403 and (await r.json())["setup_required"]
        assert (await c.get("/api/status", headers=lan)).status == 401
        # Password can only be set from the Mac, and must be long enough
        assert (await c.post("/api/auth/set_password", json={"password": "secret-pw"}, headers=lan)).status == 403
        assert (await c.post("/api/auth/set_password", json={"password": "123"})).status == 400
        assert (await c.post("/api/auth/set_password", json={"password": "secret-pw"})).status == 200

        r = await c.post("/api/auth/login", json={"password": "secret-pw"}, headers=lan)
        token = (await r.json())["token"]
        assert "loy_session" in r.cookies
        c.session.cookie_jar.clear()  # test the header / query paths on their own
        auth = {**lan, "X-LOY-Token": token}
        assert (await c.get("/api/status", headers=auth)).status == 200
        assert (await c.post("/api/update/apply", json={}, headers=auth)).status == 403  # Mac only
        assert (await c.get("/api/status", headers={**lan, "X-LOY-Token": "nope"})).status == 401
        # Query-string tokens only work for the WebSocket
        assert (await c.get(f"/api/status?token={token}", headers=lan)).status == 401

        await c.post("/api/auth/logout", headers=auth)
        assert (await c.get("/api/status", headers=auth)).status == 401

        # Changing the password signs every phone out
        r = await c.post("/api/auth/login", json={"password": "secret-pw"}, headers=lan)
        auth2 = {**lan, "X-LOY-Token": (await r.json())["token"]}
        c.session.cookie_jar.clear()
        await c.post("/api/auth/set_password", json={"password": "another-pw"})
        assert (await c.get("/api/status", headers=auth2)).status == 401
    run(body)


def test_login_rate_limited(as_lan_client):
    lan = as_lan_client

    async def body(c):
        await c.post("/api/auth/set_password", json={"password": "secret-pw"})
        codes = [(await c.post("/api/auth/login", json={"password": "wrong"}, headers=lan)).status for _ in range(7)]
        assert codes[:5] == [401] * 5 and 429 in codes[5:]
        # Even the right password is refused while locked out
        assert (await c.post("/api/auth/login", json={"password": "secret-pw"}, headers=lan)).status == 429
    run(body)


def test_legacy_auth_file_migration(tmp_path: Path):
    import json as _json
    legacy_default = tmp_path / "a.json"
    legacy_default.write_text(_json.dumps({"enabled": True, "salt": "s", "hash": hashlib.sha256(b"s:1234").hexdigest(),
                                           "session_secret": "x", "is_default": True}))
    store = LanAuthStore(legacy_default)
    assert store.password_set is False  # old default "1234" no longer unlocks anything

    legacy_custom = tmp_path / "b.json"
    legacy_custom.write_text(_json.dumps({"enabled": True, "salt": "s", "hash": hashlib.sha256(b"s:hunter22").hexdigest(),
                                          "session_secret": "x", "is_default": False}))
    store2 = LanAuthStore(legacy_custom)
    assert store2.verify_password("hunter22") and not store2.verify_password("nope")
    assert _json.loads(legacy_custom.read_text())["algo"] == "pbkdf2_sha256"  # upgraded on login
    assert LanAuthStore(legacy_custom).verify_password("hunter22")


def test_corrupt_json_is_moved_aside_not_reset(tmp_path: Path):
    f = tmp_path / "x.json"
    f.write_text("{not json")
    assert read_json(f, dict) == {}
    assert not f.exists() and list(tmp_path.glob("x.json.corrupt-*"))


# ---------------------------------------------------------------------------
# 4. Protocol safety through the API
# ---------------------------------------------------------------------------

def test_ble_profile_endpoint_blocks_sliding_window_on_hardware():
    async def body(c):
        S.state.transport = S.BleTransport()  # real transport type (never connected here)
        r = await c.post("/api/ble_profile", json={"profile": "balanced"})
        assert r.status == 400 and "sliding window" in (await r.json())["error"]
        assert (await c.post("/api/ble_profile", json={"profile": "ys_l_fast"})).status == 200
        listing = await (await c.get("/api/ble_profile")).json()
        assert listing["profiles"]["fast"]["blocked_on_hardware"] is True
    run(body)


# ---------------------------------------------------------------------------
# 5. API request shapes from every client + scheduler
# ---------------------------------------------------------------------------

def test_brightness_accepts_every_client_shape():
    async def body(c):
        await _connect(c)
        for payload, want in (({"action": "brightness", "level": 5}, 5),   # web UI / rumps menu bar
                              ({"action": "brightness", "value": 9}, 9),   # legacy menu bar / Matter
                              ({"action": "brightness", "level": 99}, 15)):
            r = await c.post("/api/control", json=payload)
            assert r.status == 200 and S.state.brightness == want
        assert (await c.post("/api/control", json={"action": "brightness"})).status == 400
    run(body)


def test_schedule_matching_table():
    import datetime as dt
    rules = [
        {"id": "night", "enabled": True, "days": ["mon"], "start": "22:30", "end": "08:00"},
        {"id": "work", "enabled": True, "days": ["mon", "tue"], "start": "09:00", "end": "17:00"},
    ]
    mon = dt.datetime(2026, 11, 23)  # a Monday
    at = lambda d, h, m: S._match_schedule_rule(rules, d + dt.timedelta(hours=h, minutes=m))  # noqa: E731
    assert at(mon, 23, 0)["id"] == "night"
    assert at(mon + dt.timedelta(days=1), 7, 59)["id"] == "night"  # after midnight belongs to Monday's rule
    assert at(mon + dt.timedelta(days=1), 8, 0) is None
    assert at(mon, 9, 0)["id"] == "work" and at(mon, 17, 0) is None


def test_overnight_rule_on_specific_days_splits_into_following_day():
    progs = S._rule_device_programs({"days": ["fri"], "start": "22:00", "end": "06:00"})
    assert progs == [{"days": ["fri"], "windows": ["22:00-23:59"]}, {"days": ["sat"], "windows": ["00:00-06:00"]}]
    every_day = S._rule_device_programs({"days": list(S.DAY_KEYS), "start": "22:00", "end": "06:00"})
    assert len(every_day) == 1 and every_day[0]["windows"] == ["22:00-23:59", "00:00-06:00"]


def test_malformed_rules_rejected_and_never_break_status():
    async def body(c):
        r = await c.post("/api/work_schedule", json={"rules": [{"kind": "gif", "start": "9am"}]})
        assert r.status == 400
        r = await c.post("/api/work_schedule", json={"rules": [{"name": "<img src=x onerror=alert(1)>", "kind": "gif",
                                                                "target": "10_mode_coding.gif", "days": ["mon"],
                                                                "start": "09:00", "end": "10:00"}]})
        assert r.status == 200
        # A hand-edited, broken file must not take /api/status (and every WebSocket) down
        S.SCHEDULE_FILE.write_text('{"enabled": true, "rules": [{"kind": "gif"}, 42]}')
        S._schedule_cache.update(mtime=None, data=None)
        assert (await c.get("/api/status")).status == 200
    run(body)


def test_flash_device_uses_schedule_slots_and_survives_live_uploads():
    async def body(c):
        panel = await _connect(c)
        await c.post("/api/gif_pack/send", json={"filename": "10_mode_coding.gif"})
        r = await c.post("/api/work_schedule/flash_device", json={})
        data = await r.json()
        assert r.status == 200, data
        flashed = [f["slot"] for f in data["flashed"]]
        assert flashed and set(flashed) <= set(S.SCHEDULE_SLOTS)
        assert len(data["skipped"]) >= 1 and all(s["reason"] for s in data["skipped"])
        assert panel.visible == "sequence" and S.state.device_schedule_active
        assert set(S.LIVE_SLOTS) <= set(panel.deleted)  # sequence only cycles scheduled programs

        n_writes = len(panel.writes)
        await c.post("/api/gif_pack/send", json={"filename": "11_mode_on_air_meeting.gif"})
        assert all(w["prog"] in LIVE for w in panel.writes[n_writes:]), "live uploads must not touch schedule slots"
        assert not S.state.device_schedule_active

        assert (await c.post("/api/work_schedule/device_mode", json={"action": "resume"})).status == 200
        assert panel.visible == "sequence"
        assert (await c.post("/api/work_schedule/device_mode", json={"action": "stop"})).status == 200
        assert set(S.SCHEDULE_SLOTS) <= set(panel.deleted)
        assert panel.violations == []
    run(body)


# ---------------------------------------------------------------------------
# 6. Updater integrity
# ---------------------------------------------------------------------------

def test_ed25519_rfc8032_vector():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = U.public_key(sk)
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = U.sign(sk, b"")
    assert sig.hex().startswith("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155")
    assert U.verify(pk, b"", sig) and not U.verify(pk, b"x", sig)


def test_verify_release_paths():
    sk = bytes(range(32))
    pk_hex = U.public_key(sk).hex()
    zip_sha = hashlib.sha256(b"zip").hexdigest()
    sums = f"{zip_sha}  LOY-Studio-macOS-arm64.zip\n".encode()
    sig_hex = (U.sign(sk, sums).hex() + "\n").encode()

    assert U.verify_release(sums, None, "LOY-Studio-macOS-arm64.zip", zip_sha, public_key_hex="") == {"signed": False}
    assert U.verify_release(sums, sig_hex, "LOY-Studio-macOS-arm64.zip", zip_sha, public_key_hex=pk_hex) == {"signed": True}
    with pytest.raises(ValueError, match="does not match"):
        U.verify_release(sums, None, "LOY-Studio-macOS-arm64.zip", "0" * 64, public_key_hex="")
    with pytest.raises(ValueError, match="missing"):
        U.verify_release(sums, None, "LOY-Studio-macOS-arm64.zip", zip_sha, public_key_hex=pk_hex)
    tampered = sums.replace(b"arm64", b"arm65")
    with pytest.raises(ValueError, match="INVALID"):
        U.verify_release(tampered, sig_hex, "LOY-Studio-macOS-arm65.zip", zip_sha, public_key_hex=pk_hex)


def test_release_asset_matches_os_and_cpu(monkeypatch: pytest.MonkeyPatch):
    assets = [{"name": "LOY-Studio-macOS-arm64.zip"}, {"name": "LOY-Studio-Windows-x64.zip"}, {"name": "SHA256SUMS"}]
    monkeypatch.setattr(S.sys, "platform", "darwin")
    monkeypatch.setattr(S, "_machine_arch", lambda: "arm64")
    assert S._pick_release_asset(assets)[0]["name"] == "LOY-Studio-macOS-arm64.zip"
    monkeypatch.setattr(S, "_machine_arch", lambda: "x64")  # Intel Mac must not get the arm64 build
    chosen, err = S._pick_release_asset(assets)
    assert chosen is None and "CPU" in err


# ---------------------------------------------------------------------------
# 7. Graceful BLE disconnect (CLAUDE.md constraint 5)
# ---------------------------------------------------------------------------

def test_shutdown_ble_disconnects_and_is_idempotent():
    async def body(c):
        panel = await _connect(c)
        await S.shutdown_ble("test")
        assert not panel.is_connected and S.state.auto_reconnect is False
        await S.shutdown_ble("test again")
    run(body)
    assert S._shutdown_ble_hook in S.create_app().on_shutdown


def test_ble_disconnect_still_disconnects_when_stop_notify_fails():
    calls = []

    class FakeClient:
        is_connected = True

        async def stop_notify(self, _uuid):
            calls.append("stop_notify")
            raise RuntimeError("characteristic gone")

        async def disconnect(self):
            calls.append("disconnect")

    t = S.BleTransport()
    t._client = FakeClient()
    asyncio.run(t.disconnect())
    assert calls == ["stop_notify", "disconnect"] and t._client is None
