# Privacy & Security Audit — LOY PLAY v1.7.15 (`com.yskj.loy`)

## Executive Summary

Static analysis of `LOY+PLAY_1.7.15_APKPure.xapk` establishes **confirmed evidence** that the application collects and transmits data unnecessary for controlling a local Bluetooth LED display, enforces server-side hardware authorization and geographic geofencing, and ships multiple hardcoded credentials and an insecure TLS trust manager.

**Is internet access required to control the LED hardware?**
**No.** The BLE protocol (`0000FFF0`/`FFF1`/`FFF2`, `AA 55` TLV packets) is 100% local. Internet access is only used by LOY PLAY for cloud accounts, material store, daily telemetry upload, remote configuration, `.wgt` hot updates, and server-side `device_check` / geofencing enforcement. **[VERIFIED STATIC]**

---

## Key Findings

### 1. Daily Behavioral, Device & GPS Location Telemetry Upload
- **Severity**: High (Privacy)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)** — Dynamic verification recommended to confirm runtime server acceptance.
- **Evidence**:
  - `app-service.js` module `7835` lines 440–447 (`getLocation`): Calls `uni.getLocation({ type: "wgs84" })` and stores `longitude` and `latitude` in Vuex state.
  - `app-service.js` module `7835` lines 450–498 (`storageGetSystemInfo`, `storageDeviceInfo`, `storageMaterialInfo`, `storageModeInfo`): Writes `systemAjax.json` containing `phone_id` (`deviceId`), `system`, `brand-model`, `lang`, `use_times` (timestamps of every app launch), `uid`, hardware LED serial (`dev_info.id_dev`), firmware `version`, **exact GPS `lon` and `lat`**, material clicks, and feature modes used.
  - `app-service.js` module `6ac7` lines 346–354 (`mounted`) & `7835` lines 499–509 (`sendSystemInfoFileAjax`): Once per calendar day, POSTs the entire `systemAjax.json` payload to `https://store-cdn.popled.cn/api/material_Base/set_recode`.
- **Necessary for product?**: No. Basic LED control does not require GPS coordinates, phone identifiers, or usage telemetry.

### 2. Server-Side Hardware Authorization Kill-Switch & GeoJSON Geofencing
- **Severity**: High (User Autonomy & Privacy)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)**
- **Evidence**:
  - `app-service.js` module `6f64` (`compareMac`): On every hardware connection, sends `{ userid: 3, did: dev_info.id_dev, uuid: dev_info.cid, auth_id }` to `https://wxbtapp-cdn.popled.cn/device_check`. If the server returns status `402`, `406`, or non-zero, it persists `compareState<id_dev> = "unauthorized"`, closes Bluetooth (`6ac7` lines 50–52), and blocks control.
  - `app-service.js` module `6ac7` lines 69–70 (`setCompareMac`) & `7835` lines 937–987 (`getCurrentPosition`, `isPointInPolygon`, `pointInPolygonTips`): Uses `plus.geolocation.getCurrentPosition` and Turf `booleanPointInPolygon` (module `aedc`) against GeoJSON polygons from `https://store-cdn.popled.cn/public/upload/files/geojson/`. If the device is flagged `"illegal"` in `devZoneInfo`, it disconnects Bluetooth and calls `plus.runtime.quit()`.
- **Necessary for product?**: No. This is a vendor DRM/region-lock mechanism.

### 3. Automatic Silent Account Registration Using Device ID
- **Severity**: Medium–High (Privacy)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)**
- **Evidence**:
  - `app-service.js` module `7835` lines 138–183 (`getLoginInfo`, `touristModel`, `registerUser`): In non-Chinese locales (`!langCN`), if no saved login exists, the app automatically logs in or registers a cloud account at `https://wxbtapp-cdn.popled.cn/register` using `user = uni.getSystemInfoSync().deviceId` and `pwd = hex_md5(deviceId)`.
- **Necessary for product?**: No.

### 4. Hardcoded Credentials & API Secrets
- **Severity**: High (Security)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)**
- **Evidence**:
  - `AndroidManifest.xml` line 75: `<meta-data android:name="WX_SECRET" android:value="<REDACTED_32_HEX>"/>` (WeChat AppSecret).
  - `app-service.js` module `6ac7` lines 290–291 (`auth_login`): Hardcoded service login `{ user: "pub_appout", pwd: "<REDACTED_MD5>" }` sent to `https://wxbtapp-cdn.popled.cn/login`.
  - `app-service.js` module `29a0`: Hardcoded `app_key` / `device_key` (`<REDACTED_32_HEX>`) and `MD5_key` (`<REDACTED_32_CHAR>`) used for MD5 request signing (`7835.generate()`).
