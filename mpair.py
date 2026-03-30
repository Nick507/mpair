import socket
import time
import sys
import json
import struct
import os
import textwrap

ESP_IP = None
PORT = 8267  # default UDP command port
tcp_socket = None

##########################################################################################

def send_udp_command(data):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.sendto(data, (ESP_IP, PORT))

##########################################################################################

def udp_reset():
    global tcp_socket
    print("Restarting via UDP command...")
    send_udp_command(b"reset")

##########################################################################################

def udp_logger(ip = None, port = None):
    global tcp_socket
    if ip is None or port is None:
        send_udp_command(f"logger".encode())
        return
    print(f"Setting logger to {ip}:{port}...")
    send_udp_command(f"logger {ip} {port}".encode())

##########################################################################################

def send_code(code):
    code_size = len(code.encode())
    # Pack length as 4-byte big-endian integer
    tcp_socket.sendall(struct.pack('>I', code_size)) 
    tcp_socket.sendall(code.encode())

##########################################################################################

def receive_response():
    # 1. Read the 4-byte length prefix
    raw_header = tcp_socket.recv(4)
    if not raw_header:
        return None
    
    total_len = struct.unpack('>I', raw_header)[0]
    
    # 2. Receive the full JSON body (handle chunks)
    data = b''
    while len(data) < total_len:
        chunk = tcp_socket.recv(min(total_len - len(data), 4096))
        if not chunk:
            break
        data += chunk
        
    return json.loads(data.decode())

##########################################################################################

def connect_to_server(timeout=5.0):
    global tcp_socket

    try:
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(timeout)
        tcp_socket.connect((ESP_IP, PORT + 1))
    except Exception as e:
        tcp_socket = None
        return False

    return True

##########################################################################################

def enter_bootmode(hold=False):
    global tcp_socket

    if hold:
        print("Connecting...", end="")
        sys.stdout.flush()
        if connect_to_server(timeout=1.0): # short timeout to check if already in bootmode
            send_code("conn.sendall('OK'.encode())")
            response = tcp_socket.recv(2)
            if response == b'OK':
                print("OK (already in bootmode)")
                return True
            print("MALFORMED RESPONSE")
            return False

    print("Entering bootmode...", end="")
    sys.stdout.flush()
    send_udp_command(b"boot")
    time.sleep(3)
    
    if not connect_to_server():
        print("FAIL")
        return False

    send_code("conn.sendall('OK'.encode())")

    response = tcp_socket.recv(2)

    if response == b'OK':
        print("OK")
        return True
    else:
        print("MALFORMED RESPONSE")
        return False

##########################################################################################

def exit_bootmode():
    
    print("Exiting bootmode...", end="")
    sys.stdout.flush()

    code = textwrap.dedent(f"""
        import machine
        print("Resetting device...")
        try:
            os.remove(".bootmode")
        except: 
            pass
        conn.sendall('OK'.encode())
        conn.recv(1)
        conn.close()
        machine.reset()""").strip()
    send_code(code)
    response = tcp_socket.recv(2)
    if response == b'OK':
        tcp_socket.sendall(b'\x00')
        print("OK")
        return True
    else:
        print("FAIL")
        return False

##########################################################################################

def put_file(local_file, remote_file):
    file_size = os.path.getsize(local_file)
    print(f"Pushing {local_file} -> {remote_file} ({file_size} bytes)...")

    code = f"""
import os, struct, json
filename = '{remote_file}.upload'
size = {file_size}

# Ensure parent directories exist
parts = filename.replace('\\\\', '/').split('/')
for i in range(1, len(parts)):
    d = '/'.join(parts[:i])
    try: os.mkdir(d)
    except OSError: pass

with open(filename, 'wb') as f:
    remaining = size
    while remaining > 0:
        chunk = conn.recv(min(remaining, 1024))
        if not chunk: break
        f.write(chunk)
        remaining -= len(chunk)

res = json.dumps({{'status': 'ok', 'file': filename}}).encode()
conn.sendall(struct.pack('>I', len(res)) + res)
""".strip()

    send_code(code)

    with open(local_file, 'rb') as f:
        while True:
            chunk = f.read(4096)
            if not chunk: break
            tcp_socket.sendall(chunk)

    response = receive_response()
    if response and response['status'] == 'ok':
        print(f"Uploaded {remote_file} successfully.")
    else:
        print(f"Failed to upload {remote_file}.")
        return False
    return True

##########################################################################################

