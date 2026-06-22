# l3-netkeys

l3-netkeys is a lightweight UDP keyboard and mouse bridge for classic Amiga systems.

A USB keyboard and mouse are connected to the Raspberry Pi. The latter acts as a network bridge: it reads the local input events and forwards them as compact UDP packets to the Amiga. The Amiga-side program receives those packets and injects the corresponding events through input.device.

The intended setup is simple:

```text
USB keyboard/mouse -> Raspberry Pi -> Wi-Fi/Ethernet -> Amiga
```

## Purpose

l3-netkeys can be useful when:

- the Amiga is placed in an awkward position
- the Amiga keyboard is damaged or unreliable
- the Amiga mouse is insufferable
- you can't live without a mouse wheel

Left Amiga is mapped to the left Windows key, while Right Amiga is mapped to the Menu/Application key.

## How it works

The Raspberry Pi client reads Linux `evdev` input events from `/dev/input/event*` and sends small UDP packets containing:

- Amiga raw key codes
- key up/down state
- relative mouse movement
- mouse button state
- heartbeat packets
- optional reset request

The Amiga server listens on a UDP port and writes the corresponding events to input.device.

## Requirements

### Amiga side

- AmigaOS 3.x
- Working TCP/IP stack, for example Miami or Roadshow

### Raspberry Pi

- Raspberry Pi OS
- Python 3
- `python3-evdev`

```sh
sudo apt-get update
sudo apt-get install python3-evdev
```

The Python client normally needs to run as root because it reads `/dev/input/event*` devices and may grab them exclusively.

## Installation

[Download NetKeys L3 v0.1](https://github.com/user-attachments/files/29198427/l3-netkeys-v1.0.zip)

Copy the Amiga executable to the Amiga.

Example:

```text
C:l3-netkeys
```

Copy the Raspberry Pi client to the Pi:

```sh
sudo cp l3-netkeys.py /usr/local/bin/l3-netkeys.py
sudo chmod +x /usr/local/bin/l3-netkeys.py
```

## Basic usage

Start the Amiga-side receiver:

```text
l3-netkeys
```

By default it listens on UDP port `6800`.

Run the client on the Pi:

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --auto --grab
```

Replace `192.168.1.29` with the IP address of the Amiga.

## Function-key training

Some USB keyboards expose `F1`-`F10` through a different Linux input device. The `--train` option records where those keys really arrive.

Run:

```sh
sudo ./l3-netkeys.py --train
```

The client listens to all readable Linux input devices and asks for `F1` to `F10`. It saves the real `eventX`, evdev code, device name, and Amiga rawcode.

## Advanced usage

List input devices:

```sh
sudo ./l3-netkeys.py --list
```

Use explicit input devices:

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 \
  --keyboard /dev/input/event0 \
  --mouse /dev/input/event1 \
  --grab
```

Use a custom UDP port:

```text
l3-netkeys 6801
```

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --port 6801 --auto --grab
```

Adjust mouse speed:

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --auto --grab --mouse-scale 1.5
```

Adjust mouse wheel emulation:

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --auto --grab --wheel-steps 3
```

The mouse wheel is emulated with repeated Amiga cursor-key presses (3 in this case).

## Remote reset

The client can request an Amiga keyboard reset.

Hold `Ctrl` and press `r` a few times until Amiga reboots.

This sends a reset packet to the Amiga-side receiver, which attempts the keyboard reset chord.

## Running at boot (Raspberry side)

NetKeys L3 can be started at boot from `/etc/rc.local`.

```sh
#!/bin/sh -e

/usr/local/bin/l3-netkeys.py --host 192.168.1.29 --auto --grab >> /var/log/l3-netkeys.log 2>&1 &

exit 0
```

Make sure the file is executable:

```sh
sudo chmod +x /etc/rc.local
```

## Auto-run with Miami (Amiga side)

You may enter "C:l3-netkeys" under Interface -> interfaces definition -> interface events -> online

## Limitations

Programs that bypass input.device, read the hardware directly, use custom low-level input handlers or expect joystick/mouse signals directly from the physical ports will not work with l3-netkeys.

Mouse wheel support is emulated through cursor-key presses because classic Amiga mouse input has no standard wheel event.

Caps Lock is handled on the Raspberry Pi side rather than by sending a synthetic Amiga Caps Lock event.

Keyboard behavior depends on the active Amiga keymap and on how applications process input.device events.

Wi-Fi latency is usually acceptable, but performance depends on the network and on the Amiga TCP/IP stack.

## License

NetKeys L3 is released under the 0BSD license.
