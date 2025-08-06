#!/usr/bin/env python3
"""
Bidirectional Robot Control System
- Reads joystick inputs and sends to Raspberry Pi
- Receives sensor data and triggers force feedback via C executable
- Blocks joystick control during force feedback
"""
import socket
import pygame
import threading
import json
import time
import subprocess
import sys
import os
import logging
import queue
import signal

# === CONFIGURATION ===
RPI_IP = "10.100.102.168"  # RPi's IP address
JOYSTICK_PORT = 5005  # Port for sending joystick commands
SENSOR_PORT = 5055  # Port for receiving sensor data
FORCE_FEEDBACK_EXE = "enhanced_force_feedback_minimal.exe"  # Force feedback executable
DEADZONE = 0.15  # Joystick deadzone threshold
UPDATE_INTERVAL = 0.01  # Send joystick updates every 10ms
OSCILLATION_DURATION = 1000  # Duration of force feedback in ms (matches C code)

# === LOGGING SETUP ===
logging.basicConfig(
    filename="robot_control_log.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# === GLOBAL VARIABLES ===
running = True
feedback_process = None
feedback_active = False
sensor_data_queue = queue.Queue()
last_notify_time = 0  # For rate-limiting notifications


def apply_deadzone(value, threshold=DEADZONE):
    """Apply deadzone to joystick values"""
    return value if abs(value) > threshold else 0.0


def init_joystick():
    """Initialize the joystick"""
    try:
        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            print("❌ No joystick detected!")
            logging.error("No joystick detected")
            return None

        joystick = pygame.joystick.Joystick(0)
        joystick.init()
        print(f"🎮 Joystick initialized: {joystick.get_name()}")
        logging.info(f"Joystick initialized: {joystick.get_name()}")
        return joystick
    except Exception as e:
        print(f"❌ Failed to initialize joystick: {e}")
        logging.error(f"Failed to initialize joystick: {e}")
        return None


def init_joystick_socket():
    """Initialize UDP socket for sending joystick data"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print(f"📡 Joystick socket initialized, target: {RPI_IP}:{JOYSTICK_PORT}")
        logging.info(f"Joystick socket initialized, target: {RPI_IP}:{JOYSTICK_PORT}")
        return sock
    except Exception as e:
        print(f"❌ Failed to create joystick socket: {e}")
        logging.error(f"Failed to create joystick socket: {e}")
        return None


def init_sensor_socket():
    """Initialize UDP socket for receiving sensor data"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", SENSOR_PORT))
        print(f"📡 Listening for sensor data on port {SENSOR_PORT}")
        logging.info(f"Sensor socket listening on port {SENSOR_PORT}")
        return sock
    except Exception as e:
        print(f"❌ Failed to create sensor socket: {e}")
        logging.error(f"Failed to create sensor socket: {e}")
        return None


def joystick_sender_thread(joystick, joystick_sock):
    """Thread for reading joystick input and sending to RPi"""
    print("🎮 Joystick sender thread started")
    logging.info("Joystick sender thread started")

    last_x, last_y = 0.0, 0.0
    last_send_time = 0
    global last_notify_time

    while running:
        try:
            # Process events to get fresh joystick data
            pygame.event.pump()

            # Read joystick axes
            x_raw = joystick.get_axis(0)
            y_raw = joystick.get_axis(1)
            x = apply_deadzone(round(x_raw, 3))
            y = apply_deadzone(round(y_raw, 3))

            # Read buttons
            buttons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]

            # Only send if force feedback is not active
            current_time = time.time()
            if not feedback_active:
                # Only send if values changed or enough time passed
                if (x != last_x or y != last_y) and current_time - last_send_time > UPDATE_INTERVAL:
                    data = {"x": x, "y": y, "buttons": buttons}
                    joystick_sock.sendto(json.dumps(data).encode(), (RPI_IP, JOYSTICK_PORT))
                    print(f"📤 Sent joystick: x={x:.2f}, y={y:.2f}")

                    last_x, last_y = x, y
                    last_send_time = current_time
            elif (x != last_x or y != last_y) and current_time - last_notify_time > 1.0:
                # Provide notification about blocked controls, but only once per second
                print("⚠️ Joystick input ignored during force feedback")
                last_notify_time = current_time
                # We still update last_x and last_y to avoid repeated notifications for the same movement
                last_x, last_y = x, y

        except Exception as e:
            print(f"⚠ Error in joystick sender: {e}")
            logging.error(f"Error in joystick sender: {e}")

        # Small delay to prevent CPU hogging
        time.sleep(0.01)


