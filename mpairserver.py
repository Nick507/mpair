import machine
import micropython
import time
import network
import socket
import os
import struct
import io

udp_socket = None
udp_logger = None

class UDPLogger(io.IOBase):
    def __init__(self, ip, port):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_dest = (ip, port)

    def write(self, data):
        try:
            self.udp_socket.sendto(data, self.udp_dest)
        except:
            pass # Avoid crashing if network is busy
        return len(data)

    def readinto(self, buf):
        # Required for dupterm, but can return None if you only want output
        return None

def enable_udp_logger(ip, port):
    global udp_logger
    udp_logger = UDPLogger(ip, port)
    os.dupterm(udp_logger)
    print(f"Logger enabled to {ip}:{port}")

def disable_udp_logger():
    global udp_logger
    os.dupterm(None)
    udp_logger = None
    print("Logger disabled")

def process_socket(arg):
    global udp_socket, logger_udp_socket, logger_upd_dest
    #print("Checking for reset packet")
    try:
        # Now we are in the 'main' context: memory allocation is OK!
        data, addr = udp_socket.recvfrom(64)
        if b"boot" in data:
            print('Boot mode requested')
            with open(".bootmode", "w") as f:
                pass # Creates an empty file
            machine.reset()
        elif b"reset" in data:
            print('Reset requested')
            machine.reset()
        elif b"logger" in data:
            params = data.decode().split(" ")
            if len(params) == 3:
                enable_udp_logger(params[1], int(params[2]))
            else:
                disable_udp_logger()
    except OSError:
        pass

def timer_callback(t):
    # This runs in the interrupt. We 'schedule' the socket work 
    # to happen in the main thread immediately after the ISR exits.
    micropython.schedule(process_socket, None)

def start(ssid, password, port = 8267, logger = None):
    global udp_socket

    print(f"Connecting to WiFi...")

    wlan = network.WLAN(network.STA_IF)
    
    # 1. Force deactivation to reset peripheral state
    if wlan.active():
        wlan.active(False)
        time.sleep(0.5) # Critical delay for ESP32-C3 hardware
    
    # 2. Re-initialize
    wlan.active(True)
    
    # 3. Handle 'already connected' ghosts
    if wlan.isconnected():
        wlan.disconnect()
        time.sleep(0.1)
        
    wlan.connect(ssid, password)
    
    # 4. Wait for connection with timeout
    timeout = 100 # 10 seconds
    while not wlan.isconnected() and timeout > 0:
        time.sleep(0.1)
        timeout -= 1
        
    if wlan.isconnected():
        print("Connected! IP:", wlan.ifconfig()[0])
    else:
        print("Connection failed.")
        return

    # Setup the socket
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(('0.0.0.0', port))
    udp_socket.setblocking(False)

    # Register a hardware timer to check every 500ms
    # This runs 'independently' of your main.py loop
    rt_timer = machine.Timer(0)
    rt_timer.init(period=500, mode=machine.Timer.PERIODIC, callback=timer_callback)

    if logger is not None:
        params = logger.split(":")
        enable_udp_logger(params[0], int(params[1]))

    # Check if we are in bootmode
    is_bootmode = False
    try:
        os.stat(".bootmode")
        is_bootmode = True
    except OSError:
        pass

    if is_bootmode:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('0.0.0.0', port + 1))
        s.listen(1)
        print("MPAIR server started")

        # On ESP8266 recv() is not guaranteed to return the exact number of bytes requested.
        def recv_exact(conn, n):
            buf = b''
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        while True:
            conn, addr = s.accept()
            try:
                while True:
                    raw_len = recv_exact(conn, 4)
                    if not raw_len: break
                    header_len = struct.unpack('>I', raw_len)[0]
                    raw_code = recv_exact(conn, header_len)
                    if not raw_code: break
                    code = raw_code.decode()
                    try:
                        print("Executing client command...")
                        exec(code, {'conn': conn, 'machine': machine, 'os': os})
                    except Exception as e:
                        print("Exec exception:", e)

            except Exception as e:
                print("Error:", e)
            finally:
                conn.close()
                try:
                    os.stat(".bootmode")
                except OSError:
                    machine.reset()
