"""Protocol constants for YS / YSTP01 BLE Pixel LED Displays."""

from enum import IntEnum

# BLE GATT UUIDs (VERIFIED STATIC: module 29a0)
BLE_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
BLE_WRITE_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

# Default advertised name prefixes (VERIFIED STATIC: module 29a0)
DEFAULT_DEVICE_PREFIXES = ("YS", "TL")

# Frame header constants (VERIFIED STATIC: module a829, 3341)
PACKET_MAGIC = 0x55AA  # Written little-endian on wire: 0xAA, 0x55
PACKET_TARGET_BROADCAST = 0xFFFF

# Frame flags byte (byte 8)
FLAG_CHECKSUM = 0x80
FLAG_ACK_REQUIRED = 0x40
FLAG_VERSION_BIT = 0x01
FLAG_DEFAULT = FLAG_CHECKSUM | FLAG_ACK_REQUIRED | FLAG_VERSION_BIT  # 0xC1 (193)
FLAG_NO_ACK = FLAG_CHECKSUM | FLAG_VERSION_BIT  # 0x81 (129)

# Command types (byte 9)
CMD_TYPE_SET = 2
CMD_TYPE_GET = 3
CMD_TYPE_UPGRADE = 4
CMD_TYPE_DOWNLOAD = 7

# Device response types (byte 9 in device -> host frames)
RESP_TYPE_SET_ACK = 130      # 0x82
RESP_TYPE_GET_ACK = 131      # 0x83
RESP_TYPE_UPGRADE_ACK = 132  # 0x84
RESP_TYPE_UPLOAD = 6         # 0x06 (unsolicited device event)

# Chunk sizes (VERIFIED STATIC: modules 29a0, aa14, a829, 4156)
DEFAULT_BLE_CHUNK_SIZE = 180   # Standard BLE write chunk; high-speed firmware supports 248 or 509
DEFAULT_DATA_CHUNK_SIZE = 960  # Max TLV 0x13 payload chunk per protocol frame


class TLV(IntEnum):
    """TLV tag identifiers recovered from modules a829 & 3341."""
    POWER = 4               # 0x04
    TIMING = 5              # 0x05 (RTC clock sync)
    LIGHT = 6               # 0x06 (brightness 1..15)
    DELETE = 8              # 0x08
    DATA_SAVE = 9           # 0x09 (1 = volatile/RAM, 0 = flash persist; encoded as 1 - data_save)
    DEV_INFO = 10           # 0x0A
    ID_PRO = 12             # 0x0C (0-indexed program ID)
    ID_RECT = 13            # 0x0D (0-indexed region ID)
    ID_ITEM = 14            # 0x0E (0-indexed item ID)
    ITEM_CTRL = 17          # 0x11 (item playback & type properties)
    DATA_META = 18          # 0x12 (chunk count, chunk index, chunk size, stream flag)
    DATA_CHUNK = 19         # 0x13 (raw chunk bytes)
    ANIMATE = 20            # 0x14 (animation type, speed, stay time)
    BORDER = 21             # 0x15 (built-in border patterns)
    PERIOD_PRO = 22         # 0x16 (schedule period)
    PARAM_IO = 23           # 0x17
    DISPATCH = 24           # 0x18 (display/switch program)
    COVER_PRO = 25          # 0x19 (overwrite program/item)
    PARAM_DEV = 27          # 0x1B (display width, height, color depth, scan type)
    PRO_PROP = 28           # 0x1C (program properties)
    RECT_POS = 29           # 0x1D (region x, y, w, h, background)
    DOWNLOAD = 34           # 0x22
    SYNC = 37               # 0x25
    CANCEL_ALARM = 38       # 0x26
    CANCEL_PLAYLIST = 39    # 0x27
    SHOW_DEV = 41           # 0x29
    PGM_FLICKER = 45        # 0x2D
    ROTATE = 47             # 0x2F (0, 1, 2, 3 -> 0/90/180/270 deg)
    GAME = 48               # 0x30
    RT_DRAW = 50            # 0x32 (real-time pixel/rect drawing)
    ANI_NUM = 51            # 0x33 (clock, countdown, scoreboard, spectrum, ambient)
    BLUETOOTH_CFG = 52      # 0x34 (BT / power_model / remote control)
    PGM_KEY = 53            # 0x35 (program SHA/MD5 hash + timestamp)
    PGM_PLAY = 54           # 0x36 (playlist / loop mode)
    PWD = 55                # 0x37 (BLE password)
    GLOBAL_ANI = 56         # 0x38
    CHANNEL_COLOR = 57      # 0x39
    MIRROR = 58             # 0x3A
    SENSOR = 62             # 0x3E
    DEV_INFO_EX = 63        # 0x3F