- **Necessary for product?**: No; secrets should never be embedded in client APKs.

### 5. Empty Accept-All TLS `X509TrustManager` & Cleartext Traffic Enabled
- **Severity**: High (Network Security)
- **Classification**: **Confirmed static presence (VERIFIED STATIC)** — Dynamic verification needed to check if `CustomTrustMgr` is actively wired to runtime HTTP clients.
- **Evidence**:
  - `com/yskj/loy/CustomTrustMgr.java` (`classes.dex`): Implements `X509TrustManager` with empty `checkClientTrusted` and `checkServerTrusted` methods.
  - `AndroidManifest.xml` line 48: `android:usesCleartextTraffic="true"`.
  - `app-service.js` module `5728`: Plain HTTP call to `http://itunes.apple.com/cn/lookup?id=1590450744`.

### 6. Remote Hot-Code Update Mechanism (`.wgt`)
- **Severity**: Medium (Supply Chain / Integrity)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)**
- **Evidence**:
  - `app-service.js` module `6ac7` lines 296–302 (`getAppCheckUpgrade`): Polls `https://store-cdn.popled.cn/api/device/get_app_file` and can download `.wgt` uni-app code packages directly from vendor servers, bypassing Play Store review.

### 7. Over-Privileged Android Permissions & Broad `<queries>`
- **Severity**: Medium (Privacy / Attack Surface)
- **Classification**: **Confirmed behavior (VERIFIED STATIC)**
- **Evidence (`AndroidManifest.xml`)**:
  - **BLE-required**: `BLUETOOTH`, `BLUETOOTH_ADMIN`, `BLUETOOTH_SCAN`, `BLUETOOTH_CONNECT`, `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` (only needed on Android <= 11 for BLE scan, but here also used for GPS telemetry & geofencing).
  - **Media-optional**: `CAMERA`, `FLASHLIGHT`, `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE`.
  - **Unnecessary / Legacy SDK Permissions**: `READ_PHONE_STATE`, `GET_ACCOUNTS`, `READ_LOGS`, `MOUNT_UNMOUNT_FILESYSTEMS`, `WRITE_SETTINGS`, `MODIFY_AUDIO_SETTINGS` (declared twice), `ACCESS_WIFI_STATE`, `CHANGE_WIFI_STATE`.
  - **App Enumeration (`<queries>`)**: Declares `com.tencent.mobileqq`, `com.tencent.mm`, plus generic `ACTION_MAIN` and `ACTION_VIEW` intents (broad package visibility).
  - **Third-Party SDKs**: DCloud (`io.dcloud.sdk.*`, `adid: 126514130407`, though `uniStatistics.enable` is `false` in `www/manifest.json`), Tencent Open/QQ (`com.tencent.open`, `QQ_APPID: 101980962`, `Common_ta_enable: 1`), WeChat (`com.tencent.mm`, `WX_APPID: wx840d3ebda5a6bc3c`), MSA/OAID config (`assets/supplierconfig.json` with Vivo appid `100215079`).

---

## Behavioral Classification Summary

| Behavior | Classification | Evidence |
|---|---|---|
| Daily upload of `systemAjax.json` (deviceId, GPS, usage) | **Confirmed behavior** | `7835.sendSystemInfoFileAjax`, `6ac7.mounted` |
| Server-side `device_check` & GeoJSON region lock | **Confirmed behavior** | `6f64.compareMac`, `6ac7.setCompareMac`, `7835.isPointInPolygon` |
| Silent `deviceId` account registration (`touristModel`) | **Confirmed behavior** | `7835.touristModel` |
| Hardcoded `WX_SECRET`, `pub_appout`, `MD5_key` | **Confirmed behavior** | `AndroidManifest.xml`, `6ac7`, `29a0` |
| `CustomTrustMgr` accept-all TLS trust manager | **Potential behavior** (static class confirmed, runtime wiring unverified) | `com/yskj/loy/CustomTrustMgr.java` |
| DCloud Ad/Download SDK (`io.dcloud.sdk`) | **Suspicious but unverified** (bundled by HBuilder, `uniStatistics.enable: false`) | `AndroidManifest.xml`, `www/manifest.json` |
| Local BLE control over `0000FFF0`/`FFF1`/`FFF2` | **Normal functionality** | `29a0`, `2658`, `aa14`, `a829` |
