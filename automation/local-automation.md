# Local Automation & Integrations (Privacy-First, Opt-In)

LOY Studio 2.0 binds to `127.0.0.1:18420` by default (localhost-only). When started with `--lan`, all non-localhost API/WebSocket calls require the local pairing token (`X-LOY-Token: <token>` from `app/.pairing_token`). Never expose port `18420` to the public internet.

## 1. Local REST API (curl / Shell Scripts)

```bash
# Turn display on / off (from Mac)
curl -X POST http://127.0.0.1:18420/api/control \
  -H "Content-Type: application/json" \
  -d '{"action":"power","on":true}'

# Set brightness to 10/15
curl -X POST http://127.0.0.1:18420/api/control \
  -H "Content-Type: application/json" \
  -d '{"action":"brightness","level":10}'

# Send a Focus / Meeting status card
curl -X POST http://127.0.0.1:18420/api/widget_frame \
  -H "Content-Type: application/json" \
  -d '{"kind":"focus","title":"IN MEETING","subtitle":"ON CALL","accent":"#ef4444","send":true}'

# Switch to stored hardware slot 2
curl -X POST http://127.0.0.1:18420/api/control \
  -H "Content-Type: application/json" \
  -d '{"action":"dispatch_slot","slot":2}'
```

From another device on your local Wi-Fi (when `--lan` is enabled), add `-H "X-LOY-Token: $(cat app/.pairing_token)"`.

## 2. macOS Shortcuts & Focus Mode Automation

1. Open **Shortcuts** on macOS.
2. Add **Get Contents of URL**:
   - URL: `http://127.0.0.1:18420/api/widget_frame`
   - Method: `POST`
   - Request Body (JSON): `{"kind":"focus","title":"DEEP WORK","subtitle":"FOCUS MODE","accent":"#3b82f6","send":true}`
3. Bind to a keyboard shortcut or Focus Mode trigger.

## 3. Home Assistant (`rest_command`)

Add to Home Assistant `configuration.yaml` (replace `<MAC_LAN_IP>` and `<PAIRING_TOKEN>`):

```yaml
rest_command:
  loy_power_on:
    url: "http://<MAC_LAN_IP>:18420/api/control"
    method: POST
    headers:
      Content-Type: "application/json"
      X-LOY-Token: "<PAIRING_TOKEN>"
    payload: '{"action":"power","on":true}'
  loy_notify_text:
    url: "http://<MAC_LAN_IP>:18420/api/send_text"
    method: POST
    headers:
      Content-Type: "application/json"
      X-LOY-Token: "<PAIRING_TOKEN>"
    payload: '{"text":"{{ message }}","color":"#38bdf8","scroll":"left"}'
```

## 4. Optional Launch at Login (macOS LaunchAgent)

```bash
cp macos/com.loystudio.gateway.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.loystudio.gateway.plist
```
