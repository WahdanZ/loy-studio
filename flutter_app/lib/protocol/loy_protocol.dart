import 'dart:typed_data';
import 'package:image/image.dart' as img;

/// Pure Dart implementation of the 64x64 BLE LED Matrix Protocol (YS / EXYC-A).
class LoyConstants {
  static const String serviceUuid = '0000fff0-0000-1000-8000-00805f9b34fb';
  static const String notifyCharUuid = '0000fff1-0000-1000-8000-00805f9b34fb';
  static const String writeCharUuid = '0000fff2-0000-1000-8000-00805f9b34fb';

  static const int packetMagic = 0x55AA;
  static const int targetBroadcast = 0xFFFF;

  static const int flagChecksum = 0x80;
  static const int flagAck = 0x40;
  static const int flagVersion = 0x01;
  static const int flagDefault = flagChecksum | flagAck | flagVersion; // 0xC1
  static const int flagNoAck = flagChecksum | flagVersion; // 0x81

  static const int cmdTypeSet = 2;
  static const int cmdTypeGet = 3;

  static const int defaultBleChunk = 180;
  static const int defaultDataChunk = 960;

  // TLV tags
  static const int tlvPower = 4;
  static const int tlvTiming = 5;
  static const int tlvLight = 6;
  static const int tlvDelete = 8;
  static const int tlvDataSave = 9;
  static const int tlvDevInfo = 10;
  static const int tlvIdPro = 12;
  static const int tlvIdRect = 13;
  static const int tlvIdItem = 14;
  static const int tlvItemCtrl = 17;
  static const int tlvDataMeta = 18;
  static const int tlvDataChunk = 19;
  static const int tlvAnimate = 20;
  static const int tlvDispatch = 24;
  static const int tlvCoverPro = 25;
  static const int tlvParamDev = 27;
  static const int tlvProProp = 28;
  static const int tlvRectPos = 29;
  static const int tlvRotate = 47;
  static const int tlvGame = 48;
  static const int tlvRtDraw = 50;
  static const int tlvAniNum = 51;
  static const int tlvPgmPlay = 54;
  static const int tlvMirror = 58;
}

class ParsedAck {
  final int sno;
  final int flags;
  final int cmdType;
  final Uint8List payload;
  final Uint8List raw;
  const ParsedAck({
    required this.sno,
    required this.flags,
    required this.cmdType,
    required this.payload,
    required this.raw,
  });
}

class LoyProtocol {
  static Uint8List encodeTlvLength(int length) {
    if (length < 128) return Uint8List.fromList([length]);
    if (length < 256) return Uint8List.fromList([0x81, length]);
    return Uint8List.fromList([0x82, length & 0xFF, (length >> 8) & 0xFF]);
  }

  static Uint8List encodeTlv(int tag, List<int> value) {
    final lenBytes = encodeTlvLength(value.length);
    final out = Uint8List(1 + lenBytes.length + value.length);
    out[0] = tag & 0xFF;
    out.setRange(1, 1 + lenBytes.length, lenBytes);
    out.setRange(1 + lenBytes.length, out.length, value);
    return out;
  }

  static int computeChecksum(List<int> bytes) {
    var sum = 0;
    for (final b in bytes) {
      sum = (sum + (b & 0xFF)) & 0xFFFF;
    }
    return sum;
  }

  static bool verifyChecksum(Uint8List frame) {
    if (frame.length < 12) return false;
    final expected = frame[frame.length - 2] | (frame[frame.length - 1] << 8);
    final actual = computeChecksum(frame.sublist(0, frame.length - 2));
    return expected == actual;
  }

