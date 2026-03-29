import socket
import time
import sys
import json
import struct
import os
import textwrap
import glob
import fnmatch

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
    print("Restarting via UPD command...")
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

def connect_to_server():
    global tcp_socket

    try:
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.connect((ESP_IP, PORT + 1))
        tcp_socket.settimeout(5.0)
    except Exception as e:
        print(f"Failed to connect to mpair server: {e}")
        tcp_socket = None
        return False

    return True

##########################################################################################

def enter_bootmode():
    global tcp_socket

    print("Entering bootmode...", end="")
    sys.stdout.flush()
    send_udp_command(b"boot")
    time.sleep(3) # Give ESP32-C3 time to reboot into Loader Mode
    
    if not connect_to_server():
        print("FAIL TO CONNECT")
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

def put_files(files_to_put):
    for filename in files_to_put:
        file_size = os.path.getsize(filename)
        print(f"Pushing {filename} ({file_size} bytes)...")

        # 1. Send the 'Receiver' script to the ESP
        # This script runs on the ESP and waits for raw bytes on 'conn'
        code = f"""
import os, struct, json
filename = '{filename}.upload'
size = {file_size}
print(f"Receiving {filename} ({file_size} bytes)...")
with open(filename, 'wb') as f:
    remaining = size
    while remaining > 0:
        chunk = conn.recv(min(remaining, 1024))
        if not chunk: break
        f.write(chunk)
        remaining -= len(chunk)

# Send confirmation back
res = json.dumps({{'status': 'ok', 'file': filename}}).encode()
conn.sendall(struct.pack('>I', len(res)) + res)
""".strip()

        # Send the script
        send_code(code)

        # 2. Send the RAW bytes (not as code, just as data)
        with open(filename, 'rb') as f:
            while True:
                chunk = f.read(4096)
                if not chunk: break
                tcp_socket.sendall(chunk)

        # 3. Wait for the ESP to finish writing and confirm
        response = receive_response()
        if response and response['status'] == 'ok':
            print(f"Uploaded {filename} successfully.")
        else:
            print(f"Failed to upload {filename}.")
            return False
    return True

##########################################################################################

def commit_files(files_to_commit):
    # Convert the list to a string representation for the injected code
    files_repr = repr(files_to_commit)
    
    code = textwrap.dedent(f"""
        import os, json, struct
        targets = {files_repr}
        committed = []
        for filename in targets:
            upload_name = filename + ".upload"
            try:
                # Ensure the temp file exists
                os.stat(upload_name)
                # Remove old version if it exists
                try: os.remove(filename)
                except: pass
                # Atomically rename
                os.rename(upload_name, filename)
                committed.append(filename)
            except OSError:
                pass
        
        # Send back the results
        res = json.dumps({{'status': 'success', 'committed': committed}}).encode()
        conn.sendall(struct.pack('>I', len(res)) + res)
    """).strip()

    # 1. Send the commit logic
    send_code(code)
    
    # 2. Wait for the confirmation
    response = receive_response()
    
    if response and response['status'] == 'success':
        for f in response['committed']:
            print(f"Committed: {f}")
        
        # Check if anything was missing
        missed = set(files_to_commit) - set(response['committed'])
        for f in missed:
            print(f"Warning: {f}.upload not found, could not commit.")
    else:
        print("Error: Commit operation failed.")
        return False
    return True

##########################################################################################

def put_files_and_commit(files_to_put_and_commit):
    if not put_files(files_to_put_and_commit):
        return False
    if not commit_files(files_to_put_and_commit):
        return False
    return True

##########################################################################################

