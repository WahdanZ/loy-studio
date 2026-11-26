#!/usr/bin/env node
/**
 * LOY Studio — Local Matter Bridge for Google Home / Google Nest Hub
 *
 * Exposes your 64x64 LED Pixel Display and favorite desk modes natively to
 * Google Home over local Wi-Fi (100% offline, zero cloud accounts):
 *   - LOY Desk Display   (Dimmable Light: On/Off + Brightness 0-100%)
 *   - LOY ON AIR         (Switch: activates ON AIR meeting mode)
 *   - LOY Coding Mode    (Switch: activates </CODE> work mode)
 *   - LOY Synthwave      (Switch: activates Synthwave Sun RTC clock)
 *   - LOY Weather Clock  (Switch: activates live Weather + RTC clock)
 *
 * State flows both ways: changes made in LOY Studio (Mac, Menu Bar, iPhone) are polled from
 * /api/status and pushed back to Google Home, and `reachable` follows the real BLE link.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Endpoint, Environment, ServerNode } from "@matter/main";
import { BridgedDeviceBasicInformationServer } from "@matter/main/behaviors/bridged-device-basic-information";
import { DimmableLightDevice } from "@matter/main/devices/dimmable-light";
import { OnOffPlugInUnitDevice } from "@matter/main/devices/on-off-plug-in-unit";
import { AggregatorEndpoint } from "@matter/main/endpoints/aggregator";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOY_BASE = process.env.LOY_URL || "http://127.0.0.1:18420";
const MATTER_PORT = Number(process.env.MATTER_PORT || 5540);
const WEATHER_CITY = process.env.LOY_CITY || "Berlin";
const REQUEST_TIMEOUT_MS = 45_000; // GIF uploads over BLE can take a while
const POLL_MS = 5_000;

// Keep Matter storage local to matter/.matter-storage (gitignored)
const storageDir = process.env.MATTER_STORAGE_DIR || path.join(__dirname, ".matter-storage");
Environment.default.vars.set("storage.path", storageDir);

// Matter spec 5.1.7.1: these setup passcodes are invalid / trivially guessable
const INVALID_PASSCODES = new Set([
  0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888, 99999999, 12345678, 87654321,
]);
const LEGACY_PASSCODE = 20202021; // well-known example code used by earlier versions
const LEGACY_DISCRIMINATOR = 3840;

/**
 * Pairing code: env override > saved per-install random code > (already-paired legacy
 * installs keep the old code so existing Google Home pairings keep working) > new random code.
 */
function loadPairing() {
  if (process.env.MATTER_PASSCODE) {
    return {
      passcode: Number(process.env.MATTER_PASSCODE),
      discriminator: Number(process.env.MATTER_DISCRIMINATOR || LEGACY_DISCRIMINATOR),
    };
  }
  const file = path.join(storageDir, "loy-pairing.json");
  try {
    const saved = JSON.parse(fs.readFileSync(file, "utf8"));
    if (Number.isInteger(saved.passcode) && Number.isInteger(saved.discriminator)) return saved;
  } catch (_) {}
  let pairing;
  if (fs.existsSync(storageDir) && fs.readdirSync(storageDir).length > 0) {
    console.warn("[LOY Matter] Existing pairing found: keeping the legacy setup code. Delete matter/.matter-storage and re-pair to get a private code.");
    pairing = { passcode: LEGACY_PASSCODE, discriminator: LEGACY_DISCRIMINATOR };
  } else {
    let passcode = 0;
    while (INVALID_PASSCODES.has(passcode)) passcode = crypto.randomInt(1, 99999999);
    pairing = { passcode, discriminator: crypto.randomInt(0, 4096) };
  }
  fs.mkdirSync(storageDir, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(pairing), { mode: 0o600 });
  return pairing;
}