  static Uint8List buildFrame(
    List<int> tlvPayload, {
    required int sno,
    required int cmdType,
    int flags = LoyConstants.flagDefault,
    int target = LoyConstants.targetBroadcast,
  }) {
    final hasChecksum = (flags & LoyConstants.flagChecksum) != 0;
    final chkLen = hasChecksum ? 2 : 0;
    final bodyLen = tlvPayload.length + chkLen + 4;
    final totalLen = 10 + tlvPayload.length + chkLen;
    final out = Uint8List(totalLen);
    final bd = ByteData.sublistView(out);
    bd.setUint16(0, LoyConstants.packetMagic, Endian.little);
    bd.setUint16(2, target & 0xFFFF, Endian.little);
    bd.setUint16(4, bodyLen & 0xFFFF, Endian.little);
    bd.setUint16(6, sno & 0xFFFF, Endian.little);
    out[8] = flags & 0xFF;
    out[9] = cmdType & 0xFF;
    out.setRange(10, 10 + tlvPayload.length, tlvPayload);
    if (hasChecksum) {
      final chk = computeChecksum(out.sublist(0, 10 + tlvPayload.length));
      bd.setUint16(10 + tlvPayload.length, chk, Endian.little);
    }
    return out;
  }

  static List<Uint8List> splitBleWrites(
    Uint8List frame, {
    int mtuPayload = LoyConstants.defaultBleChunk,
  }) {
    final chunks = <Uint8List>[];
    for (var i = 0; i < frame.length; i += mtuPayload) {
      final end = (i + mtuPayload < frame.length) ? i + mtuPayload : frame.length;
      chunks.add(Uint8List.sublistView(frame, i, end));
    }
    return chunks;
  }

  static Uint8List concat(List<List<int>> parts) {
    final total = parts.fold<int>(0, (acc, p) => acc + p.length);
    final out = Uint8List(total);
    var off = 0;
    for (final p in parts) {
      out.setRange(off, off + p.length, p);
      off += p.length;
    }
    return out;
  }

  // ---- Basic Commands ----

  static Uint8List cmdGetDevInfoAll({int sno = 1}) {
    final tlv = concat([
      encodeTlv(LoyConstants.tlvDevInfo, const []),
      encodeTlv(LoyConstants.tlvParamDev, const []),
      encodeTlv(LoyConstants.tlvPower, const []),
      encodeTlv(LoyConstants.tlvLight, const []),
    ]);
    return buildFrame(tlv, sno: sno, cmdType: LoyConstants.cmdTypeGet);
  }

