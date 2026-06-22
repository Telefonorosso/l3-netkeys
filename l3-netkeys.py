#!/usr/bin/env python3
"""
amikvm-l3.py

Raspberry Pi evdev -> AmigaOS input.device KVM client over UDP/IP.

This is the Layer-3/Wi-Fi-friendly transport variant of l2-netkeys.
It deliberately keeps the same AKVM payload and the same keyboard/mouse
logic from the latest L2 client: Caps Lock is handled locally, autorepeat
sends repeated key-down events, MOUSE2 carries button state with movement,
wheel scroll emits cursor-key pulses, and Ctrl+R x3 requests remote reset.

Requirements on Raspberry Pi OS:
  sudo apt install python3-evdev

Run example:
  ./amikvm-l3.py --host 192.168.1.29 --auto --grab

Train F1-F10:
  ./amikvm-l3.py --train

List devices:
  ./amikvm-l3.py --list

Amiga side:
  - Start Miami/Roadshow/another bsdsocket-compatible TCP/IP stack.
  - Run l3-netkeys on the Amiga.
"""

import argparse
import asyncio
import json
import select
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    print("Missing python3-evdev. Install with: sudo apt install python3-evdev", file=sys.stderr)
    raise

MAGIC = b"AKVM"
VERSION = 1

DEFAULT_PORT = 6800
DEFAULT_CONFIG = str(Path.home() / ".config" / "amikvm" / "keys-l3.json")

PKT_KEY = 1
PKT_MOUSE = 2
PKT_BUTTON = 3
PKT_ALLUP = 4
PKT_HEARTBEAT = 5
PKT_RESET = 6
PKT_MOUSE2 = 7

# Amiga raw key codes. These are physical-ish positions, not characters.
RAW = {
    # top row
    "ESC": 0x45,
    "1": 0x01, "2": 0x02, "3": 0x03, "4": 0x04, "5": 0x05,
    "6": 0x06, "7": 0x07, "8": 0x08, "9": 0x09, "0": 0x0A,
    "MINUS": 0x0B, "EQUAL": 0x0C, "BACKSLASH": 0x0D, "BACKSPACE": 0x41,

    # qwerty row
    "TAB": 0x42,
    "Q": 0x10, "W": 0x11, "E": 0x12, "R": 0x13, "T": 0x14,
    "Y": 0x15, "U": 0x16, "I": 0x17, "O": 0x18, "P": 0x19,
    "LEFTBRACE": 0x1A, "RIGHTBRACE": 0x1B,

    # home row
    "A": 0x20, "S": 0x21, "D": 0x22, "F": 0x23, "G": 0x24,
    "H": 0x25, "J": 0x26, "K": 0x27, "L": 0x28,
    "SEMICOLON": 0x29, "APOSTROPHE": 0x2A, "GRAVE": 0x00,
    "ENTER": 0x44,

    # lower row
    "102ND": 0x30,
    "Z": 0x31, "X": 0x32, "C": 0x33, "V": 0x34, "B": 0x35,
    "N": 0x36, "M": 0x37, "COMMA": 0x38, "DOT": 0x39, "SLASH": 0x3A,
    "SPACE": 0x40,

    # cursor keys
    "UP": 0x4C, "DOWN": 0x4D, "RIGHT": 0x4E, "LEFT": 0x4F,

    # function keys
    "F1": 0x50, "F2": 0x51, "F3": 0x52, "F4": 0x53, "F5": 0x54,
    "F6": 0x55, "F7": 0x56, "F8": 0x57, "F9": 0x58, "F10": 0x59,

    # modifiers
    "LEFTSHIFT": 0x60, "RIGHTSHIFT": 0x61, "CAPSLOCK": 0x62,
    "LEFTCTRL": 0x63, "LEFTALT": 0x64, "RIGHTALT": 0x65,
    "LEFTMETA": 0x66, "RIGHTMETA": 0x67,
}

