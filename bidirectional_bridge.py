import serial
import socket
import json
import time
import sys
import signal
import threading
import logging
import queue

# === CONFIGURATION ===
SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 115200
PC_IP = '10.100.102.16'
SENSOR_PORT = 5055
JOYSTICK_PORT = 5005

# === LOGGING SETUP ===
logging.basicConfig(
    filename="bridge_log.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# === GLOBAL VARIABLES ===
running = True
serial_lock = threading.Lock()
command_queue = queue.Queue()
sensor_queue = queue.Queue()
prev_command = None  # Global for direction switch checking


# === INITIALIZE SERIAL CONNECTION TO ARDUINO ===
def init_serial():
    max_reconnect_attempts = 5
    reconnect_delay = 2

    for attempt in range(max_reconnect_attempts):
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            time.sleep(2)  # Let Arduino reset
            print(f"✅ Connected to Arduino on {SERIAL_PORT}")
            logging.info(f"Connected to Arduino on {SERIAL_PORT}")
            return ser
        except Exception as e:
            print(f"❌ Attempt {attempt + 1}/{max_reconnect_attempts}: Failed to open serial port: {e}")
            logging.error(f"Failed to open serial port (attempt {attempt + 1}): {e}")
            if attempt < max_reconnect_attempts - 1:
                print(f"⏱ Retrying in {reconnect_delay} seconds...")
                time.sleep(reconnect_delay)

    print("❌ Failed to connect to Arduino after multiple attempts. Exiting.")
    logging.critical("Failed to connect to Arduino after multiple attempts")
    sys.exit(1)


# === SOCKET SETUP ===
def init_sensor_socket():
    """Setup socket for sending sensor data to PC"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"📡 Ready to forward sensor data to {PC_IP}:{SENSOR_PORT}")
    logging.info(f"Sensor socket initialized, target: {PC_IP}:{SENSOR_PORT}")
    return sock


def init_joystick_socket():
    """Setup socket for receiving joystick commands from PC"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", JOYSTICK_PORT))
    print(f"🎮 Listening for joystick commands on port {JOYSTICK_PORT}")
    logging.info(f"Joystick socket listening on port {JOYSTICK_PORT}")
    return sock


# === SENSOR DATA PROCESSING ===
def process_sensor_data(data_json):
    """Process and validate the sensor data"""
    try:
        data = json.loads(data_json)

        required_fields = ['ax', 'ay', 'az', 'gx', 'gy', 'gz', 'distance']
        for field in required_fields:
            if field not in data:
                return None

        data['timestamp'] = time.time()
        return data
    except (json.JSONDecodeError, KeyError) as e:
        print(f"⚠ Error processing sensor data: {e}")
        logging.error(f"Error processing sensor data: {e}")
        return None


# === THREAD FUNCTIONS ===
def sensor_reader_thread(ser, sensor_sock):
    """Thread for reading sensor data from Arduino and sending to PC"""
    packet_count = 0
    error_count = 0
    max_errors = 20
    last_report_time = time.time()

    print("🔄 Sensor reader thread started")
    logging.info("Sensor reader thread started")

    while running:
        try:
            with serial_lock:
                if ser.in_waiting > 0:
                    line = ser.readline().decode().strip()
                    if line and not line.startswith("ack"):
                        sensor_queue.put(line)
        except Exception as e:
            error_count += 1
            print(f"❌ Serial read error: {e}")
            logging.error(f"Serial read error: {e}")

        while not sensor_queue.empty():
            try:
                line = sensor_queue.get_nowait()
                data = process_sensor_data(line)
                if data:
                    error_count = 0
                    packet_count += 1

                    # Send to PC
                    sensor_sock.sendto(json.dumps(data).encode(), (PC_IP, SENSOR_PORT))

                    current_time = time.time()
                    if current_time - last_report_time >= 5:
                        print(f"📊 Status: Sent {packet_count} sensor packets, "
                              f"last distance: {data.get('distance', 'N/A')} cm")
                        logging.info(f"Sent {packet_count} sensor packets")

                        if packet_count % 10 == 0:
                            print(f"🔍 Latest data: {json.dumps(data)}")

                        last_report_time = current_time
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
                print(f"❌ Sensor processing error: {e}")
                logging.error(f"Sensor processing error: {e}")

        if error_count > max_errors:
            print(f"❌ Too many sensor errors ({error_count}). Exiting thread.")
            logging.critical(f"Too many sensor errors ({error_count}). Exiting thread.")
            break

        time.sleep(0.01)


