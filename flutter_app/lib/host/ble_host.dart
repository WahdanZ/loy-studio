import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:universal_ble/universal_ble.dart';
import '../protocol/loy_protocol.dart';

class ScannedDisplay {
  final String id;
  final String name;
  final int rssi;
  const ScannedDisplay({required this.id, required this.name, required this.rssi});
}

class PacketLogEntry {
  final DateTime ts;
  final String dir; // TX or RX
  final String summary;
  final String hex;
  const PacketLogEntry({required this.ts, required this.dir, required this.summary, required this.hex});
}

/// Pure Dart Cross-Platform BLE Host using `universal_ble` (macOS, Windows, Linux)
/// with built-in Simulator mode when testing without hardware.
class LoyBleHost extends ChangeNotifier {
  bool connected = false;
  bool simulatorMode = false;
  bool scanning = false;
  bool busy = false;
  String? deviceId;
  String? deviceName;
  bool powerOn = true;
  int brightness = 12;
  double transferProgress = 0.0;
  String transferLabel = '';
  String statusMessage = 'Disconnected';

  final List<ScannedDisplay> scanned = [];
  final List<PacketLogEntry> packetLog = [];
  final PacketParser _parser = PacketParser();
  int _sno = 0;
  Completer<ParsedAck>? _ackWaiter;
  int? _expectedAckSno;

  LoyBleHost() {
    UniversalBle.onScanResult = (BleDevice dev) {
      final name = dev.name ?? '';
      if (name.isEmpty) return;
      final upper = name.toUpperCase();
      if (!upper.startsWith('YS') && !upper.startsWith('TL') && !upper.contains('LED')) return;
      final idx = scanned.indexWhere((d) => d.id == dev.deviceId);
      final entry = ScannedDisplay(id: dev.deviceId, name: name, rssi: dev.rssi ?? -60);
      if (idx >= 0) {
        scanned[idx] = entry;
      } else {
        scanned.add(entry);
      }
      notifyListeners();
    };

    UniversalBle.onConnectionChange = (BleConnectionChange change) {
      if (change.deviceId == deviceId && !change.isConnected) {
        connected = false;
        statusMessage = change.error != null ? 'Disconnected (${change.error})' : 'Disconnected';
        notifyListeners();
      }
    };

    UniversalBle.onValueChange = (BleCharacteristicValue ev) {
      if (ev.deviceId != deviceId) return;
      final acks = _parser.feed(ev.value);
      for (final ack in acks) {
        _logPacket('RX', 'ACK sno=${ack.sno} type=${ack.cmdType}', ack.raw);
        if (_ackWaiter != null && !_ackWaiter!.isCompleted && (_expectedAckSno == null || _expectedAckSno == ack.sno)) {
          _ackWaiter!.complete(ack);
        }
      }
    };
  }

  int nextSno() {
    _sno = (_sno + 1) & 0xFFFF;
    if (_sno == 0) _sno = 1;
    return _sno;
  }

  void _logPacket(String dir, String summary, Uint8List raw) {
    final hex = raw.take(18).map((b) => b.toRadixString(16).padLeft(2, '0')).join(' ');
    packetLog.insert(0, PacketLogEntry(ts: DateTime.now(), dir: dir, summary: summary, hex: hex));
    if (packetLog.length > 80) packetLog.removeLast();
    notifyListeners();
  }

  Future<void> startScan() async {
    scanned.clear();
    scanning = true;
    statusMessage = 'Scanning BLE for YS* displays...';
    notifyListeners();
    try {
      await UniversalBle.startScan();
      await Future.delayed(const Duration(seconds: 4));
      await UniversalBle.stopScan();
    } catch (e) {
      statusMessage = 'Scan error: $e';
    } finally {
      scanning = false;
      if (!connected) statusMessage = 'Found ${scanned.length} display(s)';
      notifyListeners();
    }
  }

  Future<void> connectSimulator() async {
    await disconnect();
    simulatorMode = true;
    connected = true;
    deviceId = 'SIM-64X64';
    deviceName = 'LED-64x64 (Simulator)';
    statusMessage = 'Connected to Simulator';
    notifyListeners();
  }

