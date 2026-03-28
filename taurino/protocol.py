"""GIP (Game Input Protocol) constants, packet helpers, and device tables."""

import struct

# -- Vendor / Product IDs ----------------------------------------------------

PDP_VENDOR_ID = 0x0E6F

PDP_PRODUCTS = {
    0x0139: "PDP Afterglow Prismatic (Xbox One)",
    0x0146: "PDP Xbox One Controller",
    0x0213: "PDP Xbox One Controller",
    0x02F1: "PDP Wired Controller for Xbox One",
    0x02F2: "PDP Wired Controller for Xbox One (Alt)",
    0x02A1: "PDP Afterglow Prismatic (v2)",
    0x0346: "PDP RC Xbox One",
    0x0446: "PDP Xbox One (v2)",
}

EXTRA_CONTROLLERS = {
    (0x045E, 0x02D1): "Microsoft Xbox One Controller",
    (0x045E, 0x02DD): "Microsoft Xbox One Controller (FW 2015)",
    (0x045E, 0x02E3): "Microsoft Xbox One Elite Controller",
    (0x045E, 0x0B00): "Microsoft Xbox One Elite 2 Controller",
    (0x045E, 0x0B12): "Microsoft Xbox Series X|S Controller",
    (0x24C6, 0x541A): "PowerA Xbox One Controller",
    (0x24C6, 0x542A): "PowerA Xbox One Spectra",
}

# -- GIP protocol constants ---------------------------------------------------

GIP_INTERFACE = 0
GIP_READ_BUFFER = 64

# Command types (byte 0)
GIP_CMD_ACK      = 0x01
GIP_CMD_ANNOUNCE = 0x02
GIP_CMD_STATUS   = 0x03
GIP_CMD_IDENTIFY = 0x04
GIP_CMD_POWER    = 0x05
GIP_CMD_AUTH     = 0x06
GIP_CMD_GUIDE    = 0x07
GIP_CMD_AUDIO    = 0x08
GIP_CMD_RUMBLE   = 0x09
GIP_CMD_LED      = 0x0A
GIP_CMD_INPUT    = 0x20

# Header flag bits (byte 1)
GIP_OPT_ACK      = 0x10   # packet requests an ACK
GIP_OPT_INTERNAL = 0x20   # from device (client → host)

GIP_CMD_LABELS = {
    0x01: "ACK",
    0x02: "ANNOUNCE",
    0x03: "STATUS",
    0x04: "IDENTIFY",
    0x05: "POWER",
    0x06: "AUTH",
    0x07: "GUIDE",
    0x08: "AUDIO",
    0x09: "RUMBLE",
    0x0A: "LED",
    0x20: "INPUT",
}

# -- Initialization packets (order matters) -----------------------------------

GIP_INIT_POWER = bytes([0x05, 0x20, 0x00, 0x01, 0x00])
GIP_INIT_LONG  = bytes([0x05, 0x20, 0x00, 0x0F, 0x06, 0x00, 0x00, 0x00,
                         0x00, 0x00, 0x00, 0x55, 0x53])
GIP_PDP_LED    = bytes([0x0A, 0x20, 0x00, 0x03, 0x00, 0x01, 0x14])
GIP_PDP_AUTH1  = bytes([0x06, 0x20, 0x00, 0x02, 0x01, 0x00])
GIP_PDP_AUTH2  = bytes([0x06, 0x20, 0x00, 0x02, 0x02, 0x00])


# -- Packet helpers -----------------------------------------------------------

def make_ack(host_seq: int, pkt_cmd: int, pkt_opts: int,
             pkt_seq: int, pkt_len: int) -> bytes:
    """Build a GIP ACK for the given received packet header fields."""
    return bytes([
        GIP_CMD_ACK,
        0x20,               # host → device
        host_seq & 0xFF,
        0x09,               # ACK payload is always 9 bytes
        0x00,               # padding
        pkt_cmd,
        pkt_opts,
        pkt_seq,
        pkt_len,
        0x00, 0x00, 0x00, 0x00,
    ])


# -- HID report descriptor for virtual gamepad --------------------------------

GAMEPAD_HID_DESCRIPTOR = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x05,        # Usage (Game Pad)
    0xA1, 0x01,        # Collection (Application)
    0xA1, 0x00,        #   Collection (Physical)
    # --- 16 buttons ---
    0x05, 0x09,        #     Usage Page (Button)
    0x19, 0x01,        #     Usage Minimum (1)
    0x29, 0x10,        #     Usage Maximum (16)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x75, 0x01,        #     Report Size (1)
    0x95, 0x10,        #     Report Count (16)
    0x81, 0x02,        #     Input (Data, Var, Abs)
    # --- 2 triggers (0-1023) ---
    0x05, 0x02,        #     Usage Page (Simulation Controls)
    0x09, 0xC5,        #     Usage (Brake)  → Left Trigger
    0x09, 0xC4,        #     Usage (Accelerator) → Right Trigger
    0x15, 0x00,        #     Logical Minimum (0)
    0x26, 0xFF, 0x03,  #     Logical Maximum (1023)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data, Var, Abs)
    # --- Left stick (X, Y) ---
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
    0x16, 0x00, 0x80,  #     Logical Minimum (-32768)
    0x26, 0xFF, 0x7F,  #     Logical Maximum (32767)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data, Var, Abs)
    # --- Right stick (Rx, Ry) ---
    0x09, 0x33,        #     Usage (Rx)
    0x09, 0x34,        #     Usage (Ry)
    0x16, 0x00, 0x80,  #     Logical Minimum (-32768)
    0x26, 0xFF, 0x7F,  #     Logical Maximum (32767)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data, Var, Abs)
    0xC0,              #   End Collection
    0xC0,              # End Collection
])

GAMEPAD_REPORT_SIZE = 14  # 2 + 2×2 + 4×2
