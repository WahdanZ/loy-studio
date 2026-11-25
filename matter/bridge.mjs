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
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import { Endpoint, Environment, ServerNode, StorageService } from "@matter/main";
import { BridgedDeviceBasicInformationServer } from "@matter/main/behaviors/bridged-device-basic-information";
import { DimmableLightDevice } from "@matter/main/devices/dimmable-light";
import { OnOffPlugInUnitDevice } from "@matter/main/devices/on-off-plug-in-unit";
import { AggregatorEndpoint } from "@matter/main/endpoints/aggregator";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOY_BASE = process.env.LOY_URL || "http://127.0.0.1:18420";
const MATTER_PORT = Number(process.env.MATTER_PORT || 5540);
const PASSCODE = Number(process.env.MATTER_PASSCODE || 20202021);
const DISCRIMINATOR = Number(process.env.MATTER_DISCRIMINATOR || 3840);
const WEATHER_CITY = process.env.LOY_CITY || "Berlin";

// Keep Matter storage local to matter/.matter-storage (gitignored)
const storageDir = path.join(__dirname, ".matter-storage");
Environment.default.vars.set("storage.path", storageDir);

async function loyPost(endpoint, payload) {
  try {
    const res = await fetch(`${LOY_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const text = await res.text();
    if (!res.ok) {
      console.error(`[LOY Matter] POST ${endpoint} -> HTTP ${res.status}: ${text}`);
    } else {
      console.log(`[LOY Matter] POST ${endpoint} OK`);
    }
  } catch (err) {
    console.error(`[LOY Matter] Could not reach LOY Studio at ${LOY_BASE}:`, err.message);
  }
}

async function main() {
  const server = await ServerNode.create({
    id: "loy-studio-bridge",
    network: { port: MATTER_PORT },
    commissioning: {
      passcode: PASSCODE,
      discriminator: DISCRIMINATOR,
    },
    productDescription: {
      name: "LOY Studio Bridge",
      deviceType: AggregatorEndpoint.deviceType,
    },
    basicInformation: {
      vendorName: "LOY Studio",
      vendorId: 0xfff1,
      nodeLabel: "LOY Studio Bridge",
      productName: "LOY 64x64 LED Bridge",
      productLabel: "LOY 64x64 LED Bridge",
      productId: 0x8001,
      serialNumber: "LOY-MATTER-64X64",
      uniqueId: "loy-studio-matter-bridge-01",
    },
  });

  const aggregator = new Endpoint(AggregatorEndpoint, { id: "loy-aggregator" });
  await server.add(aggregator);

  // 1. Main Desk Display (On/Off + Brightness 0-100% -> hardware 1-15)
  const BridgedLight = DimmableLightDevice.with(BridgedDeviceBasicInformationServer);
  const displayEp = new Endpoint(BridgedLight, {
    id: "loy-desk-display",
    bridgedDeviceBasicInformation: {
      nodeLabel: "LOY Desk Display",
      productName: "64x64 LED Pixel Matrix",
      vendorName: "LOY Studio",
      reachable: true,
    },
    onOff: { onOff: true },
    levelControl: { currentLevel: 200 },
  });
  await aggregator.add(displayEp);

  displayEp.events.onOff.onOff$Changed.on(async (isOn) => {
    console.log(`[LOY Matter] Desk Display power -> ${isOn ? "ON" : "OFF"}`);
    await loyPost("/api/control", { action: "power", on: Boolean(isOn) });
  });

  let briTimer = null;
  displayEp.events.levelControl.currentLevel$Changed.on((level) => {
    if (level === null || level === undefined) return;
    // Debounce Google Home slider drags slightly so BLE stays snappy
    clearTimeout(briTimer);
    briTimer = setTimeout(async () => {
      const hwBri = Math.max(1, Math.min(15, Math.round((Number(level) / 254) * 15)));
      console.log(`[LOY Matter] Desk Display brightness -> ${hwBri}/15 (level ${level})`);
      await loyPost("/api/control", { action: "brightness", value: hwBri });
    }, 180);
  });

  // 2. Virtual Scene Switches for Google Home voice & Routines
  const BridgedSwitch = OnOffPlugInUnitDevice.with(BridgedDeviceBasicInformationServer);
  const modeDefs = [
    {
      id: "loy-mode-onair",
      label: "LOY ON AIR",
      trigger: () => loyPost("/api/gif_pack/send", { filename: "11_mode_on_air_meeting.gif" }),
    },
    {
      id: "loy-mode-coding",
      label: "LOY Coding Mode",
      trigger: () => loyPost("/api/gif_pack/send", { filename: "10_mode_coding.gif" }),
    },
    {
      id: "loy-mode-synthwave",
      label: "LOY Synthwave Clock",
      trigger: () => loyPost("/api/hw_text_clock", { shape: "hw_synthwave_sun", color: "#38bdf8", accent: "#ec4899" }),
    },
    {
      id: "loy-mode-weather",
      label: "LOY Weather Clock",
      trigger: () => loyPost("/api/weather_clock", { city: WEATHER_CITY }),
    },
  ];

  const switchEndpoints = [];
  for (const mode of modeDefs) {
    const ep = new Endpoint(BridgedSwitch, {
      id: mode.id,
      bridgedDeviceBasicInformation: {
        nodeLabel: mode.label,
        productName: "LOY Studio Scene",
        vendorName: "LOY Studio",
        reachable: true,
      },
      onOff: { onOff: false },
    });
    await aggregator.add(ep);
    switchEndpoints.push({ ep, mode });

    ep.events.onOff.onOff$Changed.on(async (isOn) => {
      if (!isOn) return;
      console.log(`[LOY Matter] Scene triggered -> ${mode.label}`);
      await mode.trigger();
      // Auto-reset other scene switches so Google Home shows the active scene cleanly
      for (const other of switchEndpoints) {
        if (other.mode.id !== mode.id) {
          try { await other.ep.set({ onOff: { onOff: false } }); } catch (_) {}
        }
      }
    });
  }

  await server.start();

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
