import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:loy_studio_flutter/protocol/loy_protocol.dart';

void main() {
  test('checksum and frame structure match YS/EXYC-A spec', () {
    final frame = LoyProtocol.cmdSetPower(true, sno: 1);
    expect(frame[0], 0xAA);
    expect(frame[1], 0x55);
    expect(frame[2], 0xFF);
    expect(frame[3], 0xFF);
    expect(frame[8], LoyConstants.flagDefault);
    expect(frame[9], LoyConstants.cmdTypeSet);
    expect(LoyProtocol.verifyChecksum(frame), isTrue);
  });

  test('continuous DISPATCH sends 4-byte 02 00 00 FF payload', () {
    final frame = LoyProtocol.cmdDispatchProgram(1, sno: 2);
    expect(LoyProtocol.verifyChecksum(frame), isTrue);
    // Payload starts at offset 10: tag=0x18, len=4, bytes=[0x02, 0x00, 0x00, 0xFF]
    expect(frame.sublist(10, 16), [0x18, 0x04, 0x02, 0x00, 0x00, 0xFF]);
  });

  test('PacketParser reassembles chunked frames cleanly', () {
    final frame = LoyProtocol.cmdSyncTime(sno: 7);
    final parser = PacketParser();
    final p1 = parser.feed(frame.sublist(0, 8));
    expect(p1, isEmpty);
    final p2 = parser.feed(frame.sublist(8));
    expect(p2.length, 1);
    expect(p2.first.sno, 7);
  });

  test('encodeRgbToGif and buildGifProgramFrames produce valid wire frames', () {
    final rgb = Uint8List(64 * 64 * 3);
    rgb[0] = 255;
    final gif = LoyProtocol.encodeRgbToGif(rgb);
    expect(String.fromCharCodes(gif.sublist(0, 3)), 'GIF');
    final frames = LoyProtocol.buildGifProgramFrames(gif, startSno: 10);
    expect(frames, isNotEmpty);
    for (final f in frames) {
      expect(LoyProtocol.verifyChecksum(f), isTrue);
    }
  });
}