  Future<void> connect(String id, String name) async {
    await disconnect();
    statusMessage = 'Connecting to $name...';
    notifyListeners();
    try {
      await UniversalBle.connect(id);
      await UniversalBle.discoverServices(id);
      await UniversalBle.subscribeNotifications(
        id,
        LoyConstants.serviceUuid,
        LoyConstants.notifyCharUuid,
      );
      deviceId = id;
      deviceName = name;
      simulatorMode = false;
      connected = true;
      statusMessage = 'Connected to $name';
      notifyListeners();
      // Sync RTC on connect
      await sendFrame(LoyProtocol.cmdSyncTime(sno: nextSno()), label: 'Sync RTC');
    } catch (e) {
      connected = false;
      statusMessage = 'Connect failed: $e';
      notifyListeners();
    }
  }

  Future<void> disconnect() async {
    if (!simulatorMode && deviceId != null) {
      try {
        await UniversalBle.disconnect(deviceId!);
      } catch (_) {}
    }
    connected = false;
    simulatorMode = false;
    deviceId = null;
    deviceName = null;
    statusMessage = 'Disconnected';
    notifyListeners();
  }

  /// Send a single protocol frame with strict swnd=1 ACK pacing (safe for EXYC-A).
  Future<void> sendFrame(Uint8List frame, {String label = 'CMD', bool waitAck = true}) async {
    if (!connected) return;
    final sno = frame[6] | (frame[7] << 8);
    _logPacket('TX', '$label (sno=$sno, ${frame.length}B)', frame);
    if (simulatorMode) {
      await Future.delayed(const Duration(milliseconds: 8));
      return;
    }

    final chunks = LoyProtocol.splitBleWrites(frame);
    if (waitAck) {
      _expectedAckSno = sno;
      _ackWaiter = Completer<ParsedAck>();
    }
    for (final chunk in chunks) {
      await UniversalBle.write(
        deviceId!,
        LoyConstants.serviceUuid,
        LoyConstants.writeCharUuid,
        chunk,
        withoutResponse: true,
      );
      await Future.delayed(const Duration(milliseconds: 3));
    }
    if (waitAck && _ackWaiter != null) {
      try {
        await _ackWaiter!.future.timeout(const Duration(milliseconds: 1800));
      } catch (_) {
        // Continue cleanly on timeout without wedging the host
      } finally {
        _ackWaiter = null;
        _expectedAckSno = null;
      }
    }
  }

  Future<void> setPower(bool on) async {
    powerOn = on;
    notifyListeners();
    await sendFrame(LoyProtocol.cmdSetPower(on, sno: nextSno()), label: on ? 'Power ON' : 'Power OFF');
  }

  Future<void> setBrightness(int level) async {
    brightness = level.clamp(1, 15);
    notifyListeners();
    await sendFrame(LoyProtocol.cmdSetBrightness(brightness, sno: nextSno()), label: 'Brightness $brightness');
  }

  Future<void> syncRtc() async {
    await sendFrame(LoyProtocol.cmdSyncTime(sno: nextSno()), label: 'Sync RTC');
  }

  Future<void> sendFirmwareClock(int fmt, int r, int g, int b) async {
    await syncRtc();
    await sendFrame(LoyProtocol.cmdSetFirmwareClock(fmt, r, g, b, sno: nextSno()), label: 'FW Clock #$fmt');
  }

  Future<void> sendGifBytes(Uint8List gifBytes, {String label = 'GIF'}) async {
    if (!connected || busy) return;
    busy = true;
    transferLabel = label;
    transferProgress = 0.0;
    notifyListeners();
    try {
      final frames = LoyProtocol.buildGifProgramFrames(gifBytes, startSno: nextSno(), showNow: true);
      for (var i = 0; i < frames.length; i++) {
        await sendFrame(frames[i], label: '$label ${i + 1}/${frames.length}');
        transferProgress = (i + 1) / frames.length;
        notifyListeners();
      }
      await sendFrame(LoyProtocol.cmdPgmPlay(model: 1, index: 0, sno: nextSno()), label: 'Lock Slot 1');
      statusMessage = 'Sent $label (${frames.length} frames)';
    } finally {
      busy = false;
      transferProgress = 0.0;
      transferLabel = '';
      notifyListeners();
    }
  }

  Future<void> sendRgbCanvas(Uint8List rgb64, {String label = 'Canvas'}) async {
    final gif = LoyProtocol.encodeRgbToGif(rgb64);
    await sendGifBytes(gif, label: label);
  }
}
