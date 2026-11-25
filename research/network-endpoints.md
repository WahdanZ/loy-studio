# Network Endpoints & External Domains — LOY PLAY v1.7.15

All endpoints below were extracted statically from `assets/apps/__UNI__60DF8F1/www/app-service.js` and `androidPrivacy.json`. **[VERIFIED STATIC]**

## 1. Domains

| Domain | Protocol | Purpose | Necessary for Local BLE? |
|---|---|---|---|
| `wxbtapp-cdn.popled.cn` | HTTPS | Primary REST API (`urlForm`): login, registration, `device_check`, remote config, material store, social comments/messages, DIY logs | **No** |
| `store-cdn.popled.cn` | HTTPS | CDN & telemetry API (`urlpub`): daily telemetry upload (`set_recode`), `.wgt` app updates, firmware updates, GeoJSON geofence files, material images/GIFs | **No** |
| `auth.popled.cn` | HTTPS | App metadata endpoint (`/api/device/get_app_info`) | **No** |
| `a.popled.cn` | HTTPS | Slider CAPTCHA (`/mb/tncode/get`, `/mb/tncode/check`) | **No** |
| `app.popled.cn` | HTTPS | Privacy Policy & User Agreement HTML pages | **No** |
| `lmq6store.popled.cn` | HTTPS | Legacy CDN host (rewritten at runtime to `store-cdn.popled.cn` in `7835` line 98) | **No** |
| `i7f.cn` | HTTPS | Third-party URL shortener (`POST https://i7f.cn/api/links`) used by QR-code generator (`8fc7`) | **No** |
| `open.weixin.qq.com` | HTTPS | WeChat official account QR fetch (`8fc7`) & WeChat OAuth | **No** |
| `itunes.apple.com` | **HTTP (cleartext)** | iOS App Store version lookup (`http://itunes.apple.com/cn/lookup?id=1590450744`) (`5728`) | **No** |

## 2. Notable API Endpoints (`wxbtapp-cdn.popled.cn` & `store-cdn.popled.cn`)

- **Telemetry**: `POST https://store-cdn.popled.cn/api/material_Base/set_recode` (`7835`) — Uploads `systemAjax.json` (phone ID, model, language, usage timestamps, LED serial, firmware version, GPS lat/lon, material hits).
- **Hardware Authorization**: `POST https://wxbtapp-cdn.popled.cn/device_check` (`6f64`) — Validates LED hardware `did` (`id_dev`) and `uuid` (`cid`).
- **Geofencing**: `GET https://store-cdn.popled.cn/public/upload/files/geojson/...` — GeoJSON polygon boundaries checked against user GPS coordinates.
- **Authentication**: `login`, `register`, `login_third`, `login_or_register_third`, `login_or_register_wx`, `update_user`, `drop_user`, `bind_phone_email`, `send_checkcode_sms`, `send_checkcode_smtp`.
- **Remote Config & Updates**: `get_config` (returns `filter_bt_pre` BLE name prefixes), `https://store-cdn.popled.cn/api/device/get_app_file` (`.wgt` hot updates), `https://store-cdn.popled.cn/api/device/get_new_upgrade_tip` (firmware OTA check).
- **Cloud Material Store & Social**: `query_material_list`, `query_material_release_list`, `query_theme_list`, `material_use`, `material_share`, `pgm_log_add`, `dev_pgm_diy_update`, `comment`, `secret_msg`, `user_attention`.

## 3. Request Signing Scheme

Observed in `7835.generate()` (lines 296–305):
1. Sort non-empty POST parameters alphabetically by key.
2. Concatenate `k1=v1&k2=v2&...key=<session_key_or_auth_key>`.
3. Compute `token = hex_md5(...)`.
For `store-cdn.popled.cn` endpoints (`set_recode`, `get_app_file`, `get_new_upgrade_tip`), `token = hex_md5(MD5_key + ts)`.