def sensor_receiver_thread(sensor_sock):
    """Thread for receiving sensor data from RPi"""
    print("📡 Sensor receiver thread started")
    logging.info("Sensor receiver thread started")

    packet_count = 0
    last_report_time = time.time()
    latest_sensor_data = None  # Store the latest data for regular printing

    while running:
        try:
            # Set a timeout so we can check the running flag
            sensor_sock.settimeout(0.5)

            # Receive data
            data, addr = sensor_sock.recvfrom(1024)

            try:
                sensor_data = json.loads(data.decode())
                packet_count += 1

                # Store the latest data
                latest_sensor_data = sensor_data

                # Queue the data for processing
                sensor_data_queue.put(sensor_data)

                # Print status and latest data every 5 seconds
                current_time = time.time()
                if current_time - last_report_time >= 5:
                    print(f"📊 Status: Received {packet_count} sensor packets")
                    logging.info(f"Received {packet_count} sensor packets")

                    # Print the latest sensor data in a formatted way
                    if latest_sensor_data:
                        print("\n🔍 Latest Sensor Data:")
                        print(f"  Gyroscope (deg/s): X={latest_sensor_data.get('gx', 0.0):.2f}, "
                              f"Y={latest_sensor_data.get('gy', 0.0):.2f}, "
                              f"Z={latest_sensor_data.get('gz', 0.0):.2f}")
                        print(f"  Accelerometer (m/s²): X={latest_sensor_data.get('ax', 0.0):.2f}, "
                              f"Y={latest_sensor_data.get('ay', 0.0):.2f}, "
                              f"Z={latest_sensor_data.get('az', 0.0):.2f}")
                        print(f"  Distance: {latest_sensor_data.get('distance', 'N/A')} cm")
                        print(f"  Temperature: {latest_sensor_data.get('temp', 'N/A')} °C\n")

                    last_report_time = current_time

            except json.JSONDecodeError:
                print("⚠ Received invalid JSON")
                logging.warning("Received invalid JSON")

        except socket.timeout:
            # This is normal with the timeout, just continue
            pass
        except Exception as e:
            print(f"⚠ Error in sensor receiver: {e}")
            logging.error(f"Error in sensor receiver: {e}")

        # Small delay to prevent CPU hogging
        time.sleep(0.01)


def feedback_controller_thread():
    """Thread for processing sensor data and triggering force feedback"""
    print("🎮 Feedback controller thread started")
    logging.info("Feedback controller thread started")

    GYRO_AXIS_THRESHOLD = 40.0  # degrees/sec
    DISTANCE_THRESHOLD = 20.0   # cm
    MIN_TRIGGER_INTERVAL = 3.0  # seconds

    last_trigger_time = 0

    while running:
        try:
            try:
                sensor_data = sensor_data_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            gx = sensor_data.get('gx', 0.0)
            gy = sensor_data.get('gy', 0.0)
            distance = sensor_data.get('distance', 100.0)

            current_time = time.time()

            movement_trigger = abs(gx) > GYRO_AXIS_THRESHOLD or abs(gy) > GYRO_AXIS_THRESHOLD
            obstacle_trigger = distance < DISTANCE_THRESHOLD
            should_trigger = movement_trigger or obstacle_trigger

            if should_trigger and (current_time - last_trigger_time > MIN_TRIGGER_INTERVAL):
                if obstacle_trigger:
                    feedback_type = 1  # FEEDBACK_OBSTACLE
                    print(f"\n⚠️ OBSTACLE DETECTED! Distance: {distance:.1f} cm")
                else:
                    feedback_type = 2  # FEEDBACK_MOVEMENT
                    print(f"\n⚠️ MOVEMENT DETECTED! gx={gx:.1f}, gy={gy:.1f}")

                trigger_force_feedback(feedback_type)
                last_trigger_time = current_time

        except Exception as e:
            print(f"⚠ Error in feedback controller: {e}")
            logging.error(f"Error in feedback controller: {e}")

        time.sleep(0.01)