def commit_file(remote_file):
    code = textwrap.dedent(f"""
        import os, json, struct
        filename = '{remote_file}'
        upload_name = filename + ".upload"
        try:
            os.stat(upload_name)
            try: os.remove(filename)
            except: pass
            os.rename(upload_name, filename)
            res = json.dumps({{'status': 'success', 'committed': filename}}).encode()
        except OSError:
            res = json.dumps({{'status': 'error', 'msg': upload_name + ' not found'}}).encode()
        conn.sendall(struct.pack('>I', len(res)) + res)
    """).strip()

    send_code(code)
    response = receive_response()

    if response and response['status'] == 'success':
        print(f"Committed: {response['committed']}")
    else:
        print(f"Error: Could not commit {remote_file}: {response.get('msg', 'unknown')}")
        return False
    return True

##########################################################################################

def put_file_and_commit(local_file, remote_file):
    if not put_file(local_file, remote_file):
        return False
    if not commit_file(remote_file):
        return False
    return True

##########################################################################################

def get_file(remote_file, local_file):
    code = f"""
import os, struct, json
filename = '{remote_file}'
try:
    st = os.stat(filename)
    if st[0] & 0x4000:
        raise OSError("is a directory")
    size = st[6]
    res = json.dumps({{'status': 'success', 'size': size}}).encode()
    conn.sendall(struct.pack('>I', len(res)) + res)
    with open(filename, 'rb') as f:
        while True:
            chunk = f.read(1024)
            if not chunk: break
            conn.sendall(chunk)
except Exception as e:
    res = json.dumps({{'status': 'error', 'msg': str(e)}}).encode()
    conn.sendall(struct.pack('>I', len(res)) + res)
""".strip()

    send_code(code)
    response = receive_response()

    if response and response.get('status') == 'success':
        file_size = response['size']
        print(f"Getting {remote_file} -> {local_file} ({file_size} bytes)...", end="")

        parent = os.path.dirname(local_file)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(local_file, 'wb') as f:
            remaining = file_size
            while remaining > 0:
                chunk = tcp_socket.recv(min(remaining, 4096))
                if not chunk: break
                f.write(chunk)
                remaining -= len(chunk)
        print("OK")
    else:
        print(f"Error: {response.get('msg', 'Unknown error')}")
        return False
    return True

##########################################################################################