# evdev KEY_* -> Amiga raw code. This intentionally maps keys by physical role.
KEYMAP: Dict[int, int] = {
    ecodes.KEY_ESC: RAW["ESC"],
    ecodes.KEY_1: RAW["1"], ecodes.KEY_2: RAW["2"], ecodes.KEY_3: RAW["3"], ecodes.KEY_4: RAW["4"],
    ecodes.KEY_5: RAW["5"], ecodes.KEY_6: RAW["6"], ecodes.KEY_7: RAW["7"], ecodes.KEY_8: RAW["8"],
    ecodes.KEY_9: RAW["9"], ecodes.KEY_0: RAW["0"], ecodes.KEY_MINUS: RAW["MINUS"],
    ecodes.KEY_EQUAL: RAW["EQUAL"], ecodes.KEY_BACKSPACE: RAW["BACKSPACE"],
    ecodes.KEY_TAB: RAW["TAB"],
    ecodes.KEY_Q: RAW["Q"], ecodes.KEY_W: RAW["W"], ecodes.KEY_E: RAW["E"], ecodes.KEY_R: RAW["R"],
    ecodes.KEY_T: RAW["T"], ecodes.KEY_Y: RAW["Y"], ecodes.KEY_U: RAW["U"], ecodes.KEY_I: RAW["I"],
    ecodes.KEY_O: RAW["O"], ecodes.KEY_P: RAW["P"], ecodes.KEY_LEFTBRACE: RAW["LEFTBRACE"],
    ecodes.KEY_RIGHTBRACE: RAW["RIGHTBRACE"], ecodes.KEY_ENTER: RAW["ENTER"],
    ecodes.KEY_LEFTCTRL: RAW["LEFTCTRL"], ecodes.KEY_RIGHTCTRL: RAW["LEFTCTRL"],
    ecodes.KEY_A: RAW["A"], ecodes.KEY_S: RAW["S"], ecodes.KEY_D: RAW["D"], ecodes.KEY_F: RAW["F"],
    ecodes.KEY_G: RAW["G"], ecodes.KEY_H: RAW["H"], ecodes.KEY_J: RAW["J"], ecodes.KEY_K: RAW["K"],
    ecodes.KEY_L: RAW["L"], ecodes.KEY_SEMICOLON: RAW["SEMICOLON"], ecodes.KEY_APOSTROPHE: RAW["APOSTROPHE"],
    ecodes.KEY_GRAVE: RAW["GRAVE"], ecodes.KEY_LEFTSHIFT: RAW["LEFTSHIFT"],
    ecodes.KEY_BACKSLASH: RAW["BACKSLASH"],
    ecodes.KEY_Z: RAW["Z"], ecodes.KEY_X: RAW["X"], ecodes.KEY_C: RAW["C"], ecodes.KEY_V: RAW["V"],
    ecodes.KEY_B: RAW["B"], ecodes.KEY_N: RAW["N"], ecodes.KEY_M: RAW["M"], ecodes.KEY_COMMA: RAW["COMMA"],
    ecodes.KEY_DOT: RAW["DOT"], ecodes.KEY_SLASH: RAW["SLASH"], ecodes.KEY_RIGHTSHIFT: RAW["RIGHTSHIFT"],
    ecodes.KEY_LEFTALT: RAW["LEFTALT"], ecodes.KEY_RIGHTALT: RAW["RIGHTALT"], ecodes.KEY_SPACE: RAW["SPACE"],
    ecodes.KEY_CAPSLOCK: RAW["CAPSLOCK"],
    ecodes.KEY_LEFTMETA: RAW["LEFTMETA"], ecodes.KEY_RIGHTMETA: RAW["RIGHTMETA"],
    # Some PC keyboards expose the right Windows/Menu key as KEY_COMPOSE.
    # Treat it as Right Amiga so Ctrl+LeftAmiga+RightAmiga is available.
    ecodes.KEY_COMPOSE: RAW["RIGHTMETA"],
    ecodes.KEY_UP: RAW["UP"], ecodes.KEY_DOWN: RAW["DOWN"], ecodes.KEY_LEFT: RAW["LEFT"], ecodes.KEY_RIGHT: RAW["RIGHT"],
    ecodes.KEY_F1: RAW["F1"], ecodes.KEY_F2: RAW["F2"], ecodes.KEY_F3: RAW["F3"], ecodes.KEY_F4: RAW["F4"],
    ecodes.KEY_F5: RAW["F5"], ecodes.KEY_F6: RAW["F6"], ecodes.KEY_F7: RAW["F7"], ecodes.KEY_F8: RAW["F8"],
    ecodes.KEY_F9: RAW["F9"], ecodes.KEY_F10: RAW["F10"],
}

