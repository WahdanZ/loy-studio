import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'host/ble_host.dart';
import 'host/local_http_host.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const LoyStudioFlutterApp());
}

class LoyStudioFlutterApp extends StatelessWidget {
  const LoyStudioFlutterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LOY Studio — Pure Flutter Host',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF08090D),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF3B82F6),
          secondary: Color(0xFF06B6D4),
          surface: Color(0xFF11131B),
        ),
        cardColor: const Color(0xFF11131B),
      ),
      home: const StudioShell(),
    );
  }
}

class DeskModeItem {
  final String title;
  final String file;
  const DeskModeItem(this.title, this.file);
}

const List<DeskModeItem> kDeskModes = [
  DeskModeItem('Deep Focus', '09_mode_deep_focus.gif'),
  DeskModeItem('Coding', '10_mode_coding.gif'),
  DeskModeItem('ON AIR', '11_mode_on_air_meeting.gif'),
  DeskModeItem('Do Not Disturb', '12_mode_do_not_disturb.gif'),
  DeskModeItem('Coffee Break', '13_mode_coffee_break.gif'),
  DeskModeItem('Pomodoro', '14_mode_pomodoro.gif'),
  DeskModeItem('Gaming', '15_mode_gaming_chill.gif'),
  DeskModeItem('Lo-Fi Vibe', '16_mode_music_vibe.gif'),
  DeskModeItem('BRB Away', '17_mode_away_brb.gif'),
  DeskModeItem('Done / Night', '18_mode_done_offline.gif'),
  DeskModeItem('Cyber Eyes', '01_cyber_eyes.gif'),
  DeskModeItem('Pulsing Heart', '02_pulsing_heart.gif'),
  DeskModeItem('Matrix Rain', '03_matrix_rain.gif'),
  DeskModeItem('Retro Fire', '04_retro_fire.gif'),
  DeskModeItem('Rainbow Wave', '05_rainbow_wave.gif'),
  DeskModeItem('Pac-Man', '06_pacman_chomp.gif'),
  DeskModeItem('Equalizer', '07_equalizer_bars.gif'),
  DeskModeItem('Starfield', '08_starfield_warp.gif'),
];

class StudioShell extends StatefulWidget {
  const StudioShell({super.key});

  @override
  State<StudioShell> createState() => _StudioShellState();
}

class _StudioShellState extends State<StudioShell> {
  final LoyBleHost ble = LoyBleHost();
  late final LocalHttpHost httpHost;
  int navIndex = 0;

  // 64x64 RGB pixel buffer for the native Flutter Canvas Studio
  final Uint8List canvasRgb = Uint8List(64 * 64 * 3);
  final List<Uint8List> undoStack = [];
  Color activeColor = const Color(0xFF3B82F6);
  bool eraserMode = false;
  int brushSize = 2;
  int selectedClockFmt = 0;
  Color clockColor = const Color(0xFF38BDF8);

  static const List<Color> palette = [
    Colors.white,
    Color(0xFFEF4444),
    Color(0xFFF97316),
    Color(0xFFFACC15),
    Color(0xFF10B981),
    Color(0xFF06B6D4),
    Color(0xFF3B82F6),
    Color(0xFFA855F7),
    Color(0xFFEC4899),
    Colors.black,
  ];

  @override
  void initState() {
    super.initState();
    httpHost = LocalHttpHost(ble);
    ble.addListener(_onBleChange);
    _loadHeartTemplate();
    httpHost.start(listenPort: 18421).then((_) {
      if (mounted) setState(() {});
    }).catchError((_) {});
  }

  @override
  void dispose() {
    ble.removeListener(_onBleChange);
    httpHost.stop();
    ble.disconnect();
    super.dispose();
  }

  void _onBleChange() {
    if (mounted) setState(() {});
  }

  void _loadHeartTemplate() {
    canvasRgb.fillRange(0, canvasRgb.length, 0);
    for (var y = 0; y < 64; y++) {
      for (var x = 0; x < 64; x++) {
        final nx = (x - 32) / 18.0;
        final ny = (30 - y) / 18.0;
        final v = math.pow(nx * nx + ny * ny - 1, 3) - nx * nx * ny * ny * ny;
        if (v <= 0) {
          final i = (y * 64 + x) * 3;
          canvasRgb[i] = 255;
          canvasRgb[i + 1] = 35;
          canvasRgb[i + 2] = 85;
        }
      }
    }
  }

