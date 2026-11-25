# Application Analysis — LOY PLAY v1.7.15 (`com.yskj.loy`)

## 1. Container & Artifact Metadata

| Field | Value | Classification |
|---|---|---|
| Original File | `LOY+PLAY_1.7.15_APKPure.xapk` | VERIFIED STATIC |
| SHA-256 | `a8ed4558aa67936df32062dd21bd535a97afb9b2fb773b6675f4314da40120b1` | VERIFIED STATIC |
| Package Name | `com.yskj.loy` | VERIFIED STATIC |
| App Name / Internal Desc | `LOY PLAY` / `LoyEraSuperApp` | VERIFIED STATIC |
| Version | `1.7.15` (`versionCode: 170015`) | VERIFIED STATIC |
| SDK Versions | `minSdkVersion: 24`, `targetSdkVersion: 35` | VERIFIED STATIC |
| Total XAPK Size | `74,794,930` bytes | VERIFIED STATIC |
| Base APK | `com.yskj.loy.apk` (52.36 MB) | VERIFIED STATIC |
| Native Split APK | `config.armeabi_v7a.apk` (21.96 MB) | VERIFIED STATIC |
| Locale/Density Splits | `config.{ar,de,en,es,fr,it,ja,ko,mdpi,pt,ru,th,tr,vi,zh}.apk` | VERIFIED STATIC |

## 2. Architecture & Technology Stack

- **Framework**: **DCloud uni-app (Vue / Weex hybrid)**, HBuilder runtime `1.9.9.82537`, uni-app compiler `4.87`, app ID `__UNI__60DF8F1` (`assets/data/dcloud_control.xml`, `assets/apps/__UNI__60DF8F1/www/manifest.json`). **[VERIFIED STATIC]**
- **Application Logic**: Entirely implemented in an **unencrypted, minified webpack bundle** at `assets/apps/__UNI__60DF8F1/www/app-service.js` (2,521,867 bytes, 619 webpack modules) paired with `app-view.js` (810,963 bytes) and `app-config-service.js` (722,561 bytes). **[VERIFIED STATIC]**
- **Native Uni-App Plugins (`assets/dcloud_uniplugins.json`)**:
  1. `imengyu-UniAndroidGyro-GyroModule` (`uni.imengyu.gyro.GyroModule`, v1.0.6) — Gyroscope access for cycling/turn-signal mode.
  2. `KJ-FFmpeg` (`com.kj.kjffmpeg.FFmpegModule`, v1.7.0) — Video-to-GIF/frame transcoding.
- **Custom Java Classes (`com.yskj.loy.*`)**:
  - `com.yskj.loy.CustomTrustMgr` — Empty `X509TrustManager` (accepts all TLS certificates).
  - `com.yskj.loy.wxapi.WXEntryActivity` — WeChat OAuth/Share callback.
  - `BuildConfig` & `R`.
- **Native Libraries (`config.armeabi_v7a.apk/lib/armeabi-v7a/`)**:
  - **Media**: FFmpeg (`libavcodec`, `libavformat`, `libavfilter`, `libavutil`, `libswscale`, `libswresample`, `libffmpegkit`), IJKPlayer (`libijkplayer`, `libijkffmpeg`, `libijksdl`), Fresco (`libimagepipeline`, `libgifimage`, `libstatic-webp`), `libpl_droidsonroids_gif.so`, `liblamemp3.so`.
  - **Runtime**: Weex (`libweexcore.so`, `libweexjss.so`, `libweexjsb.so`, `libweexjst.so`), UTS (`libuts-runtime.so`), Breakpad (`libbreakpad-core.so`), DCloud blur (`libdcblur.so`).
  - **BLE Native Code**: **None.** All BLE communication uses DCloud's standard Java bridge (`io.dcloud.feature.bluetooth.BluetoothFeature`) driven directly from JS (`app-service.js`). **[VERIFIED STATIC]**

## 3. Obfuscation, Reflection & Dynamic Code

- **JavaScript**: Minified via webpack (4-hex module IDs, single-letter local variables), **not encrypted** and **not packed**. All Vuex state keys, command JSON keys, API URLs, and protocol constants are plaintext. **[VERIFIED STATIC]**
- **Dynamic Code / Hot Updates**: `6ac7.getAppCheckUpgrade()` queries `https://store-cdn.popled.cn/api/device/get_app_file` and supports downloading/applying `.wgt` uni-app hot-update bundles outside Google Play (`isWgtUpdate`). **[VERIFIED STATIC]**
- **Java Reflection / JS-to-Java Bridge**: DCloud's `plus.android.importClass` is used in JS to invoke Android APIs directly (e.g., `android.bluetooth.BluetoothAdapter.enable()` in module `2658`, `android.location.LocationManager` in module `7835`). **[VERIFIED STATIC]**
- **Eval**: Module `7835` line 819 uses `eval("preseKeysData_" + model)` for remote-control preset lookup. **[VERIFIED STATIC]**