async function loyRequest(method, endpoint, payload) {
  const res = await fetch(`${LOY_BASE}${endpoint}`, {
    method,
    headers: { "Content-Type": "application/json", "X-LOY-Source": "Google Home" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : {};
}

/** POST a command; returns true on success, logs and returns false on failure. */
async function loyPost(endpoint, payload) {
  try {
    await loyRequest("POST", endpoint, payload);
    console.log(`[LOY Matter] POST ${endpoint} OK`);
    return true;
  } catch (err) {
    console.error(`[LOY Matter] POST ${endpoint} failed:`, err.message);
    return false;
  }
}

// While we push LOY Studio's state into Matter attributes, ignore the resulting $Changed events
// so they are not echoed back as new commands.
let suppressUntil = 0;
const suppressed = () => Date.now() < suppressUntil;
async function syncSet(ep, values) {
  suppressUntil = Date.now() + 1500;
  try { await ep.set(values); } catch (err) { console.error("[LOY Matter] sync set failed:", err.message); }
}

const levelFromBrightness = (b) => Math.max(1, Math.min(254, Math.round((Number(b) / 15) * 254)));

async function main() {
  const { passcode, discriminator } = loadPairing();
  const server = await ServerNode.create({
    id: "loy-studio-bridge",
    network: { port: MATTER_PORT },
    commissioning: { passcode, discriminator },
    productDescription: {
      name: "Desk Display Bridge",
      deviceType: AggregatorEndpoint.deviceType,
    },
    basicInformation: {
      vendorName: "Desk Display",
      vendorId: 0xfff1,
      nodeLabel: "Desk Display Bridge",
      productName: "Desk Display 64x64",
      productLabel: "Desk Display 64x64",
      productId: 0x8001,
      serialNumber: "DESK-DISPLAY-64X64",
      uniqueId: "desk-display-matter-bridge-01",
    },
  });

  const aggregator = new Endpoint(AggregatorEndpoint, { id: "loy-aggregator" });
  await server.add(aggregator);

  // 1. Main Desk Display (On/Off + Brightness 0-100% -> hardware 1-15)
  const BridgedLight = DimmableLightDevice.with(BridgedDeviceBasicInformationServer);
  const displayEp = new Endpoint(BridgedLight, {
    id: "loy-desk-display",
    bridgedDeviceBasicInformation: {
      nodeLabel: "Desk Display",
      productName: "64x64 LED Matrix",
      vendorName: "Desk Display",
      reachable: false, // becomes true once LOY Studio reports a connected display
    },
    onOff: { onOff: true },
    levelControl: { currentLevel: 200 },
  });
  await aggregator.add(displayEp);

  displayEp.events.onOff.onOff$Changed.on(async (isOn, wasOn) => {
    if (suppressed()) return;
    console.log(`[LOY Matter] Desk Display power -> ${isOn ? "ON" : "OFF"}`);
    const ok = await loyPost("/api/control", { action: "power", on: Boolean(isOn) });
    if (!ok) await syncSet(displayEp, { onOff: { onOff: Boolean(wasOn) } }); // show the real state again
  });

  let briTimer = null;
  displayEp.events.levelControl.currentLevel$Changed.on((level) => {
    if (suppressed() || level === null || level === undefined) return;
    // Debounce Google Home slider drags slightly so BLE stays snappy
    clearTimeout(briTimer);
    briTimer = setTimeout(async () => {
      const hwBri = Math.max(1, Math.min(15, Math.round((Number(level) / 254) * 15)));
      console.log(`[LOY Matter] Desk Display brightness -> ${hwBri}/15 (level ${level})`);
      await loyPost("/api/control", { action: "brightness", level: hwBri });
    }, 180);
  });

  // 2. Virtual Scene Switches for Google Home voice & Routines
  const BridgedSwitch = OnOffPlugInUnitDevice.with(BridgedDeviceBasicInformationServer);
  const modeDefs = [
    {
      id: "loy-mode-onair",
      label: "On Air Mode",
      isActive: (np) => np.active_pin === "pinOnAir",
      trigger: () => loyPost("/api/gif_pack/send", { filename: "11_mode_on_air_meeting.gif" }),
    },
    {
      id: "loy-mode-coding",
      label: "Coding Mode",
      isActive: (np) => np.active_pin === "pinCoding",
      trigger: () => loyPost("/api/gif_pack/send", { filename: "10_mode_coding.gif" }),
    },
    {
      id: "loy-mode-synthwave",
      label: "Synthwave Clock",
      isActive: (np) => /synthwave/i.test(np.title || ""),
      trigger: () => loyPost("/api/hw_text_clock", { shape: "hw_synthwave_sun", color: "#38bdf8", accent: "#ec4899" }),
    },
    {
      id: "loy-mode-weather",
      label: "Weather Clock",
      isActive: (np) => np.active_pin === "pinWxClock",
      trigger: () => loyPost("/api/weather_clock", { city: WEATHER_CITY }),
    },
  ];

  const switchEndpoints = [];
  for (const mode of modeDefs) {
    const ep = new Endpoint(BridgedSwitch, {
      id: mode.id,
      bridgedDeviceBasicInformation: {
        nodeLabel: mode.label,
        productName: "Desk Display Scene",
        vendorName: "Desk Display",
        reachable: false,
      },
      onOff: { onOff: false },
    });
    await aggregator.add(ep);
    switchEndpoints.push({ ep, mode });

    ep.events.onOff.onOff$Changed.on(async (isOn) => {
      if (suppressed() || !isOn) return;
      console.log(`[LOY Matter] Scene triggered -> ${mode.label}`);
      const ok = await mode.trigger();
      // Show the truth: only the scene that is really on the display stays on
      for (const other of switchEndpoints) {
        const on = ok && other.mode.id === mode.id;
        await syncSet(other.ep, { onOff: { onOff: on } });
      }
    });
  }

  // 3. Two-way sync: LOY Studio -> Google Home
  let lastReachable = null;
  async function pollStatus() {
    let st = null;
    try {
      st = await loyRequest("GET", "/api/status");
    } catch (_) {
      st = null;
    }
    const reachable = Boolean(st && st.connected);
    if (reachable !== lastReachable) {
      lastReachable = reachable;
      console.log(`[LOY Matter] Display ${reachable ? "reachable" : "unreachable"}`);
      await syncSet(displayEp, { bridgedDeviceBasicInformation: { reachable } });
      for (const { ep } of switchEndpoints) await syncSet(ep, { bridgedDeviceBasicInformation: { reachable } });
    }
    if (!st) return;
    const power = st.power_on !== false;
    if (displayEp.state.onOff.onOff !== power) await syncSet(displayEp, { onOff: { onOff: power } });
    const level = levelFromBrightness(st.brightness ?? 12);
    // Avoid fighting the user's slider: only sync when the hardware level really differs
    const curHw = Math.round((Number(displayEp.state.levelControl.currentLevel ?? 0) / 254) * 15);
    if (curHw !== Number(st.brightness ?? 12)) await syncSet(displayEp, { levelControl: { currentLevel: level } });
    const np = st.now_playing || {};
    for (const { ep, mode } of switchEndpoints) {
      const on = Boolean(mode.isActive(np));
      if (ep.state.onOff.onOff !== on) await syncSet(ep, { onOff: { onOff: on } });
    }
  }

  await server.start();
  setInterval(() => { pollStatus().catch((err) => console.error("[LOY Matter] status sync failed:", err.message)); }, POLL_MS);
  pollStatus().catch(() => {});

  const { qrPairingCode, manualPairingCode } = server.state.commissioning.pairingCodes;
  console.log("\n========================================================");
  console.log("  LOY Studio Matter Bridge is LIVE for Google Home!");
  console.log("========================================================");
  console.log(`  Gateway URL        : ${LOY_BASE}`);
  console.log(`  Matter Port        : ${MATTER_PORT}`);
  console.log(`  Manual Setup Code  : ${manualPairingCode}`);
  console.log(`  QR Pairing Payload : ${qrPairingCode}`);
  console.log("--------------------------------------------------------");
  console.log("  Google Home App -> + Add Device -> Matter-enabled device");
  console.log("  Tap 'Continue without scanning' and enter the Setup Code!");
  console.log("========================================================\n");
}

main().catch((err) => {
  console.error("[LOY Matter] Fatal error:", err);
  process.exit(1);
});