def delete_files(files_to_delete):
    files_repr = repr(files_to_delete)
    
    code = textwrap.dedent(f"""
        import os, json, struct
        targets = {files_repr}
        results = []
        errors = []

        def rmtree(path):
            for e in os.ilistdir(path):
                name, mode = e[0], e[1]
                full = path + '/' + name
                if mode & 0x4000:
                    rmtree(full)
                else:
                    os.remove(full)
            os.rmdir(path)

        for f in targets:
            try:
                mode = os.stat(f)[0]
                if mode & 0x4000:
                    rmtree(f)
                else:
                    os.remove(f)
                results.append(f)
            except OSError as e:
                errors.append({{'name': f, 'msg': str(e)}})
        
        data = json.dumps({{'status': 'ok', 'deleted': results, 'errors': errors}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()

    send_code(code)
    response = receive_response()

    if response and response['status'] == 'ok':
        for f in response['deleted']:
            print(f"Deleted: {f}")
        for e in response['errors']:
            print(f"Failed: {e['name']}: {e['msg']}")
    else:
        print("Error: Could not execute delete command.")

##########################################################################################

def make_dirs(dirs_to_create):
    dirs_repr = repr(dirs_to_create)

    code = textwrap.dedent(f"""
        import os, json, struct
        targets = {dirs_repr}
        results = []
        errors = []
        for d in targets:
            try:
                os.mkdir(d)
                results.append(d)
            except OSError as e:
                errors.append({{'dir': d, 'msg': str(e)}})

        data = json.dumps({{'status': 'ok', 'created': results, 'errors': errors}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()

    send_code(code)
    response = receive_response()

    if response and response['status'] == 'ok':
        for d in response['created']:
            print(f"Created: {d}")
        for e in response['errors']:
            print(f"Failed: {e['dir']}: {e['msg']}")
    else:
        print("Error: mkdir operation failed.")

##########################################################################################

def print_file_list(files):
    # 1. Sort: Directories (ending in /) first, then alphabetical
    files.sort(key=lambda x: (not x['name'].endswith('/'), x['name'].lower()))

    # 2. Print
    print(f"{'Size':>10}   {'Name'}")
    print("-" * 30)

    for f in files:
        name = f['name']
        is_dir = name.endswith('/')
        
        # Show "-" for directory sizes, comma-formatted for files
        size_str = "-" if is_dir else f"{f['size']:,}"
        
        print(f"{size_str:>10}   {name}")

##########################################################################################

def print_filesystem_tail(total, free):
    print("-" * 42)
    if total is not None and free is not None:
        used = total - free
        if used < 0:
            used = 0
        print(f"Filesystem: {total:,} bytes total, {free:,} bytes free ({used:,} used)")
    else:
        print("Filesystem: total/free unavailable (statvfs)")

##########################################################################################

def fetch_file_list(path='.'):
    code = textwrap.dedent(f"""
        import json, struct
        path = '{path}'
        def vfs_space(p):
            try:
                s = os.statvfs(p if p != '.' else '/')
            except OSError:
                try:
                    s = os.statvfs('.')
                except OSError:
                    return None, None
            mult = s[1] or s[0]
            bavail = s[4] if len(s) > 4 else s[3]
            return mult * s[2], mult * bavail
        try:
            res = []
            for e in os.ilistdir(path):
                name, mode = e[0], e[1]
                if mode & 0x4000:
                    name += '/'
                    size = 0
                else:
                    size = e[3] if len(e) > 3 else os.stat(path + '/' + name)[6]
                res.append({{'name': name, 'size': size}})
            pkg = {{'status': 'ok', 'files': res}}
            total, free = vfs_space(path)
            if total is not None and free is not None:
                pkg['total'] = total
                pkg['free'] = free
            data = json.dumps(pkg).encode()
        except OSError as e:
            data = json.dumps({{'status': 'error', 'msg': str(e)}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()
    send_code(code)

    response = receive_response()

    if response and response.get('status') == 'ok':
        return response
    if response and response.get('msg'):
        print(f"Error: {response['msg']}")
    return None

def list_files(path='.'):
    result = fetch_file_list(path)
    if result is not None:
        if path != '.':
            print(f"{path}:")
        print_file_list(result['files'])
        print_filesystem_tail(result.get('total'), result.get('free'))

##########################################################################################

def tree(path='.'):
    code = textwrap.dedent(f"""
        import json, struct
        root = '{path}'
        def walk(path):
            entries = []
            for e in os.ilistdir(path):
                name, mode = e[0], e[1]
                if mode & 0x4000:
                    entries.append({{'name': name, 'children': walk(path + '/' + name)}})
                else:
                    size = e[3] if len(e) > 3 else os.stat(path + '/' + name)[6]
                    entries.append({{'name': name, 'size': size}})
            return entries
        def vfs_space(p):
            try:
                s = os.statvfs(p if p != '.' else '/')
            except OSError:
                try:
                    s = os.statvfs('.')
                except OSError:
                    return None, None
            mult = s[1] or s[0]
            bavail = s[4] if len(s) > 4 else s[3]
            return mult * s[2], mult * bavail
        try:
            t = walk(root)
            pkg = {{'status': 'ok', 'tree': t}}
            total, free = vfs_space(root)
            if total is not None and free is not None:
                pkg['total'] = total
                pkg['free'] = free
            data = json.dumps(pkg).encode()
        except OSError as e:
            data = json.dumps({{'status': 'error', 'msg': str(e)}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()
    send_code(code)
    response = receive_response()

    if response and response.get('status') == 'ok':
        print_tree(response['tree'], "")
        print_filesystem_tail(response.get('total'), response.get('free'))
    elif response and response.get('msg'):
        print(f"Error: {response['msg']}")
    else:
        print("Error fetching tree.")

def print_tree(entries, prefix):
    dirs = sorted([e for e in entries if 'children' in e], key=lambda e: e['name'].lower())
    files = sorted([e for e in entries if 'children' not in e], key=lambda e: e['name'].lower())
    items = dirs + files
    for i, entry in enumerate(items):
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        if 'children' in entry:
            print(f"{prefix}{connector}{entry['name']}/")
            extension = "    " if is_last else "│   "
            print_tree(entry['children'], prefix + extension)
        else:
            size = f"{entry['size']:,}"
            print(f"{prefix}{connector}{entry['name']}  ({size})")

##########################################################################################

def print_help():
    print("Usage: python mpair.py IP[:PORT] <command> [args] [--hold]")
    print()
    print("Commands:")
    print("  reset                           Reset the device")
    print("  put <local_file> [remote_file]  Upload a file to the device")
    print("  get <remote_file> [local_file]  Download a file from the device")
    print("  ls [dir]                        List files on the device")
    print("  tree [dir]                      Show file tree recursively")
    print("  rm <file> [file...]             Delete file(s) from the device")
    print("  mkdir <dir> [dir...]            Create directory(s) on the device")
    print("  exit                            Leave boot mode and reboot to normal mode")
    print("  logger [IP:PORT]                Enable remote logger (or disable, if no [IP:PORT] provided)")
    print("  listen PORT                     Listen for UDP log messages on PORT")
    print()
    print("Options:")
    print("  --hold   Keep device in bootmode after command (skip reboot)")

##########################################################################################

def listen_udp_logs(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"Error: cannot bind to port {port}: {e}")
        sys.exit(1)
    sock.settimeout(1.0)
    print(f"Listening for UDP logs on port {port}... (Ctrl+C to stop)", flush=True)
    try:
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                print(data.decode('utf-8', errors='replace'), end='', flush=True)
            except TimeoutError:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

##########################################################################################

def parse_address(addr_str):
    if ':' in addr_str:
        ip, port = addr_str.rsplit(':', 1)
        return ip, int(port)
    return addr_str, 8267

##########################################################################################

def main():
    global ESP_IP, PORT

    argv = [a for a in sys.argv[1:] if a != '--hold']
    hold = len(argv) < len(sys.argv) - 1

    if not argv or argv[0] in ('-h', '--help', 'help'):
        print_help()
        sys.exit(0 if argv else 1)

    if argv[0] == 'listen':
        if len(argv) != 2:
            print("Usage: listen PORT")
            sys.exit(1)
        listen_udp_logs(int(argv[1]))
        return

    if len(argv) < 2:
        print_help()
        sys.exit(1)

    ESP_IP, PORT = parse_address(argv[0])
    cmd = argv[1]
    args = argv[2:]

    if cmd == 'reset':
        udp_reset()

    elif cmd == 'logger':
        if len(args) == 1 and ':' in args[0]:
            ip, port = args[0].rsplit(':', 1)
            udp_logger(ip, port)
        elif len(args) == 0:
            udp_logger()
        else:
            print("Usage: logger [IP:PORT]")
            sys.exit(1)

    elif cmd == 'put':
        if not args:
            print("Usage: put <local_file> [remote_file]")
            sys.exit(1)
        local_file = args[0].replace('\\', '/')
        remote_file = args[1].replace('\\', '/') if len(args) > 1 else local_file
        if remote_file.endswith('/'):
            remote_file += os.path.basename(local_file)
        if not os.path.isfile(local_file):
            print(f"Error: local file '{local_file}' not found.")
            sys.exit(1)
        if not enter_bootmode(hold):
            sys.exit(1)
        put_file_and_commit(local_file, remote_file)
        if not hold:
            exit_bootmode()

    elif cmd == 'get':
        if not args:
            print("Usage: get <remote_file> [local_file]")
            sys.exit(1)
        remote_file = args[0].replace('\\', '/')
        local_file = args[1] if len(args) > 1 else remote_file
        if local_file.endswith('/') or os.path.isdir(local_file):
            local_file = os.path.join(local_file, os.path.basename(remote_file))
        if not enter_bootmode(hold):
            sys.exit(1)
        get_file(remote_file, local_file)
        if not hold:
            exit_bootmode()

    elif cmd == 'ls':
        if not enter_bootmode(hold):
            sys.exit(1)
        list_files(args[0].replace('\\', '/').rstrip('/') if args else '.')
        if not hold:
            exit_bootmode()

    elif cmd == 'tree':
        if not enter_bootmode(hold):
            sys.exit(1)
        tree(args[0].replace('\\', '/').rstrip('/') if args else '.')
        if not hold:
            exit_bootmode()

    elif cmd == 'rm':
        if not args:
            print("Usage: rm <file> [file...]")
            sys.exit(1)
        if not enter_bootmode(hold):
            sys.exit(1)
        delete_files(args)
        if not hold:
            exit_bootmode()

    elif cmd == 'mkdir':
        if not args:
            print("Usage: mkdir <dir> [dir...]")
            sys.exit(1)
        if not enter_bootmode(hold):
            sys.exit(1)
        make_dirs(args)
        if not hold:
            exit_bootmode()

    elif cmd == 'exit':
        if args:
            print("Usage: exit")
            sys.exit(1)
        if not enter_bootmode(True): # suppose that the device is in bootmode
            sys.exit(1)
        exit_bootmode()

    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()