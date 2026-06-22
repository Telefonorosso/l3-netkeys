# NetKeys L3

NetKeys L3 is a lightweight UDP keyboard and mouse bridge for classic Amiga systems.

A Raspberry Pi reads local USB keyboard and mouse events and sends compact UDP packets to an Amiga. The Amiga-side program receives those packets and injects input events through `input.device`.

The intended setup is simple:

```text
USB keyboard/mouse -> Raspberry Pi -> Wi-Fi/Ethernet -> Amiga
```

## Purpose

NetKeys L3 is meant for practical use with real Amiga systems.

It can be useful when:

- the Amiga keyboard is damaged or unreliable;
- the Amiga is placed in an awkward position;
- a wireless keyboard/mouse setup is desirable;
- a Raspberry Pi is already available near the Amiga;
- the Amiga is already networked through Miami, Roadshow, or a compatible TCP/IP stack.

It is not a remote desktop system. It only forwards keyboard and mouse input.

## How it works

The Raspberry Pi client reads Linux `evdev` input events from `/dev/input/event*`.

It sends small UDP packets containing:

- Amiga raw key codes;
- key up/down state;
- relative mouse movement;
- mouse button state;
- all-keys-up recovery;
- heartbeat packets;
- optional reset request.

The Amiga server listens on a UDP port and writes the corresponding events to `input.device`.

## Requirements

### Amiga

- AmigaOS 3.x
- Working TCP/IP stack, for example Miami or Roadshow
- `bsdsocket.library`
- Network connection to the Raspberry Pi
- `l3-netkeys` Amiga executable

### Raspberry Pi

- Raspberry Pi OS
- Python 3
- `python3-evdev`
- `evtest`, useful for input-device testing and training

On Raspberry Pi OS Trixie:

```sh
sudo apt-get update
sudo apt-get install python3-evdev evtest
```

The Python client normally needs to run as root because it reads `/dev/input/event*` devices and may grab them exclusively.

## Installation

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

On the Raspberry Pi, list input devices if needed:

```sh
sudo ./l3-netkeys.py --list
```

Run the client:

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --auto --grab
```

Replace `192.168.1.29` with the IP address of the Amiga.

## Advanced usage

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

The mouse wheel is emulated with repeated Amiga cursor-key presses.

## Function-key training

Some USB keyboards expose `F1`-`F10` through a different Linux input device. The `--train` option records where those keys really arrive.

Run:

```sh
sudo ./l3-netkeys.py --train
```

The client listens to all readable Linux input devices and asks for `F1` to `F10`. It saves the real `eventX`, evdev code, device name, and Amiga rawcode.

During normal use, the learned mapping overrides the default one and can automatically open a second input device if the function keys come from there. If Linux renumbers `eventX` after a reboot, the client tries to recover the device by its saved name.

No Amiga-side changes are required.

Default training file:

```text
~/.config/amikvm/keys-l3.json
```

Use a custom config file:

```sh
sudo ./l3-netkeys.py --train --config ./keys-l3.json
```

```sh
sudo ./l3-netkeys.py --host 192.168.1.29 --auto --grab --config ./keys-l3.json
```

## Remote reset

The client can request an Amiga keyboard reset.

Hold `Ctrl` and press `R` three times.

This sends a reset packet to the Amiga-side receiver, which attempts the keyboard reset chord.

## Running at boot

A simple systemd service can be used.

Example `/etc/systemd/system/l3-netkeys.service`:

```ini
[Unit]
Description=NetKeys L3 Raspberry Pi input bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/l3-netkeys.py --host 192.168.1.29 --auto --grab
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable l3-netkeys.service
sudo systemctl start l3-netkeys.service
```

View logs:

```sh
journalctl -u l3-netkeys.service -f
```

Disable it:

```sh
sudo systemctl stop l3-netkeys.service
sudo systemctl disable l3-netkeys.service
```

## Limitations

NetKeys L3 depends on the Amiga TCP/IP stack. The Amiga must already be online before starting the receiver.

It forwards input only. It does not transmit video, audio, clipboard data, files, or Workbench state.

Mouse wheel support is emulated through cursor-key presses because classic Amiga mouse input has no standard wheel event.

Caps Lock is handled on the Raspberry Pi side rather than by sending a synthetic Amiga Caps Lock event.

Keyboard behavior depends on the active Amiga keymap and on how applications process `input.device` events.

Wi-Fi latency is usually acceptable, but performance depends on the network and on the Amiga TCP/IP stack.

## License

NetKeys L3 is released under the 0BSD license.