  static Uint8List cmdSetPower(bool on, {int sno = 1}) {
    return buildFrame(
      encodeTlv(LoyConstants.tlvPower, [0, on ? 1 : 0]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdSetBrightness(int level, {int sno = 1}) {
    final clamped = level.clamp(1, 15);
    return buildFrame(
      encodeTlv(LoyConstants.tlvLight, [0, clamped]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdSyncTime({DateTime? dt, int sno = 1}) {
    final now = dt ?? DateTime.now();
    final p = Uint8List(9);
    final bd = ByteData.sublistView(p);
    bd.setUint16(0, (now.year - 2000).clamp(0, 65535), Endian.little);
    p[2] = now.month;
    p[3] = now.day;
    p[4] = now.hour;
    p[5] = now.minute;
    p[6] = now.second;
    bd.setUint16(7, now.millisecond, Endian.little);
    return buildFrame(
      encodeTlv(LoyConstants.tlvTiming, p),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdSetRotation(int degrees, {int sno = 1}) {
    final step = ((degrees ~/ 90) % 4).abs();
    return buildFrame(
      encodeTlv(LoyConstants.tlvRotate, [step]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdSetMirror(bool enabled, {int sno = 1}) {
    return buildFrame(
      encodeTlv(LoyConstants.tlvMirror, [enabled ? 1 : 0]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdDispatchProgram(int programId, {int sno = 1}) {
    // 4-byte continuous playback: 02 <id0> 00 FF
    final body = [2, (programId - 1) & 0xFF, 0, 0xFF];
    return buildFrame(
      encodeTlv(LoyConstants.tlvDispatch, body),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdPgmPlay({int model = 1, int index = 0, int sno = 1}) {
    return buildFrame(
      encodeTlv(LoyConstants.tlvPgmPlay, [model & 0xFF, index & 0xFF]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdDeletePrograms(List<int> ids1Based, {int sno = 1}) {
    final body = <int>[0, ...ids1Based.map((p) => (p - 1) & 0xFF)];
    return buildFrame(
      encodeTlv(LoyConstants.tlvDelete, body),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdSetFirmwareClock(int fmt, int r, int g, int b, {int sno = 1}) {
    return buildFrame(
      encodeTlv(LoyConstants.tlvAniNum, [0, fmt & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF]),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
    );
  }

  static Uint8List cmdRtDrawPixels(
    List<(int, int)> pixels,
    int r,
    int g,
    int b, {
    int sno = 1,
    bool unack = true,
  }) {
    final body = Uint8List(5 + pixels.length * 4);
    final bd = ByteData.sublistView(body);
    body[0] = 2; // pixel list mode
    body[1] = r & 0xFF;
    body[2] = g & 0xFF;
    body[3] = b & 0xFF;
    body[4] = pixels.length & 0xFF;
    var off = 5;
    for (final (x, y) in pixels) {
      bd.setUint16(off, x & 0xFFFF, Endian.little);
      bd.setUint16(off + 2, y & 0xFFFF, Endian.little);
      off += 4;
    }
    return buildFrame(
      encodeTlv(LoyConstants.tlvRtDraw, body),
      sno: sno,
      cmdType: LoyConstants.cmdTypeSet,
      flags: unack ? LoyConstants.flagNoAck : LoyConstants.flagDefault,
    );
  }

  // ---- Multi-Frame Native GIF Upload ----

  static List<Uint8List> buildGifProgramFrames(
    Uint8List gifBytes, {
    int width = 64,
    int height = 64,
    int programId = 1,
    int startSno = 1,
    int dataSave = 0,
    bool showNow = true,
    int chunkSize = LoyConstants.defaultDataChunk,
  }) {
    final idPro0 = (programId - 1) & 0xFF;
    final bundles = <Uint8List>[];

    // 1. Program header
    final proProp = Uint8List(6);
    final proPropBd = ByteData.sublistView(proProp);
    proProp[0] = 0; // type_bg
    proProp[1] = 0; // play_mode = loop
    proPropBd.setUint16(2, 1, Endian.little);
    proPropBd.setUint16(4, 3, Endian.little);

    final rectPos = Uint8List(9);
    final rectBd = ByteData.sublistView(rectPos);
    rectBd.setUint16(0, 0, Endian.little);
    rectBd.setUint16(2, 0, Endian.little);
    rectBd.setUint16(4, width, Endian.little);
    rectBd.setUint16(6, height, Endian.little);
    rectPos[8] = 0;

    bundles.add(concat([
      encodeTlv(LoyConstants.tlvCoverPro, [0, idPro0]),
      encodeTlv(LoyConstants.tlvDataSave, [1 - dataSave]),
      encodeTlv(LoyConstants.tlvIdPro, [idPro0]),
      encodeTlv(LoyConstants.tlvProProp, proProp),
      encodeTlv(LoyConstants.tlvIdRect, [0]),
      encodeTlv(LoyConstants.tlvRectPos, rectPos),
    ]));

    // 2. Item header
    bundles.add(concat([
      encodeTlv(LoyConstants.tlvDataSave, [1 - dataSave]),
      encodeTlv(LoyConstants.tlvIdPro, [idPro0]),
      encodeTlv(LoyConstants.tlvIdRect, [0]),
      encodeTlv(LoyConstants.tlvIdItem, [0]),
      encodeTlv(LoyConstants.tlvAnimate, [1, 9, 0]),
      encodeTlv(LoyConstants.tlvItemCtrl, [0, 1, 0, 10]),
    ]));

    // 3. Data chunks
    final totalChunks = ((gifBytes.length + chunkSize - 1) ~/ chunkSize).clamp(1, 65535);
    final prefix = concat([
      encodeTlv(LoyConstants.tlvDataSave, [1 - dataSave]),
      encodeTlv(LoyConstants.tlvIdPro, [idPro0]),
      encodeTlv(LoyConstants.tlvIdRect, [0]),
      encodeTlv(LoyConstants.tlvIdItem, [0]),
    ]);
    for (var idx = 0; idx < totalChunks; idx++) {
      final start = idx * chunkSize;
      final end = (start + chunkSize < gifBytes.length) ? start + chunkSize : gifBytes.length;
      final chunk = Uint8List.sublistView(gifBytes, start, end);
      final meta = Uint8List(7);
      final metaBd = ByteData.sublistView(meta);
      metaBd.setUint16(0, totalChunks, Endian.little);
      metaBd.setUint16(2, idx, Endian.little);
      metaBd.setUint16(4, chunkSize, Endian.little);
      meta[6] = 0;
      bundles.add(concat([
        prefix,
        encodeTlv(LoyConstants.tlvDataMeta, meta),
        encodeTlv(LoyConstants.tlvDataChunk, chunk),
      ]));
    }

    // 4. Continuous dispatch
    if (showNow) {
      bundles.add(encodeTlv(LoyConstants.tlvDispatch, [2, idPro0, 0, 0xFF]));
    }

    final totalLen = bundles.fold<int>(0, (acc, b) => acc + b.length);
    if (totalLen <= 997) {
      return [buildFrame(concat(bundles), sno: startSno, cmdType: LoyConstants.cmdTypeSet)];
    }
    final frames = <Uint8List>[];
    var sno = startSno;
    for (final b in bundles) {
      frames.add(buildFrame(b, sno: sno, cmdType: LoyConstants.cmdTypeSet));
      sno = (sno + 1) & 0xFFFF;
    }
    return frames;
  }

  /// Encode a 64x64 RGB byte buffer (64*64*3) directly to a native GIF for hardware upload.
  static Uint8List encodeRgbToGif(Uint8List rgb, {int width = 64, int height = 64}) {
    final im = img.Image(width: width, height: height);
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        final i = (y * width + x) * 3;
        im.setPixelRgb(x, y, rgb[i], rgb[i + 1], rgb[i + 2]);
      }
    }
    return Uint8List.fromList(img.encodeGif(im));
  }
}

/// Streaming reassembly parser for FFF1 notifications.
class PacketParser {
  final List<int> _buf = [];

  void reset() => _buf.clear();

  List<ParsedAck> feed(List<int> chunk) {
    final out = <ParsedAck>[];
    if (chunk.isEmpty) return out;
    _buf.addAll(chunk);

    while (_buf.length >= 10) {
      final idx = _findMagic();
      if (idx < 0) {
        if (_buf.isNotEmpty && _buf.last == 0xAA) {
          _buf.removeRange(0, _buf.length - 1);
        } else {
          _buf.clear();
        }
        break;
      }
      if (idx > 0) _buf.removeRange(0, idx);
      if (_buf.length < 10) break;

      final bodyLen = _buf[4] | (_buf[5] << 8);
      final totalLen = 6 + bodyLen;
      if (bodyLen < 4 || totalLen > 4096) {
        _buf.removeRange(0, 2);
        continue;
      }
      if (_buf.length < totalLen) break;

      final raw = Uint8List.fromList(_buf.sublist(0, totalLen));
      _buf.removeRange(0, totalLen);

      final flags = raw[8];
      final hasChk = (flags & LoyConstants.flagChecksum) != 0;
      if (hasChk && !LoyProtocol.verifyChecksum(raw)) continue;

      final sno = raw[6] | (raw[7] << 8);
      final cmdType = raw[9];
      final payloadEnd = hasChk ? raw.length - 2 : raw.length;
      out.add(ParsedAck(
        sno: sno,
        flags: flags,
        cmdType: cmdType,
        payload: Uint8List.sublistView(raw, 10, payloadEnd),
        raw: raw,
      ));
    }
    return out;
  }

  int _findMagic() {
    for (var i = 0; i < _buf.length - 1; i++) {
      if (_buf[i] == 0xAA && _buf[i + 1] == 0x55) return i;
    }
    return -1;
  }
}
