import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart';
import 'ble_host.dart';

/// Embedded Pure Dart HTTP Host so phones, webhooks, or Google Home Matter bridge
/// can control the Flutter Desktop Host directly on port 18421.
class LocalHttpHost {
  final LoyBleHost ble;
  HttpServer? _server;
  int port = 18421;
  bool get running => _server != null;

  LocalHttpHost(this.ble);

  Future<void> start({int listenPort = 18421}) async {
    if (_server != null) return;
    port = listenPort;
    _server = await HttpServer.bind(InternetAddress.anyIPv4, port);
    _server!.listen(_handleRequest);
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _handleRequest(HttpRequest req) async {
    req.response.headers.contentType = ContentType.json;
    req.response.headers.add('Access-Control-Allow-Origin', '*');
    try {
      final path = req.uri.path;
      if (req.method == 'GET' && path == '/api/status') {
        req.response.write(jsonEncode({
          'ok': true,
          'host': 'loy-studio-flutter-dart',
          'connected': ble.connected,
          'simulator': ble.simulatorMode,
          'device_name': ble.deviceName,
          'power_on': ble.powerOn,
          'brightness': ble.brightness,
        }));
      } else if (req.method == 'POST' && path == '/api/control') {
        final raw = await utf8.decoder.bind(req).join();
        final body = raw.isNotEmpty ? jsonDecode(raw) as Map<String, dynamic> : <String, dynamic>{};
        final action = body['action']?.toString() ?? '';
        if (action == 'power') {
          await ble.setPower(body['on'] == true);
        } else if (action == 'brightness') {
          await ble.setBrightness((body['value'] as num?)?.toInt() ?? 12);
        } else if (action == 'sync_time') {
          await ble.syncRtc();
        }
        req.response.write(jsonEncode({'ok': true, 'action': action}));
      } else if (req.method == 'POST' && path == '/api/gif_pack/send') {
        final raw = await utf8.decoder.bind(req).join();
        final body = raw.isNotEmpty ? jsonDecode(raw) as Map<String, dynamic> : <String, dynamic>{};
        final filename = body['filename']?.toString() ?? '09_mode_deep_focus.gif';
        final data = await rootBundle.load('assets_gifs/$filename');
        await ble.sendGifBytes(data.buffer.asUint8List(), label: filename);
        req.response.write(jsonEncode({'ok': true, 'filename': filename}));
      } else {
        req.response.statusCode = 404;
        req.response.write(jsonEncode({'error': 'Not found'}));
      }
    } catch (e) {
      req.response.statusCode = 500;
      req.response.write(jsonEncode({'error': e.toString()}));
    } finally {
      await req.response.close();
    }
  }
}
