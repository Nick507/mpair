# Description

**mpair** (MicroPython Air) is a lightweight tool for developing MicroPython projects over WiFi. It lets you upload/download files, manage the filesystem, reset the device, and stream logs — all wirelessly from your PC.

# Motivation

`mpremote` over WebREPL proved unreliable. mpair was built as a simple, robust alternative for WiFi-based development without USB.

# Advantages

- Very small server-side overhead (single file, minimal dependencies)
- UDP log streaming that doesn't block or hang the device
- Stable device reset with boot-mode handshake
- Simple CLI — no daemons, no config files
- Atomic file uploads — files are first written as `.upload` temporaries, then renamed in a separate commit step. If the connection drops mid-transfer, the original file remains intact
- **`--hold` mode** — keeps the device in boot mode between commands. The client reconnects over TCP immediately (no UDP boot + 3s wait each time), so you can run many file operations in a row quickly; omit `--hold` on the last command to reboot back to normal mode

# How it works

mpair uses a two-channel approach:

- **UDP** (by default, port 8267) — for out-of-band commands: `reset`, `boot mode`, `logger control`. Handled by a background timer on the ESP, so it works even while `main.py` is running.
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
mpair IP[:PORT] <command> [args] [--hold]
```

| Command | Description |
|---|---|
| `reset` | Reset the device (hard reset via UDP command) |
| `put <local_file> [remote_file]` | Upload a file to the device |
| `get <remote_file> [local_file]` | Download a file from the device |
| `cat <remote_file>` | Print a remote file to stdout |
| `ls [dir]` | List files on the device |
| `tree [dir]` | Show a recursive file and directory tree |
| `rm <file> [file...]` | Delete file(s) or directory(s) from the device |
| `mkdir <dir> [dir...]` | Create directory(s) on the device |
| `exit` | If device is in boot mode, then leave it and reboot to normal mode (no file operations) |
| `logger [IP:PORT]` | Enable UDP log streaming to PC (omit IP:PORT to disable) |
| `listen PORT` | Listen for incoming UDP log messages on PC |

The `--hold` flag can be placed anywhere in the command. It keeps the device in boot mode after the command completes, so the next command can reconnect instantly without a full reboot cycle. End a hold session by running a command without `--hold`, or run `exit` (use `mpair IP --hold exit` to reconnect quickly if the device is already in boot mode, then reboot out).

Paths with directories are supported — `put` auto-creates parent directories on the device, `get` auto-creates them locally. If the destination ends with `/`, the source filename is appended (e.g. `put main.py lib/` uploads as `lib/main.py`).

**Examples:**

```bash
# Upload a file
mpair 192.168.0.113 put main.py

# Upload to a specific remote path
mpair 192.168.0.113 put ./utils.py lib/utils.py

# Upload multiple files quickly using --hold
mpair 192.168.0.113 --hold put main.py
mpair 192.168.0.113 --hold put boot.py
mpair 192.168.0.113 put lib/utils.py

# Leave boot mode after a --hold session (fast reconnect + reboot)
mpair 192.168.0.113 --hold exit

# List files
mpair 192.168.0.113 ls
mpair 192.168.0.113 ls lib

# Recursive tree (optional root directory)
mpair 192.168.0.113 tree
mpair 192.168.0.113 tree lib

# Stream logs: enable on device, then listen on PC
mpair 192.168.0.113 logger 192.168.0.10:6000
mpair listen 6000

# Reset device
mpair 192.168.0.113 reset
```

# Caveats

- **No security today** — there is no authentication or authorization. Anyone who can reach the device on the LAN (UDP command port and TCP boot-mode port) can reset the board, enter boot mode, run arbitrary code via the TCP channel, and read or change the filesystem. Treat the device as fully exposed on whatever network runs `mpairserver`.
- **Development use only** — mpair is meant for trusted local development (home lab, isolated WiFi, or a VLAN you control). Do **not** rely on it in production deployments, on public networks, or anywhere an untrusted party could reach those ports.

# TODO

- add error handling, test negative cases
- get/put directories
- Add API for user app to be able to run logger
- Support input for stdin?
- Secure operations?
- eval/exec for UDP?
- support bulk commands
- flag to disable temporary files during upload? (for cases where no free space)