def joystick_receiver_thread(joystick_sock, ser):
    """Thread for receiving joystick commands from PC and sending to Arduino"""
    print("🎮 Joystick receiver thread started")
    logging.info("Joystick receiver thread started")

    prev_servo = None

    while running:
        try:
            data, addr = joystick_sock.recvfrom(1024)
            decoded = json.loads(data.decode())
            x = decoded.get("x", 0.0)
            y = decoded.get("y", 0.0)

            pwm_speed = int(abs(y) * 255)
            servo_angle = int(85 + (x * 15))

            if y < -0.1:
                command = f"backward:{pwm_speed}"
            elif y > 0.1:
                command = f"forward:{pwm_speed}"
            else:
                command = "stop"

            # Queue the commands for sending to Arduino
            global prev_command
            if command != prev_command:
                command_queue.put(command)
                prev_command = command

            if servo_angle != prev_servo:
                command_queue.put(f"servo:{servo_angle}")
                prev_servo = servo_angle

        except socket.timeout:
            pass
        except Exception as e:
            print(f"⚠ Error in joystick receiver: {e}")
            logging.error(f"Error in joystick receiver: {e}")

        time.sleep(0.01)


def command_sender_thread(ser):
    """Thread for sending commands from queue to Arduino"""
    print("🎯 Command sender thread started")
    logging.info("Command sender thread started")

    def wait_for_ack(timeout=0.1):
        start = time.time()
        while time.time() - start < timeout:
            with serial_lock:
                if ser.in_waiting:
                    line = ser.readline().decode().strip()
                    if line == "ack":
                        return True
        return False

    while running:
        try:
            try:
                command = command_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            global prev_command
            if (command.startswith("forward") and prev_command and prev_command.startswith("backward")) or \
                    (command.startswith("backward") and prev_command and prev_command.startswith("forward")):
                with serial_lock:
                    ser.write(b"stop\n")
                print("🛑 Sent stop for direction switch")
                logging.info("Sent stop for direction switch")
                time.sleep(0.1)

            # Send the command
            with serial_lock:
                ser.write(f"{command}\n".encode())
            print(f"🧭 Sent to Arduino: {command}")
            logging.info(f"Sent to Arduino: {command}")

            if wait_for_ack():
                print("✅ Ack received")
                logging.debug("Ack received")
            else:
                print("⚠ No ack for command")
                logging.warning("No ack received for command")

        except Exception as e:
            print(f"⚠ Error in command sender: {e}")
            logging.error(f"Error in command sender: {e}")

        time.sleep(0.01)


# === CLEANUP FUNCTION ===
def cleanup(ser, sensor_sock, joystick_sock):
    """Clean up resources before exiting"""
    print("\n🛑 Shutting down...")
    logging.info("Shutting down")

    # Close serial connection
    if ser and ser.is_open:
        ser.close()
        print("✅ Serial connection closed")
        logging.info("Serial connection closed")

    # Close sockets
    if sensor_sock:
        sensor_sock.close()
        print("✅ Sensor socket closed")
        logging.info("Sensor socket closed")

    if joystick_sock:
        joystick_sock.close()
        print("✅ Joystick socket closed")
        logging.info("Joystick socket closed")

    print("✅ Cleanup complete")
    logging.info("Cleanup complete")


# === SIGNAL HANDLER ===
def signal_handler(sig, frame):
    """Handle keyboard interrupts"""
    global running
    running = False
    print("\n🛑 Program termination requested")
    logging.info("Program termination requested")


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# === MAIN FUNCTION ===
def main():
    """Main function"""
    global running

    print("🤖 Bidirectional Robot Control Bridge")
    print("------------------------------------")
    logging.info("Bidirectional Robot Control Bridge started")

    # Initialize connections
    ser = init_serial()
    sensor_sock = init_sensor_socket()
    joystick_sock = init_joystick_socket()

    # Create and start threads
    threads = []

    sensor_thread = threading.Thread(target=sensor_reader_thread, args=(ser, sensor_sock))
    sensor_thread.daemon = True
    threads.append(sensor_thread)

    joystick_thread = threading.Thread(target=joystick_receiver_thread, args=(joystick_sock, ser))
    joystick_thread.daemon = True
    threads.append(joystick_thread)

    command_thread = threading.Thread(target=command_sender_thread, args=(ser,))
    command_thread.daemon = True
    threads.append(command_thread)

    # Start all threads
    for thread in threads:
        thread.start()

    # Main loop - just keep program alive and check for exit condition
    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        running = False
        print("\n🛑 Keyboard interrupt received")
        logging.info("Keyboard interrupt received")

    # Wait for threads to terminate (short timeout)
    print("Waiting for threads to terminate...")
    for thread in threads:
        thread.join(timeout=2.0)

    # Clean up when loop exits
    cleanup(ser, sensor_sock, joystick_sock)

    return 0


if _name_ == "_main_":
    sys.exit(main())