# ISO keyboard extra key, usually the < > key left of Z.
_KEY_102ND = getattr(ecodes, "KEY_102ND", None)
if _KEY_102ND is not None:
    KEYMAP[_KEY_102ND] = RAW["102ND"]

BUTTONMAP = {
    ecodes.BTN_LEFT: 1,
    ecodes.BTN_RIGHT: 2,
    ecodes.BTN_MIDDLE: 3,
}


class Sender:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0

    def _send(self, typ: int, payload: bytes = b"") -> None:
        self.seq = (self.seq + 1) & 0xFFFF
        pkt = MAGIC + bytes([VERSION, typ]) + struct.pack(">H", self.seq) + payload
        self.sock.sendto(pkt, self.addr)

    def key(self, raw: int, down: bool) -> None:
        self._send(PKT_KEY, bytes([raw & 0x7F, 1 if down else 0]))

    def key_pulse(self, raw: int) -> None:
        self.key(raw, True)
        self.key(raw, False)

    def mouse_move(self, dx: int, dy: int) -> None:
        dx = max(-32768, min(32767, dx))
        dy = max(-32768, min(32767, dy))
        if dx or dy:
            self._send(PKT_MOUSE, struct.pack(">hh", dx, dy))

    def mouse2(self, dx: int, dy: int, buttons: int) -> None:
        dx = max(-32768, min(32767, dx))
        dy = max(-32768, min(32767, dy))
        self._send(PKT_MOUSE2, struct.pack(">hhB", dx, dy, buttons & 0x07))

    def button(self, button: int, down: bool) -> None:
        self._send(PKT_BUTTON, bytes([button, 1 if down else 0]))

    def allup(self) -> None:
        self._send(PKT_ALLUP)

    def heartbeat(self) -> None:
        self._send(PKT_HEARTBEAT)

    def reset(self) -> None:
        self._send(PKT_RESET)


def key_name(code: int) -> str:
    name = ecodes.KEY.get(code, str(code))
    if isinstance(name, list):
        return "/".join(name)
    return str(name)


def list_input_devices() -> None:
    for path in list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities(verbose=True)
        print(f"{path}: {dev.name}")
        if ("EV_KEY", ecodes.EV_KEY) in caps:
            keys = caps[("EV_KEY", ecodes.EV_KEY)]
            interesting = []
            for item in keys:
                # With verbose=True, evdev may return items like:
                #   (('KEY_LEFTCTRL', 'KEY_LEFTMETA'), 29)
                # for aliases. Older code assumed item[0] was always a string.
                if isinstance(item, tuple):
                    names = item[0]
                else:
                    names = item

                if isinstance(names, (tuple, list)):
                    labels = [str(x) for x in names]
                else:
                    labels = [str(names)]

                for label in labels:
                    if label.startswith("KEY_") or label.startswith("BTN_"):
                        interesting.append(label)
                        break
            print("  keys/buttons:", ", ".join(interesting[:20]), "...")
        if ("EV_REL", ecodes.EV_REL) in caps:
            print("  relative axes:", caps[("EV_REL", ecodes.EV_REL)])


FKEY_TRAINING = [
    ("F1", RAW["F1"]), ("F2", RAW["F2"]), ("F3", RAW["F3"]), ("F4", RAW["F4"]), ("F5", RAW["F5"]),
    ("F6", RAW["F6"]), ("F7", RAW["F7"]), ("F8", RAW["F8"]), ("F9", RAW["F9"]), ("F10", RAW["F10"]),
]