def trigger_force_feedback(feedback_type):
    """Start the force feedback executable with the specified feedback type"""
    global feedback_process, feedback_active

    if feedback_active:
        print("⚠️ Force feedback already active, ignoring new trigger")
        return False

    # Set feedback active flag at the start to block joystick input immediately
    feedback_active = True
    print("🔒 ENTERING FEEDBACK MODE - Joystick controls temporarily disabled")

    try:
        # Windows-specific approach to run with admin privileges
        if os.name == 'nt':  # Windows
            # Create a VBS script that elevates privileges (RunAs)
            exe_path = os.path.abspath(FORCE_FEEDBACK_EXE)
            params = str(feedback_type)

            vbs_content = f'''
            Set UAC = CreateObject("Shell.Application")
            UAC.ShellExecute "{exe_path}", "{params}", "", "runas", 1
            '''

            # Write temporary VBS script
            vbs_path = os.path.join(os.environ['TEMP'], "run_elevated.vbs")
            with open(vbs_path, 'w') as f:
                f.write(vbs_content)

            # Execute the VBS script
            print(f"🎮 Triggering force feedback, type: {feedback_type}")
            logging.info(f"Triggering force feedback, type: {feedback_type}")

            # Use subprocess to start the VBS script (which then starts the exe with elevation)
            subprocess.Popen(["cscript", "//NoLogo", vbs_path])

            # Set a timer to clean up the temporary script
            def cleanup_vbs():
                time.sleep(5)  # Wait to ensure the script has run
                try:
                    os.remove(vbs_path)
                except:
                    pass

            threading.Thread(target=cleanup_vbs, daemon=True).start()

            # Since we can't track the elevated process directly, set a timer to reset feedback_active
            def reset_feedback_active():
                time.sleep((OSCILLATION_DURATION / 1000) + 1)  # Convert ms to seconds + 1s buffer
                global feedback_active
                feedback_active = False
                print("🔓 EXITING FEEDBACK MODE - Joystick controls re-enabled")

            threading.Thread(target=reset_feedback_active, daemon=True).start()

        else:
            # For non-Windows systems (fallback, though force feedback likely won't work)
            print(f"🎮 Triggering force feedback, type: {feedback_type}")
            logging.info(f"Triggering force feedback, type: {feedback_type}")

            feedback_process = subprocess.Popen([FORCE_FEEDBACK_EXE, str(feedback_type)])

        return True

    except Exception as e:
        print(f"❌ Failed to start force feedback: {e}")
        logging.error(f"Failed to start force feedback: {e}")
        feedback_active = False  # Reset flag on failure
        return False


def cleanup():
    """Clean up resources before exiting"""
    print("\n🛑 Shutting down...")
    logging.info("Shutting down")

    # Terminate the force feedback process if running
    global feedback_process
    if feedback_process is not None:
        try:
            feedback_process.terminate()
            print("✅ Force feedback process terminated")
            logging.info("Force feedback process terminated")
        except Exception as e:
            print(f"⚠️ Error terminating force feedback: {e}")
            logging.error(f"Error terminating force feedback: {e}")

    # Close pygame
    pygame.quit()
    print("✅ Pygame closed")
    logging.info("Pygame closed")

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


def main():
    """Main function"""
    global running

    print("🤖 Bidirectional Robot Control System")
    print("-----------------------------------")
    logging.info("Bidirectional Robot Control System started")

    # Initialize components
    joystick = init_joystick()
    joystick_sock = init_joystick_socket()
    sensor_sock = init_sensor_socket()

    if not joystick or not joystick_sock or not sensor_sock:
        print("❌ Initialization failed. Exiting.")
        return 1

    # Create and start threads
    threads = []

    joystick_thread = threading.Thread(target=joystick_sender_thread, args=(joystick, joystick_sock))
    joystick_thread.daemon = True
    threads.append(joystick_thread)

    sensor_thread = threading.Thread(target=sensor_receiver_thread, args=(sensor_sock,))
    sensor_thread.daemon = True
    threads.append(sensor_thread)

    feedback_thread = threading.Thread(target=feedback_controller_thread)
    feedback_thread.daemon = True
    threads.append(feedback_thread)

    # Start all threads
    for thread in threads:
        thread.start()

    print("\n✅ System initialized and running...")
    print("Press Ctrl+C to exit")

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

    # Cleanup
    cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())