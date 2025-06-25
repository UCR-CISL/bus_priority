import traci
import sumolib
import csv
import os
import sys

# === CONFIG ===
DELAY_TIME = 8           # Delay before green after detection (in steps)
COOL_OFF_PERIOD = 60    # Cooldown period in steps
EXIT_DELAY = 300         # Time after last bus to stop simulation (in steps)

def run_simulation(sumoConfigFile):
    sumoBinary = "sumo-gui"  # Or use "sumo" if you don't want the GUI
    # Check if sumo-gui is in the system path
    print(f"Attempting to start SUMO with config: {sumoConfigFile}")
    
    try:
        traci.start([sumoBinary, "-c", sumoConfigFile])
        print(f"Simulation started with config: {sumoConfigFile}")
    except Exception as e:
        print(f"Error starting simulation: {e}")
        return

    step = 0
    all_tls_ids = traci.trafficlight.getIDList()

    # === Log files ===
    log_file = open("vehicle_detector_log.csv", "w", newline="")
    log_writer = csv.writer(log_file)
    log_writer.writerow([
        "sim_time", "vehicle_id", "vehicle_type", "tls_id", "lane_id", "position", "speed",
        "priority_granted", "phase_index", "note", "actual_delay"
    ])

    trip_file = open("vehicle_trip_times.csv", "w", newline="")
    trip_writer = csv.writer(trip_file)
    trip_writer.writerow(["vehicle_id", "vehicle_type", "start_time", "end_time", "duration"])

    wait_file = open("vehicle_wait_times.csv", "w", newline="")
    wait_writer = csv.writer(wait_file)
    wait_writer.writerow(["vehicle_id", "vehicle_type", "tls_id", "wait_start_time", "wait_end_time", "wait_duration"])

    speed_file = open("vehicle_tls_speeds.csv", "w", newline="")
    speed_writer = csv.writer(speed_file)
    speed_writer.writerow(["sim_time", "vehicle_id", "vehicle_type", "tls_id", "lane_id", "position_marker", "actual_position", "speed"])

    # === New: Start comparison log ===
    start_comparison_log = []

    # === Tracking structures ===
    vehicle_start_times = {}
    vehicle_end_times = set()
    last_bus_exit_time = None
    pending_detections = {}
    waiting_vehicles = {}
    logged_speeds = {}
    tls_cooldowns = {}

    try:
        while True:
            traci.simulationStep()

            # === Detect vehicles on induction loops ===
            for det_id in traci.inductionloop.getIDList():
                veh_ids = traci.inductionloop.getLastStepVehicleIDs(det_id)
                for vid in veh_ids:
                    try:
                        vehicle_type = traci.vehicle.getTypeID(vid)
                        lane_id = traci.inductionloop.getLaneID(det_id)
                        position = traci.vehicle.getLanePosition(vid)
                        speed = traci.vehicle.getSpeed(vid)

                        if vid not in pending_detections:
                            pending_detections[vid] = (step, lane_id)

                        if vid not in vehicle_start_times:
                            vehicle_start_times[vid] = (step, vehicle_type)

                    except traci.exceptions.TraCIException:
                        continue

            # === Log vehicle entry time compared to SUMO-defined departure time ===
            for vid in traci.vehicle.getIDList():
                if vid not in vehicle_start_times:
                    try:
                        vehicle_type = traci.vehicle.getTypeID(vid)
                        dep_time = traci.vehicle.getDeparture(vid)
                    except traci.TraCIException:
                        dep_time = step
                    detection_time = step
                    vehicle_start_times[vid] = (detection_time, vehicle_type)

                    start_comparison_log.append({
                        "vehicle_id": vid,
                        "vehicle_type": vehicle_type,
                        "departure_time": dep_time,
                        "detection_time": detection_time,
                        "delta": detection_time - dep_time
                    })

                    print(f"[START] {vid} ({vehicle_type}): SUMO dep_time = {dep_time}, detected = {detection_time}, Δ = {detection_time - dep_time}")

            # === Grant green light after delay and cooldown ===
            for vid in list(pending_detections.keys()):
                detected_step, lane_id = pending_detections[vid]
                if step - detected_step < DELAY_TIME:
                    continue

                try:
                    position = traci.vehicle.getLanePosition(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    vehicle_type = traci.vehicle.getTypeID(vid)

                    for tls_id in all_tls_ids:
                        controlled_lanes = traci.trafficlight.getControlledLanes(tls_id)
                        if lane_id in controlled_lanes:
                            if tls_id in tls_cooldowns and step - tls_cooldowns[tls_id] < COOL_OFF_PERIOD:
                                continue

                            logics = traci.trafficlight.getAllProgramLogics(tls_id)
                            if not logics:
                                continue
                            logic = logics[0]

                            try:
                                lane_index = controlled_lanes.index(lane_id)
                            except ValueError:
                                continue

                            for phase_index, phase in enumerate(logic.getPhases()):
                                if phase.state[lane_index].lower() == 'g':
                                    traci.trafficlight.setPhase(tls_id, phase_index)
                                    tls_cooldowns[tls_id] = step
                                    log_writer.writerow([
                                        step, vid, vehicle_type, tls_id, lane_id, position, speed,
                                        "yes", phase_index,
                                        f"green granted after {DELAY_TIME}s",
                                        step - detected_step
                                    ])
                                    break
                            else:
                                log_writer.writerow([
                                    step, vid, vehicle_type, tls_id, lane_id, position, speed,
                                    "no", -1,
                                    f"{vehicle_type} detected, no green phase after {DELAY_TIME}s",
                                    step - detected_step
                                ])
                            break

                    del pending_detections[vid]
                except Exception as e:
                    print(f"Error processing {vid}: {e}")
                    continue

            # === Track wait times ===
            for vid in traci.vehicle.getIDList():
                try:
                    vehicle_type = traci.vehicle.getTypeID(vid)
                    lane_id = traci.vehicle.getLaneID(vid)
                    speed = traci.vehicle.getSpeed(vid)

                    for tls_id in all_tls_ids:
                        controlled_lanes = traci.trafficlight.getControlledLanes(tls_id)
                        if lane_id in controlled_lanes:
                            logic = traci.trafficlight.getAllProgramLogics(tls_id)[0]
                            lane_index = controlled_lanes.index(lane_id)
                            current_phase = logic.getPhases()[traci.trafficlight.getPhase(tls_id)]

                            if speed < 0.1 and current_phase.state[lane_index].lower() != 'g':
                                if vid not in waiting_vehicles:
                                    waiting_vehicles[vid] = (step, tls_id)
                            else:
                                if vid in waiting_vehicles:
                                    wait_start, tls_logged = waiting_vehicles[vid]
                                    wait_duration = step - wait_start
                                    wait_writer.writerow([
                                        vid, vehicle_type, tls_logged, wait_start, step, wait_duration
                                    ])
                                    del waiting_vehicles[vid]
                except Exception:
                    continue

            # === Log vehicle speeds at lane positions ===
            for vid in traci.vehicle.getIDList():
                try:
                    vehicle_type = traci.vehicle.getTypeID(vid)
                    lane_id = traci.vehicle.getLaneID(vid)
                    pos = traci.vehicle.getLanePosition(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    lane_length = traci.lane.getLength(lane_id)

                    lane_markers = {
                        "start": 0.5,
                        "middle": lane_length / 2,
                        "end": lane_length - 0.5
                    }

                    for label, target_pos in lane_markers.items():
                        if abs(pos - target_pos) <= 0.5:
                            key = (vid, lane_id, label)
                            if key not in logged_speeds:
                                speed_writer.writerow([
                                    step, vid, vehicle_type, "N/A", lane_id, label, round(pos, 2), speed
                                ])
                                logged_speeds[key] = True

                except Exception as e:
                    print(f"Error logging speed for {vid}: {e}")
                    continue

            # === Track vehicle arrivals ===
            for vid in traci.simulation.getArrivedIDList():
                if vid in vehicle_start_times and vid not in vehicle_end_times:
                    end_time = step
                    start_time, vehicle_type = vehicle_start_times[vid]
                    trip_writer.writerow([vid, vehicle_type, start_time, end_time, end_time - start_time])
                    vehicle_end_times.add(vid)

                    if vehicle_type == "bus":
                        last_bus_exit_time = step

            # === Stop condition ===
            if last_bus_exit_time is not None and step - last_bus_exit_time > EXIT_DELAY:
                print("✅ All buses finished. Ending simulation 30s after last bus exit.")
                break

            step += 1
            if step > 20000:
                print("⚠️ Max step limit reached.")
                break

    except KeyboardInterrupt:
        print("\n🛑 Simulation interrupted by user.")
    finally:
        traci.close()
        log_file.close()
        trip_file.close()
        wait_file.close()
        speed_file.close()
        print("✅ Simulation complete. All logs saved.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python traci_induction_loop.py <your_config.sumocfg>")
    else:
        run_simulation(sys.argv[1])