  Future<void> _sendBuiltinGif(DeskModeItem item) async {
    if (!ble.connected) {
      _snack('Connect to display (or Simulator) first');
      return;
    }
    final data = await rootBundle.load('assets_gifs/${item.file}');
    await ble.sendGifBytes(data.buffer.asUint8List(), label: item.title);
    _snack('Sent ${item.title}');
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), duration: const Duration(seconds: 2)),
    );
  }

  void _paintAt(Offset localPos, double boxSize) {
    final cx = ((localPos.dx / boxSize) * 64).floor().clamp(0, 63);
    final cy = ((localPos.dy / boxSize) * 64).floor().clamp(0, 63);
    final col = eraserMode ? Colors.black : activeColor;
    final r = (col.r * 255).round();
    final g = (col.g * 255).round();
    final b = (col.b * 255).round();
    final half = brushSize ~/ 2;
    for (var dy = -half; dy < brushSize - half; dy++) {
      for (var dx = -half; dx < brushSize - half; dx++) {
        final tx = cx + dx;
        final ty = cy + dy;
        if (tx >= 0 && tx < 64 && ty >= 0 && ty < 64) {
          final i = (ty * 64 + tx) * 3;
          canvasRgb[i] = r;
          canvasRgb[i + 1] = g;
          canvasRgb[i + 2] = b;
        }
      }
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            backgroundColor: const Color(0xFF0E1017),
            selectedIndex: navIndex,
            onDestinationSelected: (i) => setState(() => navIndex = i),
            labelType: NavigationRailLabelType.all,
            leading: Padding(
              padding: const EdgeInsets.symmetric(vertical: 14),
              child: Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  gradient: const LinearGradient(colors: [Color(0xFF3B82F6), Color(0xFF06B6D4)]),
                ),
                child: const Icon(Icons.grid_view_rounded, color: Colors.white),
              ),
            ),
            destinations: const [
              NavigationRailDestination(icon: Icon(Icons.home_rounded), label: Text('Home')),
              NavigationRailDestination(icon: Icon(Icons.brush_rounded), label: Text('Create')),
              NavigationRailDestination(icon: Icon(Icons.access_time_rounded), label: Text('Clocks')),
              NavigationRailDestination(icon: Icon(Icons.bluetooth_connected_rounded), label: Text('Host')),
            ],
          ),
          const VerticalDivider(width: 1, color: Color(0xFF1E2230)),
          Expanded(
            child: Column(
              children: [
                _buildTopBar(),
                if (ble.busy)
                  LinearProgressIndicator(
                    value: ble.transferProgress > 0 ? ble.transferProgress : null,
                    minHeight: 3,
                    backgroundColor: const Color(0xFF181C27),
                  ),
                Expanded(
                  child: IndexedStack(
                    index: navIndex,
                    children: [
                      _buildHomeTab(),
                      _buildCreateTab(),
                      _buildClocksTab(),
                      _buildHostTab(),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTopBar() {
    return Container(
      height: 58,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: const BoxDecoration(
        color: Color(0xFF0D0F16),
        border: Border(bottom: BorderSide(color: Color(0xFF1D2230))),
      ),
      child: Row(
        children: [
          const Text(
            'LOY Studio · Pure Flutter Host',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const Spacer(),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: ble.connected ? const Color(0xFF0F2D22) : const Color(0xFF1A1E2B),
              borderRadius: BorderRadius.circular(99),
              border: Border.all(
                color: ble.connected ? const Color(0xFF10B981) : const Color(0xFF2D3347),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  Icons.circle,
                  size: 9,
                  color: ble.connected ? const Color(0xFF10B981) : Colors.grey,
                ),
                const SizedBox(width: 8),
                Text(
                  ble.connected ? '${ble.deviceName} · ${ble.brightness}/15' : ble.statusMessage,
                  style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          FilledButton.icon(
            onPressed: ble.connected ? () => ble.sendRgbCanvas(canvasRgb, label: 'Flutter Canvas') : null,
            icon: const Icon(Icons.send_rounded, size: 16),
            label: const Text('Send Canvas'),
          ),
        ],
      ),
    );
  }

  Widget _buildHomeTab() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Hero Card
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              color: const Color(0xFF11131B),
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: const Color(0xFF232838)),
            ),
            child: Row(
              children: [
                SizedBox(
                  width: 132,
                  height: 132,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: CustomPaint(painter: _LedMatrixPainter(canvasRgb)),
                  ),
                ),
                const SizedBox(width: 20),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text(
                            ble.deviceName ?? 'No Display Connected',
                            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                          ),
                          const Spacer(),
                          FilledButton.tonalIcon(
                            onPressed: ble.scanning ? null : () => ble.startScan(),
                            icon: const Icon(Icons.bluetooth_searching_rounded, size: 16),
                            label: Text(ble.scanning ? 'Scanning...' : 'Scan BLE'),
                          ),
                          const SizedBox(width: 8),
                          OutlinedButton(
                            onPressed: () => ble.connectSimulator(),
                            child: const Text('Simulator'),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Row(
                        children: [
                          const Text('Brightness', style: TextStyle(color: Color(0xFF9AA3B5))),
                          Expanded(
                            child: Slider(
                              value: ble.brightness.toDouble(),
                              min: 1,
                              max: 15,
                              divisions: 14,
                              onChanged: ble.connected ? (v) => ble.setBrightness(v.round()) : null,
                            ),
                          ),
                          Text('${ble.brightness} / 15'),
                        ],
                      ),
                      Wrap(
                        spacing: 8,
                        children: [
                          FilledButton.tonalIcon(
                            onPressed: ble.connected ? () => ble.setPower(!ble.powerOn) : null,
                            icon: const Icon(Icons.power_settings_new_rounded, size: 16),
                            label: Text(ble.powerOn ? 'Power: ON' : 'Power: OFF'),
                          ),
                          OutlinedButton.icon(
                            onPressed: ble.connected ? () => ble.syncRtc() : null,
                            icon: const Icon(Icons.sync_rounded, size: 16),
                            label: const Text('Sync RTC'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (ble.scanned.isNotEmpty) ...[
            const SizedBox(height: 16),
            const Text('Discovered BLE Displays', style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 10,
              runSpacing: 8,
              children: ble.scanned
                  .map(
                    (d) => ActionChip(
                      avatar: const Icon(Icons.bluetooth_rounded, size: 16),
                      label: Text('${d.name} (${d.rssi} dBm)'),
                      onPressed: () => ble.connect(d.id, d.name),
                    ),
                  )
                  .toList(),
            ),
          ],
          const SizedBox(height: 22),
          const Text(
            '18 Built-In 64×64 Work Modes & Pixel GIFs',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 12),
          GridView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
              maxCrossAxisExtent: 140,
              mainAxisSpacing: 10,
              crossAxisSpacing: 10,
              childAspectRatio: 0.92,
            ),
            itemCount: kDeskModes.length,
            itemBuilder: (_, idx) {
              final item = kDeskModes[idx];
              return InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => _sendBuiltinGif(item),
                child: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF141824),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: const Color(0xFF252C3E)),
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.asset(
                          'assets_gifs/${item.file}',
                          width: 64,
                          height: 64,
                          filterQuality: FilterQuality.none,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        item.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                ),
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _buildCreateTab() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            flex: 3,
            child: Center(
              child: AspectRatio(
                aspectRatio: 1,
                child: LayoutBuilder(
                  builder: (_, constraints) {
                    final sz = constraints.maxWidth;
                    return GestureDetector(
                      onPanStart: (d) {
                        undoStack.add(Uint8List.fromList(canvasRgb));
                        if (undoStack.length > 30) undoStack.removeAt(0);
                        _paintAt(d.localPosition, sz);
                      },
                      onPanUpdate: (d) => _paintAt(d.localPosition, sz),
                      onTapDown: (d) {
                        undoStack.add(Uint8List.fromList(canvasRgb));
                        _paintAt(d.localPosition, sz);
                      },
                      child: Container(
                        decoration: BoxDecoration(
                          color: const Color(0xFF050608),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: const Color(0xFF2A3146), width: 2),
                        ),
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: CustomPaint(painter: _LedMatrixPainter(canvasRgb)),
                        ),
                      ),
                    );
                  },
                ),
              ),
            ),
          ),
          const SizedBox(width: 20),
          SizedBox(
            width: 280,
            child: Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xFF11131B),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFF232838)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Pixel Studio (64×64)', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: palette
                        .map(
                          (c) => GestureDetector(
                            onTap: () => setState(() {
                              activeColor = c;
                              eraserMode = false;
                            }),
                            child: Container(
                              width: 30,
                              height: 30,
                              decoration: BoxDecoration(
                                color: c,
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                  color: activeColor == c && !eraserMode ? Colors.white : Colors.white24,
                                  width: activeColor == c && !eraserMode ? 2.5 : 1,
                                ),
                              ),
                            ),
                          ),
                        )
                        .toList(),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      const Text('Brush'),
                      Expanded(
                        child: Slider(
                          value: brushSize.toDouble(),
                          min: 1,
                          max: 6,
                          divisions: 5,
                          onChanged: (v) => setState(() => brushSize = v.round()),
                        ),
                      ),
                      Text('$brushSize'),
                    ],
                  ),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      ChoiceChip(
                        label: const Text('Pen'),
                        selected: !eraserMode,
                        onSelected: (_) => setState(() => eraserMode = false),
                      ),
                      ChoiceChip(
                        label: const Text('Eraser'),
                        selected: eraserMode,
                        onSelected: (_) => setState(() => eraserMode = true),
                      ),
                      OutlinedButton(
                        onPressed: undoStack.isEmpty
                            ? null
                            : () => setState(() {
                                  final prev = undoStack.removeLast();
                                  canvasRgb.setAll(0, prev);
                                }),
                        child: const Text('Undo'),
                      ),
                      OutlinedButton(
                        onPressed: () => setState(() {
                          undoStack.add(Uint8List.fromList(canvasRgb));
                          canvasRgb.fillRange(0, canvasRgb.length, 0);
                        }),
                        child: const Text('Clear'),
                      ),
                      OutlinedButton(
                        onPressed: () => setState(_loadHeartTemplate),
                        child: const Text('❤️ Heart'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      onPressed: ble.connected ? () => ble.sendRgbCanvas(canvasRgb, label: 'Pixel Art') : null,
                      icon: const Icon(Icons.send_rounded, size: 16),
                      label: const Text('Send Artwork to Display'),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildClocksTab() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Native Hardware RTC Clock Faces (TLV 0x33)', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const Text('Ticks autonomously offline on your 64×64 display.', style: TextStyle(color: Color(0xFF9AA3B5))),
          const SizedBox(height: 16),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: List.generate(
              8,
              (i) => ChoiceChip(
                label: Text('Firmware Face #$i'),
                selected: selectedClockFmt == i,
                onSelected: (_) => setState(() => selectedClockFmt = i),
              ),
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            children: palette
                .where((c) => c != Colors.black)
                .map(
                  (c) => GestureDetector(
                    onTap: () => setState(() => clockColor = c),
                    child: Container(
                      width: 32,
                      height: 32,
                      decoration: BoxDecoration(
                        color: c,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(
                          color: clockColor == c ? Colors.white : Colors.white24,
                          width: clockColor == c ? 2.5 : 1,
                        ),
                      ),
                    ),
                  ),
                )
                .toList(),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: ble.connected
                ? () => ble.sendFirmwareClock(
                      selectedClockFmt,
                      (clockColor.r * 255).round(),
                      (clockColor.g * 255).round(),
                      (clockColor.b * 255).round(),
                    )
                : null,
            icon: const Icon(Icons.access_time_rounded),
            label: const Text('Sync RTC & Apply Clock Face'),
          ),
        ],
      ),
    );
  }

  Widget _buildHostTab() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: const Color(0xFF11131B),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0xFF232838)),
            ),
            child: Row(
              children: [
                const Icon(Icons.dns_rounded, color: Color(0xFF38BDF8)),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Embedded Pure Dart Local HTTP Host', style: TextStyle(fontWeight: FontWeight.bold)),
                      Text(
                        httpHost.running
                            ? 'Listening on http://0.0.0.0:${httpHost.port} (/api/status, /api/control, /api/gif_pack/send)'
                            : 'Stopped',
                        style: const TextStyle(color: Color(0xFF9AA3B5), fontSize: 12),
                      ),
                    ],
                  ),
                ),
                Switch(
                  value: httpHost.running,
                  onChanged: (v) async {
                    if (v) {
                      await httpHost.start(listenPort: 18421);
                    } else {
                      await httpHost.stop();
                    }
                    setState(() {});
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          const Text('Live BLE Packet Log (TX / RX)', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          Expanded(
            child: Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xFF0A0C12),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: const Color(0xFF1E2230)),
              ),
              child: ListView.builder(
                itemCount: ble.packetLog.length,
                itemBuilder: (_, i) {
                  final e = ble.packetLog[i];
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Text(
                      '[${e.dir}] ${e.summary}  |  ${e.hex}',
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11.5,
                        color: e.dir == 'TX' ? const Color(0xFF60A5FA) : const Color(0xFF34D399),
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _LedMatrixPainter extends CustomPainter {
  final Uint8List rgb;
  _LedMatrixPainter(this.rgb);

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = const Color(0xFF050608));
    final cw = size.width / 64.0;
    final ch = size.height / 64.0;
    final r = (math.min(cw, ch) * 0.38).clamp(0.8, 12.0);
    final offPaint = Paint()..color = const Color(0xFF131620);
    final onPaint = Paint();
    for (var y = 0; y < 64; y++) {
      for (var x = 0; x < 64; x++) {
        final i = (y * 64 + x) * 3;
        final rv = rgb[i], gv = rgb[i + 1], bv = rgb[i + 2];
        final center = Offset((x + 0.5) * cw, (y + 0.5) * ch);
        if (rv + gv + bv > 0) {
          onPaint.color = Color.fromARGB(255, rv, gv, bv);
          canvas.drawCircle(center, r, onPaint);
        } else {
          canvas.drawCircle(center, r * 0.72, offPaint);
        }
      }
    }
  }

  @override
  bool shouldRepaint(covariant _LedMatrixPainter oldDelegate) => true;
}