def _event_key_name(code: int) -> str:
    name = ecodes.KEY.get(code, str(code))
    if isinstance(name, list):
        return "/".join(str(x) for x in name)
    return str(name)


def load_config(config_path: str) -> Tuple[Dict[int, int], Set[str]]:
    keymap = dict(KEYMAP)
    extra_devices: Set[str] = set()
    path = Path(config_path).expanduser()

    if not path.exists():
        return keymap, extra_devices

    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        print(f"warning: cannot read config {path}: {exc}", file=sys.stderr)
        return keymap, extra_devices

    bindings = data.get("bindings", [])
    known_devices = {p: InputDevice(p) for p in list_devices()}

    for b in bindings:
        try:
            code = int(b["code"])
            raw = int(b["raw"])
        except (KeyError, TypeError, ValueError):
            continue

        keymap[code] = raw & 0x7F

        configured_path = str(b.get("device", ""))
        configured_name = str(b.get("device_name", ""))
        resolved = None

        if configured_path in known_devices:
            resolved = configured_path
        elif configured_name:
            for candidate_path, dev in known_devices.items():
                if dev.name == configured_name:
                    resolved = candidate_path
                    break

        if resolved:
            extra_devices.add(resolved)

    return keymap, extra_devices


def train_fkeys(config_path: str) -> None:
    devices: List[InputDevice] = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps:
                devices.append(dev)
        except OSError as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)

    if not devices:
        raise SystemExit("No readable evdev keyboard devices found.")

    print("Training F1-F10. Press the requested key on the keyboard you want to use.")
    print("Listening on:")
    for dev in devices:
        print(f"  {dev.path}: {dev.name}")

    bindings = []

    for label, raw in FKEY_TRAINING:
        print(f"\nPress {label}...", flush=True)

        captured = None
        while captured is None:
            readable, _, _ = select.select(devices, [], [])
            for dev in readable:
                for event in dev.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    if event.value != 1:
                        continue
                    captured = (dev, event.code)
                    break
                if captured:
                    break

        dev, code = captured
        evname = _event_key_name(code)
        print(f"{label}: {dev.path} code={code} {evname} -> Amiga raw 0x{raw:02X}")
        bindings.append({
            "name": label,
            "device": dev.path,
            "device_name": dev.name,
            "code": int(code),
            "evdev": evname,
            "raw": int(raw),
        })

        # Avoid a held key, bounce, or first repeat being consumed as the next answer.
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            readable, _, _ = select.select(devices, [], [], 0.05)
            for rdev in readable:
                try:
                    list(rdev.read())
                except BlockingIOError:
                    pass

    out = {
        "version": 1,
        "description": "amikvm-l3 trained F1-F10 evdev bindings",
        "bindings": bindings,
    }

    path = Path(config_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nSaved: {path}")



async def read_keyboard(path: str, sender: Sender, grab: bool, keymap: Dict[int, int]) -> None:
    dev = InputDevice(path)
    if grab:
        dev.grab()
    print(f"keyboard: {path} ({dev.name})")
    print("remote reset: hold Ctrl and press R three times")
    print("caps lock: handled locally by this client")

    ctrl_down = False
    shift_down = False
    caps_on = False
    reset_count = 0
    reset_last = 0.0

    # When caps_on is active, letter key-down injects a temporary Amiga Shift.
    # We remember which physical key owns that injected shift so key-up can
    # release it in the right order.
    caps_shift_owner = set()

    letter_codes = {
        ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_C, ecodes.KEY_D, ecodes.KEY_E,
        ecodes.KEY_F, ecodes.KEY_G, ecodes.KEY_H, ecodes.KEY_I, ecodes.KEY_J,
        ecodes.KEY_K, ecodes.KEY_L, ecodes.KEY_M, ecodes.KEY_N, ecodes.KEY_O,
        ecodes.KEY_P, ecodes.KEY_Q, ecodes.KEY_R, ecodes.KEY_S, ecodes.KEY_T,
        ecodes.KEY_U, ecodes.KEY_V, ecodes.KEY_W, ecodes.KEY_X, ecodes.KEY_Y,
        ecodes.KEY_Z,
    }

    async for event in dev.async_read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        is_repeat = event.value == 2
        is_down = event.value == 1
        is_up = event.value == 0

        if event.code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
            ctrl_down = is_down
        if event.code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            shift_down = is_down

        # Caps Lock is handled entirely in this Python client. We do not send
        # RAW_CAPS to Amiga because synthetic Caps Lock via input.device is not
        # reliable on all systems. Instead, caps_on makes letters get a temporary
        # Amiga Shift. No USB LED handling: too hardware/driver-dependent.
        if event.code == ecodes.KEY_CAPSLOCK:
            if is_down:
                caps_on = not caps_on
                print(f"caps lock {'ON' if caps_on else 'OFF'}")
            continue

        # Old PuTTY mode received Ctrl-R as byte 0x12. In evdev mode we get
        # physical key events instead, so the reset trigger must live here.
        # Ctrl+R is swallowed locally; the third press/repeat sends PKT_RESET.
        if ctrl_down and event.code == ecodes.KEY_R and (is_down or is_repeat):
            now = time.monotonic()
            if now - reset_last > 3.0:
                reset_count = 0
            reset_last = now
            reset_count += 1
            print(f"remote reset armed: {reset_count}/3")
            if reset_count >= 3:
                reset_count = 0
                sender.allup()
                sender.reset()
            continue

        # Linux evdev sends value=2 for autorepeat.
        # Do not synthesize a too-short up/down pulse here: without Miami in the
        # middle this can become too tight and stutter on Amiga. Instead send
        # another key-down event. The Amiga receiver accepts repeated downs and
        # reinjects them as RAWKEY events.
        if is_repeat:
            raw = keymap.get(event.code)
            if raw is None:
                continue
            if event.code in (
                ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
                ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
                ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
                ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA, ecodes.KEY_COMPOSE,
                ecodes.KEY_CAPSLOCK,
            ):
                continue
            sender.key(raw, True)
            continue

        if is_down and event.code not in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
            reset_count = 0

        raw = keymap.get(event.code)
        if raw is None:
            continue

        if caps_on and event.code in letter_codes and not shift_down:
            if is_down:
                sender.key(RAW["LEFTSHIFT"], True)
                sender.key(raw, True)
                caps_shift_owner.add(event.code)
            elif is_up:
                sender.key(raw, False)
                if event.code in caps_shift_owner:
                    sender.key(RAW["LEFTSHIFT"], False)
                    caps_shift_owner.discard(event.code)
            continue

        # If Caps was on when the key went down, but Shift state changed before
        # key-up, still release the temporary injected Shift cleanly.
        if is_up and event.code in caps_shift_owner:
            sender.key(raw, False)
            sender.key(RAW["LEFTSHIFT"], False)
            caps_shift_owner.discard(event.code)
            continue

        sender.key(raw, is_down)


async def read_mouse(path: str, sender: Sender, grab: bool, scale: float, wheel_steps: int) -> None:
    dev = InputDevice(path)
    if grab:
        dev.grab()
    print(f"mouse: {path} ({dev.name})")

    pending_dx = 0
    pending_dy = 0
    buttons = 0
    frac_x = 0.0
    frac_y = 0.0

    async for event in dev.async_read_loop():
        if event.type == ecodes.EV_REL:
            if event.code == ecodes.REL_X:
                scaled = event.value * scale + frac_x
                send_dx = int(scaled)
                frac_x = scaled - send_dx
                pending_dx += send_dx
            elif event.code == ecodes.REL_Y:
                scaled = event.value * scale + frac_y
                send_dy = int(scaled)
                frac_y = scaled - send_dy
                pending_dy += send_dy
            elif event.code == ecodes.REL_WHEEL:
                # Classic Amiga mouse has no wheel. Emulate wheel scroll with
                # repeated cursor keys: wheel up -> Up, wheel down -> Down.
                if event.value:
                    raw = RAW["UP"] if event.value > 0 else RAW["DOWN"]
                    for _ in range(abs(event.value) * max(0, wheel_steps)):
                        sender.key_pulse(raw)
        elif event.type == ecodes.EV_KEY:
            bit = 0

            if event.code == ecodes.BTN_LEFT:
                bit = 1
            elif event.code == ecodes.BTN_RIGHT:
                bit = 2
            elif event.code == ecodes.BTN_MIDDLE:
                bit = 4

            if bit:
                if event.value == 1:
                    buttons |= bit
                elif event.value == 0:
                    buttons &= ~bit
                else:
                    continue

                # New protocol: button state travels with the mouse event.
                # dx=dy=0 still matters because it carries button transitions.
                sender.mouse2(0, 0, buttons)
        elif event.type == ecodes.EV_SYN:
            if pending_dx or pending_dy:
                sender.mouse2(pending_dx, pending_dy, buttons)
                pending_dx = pending_dy = 0


async def heartbeat(sender: Sender) -> None:
    while True:
        sender.heartbeat()
        await asyncio.sleep(1.0)


def guess_devices() -> Tuple[Optional[str], Optional[str]]:
    keyboard = None
    mouse = None
    for path in list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities()
        keys = set(caps.get(ecodes.EV_KEY, []))
        rels = set(caps.get(ecodes.EV_REL, []))
        if keyboard is None and ecodes.KEY_A in keys and ecodes.KEY_ENTER in keys:
            keyboard = path
        if mouse is None and ecodes.REL_X in rels and ecodes.REL_Y in rels and ecodes.BTN_LEFT in keys:
            mouse = path
    return keyboard, mouse


async def main_async(args: argparse.Namespace) -> None:
    sender = Sender(args.host, args.port)
    keymap, trained_devices = load_config(args.config)
    keyboard = args.keyboard
    mouse = args.mouse

    print(f"target: {args.host}:{args.port}/UDP")
    print(f"config: {Path(args.config).expanduser()}")

    if args.auto:
        gk, gm = guess_devices()
        keyboard = keyboard or gk
        mouse = mouse or gm

    keyboard_devices: List[str] = []
    if keyboard:
        keyboard_devices.append(keyboard)
    for dev in sorted(trained_devices):
        if dev not in keyboard_devices:
            keyboard_devices.append(dev)

    tasks = [asyncio.create_task(heartbeat(sender))]

    for kbd in keyboard_devices:
        tasks.append(asyncio.create_task(read_keyboard(kbd, sender, args.grab, keymap)))
    if mouse:
        tasks.append(asyncio.create_task(read_mouse(mouse, sender, args.grab, args.mouse_scale, args.wheel_steps)))

    if len(tasks) == 1:
        raise SystemExit("No input device selected. Use --list, then --keyboard/--mouse, --auto, or --train first.")

    sender.allup()
    try:
        await asyncio.gather(*tasks)
    finally:
        sender.allup()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Raspberry Pi evdev to Amiga UDP/IP KVM client")
    p.add_argument("--host", help="Amiga IP address or hostname")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port, default 6800")
    p.add_argument("--keyboard", help="keyboard event device, e.g. /dev/input/event0")
    p.add_argument("--mouse", help="mouse event device, e.g. /dev/input/event1")
    p.add_argument("--auto", action="store_true", help="try to auto-detect keyboard and mouse")
    p.add_argument("--grab", action="store_true", help="grab devices so Linux console does not also receive input")
    p.add_argument("--mouse-scale", type=float, default=1.0, help="relative mouse multiplier")
    p.add_argument("--wheel-steps", type=int, default=5,
                   help="cursor-key pulses per mouse wheel notch, default 5")
    p.add_argument("--config", default=DEFAULT_CONFIG, help=f"trained key config, default {DEFAULT_CONFIG}")
    p.add_argument("--train", action="store_true", help="learn F1-F10 from all readable evdev devices and save config")
    p.add_argument("--list", action="store_true", help="list input devices and exit")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.list:
        list_input_devices()
        return
    if args.train:
        train_fkeys(args.config)
        return
    if not args.host:
        raise SystemExit("--host is required unless --list is used")
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
