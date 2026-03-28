"""Taurino CLI — PDP Xbox Controller Driver for macOS."""

import argparse


def cmd_scan():
    from .controller import PDP360Controller
    print("Scanning for Xbox controllers...\n")
    devices = PDP360Controller.list_devices()
    if not devices:
        print("  (none found)")
        return
    for i, d in enumerate(devices):
        print(f"  [{i}] {d['name']}")
        print(f"      VID=0x{d['vendor_id']:04X}  PID=0x{d['product_id']:04X}  "
              f"bus={d['bus']}  addr={d['address']}")


def cmd_dump():
    from .controller import PDP360Controller
    from .protocol import GIP_CMD_LABELS
    ctrl = PDP360Controller.find()
    with ctrl:
        print("[taurino] Dumping GIP packets (Ctrl-C to stop)\n")
        count = 0
        try:
            while True:
                data = ctrl.read_raw(timeout_ms=50)
                if data:
                    count += 1
                    cmd = data[0]
                    label = GIP_CMD_LABELS.get(cmd, f"0x{cmd:02X}")
                    hexs = " ".join(f"{b:02X}" for b in data)
                    print(f"[{count:4d}] {label:8s} len={len(data):2d}  {hexs}")
        except KeyboardInterrupt:
            print(f"\n[taurino] {count} packets")


def cmd_monitor():
    from .controller import PDP360Controller
    ctrl = PDP360Controller.find()
    with ctrl:
        print("[taurino] Live input (Ctrl-C to stop)\n")
        prev = ""
        try:
            while True:
                state = ctrl.read(timeout_ms=16)
                if state:
                    text = str(state)
                    if text != prev:
                        print(f"\r{text:<80}", end="", flush=True)
                        prev = text
        except KeyboardInterrupt:
            print("\n")


def cmd_read(n, vid, pid):
    from .controller import PDP360Controller
    ctrl = PDP360Controller.find(vid, pid)
    with ctrl:
        count = 0
        while count < n:
            state = ctrl.read(timeout_ms=50)
            if state:
                count += 1
                print(f"[{count:4d}] {state}")
    print(f"\nRead {count} reports.")


def cmd_gui(vid, pid):
    from .gui import run_gui
    run_gui(vid, pid)


def cmd_bridge(vid, pid, no_hid, no_udp):
    from .controller import PDP360Controller
    from .bridge import ControllerBridge
    ctrl = PDP360Controller.find(vid, pid)
    with ctrl:
        bridge = ControllerBridge(ctrl, use_hid=not no_hid,
                                  use_udp=not no_udp)
        bridge.start()


def cmd_doctor():
    from .bridge import format_bridge_doctor_report
    print(format_bridge_doctor_report())


def main():
    p = argparse.ArgumentParser(
        prog="taurino",
        description="Taurino — PDP Xbox Controller Driver for macOS (GIP)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("scan", help="List connected controllers")
    sub.add_parser("dump", help="Dump raw GIP packets")
    sub.add_parser("monitor", help="Live input display")

    rp = sub.add_parser("read", help="Read N reports and exit")
    rp.add_argument("-n", type=int, default=100)
    rp.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    rp.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    gp = sub.add_parser("gui", help="Visual controller tester (pygame)")
    gp.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    gp.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    bp = sub.add_parser("bridge",
                        help="Forward controller to macOS apps via virtual HID")
    bp.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    bp.add_argument("--pid", type=lambda x: int(x, 16), default=None)
    bp.add_argument("--no-hid", action="store_true",
                    help="Skip IOKit virtual HID device")
    bp.add_argument("--no-udp", action="store_true",
                    help="Skip UDP broadcast")
    sub.add_parser("doctor", help="Show runtime, install, and HID entitlement status")

    args = p.parse_args()

    if args.command == "scan":
        cmd_scan()
    elif args.command == "dump":
        cmd_dump()
    elif args.command == "monitor":
        cmd_monitor()
    elif args.command == "read":
        cmd_read(args.n, args.vid, args.pid)
    elif args.command == "gui":
        cmd_gui(args.vid, args.pid)
    elif args.command == "bridge":
        cmd_bridge(args.vid, args.pid, args.no_hid, args.no_udp)
    elif args.command == "doctor":
        cmd_doctor()
    else:
        p.print_help()
        print("\nQuick start:")
        print("  python -m taurino scan       # list controllers")
        print("  python -m taurino dump       # raw packet debug")
        print("  python -m taurino monitor    # live input display")
        print("  python -m taurino gui        # pygame visual tester")
        print("  python -m taurino bridge     # forward to macOS apps")
        print("  python -m taurino doctor     # verify install/entitlements")


if __name__ == "__main__":
    main()
