/* LOY Studio 2.0 — Client Application */
(() => {
  const PALETTES = {
    studio: ["#000000","#ffffff","#ef4444","#f97316","#f59e0b","#10b981","#06b6d4","#3b82f6","#6366f1","#a855f7","#ec4899","#64748b"],
    pico8: ["#000000","#1d2b53","#7e2553","#008751","#ab5236","#5f574f","#c2c3c7","#fff1e8","#ff004d","#ffa300","#ffec27","#00e436","#29adff","#83769c","#ff77a8","#ffccaa"],
    pastel: ["#111827","#f8fafc","#fda4af","#fdba74","#fde047","#86efac","#67e8f9","#93c5fd","#c4b5fd","#f0abfc","#cbd5e1","#475569"],
    mono: ["#000000","#1e293b","#475569","#94a3b8","#e2e8f0","#ffffff","#0ea5e9","#38bdf8","#7dd3fc","#bae6fd"],
  };

  let gridW = 64, gridH = 64;
  let frames = [new Uint8Array(gridW * gridH * 3)];
  let activeFrame = 0;
  let undoStack = [], redoStack = [];
  let currentTool = "pen", currentColor = "#3b82f6";
  let recentColors = ["#3b82f6", "#ef4444", "#10b981", "#ffffff"];
  let isDrawing = false, shapeStart = null, shapeSnapshot = null;
  let zoomScale = 7;
  let playTimer = null, pomoTimer = null, transferPoll = null;
  let connected = false;
  let recentSent = [];
  let draggedFrameIdx = null;

  const canvas = document.getElementById("pixelCanvas");
  const ctx = canvas.getContext("2d");
  const homeCanvas = document.getElementById("homePreview");
  const homeCtx = homeCanvas.getContext("2d");

  const $ = (id) => document.getElementById(id);

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add("hidden"), 2600);
  }

  function hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function rgbToHex(r, g, b) {
    return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  function bytesToBase64(bytes) {
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  function base64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { error: text || `HTTP ${res.status}` }; }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function recordSent(label) {
    recentSent.unshift(`${new Date().toLocaleTimeString()} — ${label}`);
    recentSent = recentSent.slice(0, 8);
    $("homeRecent").innerHTML = recentSent.map((s) => `<div style="padding:4px 0;border-bottom:1px solid var(--border);">${s}</div>`).join("");
  }

  // ---------------- Navigation ----------------
  const wsTitles = { wsHome: "Home", wsCreate: "Create Studio", wsExplore: "Explore & Library", wsLive: "Smart & Live", wsSchedule: "Auto Schedule", wsDevice: "Device & Pairing" };
  function switchWorkspace(wsId) {
    if (wsId === "wsLibrary") {
      wsId = "wsExplore";
      document.querySelector('[data-exp="expLibrary"]')?.click();
    }
    document.querySelectorAll(".workspace").forEach((el) => el.classList.toggle("active", el.id === wsId));
    document.querySelectorAll("[data-ws]").forEach((el) => el.classList.toggle("active", el.dataset.ws === wsId));
    $("wsTitle").textContent = wsTitles[wsId] || "LOY Studio";
    if (wsId === "wsExplore") { loadExploreGifs(); loadLibrary(); }
    if (wsId === "wsLive") refreshSystemStats();
    if (wsId === "wsSchedule") loadWorkSchedule();
    if (wsId === "wsDevice") { refreshDiagnostics(); loadPhoneConnect(); }
    if (wsId === "wsHome") { renderHomePreview(); loadLibrary(); }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  document.querySelectorAll("[data-ws]").forEach((b) => (b.onclick = () => switchWorkspace(b.dataset.ws)));
  document.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => switchWorkspace(b.dataset.jump)));

  // Explore 3-way segmented tabs (Live Clocks | 18 GIFs | My Library)
  document.querySelectorAll(".seg-btn[data-exp]").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".seg-btn[data-exp]").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".exp-pane").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      const pane = $(btn.dataset.exp);
      if (pane) pane.classList.add("active");
    };
  });

  document.querySelectorAll(".pill[data-sub]").forEach((p) => {
    p.onclick = () => {
      document.querySelectorAll(".pill[data-sub]").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".sub-pane").forEach((x) => x.classList.remove("active"));
      p.classList.add("active");
      $(p.dataset.sub).classList.add("active");
    };
  });

  // ---------------- Canvas Rendering & Physical LED Sim ----------------
  function updateCanvasSize() {
    canvas.width = gridW * zoomScale;
    canvas.height = gridH * zoomScale;
    renderCanvas();
  }

  function renderHomePreview() {
    homeCanvas.width = gridW;
    homeCanvas.height = gridH;
    const img = homeCtx.createImageData(gridW, gridH);
    const buf = frames[activeFrame];
    for (let i = 0; i < gridW * gridH; i++) {
      img.data[i * 4] = buf[i * 3];
      img.data[i * 4 + 1] = buf[i * 3 + 1];
      img.data[i * 4 + 2] = buf[i * 3 + 2];
      img.data[i * 4 + 3] = 255;
    }
    homeCtx.putImageData(img, 0, 0);
  }

  function renderCanvas() {
    const showGrid = $("chkGrid").checked;
    const ledSim = $("chkLedSim").checked;
    const onion = $("chkOnion").checked && activeFrame > 0;
    const prevBuf = onion ? frames[activeFrame - 1] : null;
    const buf = frames[activeFrame];

    ctx.fillStyle = "#050608";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    for (let y = 0; y < gridH; y++) {
      for (let x = 0; x < gridW; x++) {
        const i = (y * gridW + x) * 3;
        let r = buf[i], g = buf[i + 1], b = buf[i + 2];
        if (onion && r === 0 && g === 0 && b === 0) {
          r = Math.floor(prevBuf[i] * 0.25);
          g = Math.floor(prevBuf[i + 1] * 0.25);
          b = Math.floor(prevBuf[i + 2] * 0.25);
        }
        const px = x * zoomScale, py = y * zoomScale;
        if (ledSim && zoomScale >= 5) {
          const rad = (zoomScale - 1.4) / 2;
          ctx.fillStyle = (r + g + b) > 0 ? `rgb(${r},${g},${b})` : "#12151c";
          ctx.beginPath();
          ctx.arc(px + zoomScale / 2, py + zoomScale / 2, Math.max(1, rad), 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillStyle = `rgb(${r},${g},${b})`;
          ctx.fillRect(px, py, showGrid ? zoomScale - 1 : zoomScale, showGrid ? zoomScale - 1 : zoomScale);
        }
      }
    }
    renderHomePreview();
  }

  function saveDraft() {
    try {
      localStorage.setItem("loy_draft_v2", JSON.stringify({ w: gridW, h: gridH, f: bytesToBase64(frames[0]) }));
    } catch (_) {}
  }

  function renderFrameStrip() {
    const strip = $("frameStrip");
    strip.innerHTML = "";
    frames.forEach((buf, idx) => {
      const wrap = document.createElement("div");
      wrap.className = "frame-thumb" + (idx === activeFrame ? " active" : "");
      wrap.draggable = true;
      wrap.ondragstart = () => (draggedFrameIdx = idx);
      wrap.ondragover = (e) => e.preventDefault();
      wrap.ondrop = () => {
        if (draggedFrameIdx === null || draggedFrameIdx === idx) return;
        const [moved] = frames.splice(draggedFrameIdx, 1);
        frames.splice(idx, 0, moved);
        activeFrame = idx;
        draggedFrameIdx = null;
        renderCanvas(); renderFrameStrip();
      };
      const c = document.createElement("canvas");
      c.width = gridW; c.height = gridH;
      const cctx = c.getContext("2d");
      const img = cctx.createImageData(gridW, gridH);
      for (let p = 0; p < gridW * gridH; p++) {
        img.data[p * 4] = buf[p * 3]; img.data[p * 4 + 1] = buf[p * 3 + 1]; img.data[p * 4 + 2] = buf[p * 3 + 2]; img.data[p * 4 + 3] = 255;
      }
      cctx.putImageData(img, 0, 0);
      wrap.appendChild(c);
      const lbl = document.createElement("div");
      lbl.textContent = `#${idx + 1}`;
      wrap.appendChild(lbl);
      wrap.onclick = () => { activeFrame = idx; renderCanvas(); renderFrameStrip(); };
      strip.appendChild(wrap);
    });
  }

  // ---------------- Drawing Tools ----------------
  function pushUndo() {
    undoStack.push(new Uint8Array(frames[activeFrame]));
    if (undoStack.length > 40) undoStack.shift();
    redoStack = [];
  }

  function plotBrush(buf, x, y, rgb) {
    const sz = Math.max(1, Math.min(5, Number($("brushSize").value) || 1));
    const half = Math.floor(sz / 2);
    const symX = $("chkSymX").checked, symY = $("chkSymY").checked;
    const pts = [];
    for (let dy = -half; dy < sz - half; dy++) {
      for (let dx = -half; dx < sz - half; dx++) {
        const coords = [[x + dx, y + dy]];
        if (symX) coords.push([gridW - 1 - (x + dx), y + dy]);
        if (symY) coords.push([x + dx, gridH - 1 - (y + dy)]);
        if (symX && symY) coords.push([gridW - 1 - (x + dx), gridH - 1 - (y + dy)]);
        for (const [tx, ty] of coords) {
          if (tx >= 0 && ty >= 0 && tx < gridW && ty < gridH) {
            const off = (ty * gridW + tx) * 3;
            buf[off] = rgb[0]; buf[off + 1] = rgb[1]; buf[off + 2] = rgb[2];
            pts.push([tx, ty]);
          }
        }
      }
    }
    return pts;
  }

  function drawLine(buf, x0, y0, x1, y1, rgb) {
    let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    let dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    while (true) {
      plotBrush(buf, x0, y0, rgb);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }

  function drawShape(buf, tool, x0, y0, x1, y1, rgb) {
    if (tool === "line") return drawLine(buf, x0, y0, x1, y1, rgb);
    const minX = Math.min(x0, x1), maxX = Math.max(x0, x1);
    const minY = Math.min(y0, y1), maxY = Math.max(y0, y1);
    if (tool === "rect") {
      for (let x = minX; x <= maxX; x++) { plotBrush(buf, x, minY, rgb); plotBrush(buf, x, maxY, rgb); }
      for (let y = minY; y <= maxY; y++) { plotBrush(buf, minX, y, rgb); plotBrush(buf, maxX, y, rgb); }
    } else if (tool === "circle") {
      const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
      const rx = Math.max(1, (maxX - minX) / 2), ry = Math.max(1, (maxY - minY) / 2);
      for (let a = 0; a < 360; a += 3) {
        const rad = (a * Math.PI) / 180;
        plotBrush(buf, Math.round(cx + rx * Math.cos(rad)), Math.round(cy + ry * Math.sin(rad)), rgb);
      }
    }
  }

  function floodFill(x, y, targetRgb, fillRgb) {
    if (targetRgb.every((v, i) => v === fillRgb[i])) return;
    const buf = frames[activeFrame], q = [[x, y]], seen = new Set();
    while (q.length) {
      const [cx, cy] = q.pop();
      if (cx < 0 || cy < 0 || cx >= gridW || cy >= gridH) continue;
      const key = cy * gridW + cx;
      if (seen.has(key)) continue;
      seen.add(key);
      const off = key * 3;
      if (buf[off] !== targetRgb[0] || buf[off + 1] !== targetRgb[1] || buf[off + 2] !== targetRgb[2]) continue;
      buf[off] = fillRgb[0]; buf[off + 1] = fillRgb[1]; buf[off + 2] = fillRgb[2];
      q.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
    }
  }

  function pointerToCell(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor(((ev.clientX - rect.left) / rect.width) * gridW);
    const y = Math.floor(((ev.clientY - rect.top) / rect.height) * gridH);
    return [Math.max(0, Math.min(gridW - 1, x)), Math.max(0, Math.min(gridH - 1, y))];
  }

  function setColor(hex) {
    currentColor = hex;
    $("colorPicker").value = hex;
    if (!recentColors.includes(hex)) {
      recentColors.unshift(hex);
      recentColors = recentColors.slice(0, 10);
      renderRecentColors();
    }
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    const [x, y] = pointerToCell(e);
    const buf = frames[activeFrame];
    if (currentTool === "picker") {
      const off = (y * gridW + x) * 3;
      setColor(rgbToHex(buf[off], buf[off + 1], buf[off + 2]));
      selectTool("pen");
      return;
    }
    pushUndo();
    isDrawing = true;
    const rgb = currentTool === "eraser" ? [0, 0, 0] : hexToRgb(currentColor);
    if (currentTool === "fill") {
      const off = (y * gridW + x) * 3;
      floodFill(x, y, [buf[off], buf[off + 1], buf[off + 2]], rgb);
      renderCanvas(); renderFrameStrip(); saveDraft();
      return;
    }
    if (["line", "rect", "circle"].includes(currentTool)) {
      shapeStart = [x, y];
      shapeSnapshot = new Uint8Array(buf);
      return;
    }
    const pts = plotBrush(buf, x, y, rgb);
    if (connected && $("chkLiveDraw").checked && pts.length) {
      api("/api/control", { method: "POST", body: JSON.stringify({ action: "rt_pixel", pixels: pts, color: rgbToHex(...rgb) }) }).catch(() => {});
    }
    renderCanvas(); renderFrameStrip();
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!isDrawing) return;
    const [x, y] = pointerToCell(e);
    const rgb = currentTool === "eraser" ? [0, 0, 0] : hexToRgb(currentColor);
    if (shapeStart && shapeSnapshot) {
      frames[activeFrame] = new Uint8Array(shapeSnapshot);
      drawShape(frames[activeFrame], currentTool, shapeStart[0], shapeStart[1], x, y, rgb);
      renderCanvas();
      return;
    }
    const pts = plotBrush(frames[activeFrame], x, y, rgb);
    if (connected && $("chkLiveDraw").checked && pts.length) {
      api("/api/control", { method: "POST", body: JSON.stringify({ action: "rt_pixel", pixels: pts, color: rgbToHex(...rgb) }) }).catch(() => {});
    }
    renderCanvas();
  });

  window.addEventListener("pointerup", () => {
    if (!isDrawing) return;
    isDrawing = false; shapeStart = null; shapeSnapshot = null;
    renderFrameStrip(); saveDraft();
  });

  function selectTool(t) {
    currentTool = t;
    document.querySelectorAll("[data-tool]").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
  }
  document.querySelectorAll("[data-tool]").forEach((b) => (b.onclick = () => selectTool(b.dataset.tool)));

  $("btnUndo").onclick = () => {
    if (!undoStack.length) return;
    redoStack.push(new Uint8Array(frames[activeFrame]));
    frames[activeFrame] = undoStack.pop();
    renderCanvas(); renderFrameStrip();
  };
  $("btnRedo").onclick = () => {
    if (!redoStack.length) return;
    undoStack.push(new Uint8Array(frames[activeFrame]));
    frames[activeFrame] = redoStack.pop();
    renderCanvas(); renderFrameStrip();
  };
  $("btnClear").onclick = () => { pushUndo(); frames[activeFrame].fill(0); renderCanvas(); renderFrameStrip(); saveDraft(); };
  $("btnFullscreen").onclick = () => $("canvasCard").classList.toggle("fullscreen");
  $("btnZoomIn").onclick = () => { zoomScale = Math.min(12, zoomScale + 1); updateCanvasSize(); };
  $("btnZoomOut").onclick = () => { zoomScale = Math.max(3, zoomScale - 1); updateCanvasSize(); };
  ["chkGrid", "chkLedSim", "chkOnion"].forEach((id) => ($(id).onchange = renderCanvas));

  // Canvas Transforms
  $("btnFlipH").onclick = () => {
    pushUndo();
    const src = frames[activeFrame], dst = new Uint8Array(src.length);
    for (let y = 0; y < gridH; y++) for (let x = 0; x < gridW; x++) {
      const si = (y * gridW + x) * 3, di = (y * gridW + (gridW - 1 - x)) * 3;
      dst[di] = src[si]; dst[di + 1] = src[si + 1]; dst[di + 2] = src[si + 2];
    }
    frames[activeFrame] = dst; renderCanvas(); renderFrameStrip();
  };
  $("btnFlipV").onclick = () => {
    pushUndo();
    const src = frames[activeFrame], dst = new Uint8Array(src.length);
    for (let y = 0; y < gridH; y++) for (let x = 0; x < gridW; x++) {
      const si = (y * gridW + x) * 3, di = ((gridH - 1 - y) * gridW + x) * 3;
      dst[di] = src[si]; dst[di + 1] = src[si + 1]; dst[di + 2] = src[si + 2];
    }
    frames[activeFrame] = dst; renderCanvas(); renderFrameStrip();
  };
  $("btnRot90").onclick = () => {
    if (gridW !== gridH) return toast("Rotate 90° requires square canvas");
    pushUndo();
    const src = frames[activeFrame], dst = new Uint8Array(src.length);
    for (let y = 0; y < gridH; y++) for (let x = 0; x < gridW; x++) {
      const si = (y * gridW + x) * 3, di = (x * gridW + (gridH - 1 - y)) * 3;
      dst[di] = src[si]; dst[di + 1] = src[si + 1]; dst[di + 2] = src[si + 2];
    }
    frames[activeFrame] = dst; renderCanvas(); renderFrameStrip();
  };

  // Keyboard shortcuts (Undo/Redo + Game Arrows)
  window.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      e.shiftKey ? $("btnRedo").click() : $("btnUndo").click();
      return;
    }
    if ($("wsExplore").classList.contains("active")) {
      const map = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "start", Escape: "exit" };
      if (map[e.key]) { e.preventDefault(); sendCtrl({ action: "game_opt", opt: map[e.key] }); }
    }
  });

  // Palettes
  function renderPalette(list) {
    const row = $("paletteRow");
    row.innerHTML = "";
    list.forEach((hex) => {
      const sw = document.createElement("div");
      sw.className = "swatch" + (hex === currentColor ? " active" : "");
      sw.style.background = hex;
      sw.onclick = () => { setColor(hex); renderPalette(list); };
      row.appendChild(sw);
    });
  }
  function renderRecentColors() {
    const row = $("recentColors");
    row.innerHTML = "";
    recentColors.forEach((hex) => {
      const sw = document.createElement("div");
      sw.className = "swatch";
      sw.style.background = hex;
      sw.onclick = () => setColor(hex);
      row.appendChild(sw);
    });
  }
  $("colorPicker").oninput = (e) => setColor(e.target.value);
  $("selPalette").onchange = (e) => renderPalette(PALETTES[e.target.value] || PALETTES.studio);

  // ---------------- Animation Studio ----------------
  $("btnAddFrame").onclick = () => { frames.push(new Uint8Array(gridW * gridH * 3)); activeFrame = frames.length - 1; renderCanvas(); renderFrameStrip(); };
  $("btnDupFrame").onclick = () => { frames.splice(activeFrame + 1, 0, new Uint8Array(frames[activeFrame])); activeFrame++; renderCanvas(); renderFrameStrip(); };
  $("btnDelFrame").onclick = () => { if (frames.length <= 1) return; frames.splice(activeFrame, 1); activeFrame = Math.min(activeFrame, frames.length - 1); renderCanvas(); renderFrameStrip(); };
  $("btnReverseAnim").onclick = () => { frames.reverse(); activeFrame = 0; renderCanvas(); renderFrameStrip(); toast("Reversed frames"); };
  $("btnPingPong").onclick = () => {
    if (frames.length < 2) return toast("Add 2+ frames for ping-pong");
    const back = frames.slice(1, -1).reverse().map((f) => new Uint8Array(f));
    frames = frames.concat(back);
    renderFrameStrip(); toast(`Ping-pong: ${frames.length} frames`);
  };
  $("btnDedupFrames").onclick = () => {
    const out = [frames[0]];
    for (let i = 1; i < frames.length; i++) {
      let same = true;
      for (let k = 0; k < frames[i].length; k++) if (frames[i][k] !== out[out.length - 1][k]) { same = false; break; }
      if (!same) out.push(frames[i]);
    }
    frames = out; activeFrame = 0; renderCanvas(); renderFrameStrip(); toast(`Kept ${frames.length} unique frames`);
  };
  $("btnPlayAnim").onclick = (e) => {
    if (playTimer) { clearInterval(playTimer); playTimer = null; e.target.textContent = "Play"; return; }
    e.target.textContent = "Stop";
    const delay = Math.max(40, Number($("gifDelay").value) || 140);
    playTimer = setInterval(() => { activeFrame = (activeFrame + 1) % frames.length; renderCanvas(); renderFrameStrip(); }, delay);
  };

  function downloadB64Gif(b64, name) {
    const blob = new Blob([base64ToBytes(b64)], { type: "image/gif" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  $("btnExportGif").onclick = async () => {
    try {
      const res = await api("/api/export_gif", { method: "POST", body: JSON.stringify({ width: gridW, height: gridH, delay_ms: Number($("gifDelay").value) || 140, frames: frames.map(bytesToBase64), send: false }) });
      downloadB64Gif(res.gif_b64, `loy_studio_${gridW}x${gridH}.gif`);
      toast(`Downloaded GIF (${res.gif_bytes} B)`);
    } catch (err) { toast(err.message); }
  };

  async function sendCanvasToDisplay(asGif = false) {
    try {
      startTransferPoll();
      const endpoint = asGif || frames.length > 1 ? "/api/export_gif" : "/api/upload_frames";
      const payload = { width: gridW, height: gridH, delay_ms: Number($("gifDelay").value) || 140, frames: frames.map(bytesToBase64), send: true };
      const res = await api(endpoint, { method: "POST", body: JSON.stringify(payload) });
      recordSent(`Canvas (${frames.length}f)`);
      toast(`Sent (${res.wire_frames} BLE frames)`);
    } catch (err) { toast(err.message); } finally { stopTransferPoll(); }
  }
  $("btnSendGif").onclick = () => sendCanvasToDisplay(true);
  $("btnTopSend").onclick = () => sendCanvasToDisplay(frames.length > 1);
  if ($("btnMobCanvasSend")) $("btnMobCanvasSend").onclick = () => sendCanvasToDisplay(frames.length > 1);

  // ---------------- Transfer Progress ----------------
  function startTransferPoll() {
    $("transferBar").classList.remove("hidden");
    clearInterval(transferPoll);
    transferPoll = setInterval(async () => {
      try {
        const { transfer } = await api("/api/transfer");
        if (!transfer.active) return;
        $("transferLabel").textContent = `${transfer.label || "Uploading"} (${transfer.profile})`;
        $("transferFill").style.width = `${transfer.percent}%`;
        $("transferMeta").textContent = `${transfer.percent}% · ETA ${transfer.eta_s}s`;
      } catch (_) {}
    }, 250);
  }
  function stopTransferPoll() {
    clearInterval(transferPoll);
    setTimeout(() => $("transferBar").classList.add("hidden"), 500);
  }
  $("btnCancelTransfer").onclick = () => api("/api/transfer/cancel", { method: "POST" }).then(() => toast("Cancelling transfer...")).catch(() => {});

  // ---------------- Connection & Device ----------------
  function applyStatus(st) {
    connected = st.connected;
    const shortName = st.connected ? (st.device_name || st.address || "Connected") : "Connect";
    const label = st.connected ? `${shortName} (${st.width}×${st.height})` : "Disconnected";
    $("sideConnDot").className = "conn-dot" + (st.connected ? " online" : "");
    $("sideConnLabel").textContent = label;
    $("topConnBadge").className = "conn-pill" + (st.connected ? " online" : "");
    if ($("topConnDot")) $("topConnDot").className = "conn-dot" + (st.connected ? " online" : "");
    if ($("topConnText")) $("topConnText").textContent = st.connected ? `${shortName} · ${st.brightness}` : "Connect";
    $("homeDevName").textContent = st.connected ? shortName : "No Display Connected";
    $("homeDevMeta").textContent = st.connected ? `${st.width}×${st.height} · ${st.mode.toUpperCase()} · Brightness ${st.brightness}/15` : "Tap top pill to connect Bluetooth";
    $("homeBriSlider").value = st.brightness;
    $("homeBriVal").textContent = st.brightness;
    $("btnDevDisconnect").classList.toggle("hidden", !st.connected);
  }

  async function scanBle() {
    switchWorkspace("wsDevice");
    const list = $("devList");
    list.innerHTML = '<p class="muted">Scanning for YS/TL displays (4s)...</p>';
    try {
      const { devices } = await api("/api/scan?timeout=4");
      list.innerHTML = devices.length ? "" : '<p class="muted">No YS/TL displays found. Ensure display is powered on.</p>';
      devices.forEach((d) => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `<div><strong>${d.name}</strong> <span class="muted">${d.rssi} dBm</span></div>`;
        const b = document.createElement("button");
        b.className = "btn sm primary"; b.textContent = "Connect";
        b.onclick = async () => {
          try {
            const st = await api("/api/connect", { method: "POST", body: JSON.stringify({ address: d.address, name: d.name, mock: false }) });
            applyStatus(st); toast(`Connected to ${d.name}`);
          } catch (err) { toast(err.message); }
        };
        row.appendChild(b); list.appendChild(row);
      });
    } catch (err) { list.innerHTML = `<p class="muted">${err.message}</p>`; }
  }

  async function connectMock() {
    const st = await api("/api/connect", { method: "POST", body: JSON.stringify({ mock: true, width: 64, height: 64, name: "YS-SIM-64x64" }) });
    applyStatus(st); toast("Connected to 64×64 Simulator");
  }

  $("btnQuickScan").onclick = scanBle;
  $("btnDevScan").onclick = scanBle;
  $("btnQuickMock").onclick = connectMock;
  $("btnDevMock").onclick = connectMock;
  $("btnDevDisconnect").onclick = async () => { applyStatus(await api("/api/disconnect", { method: "POST" })); toast("Disconnected"); };

  async function sendCtrl(payload, msg) {
    try {
      const res = await api("/api/control", { method: "POST", body: JSON.stringify(payload) });
      applyStatus(res.status);
      if (msg) { toast(msg); recordSent(msg); }
    } catch (err) { toast(err.message); }
  }

  $("btnHomePowerOn").onclick = () => sendCtrl({ action: "power", on: true }, "Power On");
  $("btnHomePowerOff").onclick = () => sendCtrl({ action: "power", on: false }, "Power Off");
  $("homeBriSlider").oninput = (e) => ($("homeBriVal").textContent = e.target.value);
  $("homeBriSlider").onchange = (e) => sendCtrl({ action: "brightness", level: Number(e.target.value) }, `Brightness ${e.target.value}/15`);
  $("btnHomeSyncTime").onclick = () => sendCtrl({ action: "sync_time" }, "RTC Synced");
  $("btnHomeExitMode").onclick = () => sendCtrl({ action: "game_opt", opt: "exit" }, "Exited Clock / Game mode");
  $("devRotate").onchange = (e) => sendCtrl({ action: "rotate", degrees: Number(e.target.value) }, `Rotated ${e.target.value}°`);
  $("devMirror").onchange = (e) => sendCtrl({ action: "mirror", enabled: e.target.checked }, `Mirror ${e.target.checked ? "On" : "Off"}`);
  $("devProfile").onchange = async (e) => {
    try { await api("/api/ble_profile", { method: "POST", body: JSON.stringify({ profile: e.target.value }) }); toast(`Profile: ${e.target.value}`); }
    catch (err) { toast(err.message); }
  };
  $("btnRunBench").onclick = async () => {
    $("benchResult").textContent = "Benchmarking...";
    try {
      const r = await api("/api/benchmark", { method: "POST", body: JSON.stringify({ bytes: 4096 }) });
      $("benchResult").textContent = `${r.measured_bps} B/s (${r.elapsed_s}s, ${r.frames} frames, ${r.hardware_measured ? "BLE" : "Mock"})`;
    } catch (err) { $("benchResult").textContent = err.message; }
  };
  $("btnBriSchedule").onclick = () =>
    sendCtrl({
      action: "brightness_schedule",
      entries: [
        { time: $("schDayTime").value.trim(), level: Number($("schDayLvl").value) || 12 },
        { time: $("schNightTime").value.trim(), level: Number($("schNightLvl").value) || 2 },
      ],
    }, "Hardware Day/Night brightness schedule saved");
  $("btnPowSchedule").onclick = () =>
    sendCtrl({
      action: "power_schedule",
      periods: [$("schPowerWin").value.trim()],
    }, "Hardware power schedule saved");

  // ---------------- Time & Day Auto-Mode Scheduler (Dedicated Tab) ----------------
  const SCHED_DAYS = [
    ["mon", "M"], ["tue", "T"], ["wed", "W"], ["thu", "T"], ["fri", "F"], ["sat", "S"], ["sun", "S"],
  ];
  const SCHED_COLORS = ["#06b6d4", "#10b981", "#3b82f6", "#a855f7", "#f59e0b", "#ec4899", "#ef4444", "#14b8a6", "#6366f1"];
  let workSchedule = { enabled: true, rules: [], matched_rule: null, available_gifs: [], now_day: "mon", now_minutes: 0 };
  let schedPreviewDay = null;

  function hhmmToMin(s) {
    const p = String(s || "00:00").split(":");
    return (Number(p[0]) || 0) * 60 + (Number(p[1]) || 0);
  }

  function buildTargetOptions(selectedKind, selectedTarget) {
    const gifs = workSchedule.available_gifs && workSchedule.available_gifs.length
      ? workSchedule.available_gifs
      : ["09_mode_deep_focus.gif", "10_mode_coding.gif", "11_mode_on_air_meeting.gif", "12_mode_do_not_disturb.gif", "13_mode_coffee_break.gif", "14_mode_pomodoro.gif", "15_mode_gaming_chill.gif", "16_mode_music_vibe.gif", "17_mode_away_brb.gif", "18_mode_done_offline.gif"];
    const curVal = `${selectedKind}:${selectedTarget || ""}`;
    const opts = [];
    opts.push(`<optgroup label="Animated GIFs">`);
    gifs.forEach((g) => {
      const v = `gif:${g}`;
      const label = g.replace(/^\d+_/, "").replace(/\.gif$/i, "").replace(/_/g, " ");
      opts.push(`<option value="${v}" ${v === curVal ? "selected" : ""}>GIF · ${label}</option>`);
    });
    opts.push(`</optgroup><optgroup label="Live Hardware Clocks (RTC)">`);
    [
      ["hw_clock:hw_synthwave_sun", "Live Clock · Synthwave Sunset"],
      ["hw_clock:hw_neon_ring", "Live Clock · Neon Ring"],
      ["hw_clock:hw_pipboy_hud", "Live Clock · Sci-Fi HUD"],
      ["hw_clock:hw_matrix_terminal", "Live Clock · Hacker Terminal"],
      ["hw_clock:hw_gameboy_retro", "Live Clock · Retro Handheld LCD"],
      ["hw_clock:hw_galaxy_stars", "Live Clock · Starlight Night"],
      ["hw_clock:hw_arcade_cabinet", "Live Clock · Arcade Scoreboard"],
      ["hw_clock:hw_heart_bezel", "Live Clock · Neon Heartbeat EKG"],
      ["hw_clock:hw_gradient_pill", "Live Clock · Neon Gradient Pill"],
      ["hw_clock:hw_flip_cards", "Live Clock · Flip Cards"],
      ["hw_clock:hw_cyber_frame", "Live Clock · Cyber Frame"],
      ["hw_clock:hw_stacked", "Live Clock · Giant Stacked"],
      ["hw_clock:hw_seconds", "Live Clock · HH:MM + Seconds"],
      ["hw_clock:hw_date_time", "Live Clock · Date + HH:MM:SS"],
      ["hw_clock:hw_minimal", "Live Clock · Minimal Big"],
    ].forEach(([v, lbl]) => {
      opts.push(`<option value="${v}" ${v === curVal ? "selected" : ""}>${lbl}</option>`);
    });
    opts.push(`</optgroup><optgroup label="Display Power">`);
    opts.push(`<option value="power_off:" ${curVal === "power_off:" ? "selected" : ""}>Power Off Display</option>`);
    opts.push(`</optgroup>`);
    return opts.join("");
  }

  function renderTimeline() {
    const tabs = $("schedDayTabs");
    const bar = $("schedTimelineBar");
    const legend = $("schedTimelineLegend");
    if (!tabs || !bar) return;
    const activeDay = schedPreviewDay || workSchedule.now_day || "mon";
    const dayIdx = SCHED_DAYS.findIndex(([k]) => k === activeDay);
    const prevDay = SCHED_DAYS[(dayIdx + 6) % 7][0];

    tabs.innerHTML = "";
    SCHED_DAYS.forEach(([key]) => {
      const btn = document.createElement("button");
      btn.className = `btn sm${key === activeDay ? " primary" : ""}`;
      btn.textContent = key.toUpperCase();
      btn.style.padding = "3px 8px";
      btn.onclick = () => { schedPreviewDay = key; renderTimeline(); };
      tabs.appendChild(btn);
    });

    bar.innerHTML = "";
    legend.innerHTML = "";
    const seenLegend = new Set();

    // Draw segments in reverse priority order so higher-priority (earlier) rules paint on top
    const rules = workSchedule.rules || [];
    for (let i = rules.length - 1; i >= 0; i--) {
      const r = rules[i];
      if (!r.enabled) continue;
      const days = (r.days || []).map((d) => d.toLowerCase());
      const s = hhmmToMin(r.start), e = hhmmToMin(r.end);
      const col = SCHED_COLORS[i % SCHED_COLORS.length];
      const segs = [];
      if (s <= e) {
        if (days.includes(activeDay) && e > s) segs.push([s, e]);
      } else {
        if (days.includes(activeDay)) segs.push([s, 1440]);
        if (days.includes(prevDay) && e > 0) segs.push([0, e]);
      }
      segs.forEach(([a, b]) => {
        const el = document.createElement("div");
        el.title = `${r.name} (${r.start}–${r.end} · Bri ${r.brightness ?? 12})`;
        el.style.cssText = `position:absolute;top:0;bottom:0;left:${(a / 1440) * 100}%;width:${((b - a) / 1440) * 100}%;background:${col};opacity:0.82;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;color:#fff;overflow:hidden;white-space:nowrap;padding:0 3px;border-right:1px solid rgba(0,0,0,0.35);`;
        if (b - a >= 90) el.textContent = r.name;
        bar.appendChild(el);
        if (!seenLegend.has(r.id)) {
          seenLegend.add(r.id);
          const item = document.createElement("span");
          item.className = "inline";
          item.style.gap = "5px";
          item.innerHTML = `<span style="width:10px;height:10px;border-radius:3px;background:${col};display:inline-block;"></span>${r.name} <span class="muted">(${r.start}–${r.end})</span>`;
          legend.appendChild(item);
        }
      });
    }

    // Live NOW needle if viewing today
    if (activeDay === workSchedule.now_day) {
      const nowLine = document.createElement("div");
      nowLine.title = `Now (${workSchedule.now})`;
      nowLine.style.cssText = `position:absolute;top:0;bottom:0;left:${((workSchedule.now_minutes || 0) / 1440) * 100}%;width:2px;background:#fff;box-shadow:0 0 6px #fff;z-index:5;`;
      bar.appendChild(nowLine);
    }
  }

  function renderWorkSchedule() {
    const list = $("schedRulesList");
    if (!list) return;
    $("schedEnabled").checked = Boolean(workSchedule.enabled);
    const matchedId = workSchedule.matched_rule ? workSchedule.matched_rule.id : null;
    const activeLabel = workSchedule.matched_rule
      ? `${workSchedule.matched_rule.name} (${workSchedule.now || ""})`
      : `No match (${workSchedule.now || ""})`;
    $("schedActiveBadge").textContent = workSchedule.matched_rule ? `Active: ${activeLabel}` : activeLabel;
    if ($("topSchedText")) {
      $("topSchedText").textContent = workSchedule.enabled && workSchedule.matched_rule
        ? workSchedule.matched_rule.name
        : (workSchedule.enabled ? "Schedule Idle" : "Schedule Paused");
    }
    if ($("homeSchedLink")) {
      $("homeSchedLink").textContent = workSchedule.enabled && workSchedule.matched_rule
        ? `Auto: ${workSchedule.matched_rule.name}`
        : "Auto Schedule";
    }
    renderTimeline();

    list.innerHTML = "";
    (workSchedule.rules || []).forEach((rule, idx) => {
      const isLive = rule.id === matchedId;
      const col = SCHED_COLORS[idx % SCHED_COLORS.length];
      const card = document.createElement("div");
      card.className = `sched-card${isLive ? " live" : ""}${rule.enabled ? "" : " disabled"}`;

      const thumbHtml = rule.kind === "gif" && rule.target
        ? `<img src="/gifs/${rule.target}" alt="" style="width:44px;height:44px;border-radius:9px;image-rendering:pixelated;border:1px solid var(--border);background:#000;flex-shrink:0;" />`
        : rule.kind === "hw_clock" && rule.target
          ? `<img src="/api/hw_clock_preview/${rule.target}" alt="" style="width:44px;height:44px;border-radius:9px;image-rendering:pixelated;border:1px solid var(--border);background:#000;flex-shrink:0;" />`
          : `<div style="width:44px;height:44px;border-radius:9px;border:1px solid var(--border);background:#000;display:grid;place-items:center;color:var(--danger);flex-shrink:0;"><svg class="ico"><use href="#i-power"/></svg></div>`;

      const dayChipsHtml = SCHED_DAYS.map(([dk, lbl]) => {
        const on = (rule.days || []).includes(dk);
        return `<button type="button" class="sched-day-btn" data-day="${dk}" style="${on ? `background:${col};border-color:${col};color:#fff;` : ""}">${lbl}</button>`;
      }).join("");

      card.innerHTML = `
        <div class="sched-top">
          <div class="inline" style="gap:10px;flex:1;min-width:0;flex-wrap:nowrap;">
            ${thumbHtml}
            <div style="flex:1;min-width:0;">
              <input type="text" data-field="name" value="${(rule.name || "").replace(/"/g, "&quot;")}" style="width:100%;font-weight:700;padding:6px 9px;" />
            </div>
          </div>
          <input type="checkbox" data-field="enabled" ${rule.enabled ? "checked" : ""} title="Enable / disable rule" />
        </div>
        <div class="sched-days-grid">${dayChipsHtml}</div>
        <div class="sched-controls-grid">
          <div class="inline" style="gap:5px;flex-wrap:nowrap;">
            <input type="time" data-field="start" value="${rule.start || "09:00"}" style="flex:1;" />
            <span class="muted">→</span>
            <input type="time" data-field="end" value="${rule.end || "17:00"}" style="flex:1;" />
          </div>
          <select data-field="target" style="width:100%;">${buildTargetOptions(rule.kind, rule.target)}</select>
          <label class="inline muted" style="gap:5px;justify-content:flex-end;" title="Brightness (1-15)">
            <svg class="ico sm"><use href="#i-sun"/></svg>
            <input type="number" data-field="brightness" min="1" max="15" value="${rule.brightness ?? 12}" style="width:54px;" />
          </label>
        </div>
        <div class="sched-footer">
          <button class="btn sm primary" data-act="test"><svg class="ico sm"><use href="#i-play"/></svg> Test on Panel</button>
          <div class="inline" style="gap:5px;flex-wrap:nowrap;">
            <button class="btn sm icon-btn" data-act="up" ${idx === 0 ? "disabled" : ""} title="Priority Up"><svg class="ico sm"><use href="#i-up"/></svg></button>
            <button class="btn sm icon-btn" data-act="down" ${idx === workSchedule.rules.length - 1 ? "disabled" : ""} title="Priority Down"><svg class="ico sm"><use href="#i-down"/></svg></button>
            <button class="btn sm icon-btn" data-act="dup" title="Duplicate"><svg class="ico sm"><use href="#i-copy"/></svg></button>
            <button class="btn sm danger icon-btn" data-act="del" title="Delete"><svg class="ico sm"><use href="#i-trash"/></svg></button>
          </div>
        </div>
      `;

      card.querySelector('[data-field="enabled"]').onchange = (e) => { rule.enabled = e.target.checked; saveWorkSchedule(); };
      card.querySelector('[data-field="name"]').onchange = (e) => { rule.name = e.target.value.trim() || "Mode"; saveWorkSchedule(); };
      card.querySelector('[data-field="start"]').onchange = (e) => { rule.start = e.target.value; saveWorkSchedule(); };
      card.querySelector('[data-field="end"]').onchange = (e) => { rule.end = e.target.value; saveWorkSchedule(); };
      card.querySelector('[data-field="brightness"]').onchange = (e) => { rule.brightness = Math.max(1, Math.min(15, Number(e.target.value) || 12)); saveWorkSchedule(); };
      card.querySelector('[data-field="target"]').onchange = (e) => {
        const [k, t] = e.target.value.split(":");
        rule.kind = k;
        rule.target = t || "";
        saveWorkSchedule();
      };
      card.querySelectorAll("[data-day]").forEach((btn) => {
        btn.onclick = () => {
          const dk = btn.dataset.day;
          const set = new Set(rule.days || []);
          if (set.has(dk)) set.delete(dk); else set.add(dk);
          rule.days = SCHED_DAYS.map(([k]) => k).filter((k) => set.has(k));
          saveWorkSchedule();
        };
      });
      card.querySelector('[data-act="test"]').onclick = async () => {
        try {
          await api("/api/work_schedule/test_rule", { method: "POST", body: JSON.stringify({ rule }) });
          toast(`Sent "${rule.name}" to display`);
        } catch (err) { toast(err.message); }
      };
      card.querySelector('[data-act="up"]').onclick = () => {
        if (idx <= 0) return;
        [workSchedule.rules[idx - 1], workSchedule.rules[idx]] = [workSchedule.rules[idx], workSchedule.rules[idx - 1]];
        saveWorkSchedule();
      };
      card.querySelector('[data-act="down"]').onclick = () => {
        if (idx >= workSchedule.rules.length - 1) return;
        [workSchedule.rules[idx + 1], workSchedule.rules[idx]] = [workSchedule.rules[idx], workSchedule.rules[idx + 1]];
        saveWorkSchedule();
      };
      card.querySelector('[data-act="dup"]').onclick = () => {
        const copy = JSON.parse(JSON.stringify(rule));
        copy.id = "rule_" + Date.now();
        copy.name = `${rule.name} (Copy)`;
        workSchedule.rules.splice(idx + 1, 0, copy);
        saveWorkSchedule();
      };
      card.querySelector('[data-act="del"]').onclick = () => {
        workSchedule.rules.splice(idx, 1);
        saveWorkSchedule();
      };
      list.appendChild(card);
    });
  }

  async function loadWorkSchedule() {
    try {
      workSchedule = await api("/api/work_schedule");
      renderWorkSchedule();
    } catch (_) {}
  }

  async function saveWorkSchedule() {
    try {
      const res = await api("/api/work_schedule", {
        method: "POST",
        body: JSON.stringify({ enabled: $("schedEnabled").checked, rules: workSchedule.rules }),
      });
      workSchedule.enabled = res.enabled;
      workSchedule.rules = res.rules;
      workSchedule.matched_rule = res.matched_rule;
      workSchedule.now = res.now || workSchedule.now;
      workSchedule.now_day = res.now_day || workSchedule.now_day;
      workSchedule.now_minutes = res.now_minutes ?? workSchedule.now_minutes;
      renderWorkSchedule();
      toast("Schedule saved");
    } catch (err) { toast(err.message); }
  }

  function addNewScheduleRule() {
    workSchedule.rules.unshift({
      id: "rule_" + Date.now(),
      enabled: true,
      name: "New Mode",
      days: ["mon", "tue", "wed", "thu", "fri"],
      start: "10:00",
      end: "12:00",
      kind: "gif",
      target: "09_mode_deep_focus.gif",
      brightness: 12,
    });
    saveWorkSchedule();
  }

  if ($("schedEnabled")) $("schedEnabled").onchange = () => saveWorkSchedule();
  if ($("btnSchedApplyNow")) $("btnSchedApplyNow").onclick = async () => {
    try {
      const res = await api("/api/work_schedule/apply", { method: "POST" });
      toast(`Applied: ${res.applied.name}`);
      loadWorkSchedule();
    } catch (err) { toast(err.message); }
  };
  if ($("btnSchedAdd")) $("btnSchedAdd").onclick = addNewScheduleRule;
  if ($("btnSchedAddTop")) $("btnSchedAddTop").onclick = addNewScheduleRule;
  document.querySelectorAll("[data-sched-preset]").forEach((btn) => {
    btn.onclick = async () => {
      try {
        const res = await api("/api/work_schedule/preset", { method: "POST", body: JSON.stringify({ preset: btn.dataset.schedPreset }) });
        workSchedule.rules = res.rules;
        workSchedule.matched_rule = res.matched_rule;
        workSchedule.now = res.now;
        renderWorkSchedule();
        toast(`Loaded "${btn.textContent}" preset`);
      } catch (err) { toast(err.message); }
    };
  });

  // ---------------- Image, Video, Text, QR, Procedural, Avatar ----------------
  async function runImageStudio(send) {
    const file = $("imgFile").files[0];
    if (!file) return toast("Select an image first");
    const data_b64 = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
    try {
      const res = await api("/api/process_image", {
        method: "POST",
        body: JSON.stringify({
          data_b64, width: gridW, height: gridH, fit: $("imgFit").value,
          brightness: Number($("imgBri").value), contrast: Number($("imgCon").value),
          saturation: Number($("imgSat").value), colors: Number($("imgColors").value),
          dither: $("imgDither").checked, send,
        }),
      });
      frames = [base64ToBytes(res.frame_b64)]; activeFrame = 0; renderCanvas(); renderFrameStrip();
      $("imgEst").textContent = `YSTP01: ${res.ystp_bytes} B · Est. transfer: ~${res.estimated_s}s`;
      if (send) { recordSent(`Image: ${file.name}`); toast(`Sent (${res.wire_frames} BLE frames)`); }
    } catch (err) { toast(err.message); }
  }
  $("btnImgPreview").onclick = () => runImageStudio(false);
  $("btnImgSend").onclick = () => runImageStudio(true);
  $("btnImgPalette").onclick = async () => {
    const file = $("imgFile").files[0];
    if (!file) return toast("Select an image first");
    const res = await api("/api/extract_palette", { method: "POST", body: JSON.stringify({ data_b64: bytesToBase64(new Uint8Array(await file.arrayBuffer())), colors: 12 }) });
    renderPalette(res.palette); toast("Extracted 12-color palette into Pixel Art");
  };

  async function runVideoStudio(send) {
    const file = $("vidFile").files[0];
    if (!file) return toast("Select a video file first");
    $("vidEst").textContent = "Converting with local FFmpeg...";
    try {
      const res = await api("/api/upload_video", {
        method: "POST",
        body: JSON.stringify({
          data_b64: bytesToBase64(new Uint8Array(await file.arrayBuffer())),
          filename: file.name, width: gridW, height: gridH,
          start_s: Number($("vidStart").value) || 0, duration_s: Number($("vidDur").value) || 3,
          fps: Number($("vidFps").value) || 10, send,
        }),
      });
      $("vidEst").textContent = `Output GIF: ${res.gif_bytes} B · Est. transfer: ~${res.estimated_s}s`;
      if (!send) downloadB64Gif(res.gif_b64, `${file.name}.gif`);
      else { recordSent(`Video: ${file.name}`); toast(`Sent Video GIF (${res.wire_frames} frames)`); }
    } catch (err) { $("vidEst").textContent = err.message; toast(err.message); }
  }
  $("btnVidPreview").onclick = () => runVideoStudio(false);
  $("btnVidSend").onclick = () => runVideoStudio(true);

  $("btnSendText").onclick = async () => {
    try {
      const res = await api("/api/send_text", { method: "POST", body: JSON.stringify({ text: $("txtMsg").value, color: $("txtFg").value, bg: $("txtBg").value, scroll: $("txtScroll").value }) });
      recordSent(`Text: ${$("txtMsg").value}`); toast(`Sent text (${res.wire_frames} frames)`);
    } catch (err) { toast(err.message); }
  };

  function buildQrPayload() {
    const t = $("qrType").value, v1 = $("qrInput1").value.trim(), v2 = $("qrInput2").value.trim();
    if (t === "wifi") return `WIFI:S:${v1};T:WPA;P:${v2};;`;
    if (t === "vcard") return `BEGIN:VCARD\nVERSION:3.0\nFN:${v1}\nTEL:${v2}\nEND:VCARD`;
    return v1;
  }
  async function runQr(send) {
    try {
      const res = await api("/api/qrcode", { method: "POST", body: JSON.stringify({ text: buildQrPayload(), width: gridW, height: gridH, send }) });
      frames = [base64ToBytes(res.frame_b64)]; activeFrame = 0; renderCanvas(); renderFrameStrip();
      toast(send ? `QR sent (${res.wire_frames} frames)` : "QR preview loaded");
    } catch (err) { toast(err.message); }
  }
  $("btnQrPrev").onclick = () => runQr(false);
  $("btnQrSend").onclick = () => runQr(true);

  async function runSeq(send) {
    const files = Array.from($("seqFiles").files || []);
    if (!files.length) return toast("Select 2+ images first");
    const imgs = [];
    for (const f of files) imgs.push(bytesToBase64(new Uint8Array(await f.arrayBuffer())));
    try {
      const res = await api("/api/upload_sequence", { method: "POST", body: JSON.stringify({ images: imgs, width: gridW, height: gridH, delay_ms: Number($("seqDelay").value) || 400, mode: $("seqMode").value, send }) });
      frames = res.frames.map(base64ToBytes); activeFrame = 0; renderCanvas(); renderFrameStrip();
      toast(send ? `Sent sequence (${res.wire_frames} frames)` : `Loaded ${res.count} frames`);
    } catch (err) { toast(err.message); }
  }
  $("btnSeqLoad").onclick = () => runSeq(false);
  $("btnSeqSend").onclick = () => runSeq(true);

  async function runProcedural(send) {
    try {
      const res = await api("/api/procedural", {
        method: "POST",
        body: JSON.stringify({
          effect: $("procEffect").value, width: gridW, height: gridH,
          color1: $("procC1").value, color2: $("procC2").value,
          density: Number($("procDensity").value), frames: Number($("procFrames").value), send,
        }),
      });
      frames = res.frames.map(base64ToBytes); activeFrame = 0; renderCanvas(); renderFrameStrip();
      $("procEst").textContent = `GIF: ${res.gif_bytes} B · Est. transfer: ~${res.estimated_s}s`;
      if (send) { recordSent(`Procedural: ${res.effect}`); toast(`Sent (${res.wire_frames} BLE frames)`); }
    } catch (err) { toast(err.message); }
  }
  $("btnProcPreview").onclick = () => runProcedural(false);
  $("btnProcSend").onclick = () => runProcedural(true);

  let avatarSeed = 101;
  async function runAvatar(send) {
    try {
      const res = await api("/api/avatar", { method: "POST", body: JSON.stringify({ seed: avatarSeed, width: gridW, height: gridH, primary: $("avPrimary").value, accent: $("avAccent").value, bg: $("avBg").value, send }) });
      frames = [base64ToBytes(res.frame_b64)]; activeFrame = 0; renderCanvas(); renderFrameStrip();
      if (send) { recordSent(`Avatar #${avatarSeed}`); toast("Avatar sent"); }
    } catch (err) { toast(err.message); }
  }
  $("btnAvRandom").onclick = () => { avatarSeed = Math.floor(Math.random() * 99999); runAvatar(false); };
  $("btnAvSend").onclick = () => runAvatar(true);

  // ---------------- Explore: Clocks, Games, GIF Pack, Online Search ----------------
  const HW_CLOCK_CATALOG = [
    ["hw_synthwave_sun", "Synthwave Sun"],
    ["hw_neon_ring", "Neon Ring"],
    ["hw_pipboy_hud", "Sci-Fi HUD"],
    ["hw_matrix_terminal", "Hacker Terminal"],
    ["hw_gameboy_retro", "Retro Handheld"],
    ["hw_galaxy_stars", "Starlight Night"],
    ["hw_arcade_cabinet", "Arcade Board"],
    ["hw_heart_bezel", "Heartbeat EKG"],
    ["hw_gradient_pill", "Gradient Pill"],
    ["hw_flip_cards", "Flip Cards"],
    ["hw_cyber_frame", "Cyber Frame"],
    ["hw_stacked", "Giant Stacked"],
    ["hw_seconds", "HH:MM + Sec"],
    ["hw_date_time", "Date + Time"],
    ["hw_minimal", "Minimal Big"],
  ];

  function renderHwClockGallery() {
    const grid = $("hwClockGallery");
    if (!grid) return;
    const selected = $("hwClkShape").value;
    const fg = encodeURIComponent($("hwClkFg").value);
    const acc = encodeURIComponent($("hwClkAccent").value);
    grid.innerHTML = "";
    HW_CLOCK_CATALOG.forEach(([id, label]) => {
      const card = document.createElement("div");
      const active = id === selected;
      card.className = `clock-card${active ? " active" : ""}`;
      card.innerHTML = `
        <img src="/api/hw_clock_preview/${id}?color=${fg}&accent=${acc}" alt="${label}" />
        <div class="clock-card-title">${label}</div>
        <button type="button" class="btn sm ${active ? "primary" : ""} full" data-send-clk="${id}">Send</button>
      `;
      card.onclick = (e) => {
        if (e.target.closest("[data-send-clk]")) return;
        $("hwClkShape").value = id;
        renderHwClockGallery();
      };
      card.querySelector("[data-send-clk]").onclick = () => {
        $("hwClkShape").value = id;
        renderHwClockGallery();
        $("btnHwClkSend").click();
      };
      grid.appendChild(card);
    });
  }

  $("hwClkShape").onchange = renderHwClockGallery;
  $("hwClkFg").onchange = renderHwClockGallery;
  $("hwClkAccent").onchange = renderHwClockGallery;
  renderHwClockGallery();

  $("btnHwClkSend").onclick = async () => {
    try {
      const res = await api("/api/hw_text_clock", {
        method: "POST",
        body: JSON.stringify({
          shape: $("hwClkShape").value,
          color: $("hwClkFg").value,
          accent: $("hwClkAccent").value,
          bg_gif: $("hwClkBgGif") ? $("hwClkBgGif").value : "",
          bg_mode: $("hwClkBgMode") ? $("hwClkBgMode").value : "behind",
          sync_first: true,
        }),
      });
      recordSent(`HW Clock: ${res.shape}`); toast(`Uploaded live RTC clock (${res.wire_frames} frames)`);
    } catch (err) { toast(err.message); }
  };
  $("btnFwClkApply").onclick = () => sendCtrl({ action: "clock", sync_first: true, style: Number($("fwClkFmt").value) || 0, clock_style: 0, date_style: 0, color: $("fwClkCol").value }, "Firmware clock applied");

  $("btnGameLaunch").onclick = () => sendCtrl({ action: "game_launch", game_id: Number($("selGameId").value) }, "Game launched");
  document.querySelectorAll(".game-opt").forEach((b) => (b.onclick = () => sendCtrl({ action: "game_opt", opt: b.dataset.opt })));

  async function loadExploreGifs() {
    const grid = $("exploreGifGrid");
    try {
      const { gifs } = await api("/api/gif_pack");
      grid.innerHTML = "";
      gifs.forEach((g) => {
        const tile = document.createElement("div");
        tile.className = "art-tile";
        tile.innerHTML = `<img src="${g.url}" /><div class="art-tile-name">${g.file.replace(/^\d+_/, "").replace(".gif", "")}</div>`;
        const b = document.createElement("button");
        b.className = "btn sm primary full"; b.textContent = "Send";
        b.onclick = async () => {
          try {
            const res = await api("/api/gif_pack/send", { method: "POST", body: JSON.stringify({ file: g.file }) });
            recordSent(g.file); toast(`Sent ${g.file} (${res.wire_frames} frames)`);
          } catch (err) { toast(err.message); }
        };
        tile.appendChild(b); grid.appendChild(tile);
      });
    } catch (_) {}
  }

  $("btnGifSearch").onclick = async () => {
    const grid = $("gifSearchGrid");
    grid.innerHTML = '<p class="muted">Searching...</p>';
    try {
      const params = new URLSearchParams({ provider: $("gifProvider").value, api_key: $("gifApiKey").value.trim(), q: $("gifQuery").value.trim(), limit: "12" });
      const { results } = await api(`/api/gif_search?${params}`);
      grid.innerHTML = results.length ? "" : '<p class="muted">No results.</p>';
      results.forEach((g, idx) => {
        const tile = document.createElement("div");
        tile.className = "art-tile";
        tile.innerHTML = `<img src="${g.preview}" />`;
        const b = document.createElement("button");
        b.className = "btn sm primary full"; b.textContent = "Select & Send";
        b.onclick = async () => {
          b.disabled = true; b.textContent = "Sending...";
          try {
            const res = await api("/api/gif_fetch_send", { method: "POST", body: JSON.stringify({ url: g.url, save_name: `web_${g.id || idx}`, width: gridW, height: gridH, send: connected }) });
            toast(connected ? `Sent (${res.gif_bytes} B)` : `Saved locally (${res.gif_bytes} B)`);
            loadExploreGifs();
          } catch (err) { toast(err.message); } finally { b.disabled = false; b.textContent = "Select & Send"; }
        };
        tile.appendChild(b); grid.appendChild(tile);
      });
    } catch (err) { grid.innerHTML = `<p class="muted">${err.message}</p>`; }
  };

  // ---------------- Library ----------------
  $("btnSaveCreation").onclick = async () => {
    const name = $("saveArtName").value.trim() || `Art ${new Date().toLocaleTimeString()}`;
    await api("/api/library", { method: "POST", body: JSON.stringify({ name, width: gridW, height: gridH, frames: frames.map(bytesToBase64) }) });
    $("saveArtName").value = ""; toast(`Saved "${name}"`); loadLibrary();
  };

  async function loadLibrary() {
    try {
      const { items } = await api("/api/library");
      const q = ($("libSearch")?.value || "").toLowerCase();
      const favOnly = $("libFavOnly")?.checked;
      const filtered = items.filter((it) => (!q || it.name.toLowerCase().includes(q)) && (!favOnly || it.favorite));

      const renderInto = (container, list) => {
        if (!container) return;
        container.innerHTML = list.length ? "" : '<p class="muted">No saved creations.</p>';
        list.forEach((it) => {
          const tile = document.createElement("div");
          tile.className = "art-tile";
          const c = document.createElement("canvas");
          c.width = it.width; c.height = it.height;
          const b0 = base64ToBytes(it.frames[0]);
          const idata = c.getContext("2d").createImageData(it.width, it.height);
          for (let p = 0; p < it.width * it.height; p++) {
            idata.data[p * 4] = b0[p * 3]; idata.data[p * 4 + 1] = b0[p * 3 + 1]; idata.data[p * 4 + 2] = b0[p * 3 + 2]; idata.data[p * 4 + 3] = 255;
          }
          c.getContext("2d").putImageData(idata, 0, 0);
          tile.appendChild(c);
          const nm = document.createElement("div"); nm.className = "art-tile-name"; nm.textContent = it.name; tile.appendChild(nm);
          const row = document.createElement("div"); row.className = "inline";
          const loadB = document.createElement("button"); loadB.className = "btn sm"; loadB.textContent = "Edit";
          loadB.onclick = () => { frames = it.frames.map(base64ToBytes); activeFrame = 0; renderCanvas(); renderFrameStrip(); switchWorkspace("wsCreate"); };
          const favB = document.createElement("button"); favB.className = "btn sm"; favB.textContent = it.favorite ? "★" : "☆";
          favB.onclick = async () => { await api("/api/library", { method: "POST", body: JSON.stringify({ ...it, favorite: !it.favorite }) }); loadLibrary(); };
          const delB = document.createElement("button"); delB.className = "btn sm danger"; delB.textContent = "×";
          delB.onclick = async () => { await api(`/api/library/${it.id}`, { method: "DELETE" }); loadLibrary(); };
          row.append(loadB, favB, delB); tile.appendChild(row); container.appendChild(tile);
        });
      };
      renderInto($("libGrid"), filtered);
      renderInto($("homeFavorites"), items.filter((x) => x.favorite));
    } catch (_) {}
  }
  $("libSearch").oninput = loadLibrary;
  $("libFavOnly").onchange = loadLibrary;
  $("btnSlotShow").onclick = () => sendCtrl({ action: "dispatch_slot", slot: Number($("slotNum").value) || 1 }, "Switched slot");
  $("btnSlotDel").onclick = () => sendCtrl({ action: "delete_slots", slots: [Number($("slotNum").value) || 1] }, "Deleted slot");
  $("btnPlaylistLoop").onclick = () => {
    const ids = $("playlistInput").value.split(",").map((x) => parseInt(x.trim(), 10)).filter((n) => !isNaN(n) && n > 0);
    if (ids.length) sendCtrl({ action: "pgm_play", model: 2, index: 0, slots: ids }, `Looping slots ${ids.join(",")}`);
  };

  // ---------------- Live Widgets ----------------
  async function refreshSystemStats() {
    try {
      const { system, now_playing } = await api("/api/system_stats");
      $("sysStatsReadout").textContent = `CPU: ${system.cpu_pct}% (load ${system.load1}) · RAM: ${system.ram_pct}%`;
      $("nowPlayingReadout").textContent = `Now Playing (${now_playing.app}): ${now_playing.track}`;
    } catch (_) {}
    refreshSmartConfig();
  }

  async function refreshSmartConfig() {
    try {
      const { config, context, active_override, weather_cache } = await api("/api/smart_config");
      if ($("ctxEnabled")) {
        $("ctxEnabled").checked = Boolean(config.context_enabled);
        $("ctxOnAir").checked = Boolean(config.auto_on_air);
        $("ctxCoding").checked = Boolean(config.auto_coding_app);
        $("ctxIdle").checked = Boolean(config.auto_idle_brb);
        $("ctxIdleMin").value = config.idle_minutes ?? 5;
        $("ctxCal").checked = Boolean(config.calendar_alerts);
        $("wxCity").value = config.weather_city || "Berlin";
        $("wxAuto").checked = Boolean(config.weather_enabled);
        $("hydEnabled").checked = Boolean(config.hydration_enabled);
        $("hydMin").value = config.hydration_interval_min ?? 60;
      }
      if ($("macCtxReadout") && context) {
        $("macCtxReadout").textContent = `App: ${context.front_app || "—"} · Call: ${context.in_call ? "ON AIR 🔴" : "No"} · Idle: ${context.idle_seconds}s${active_override ? ` · Override: ${active_override}` : ""}`;
      }
      if ($("wxReadout") && weather_cache) {
        $("wxReadout").textContent = `Last: ${weather_cache.city} · ${weather_cache.temp_c}°C (${weather_cache.kind})`;
      }
    } catch (_) {}
  }

  async function saveSmartConfig() {
    if (!$("ctxEnabled")) return;
    try {
      await api("/api/smart_config", {
        method: "POST",
        body: JSON.stringify({
          context_enabled: $("ctxEnabled").checked,
          auto_on_air: $("ctxOnAir").checked,
          auto_coding_app: $("ctxCoding").checked,
          auto_idle_brb: $("ctxIdle").checked,
          idle_minutes: Number($("ctxIdleMin").value) || 5,
          calendar_alerts: $("ctxCal").checked,
          weather_enabled: $("wxAuto").checked,
          weather_city: $("wxCity").value.trim() || "Berlin",
          hydration_enabled: $("hydEnabled").checked,
          hydration_interval_min: Number($("hydMin").value) || 60,
        }),
      });
      toast("Smart desk settings saved");
      refreshSmartConfig();
    } catch (err) { toast(err.message); }
  }

  ["ctxEnabled", "ctxOnAir", "ctxCoding", "ctxIdle", "ctxIdleMin", "ctxCal", "wxAuto", "hydEnabled", "hydMin"].forEach((id) => {
    if ($(id)) $(id).onchange = saveSmartConfig;
  });
  if ($("btnWxSend")) $("btnWxSend").onclick = async () => {
    try {
      const res = await api("/api/weather_clock", { method: "POST", body: JSON.stringify({ city: $("wxCity").value.trim() || "Berlin" }) });
      $("wxReadout").textContent = `Live: ${res.weather.city} · ${res.weather.temp_c}°C (${res.weather.kind})`;
      toast(`Sent ${res.weather.city} ${res.weather.temp_c}°C Weather Clock!`);
    } catch (err) { toast(err.message); }
  };
  if ($("btnNtfFlash")) $("btnNtfFlash").onclick = async () => {
    try {
      await api("/api/notify", {
        method: "POST",
        body: JSON.stringify({
          title: $("ntfTitle").value.trim() || "ALERT",
          subtitle: $("ntfSub").value.trim(),
          style: $("ntfStyle").value,
          duration_s: Number($("ntfDur").value) || 8,
        }),
      });
      toast("Alert flashed on display (auto-restores Slot 1)");
    } catch (err) { toast(err.message); }
  };
  if ($("btnHydrateNow")) $("btnHydrateNow").onclick = async () => {
    try {
      await api("/api/hydration_now", { method: "POST", body: JSON.stringify({ duration_s: 10 }) });
      toast("💧 Hydration reminder flashed!");
    } catch (err) { toast(err.message); }
  };
  $("btnSendSysWidget").onclick = () => api("/api/widget_frame", { method: "POST", body: JSON.stringify({ kind: "system", send: connected }) }).then((r) => { frames = [base64ToBytes(r.frame_b64)]; activeFrame = 0; renderCanvas(); toast("System widget rendered"); }).catch((e) => toast(e.message));
  $("btnSendNpWidget").onclick = () => api("/api/widget_frame", { method: "POST", body: JSON.stringify({ kind: "now_playing", send: connected }) }).then((r) => { frames = [base64ToBytes(r.frame_b64)]; activeFrame = 0; renderCanvas(); toast("Now Playing widget rendered"); }).catch((e) => toast(e.message));
  $("btnSendCard").onclick = () => api("/api/widget_frame", { method: "POST", body: JSON.stringify({ kind: "focus", title: $("cardTitle").value, subtitle: $("cardSub").value, accent: $("cardColor").value, send: connected }) }).then((r) => { frames = [base64ToBytes(r.frame_b64)]; activeFrame = 0; renderCanvas(); toast("Status card sent"); }).catch((e) => toast(e.message));

  let pomoRem = 1500, pomoTot = 1500;
  $("btnPomoStart").onclick = () => {
    clearInterval(pomoTimer);
    pomoTot = (Number($("pomoMin").value) || 25) * 60;
    pomoRem = pomoTot;
    const tick = () => {
      const mm = String(Math.floor(pomoRem / 60)).padStart(2, "0"), ss = String(pomoRem % 60).padStart(2, "0");
      $("pomoReadout").textContent = `${mm}:${ss}`;
      if (pomoRem % 15 === 0 || pomoRem === pomoTot) {
        api("/api/widget_frame", { method: "POST", body: JSON.stringify({ kind: "pomodoro", remaining_s: pomoRem, total_s: pomoTot, send: connected }) }).catch(() => {});
      }
      if (pomoRem > 0) pomoRem--; else clearInterval(pomoTimer);
    };
    tick();
    pomoTimer = setInterval(tick, 1000);
    toast("Pomodoro started (syncs to display every 15s)");
  };
  $("btnPomoStop").onclick = () => { clearInterval(pomoTimer); toast("Pomodoro stopped"); };

  $("btnMicEnable").onclick = () => sendCtrl({ action: "music_spectrum", style: Number($("micStyle").value) || 0, use_device_mic: true }, "Mic rhythm enabled");
  $("btnAmbApply").onclick = () => sendCtrl({ action: "ambient_light", style_light: Number($("ambMode").value), color: $("ambCol").value }, "Ambient light applied");
  $("btnCdStart").onclick = () => sendCtrl({ action: "countdown", hours: 0, minutes: Number($("cdM").value) || 0, seconds: Number($("cdS").value) || 0 }, "Countdown started");
  $("btnSbSet").onclick = () => sendCtrl({ action: "scoreboard", red: Number($("sbR").value) || 0, blue: Number($("sbB").value) || 0 }, "Scoreboard updated");

  // ---------------- Developer Diagnostics ----------------
  async function refreshDiagnostics() {
    try {
      const d = await api("/api/diagnostics");
      $("diagSummary").textContent = `Profile: ${d.profile} · MTU: ${d.negotiated_mtu ?? "—"} · RSSI: ${d.last_rssi ?? "—"} · Measured: ${d.last_measured_bps ?? "—"} B/s`;
      const log = $("packetLog");
      log.innerHTML = d.recent_packets.map((p) => `<div class="pkt-row ${p.dir}"><strong>${p.dir}</strong> (${p.len}B) ${p.hex.slice(0, 140)}</div>`).join("");
    } catch (_) {}
  }
  $("btnRefreshPkts").onclick = refreshDiagnostics;
  $("btnExportDiag").onclick = async () => {
    const d = await api("/api/diagnostics");
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "loy_studio_diagnostics.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  $("btnClearFlash").onclick = () => {
    if (confirm("Clear ALL stored programs from the display's internal flash?")) {
      sendCtrl({ action: "delete_all" }, "Cleared device flash");
    }
  };

  // ---------------- Connect from Phone & Real-Time WebSocket Sync ----------------
  function renderClientsList(clients) {
    if ($("clientCount")) $("clientCount").textContent = clients.length;
    if ($("connectedClientsList")) {
      $("connectedClientsList").innerHTML = clients.length
        ? clients.map((c) => `<div><strong>${c.kind}</strong> · ${c.ip} <span class="muted">(since ${c.connected_at})</span></div>`).join("")
        : "No active browser clients.";
    }
  }

  async function loadPhoneConnect(rotate = false) {
    try {
      const data = await api(rotate ? "/api/phone_connect/rotate" : "/api/phone_connect", { method: rotate ? "POST" : "GET" });
      $("phoneQrImg").src = "data:image/png;base64," + data.qr_png_b64;
      $("phoneUrlBox").value = data.url;
      $("lanStatusBadge").textContent = data.lan_enabled ? `LAN Active (${data.lan_ip})` : "Localhost Only";
      $("lanStatusBadge").className = "badge " + (data.lan_enabled ? "green" : "amber");
      $("phoneLanHint").innerHTML = data.lan_enabled
        ? `Scan this QR code with your iPhone or Android camera on the same Wi-Fi. Pairing token is embedded automatically.`
        : `Currently bound to 127.0.0.1 only. Restart with <code>python app/server.py --lan</code> (or <code>macos/loy_ctl.sh start --lan</code>) to enable phone access.`;
      renderClientsList(data.clients || []);
    } catch (_) {}
  }
  $("btnCopyPhoneUrl").onclick = () => {
    navigator.clipboard?.writeText($("phoneUrlBox").value);
    toast("Copied paired URL");
  };
  $("btnRotateToken").onclick = () => loadPhoneConnect(true).then(() => toast("Pairing token rotated — rescan QR on phones"));

  let ws = null;
  function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
      $("offlineBanner").classList.add("hidden");
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.event === "status_update") applyStatus(msg.data);
        else if (msg.event === "clients_update") renderClientsList(msg.data);
        else if (msg.event === "library_update") loadLibrary();
        else if (msg.event === "schedule_update") loadWorkSchedule();
        else if (msg.event === "transfer_update") {
          const t = msg.data;
          if (t.active) {
            $("transferBar").classList.remove("hidden");
            $("transferLabel").textContent = `${t.label || "Uploading"} (${t.profile})`;
            $("transferFill").style.width = `${t.percent}%`;
            $("transferMeta").textContent = `${t.sent_frames}/${t.total_frames} frames · ${t.percent}% · ETA ${t.eta_s}s`;
          } else if (t.error) {
            $("transferBar").classList.add("hidden");
          } else {
            $("transferFill").style.width = "100%";
            $("transferMeta").textContent = `Done (${t.measured_bps} B/s)`;
            setTimeout(() => $("transferBar").classList.add("hidden"), 900);
          }
        }
      } catch (_) {}
    };
    ws.onclose = () => {
      $("offlineBanner").classList.remove("hidden");
      setTimeout(connectWebSocket, 2500);
    };
    ws.onerror = () => ws.close();
  }

  // Register PWA Service Worker when running in a secure context (localhost / HTTPS)
  if ("serviceWorker" in navigator && (location.hostname === "127.0.0.1" || location.hostname === "localhost" || location.protocol === "https:")) {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  }

  // ---------------- Home Quick Desk Modes ----------------
  const QUICK_DESK_MODES = [
    { label: "Deep Focus", kind: "gif", target: "09_mode_deep_focus.gif", img: "/gifs/09_mode_deep_focus.gif" },
    { label: "Coding", kind: "gif", target: "10_mode_coding.gif", img: "/gifs/10_mode_coding.gif" },
    { label: "ON AIR", kind: "gif", target: "11_mode_on_air_meeting.gif", img: "/gifs/11_mode_on_air_meeting.gif" },
    { label: "Coffee Break", kind: "gif", target: "13_mode_coffee_break.gif", img: "/gifs/13_mode_coffee_break.gif" },
    { label: "Lo-Fi Vibe", kind: "gif", target: "16_mode_music_vibe.gif", img: "/gifs/16_mode_music_vibe.gif" },
    { label: "Gaming", kind: "gif", target: "15_mode_gaming_chill.gif", img: "/gifs/15_mode_gaming_chill.gif" },
    { label: "Synthwave Clock", kind: "clock", target: "hw_synthwave_sun", img: "/api/hw_clock_preview/hw_synthwave_sun" },
    { label: "Hacker Clock", kind: "clock", target: "hw_matrix_terminal", img: "/api/hw_clock_preview/hw_matrix_terminal" },
    { label: "Starlight", kind: "clock", target: "hw_galaxy_stars", img: "/api/hw_clock_preview/hw_galaxy_stars" },
  ];
  function renderQuickModes() {
    const grid = $("homeQuickModes");
    if (!grid) return;
    grid.innerHTML = "";
    QUICK_DESK_MODES.forEach((m) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "quick-mode-btn";
      btn.innerHTML = `<img src="${m.img}" alt="${m.label}" /><span>${m.label}</span>`;
      btn.onclick = async () => {
        try {
          if (m.kind === "gif") {
            await api("/api/gif_pack/send", { method: "POST", body: JSON.stringify({ file: m.target }) });
          } else {
            await api("/api/hw_text_clock", { method: "POST", body: JSON.stringify({ shape: m.target, sync_first: true }) });
          }
          recordSent(m.label);
          toast(`Switched to ${m.label}`);
        } catch (err) { toast(err.message); }
      };
      grid.appendChild(btn);
    });
  }

  // ---------------- Init ----------------
  renderQuickModes();
  try {
    const draft = JSON.parse(localStorage.getItem("loy_draft_v2") || "null");
    if (draft && draft.w === gridW && draft.h === gridH && draft.f) frames = [base64ToBytes(draft.f)];
  } catch (_) {}
  renderPalette(PALETTES.studio);
  renderRecentColors();
  updateCanvasSize();
  renderFrameStrip();
  api("/api/status").then(applyStatus).catch(() => {});
  loadWorkSchedule();
  connectWebSocket();
})();
