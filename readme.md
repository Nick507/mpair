# Description

**mpair** (MicroPython Air) is a lightweight tool for developing MicroPython projects over WiFi. It lets you upload/download files, manage the filesystem, reset the device, and stream logs — all wirelessly from your PC.

# Motivation

`mpremote` over WebREPL proved unreliable. mpair was built as a simple, robust alternative for WiFi-based development without USB.

# Advantages

- Very small server-side overhead (single file, minimal dependencies)
- UDP log streaming that doesn't block or hang the device
- Stable device reset with boot-mode handshake
- Simple CLI — no daemons, no config files

# How it works

mpair uses a two-channel approach:

- **UDP** (be default, port 8267) — for out-of-band commands: `reset`, `boot mode`, `logger control`. Handled by a background timer on the ESP, so it works even while `main.py` is running.
- **TCP** (UDP port + 1) — for file operations. The PC triggers a reboot into *boot mode*, connects over TCP, and sends Python snippets that execute directly on the ESP via `exec()`. After all operations are done, the device resets back to normal mode. Boot mode is signaled by the presence of a `.bootmode` marker file on the device filesystem — `mpairserver` creates it before rebooting and deletes it on exit, so a crash or power loss during a session won't leave the device stuck.
- **UDP log streaming** — `os.dupterm()` redirects MicroPython's stdout to UDP packets, which the PC receives with the `listen` command.

# Environment

Tested on **ESP32-C3**.

# How to install

**Install the PC tool:**

```bash
pip install mpair
```

Or from source:

```bash
pip install .
```

After installing, the `mpair` command becomes available directly:

```bash
mpair IP <command> [args]
```

**On the device** — upload `mpairserver.py` using any method:

```
mpremote fs cp mpairserver.py :
```

Or use WebREPL, Thonny, or any other tool for the first upload.

**In `boot.py`** — add:

```python
import mpairserver
mpairserver.start("SSID", "PASSWORD")
```

Optionally enable UDP log streaming from boot:

```python
mpairserver.start("SSID", "PASSWORD", logger="192.168.1.100:6000")
```

# How to use

```
mpair IP[:PORT] <command> [args]
```

| Command | Description |
|---|---|
| `reset` | Reset the device |
| `put <file> [file...]` | Upload file(s) to the device |
| `get <file> [file...]` | Download file(s) from the device |
| `ls` | List files on the device |
| `rm <file> [file...]` | Delete file(s) from the device |
| `mkdir <dir> [dir...]` | Create directory(s) on the device |
| `logger [IP:PORT]` | Enable UDP log streaming to PC (omit IP:PORT to disable) |
| `listen PORT` | Listen for incoming UDP log messages on PC |

**Examples:**

```bash
# Upload files
mpair 10.10.10.113 put main.py boot.py

# List files
mpair 10.10.10.113 ls

# Stream logs: enable on device, then listen on PC
mpair 10.10.10.113 logger 10.10.10.10:6000
mpair listen 6000

# Reset device
mpair 10.10.10.113 reset
```

# TODO

- Improve error handling
- Support `src dst` paths for `get` and `put`
- Add flag to keep bootmode, and 'exit' command
- Support input for stdin?
- Secure operations?