def get_files(files_to_get):
    for filename in files_to_get:
        # 1. Send the 'Sender' script to the ESP
        # This script opens the file and sends [4-byte JSON size][JSON header][Raw Bytes]
        code = f"""
import os, struct, json
filename = '{filename}'
try:
    size = os.stat(filename)[6]
    # Send success header
    res = json.dumps({{'status': 'success', 'size': size}}).encode()
    conn.sendall(struct.pack('>I', len(res)) + res)
    
    # Send raw binary data
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

        # 2. Receive the JSON header from ESP
        response = receive_response()
        
        if response and response.get('status') == 'success':
            file_size = response['size']
            print(f"Getting {filename} ({file_size} bytes)...", end="")
            
            # 3. Receive the RAW bytes directly from the socket
            with open(filename, 'wb') as f:
                remaining = file_size
                while remaining > 0:
                    chunk = tcp_socket.recv(min(remaining, 4096))
                    if not chunk: break
                    f.write(chunk)
                    remaining -= len(chunk)
            print("OK")
        else:
            print(f"Error: {response.get('msg', 'Unknown error')}")

##########################################################################################

def delete_files(files_to_delete):
    # Convert Python list to a string representation for the 'code' block
    files_repr = repr(files_to_delete)
    
    code = textwrap.dedent(f"""
        import os, json, struct
        targets = {files_repr}
        results = []
        for f in targets:
            try:
                os.remove(f)
                results.append(f)
            except OSError:
                pass # File didn't exist or was a directory
        
        # Package the result
        data = json.dumps({{'status': 'ok', 'deleted': results}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()

    # 1. Send the 'exec' logic
    send_code(code)
    
    # 2. Receive the structured response
    response = receive_response()
    
    # 3. Print the outcome
    if response and response['status'] == 'ok':
        for f in response['deleted']:
            print(f"Deleted: {f}")
        
        # Check if any files failed to delete
        failed = set(files_to_delete) - set(response['deleted'])
        for f in failed:
            print(f"Failed (not found): {f}")
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

def fetch_file_list():
    code = textwrap.dedent(f"""
        import json, struct
        res = []
        for e in os.ilistdir('.'):
            name, mode = e[0], e[1]
            if mode & 0x4000:
                name += '/'
                size = 0
            else:
                # ilistdir index 3 is size, if missing use stat
                size = e[3] if len(e) > 3 else os.stat(name)[6]
            res.append({{'name': name, 'size': size}})
            
        # Manually package the response with size prefix
        data = json.dumps({{'status': 'ok', 'files': res}}).encode()
        conn.sendall(struct.pack('>I', len(data)) + data)
    """).strip()
    send_code(code)

    response = receive_response()

    if response and response['status'] == 'ok':
        return response['files']
    return None

def list_files():
    files = fetch_file_list()
    if files is not None:
        print_file_list(files)
    else:
        print("Error fetching file list.")

##########################################################################################

def expand_local_patterns(patterns):
    expanded = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            expanded.extend(matches)
        else:
            expanded.append(pattern)  # let it fail naturally with a clear error
    return expanded

def expand_remote_patterns(patterns, remote_files):
    names = [f['name'] for f in remote_files if not f['name'].endswith('/')]
    expanded = []
    for pattern in patterns:
        if any(c in pattern for c in ('*', '?', '[')):
            matches = fnmatch.filter(names, pattern)
            if not matches:
                print(f"Warning: no remote files match '{pattern}'")
            expanded.extend(matches)
        else:
            expanded.append(pattern)
    return expanded

##########################################################################################

def print_help():
    print("Usage: python mpair.py IP[:PORT] <command> [args]")
    print()
    print("Commands:")
    print("  reset                  Reset the device")
    print("  put <file> [file...]   Upload file(s) to the device")
    print("  get <file> [file...]   Download file(s) from the device")
    print("  ls                     List files on the device")
    print("  rm <file> [file...]    Delete file(s) from the device")
    print("  mkdir <dir> [dir...]   Create directory(s) on the device")
    print("  logger [IP:PORT]       Enable remote logger (or disable, if no [IP:PORT] provided)")
    print("  listen PORT            Listen for UDP log messages on PORT")

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

    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print_help()
        sys.exit(0 if len(sys.argv) > 1 else 1)

    # 'listen' doesn't need a device IP
    if sys.argv[1] == 'listen':
        if len(sys.argv) != 3:
            print("Usage: listen PORT")
            sys.exit(1)
        listen_udp_logs(int(sys.argv[2]))
        return

    if len(sys.argv) < 3:
        print_help()
        sys.exit(1)

    ESP_IP, PORT = parse_address(sys.argv[1])
    cmd = sys.argv[2]
    args = sys.argv[3:]

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
            print("Usage: put <file> [file...]")
            sys.exit(1)
        files = expand_local_patterns(args)
        if not enter_bootmode():
            print("Failed to enter bootmode")
            sys.exit(1)
        put_files_and_commit(files)
        exit_bootmode()

    elif cmd == 'get':
        if not args:
            print("Usage: get <file> [file...]")
            sys.exit(1)
        if not enter_bootmode():
            print("Failed to enter bootmode")
            sys.exit(1)
        remote_files = fetch_file_list()
        if remote_files is None:
            print("Failed to fetch remote file list")
            sys.exit(1)
        files = expand_remote_patterns(args, remote_files)
        if files:
            get_files(files)
        exit_bootmode()

    elif cmd == 'ls':
        if not enter_bootmode():
            print("Failed to enter bootmode")
            sys.exit(1)
        list_files()
        exit_bootmode()

    elif cmd == 'rm':
        if not args:
            print("Usage: rm <file> [file...]")
            sys.exit(1)
        if not enter_bootmode():
            print("Failed to enter bootmode")
            sys.exit(1)
        delete_files(args)
        exit_bootmode()

    elif cmd == 'mkdir':
        if not args:
            print("Usage: mkdir <dir> [dir...]")
            sys.exit(1)
        if not enter_bootmode():
            print("Failed to enter bootmode")
            sys.exit(1)
        make_dirs(args)
        exit_bootmode()

    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()