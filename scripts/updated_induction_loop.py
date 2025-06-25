import traci
import sumolib
import csv
import os
import sys

# === CONFIG ===
DELAY_TIME = 3
COOL_OFF_PERIOD = 10
EXIT_DELAY = 30

def run_simulation(sumoConfigFile):
    sumoBinary = "sumo-gui"  # Use "sumo" if you don't want GUI
    print(f"Attempting to start SUMO with config: {sumoConfigFile}")
    
    try:
        traci.start([sumoBinary, "-c", sumoConfigFile, "--no-step-log", "--quit-on-end", "false"])
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

    start_comparison_log = []
    vehicle_start_times = {}
    vehicle_end_times = set()
    pending_detections = {}
    waiting_vehicles = {}
    logged_speeds = {}
    tls_cooldowns = {}

    try:
        while True:
            traci.simulationStep()

            # === Detect vehicles on loops ===
            for det_id in traci.inductionloop.getIDList():
                for vid in traci.inductionloop.getLastStepVehicleIDs(det_id):
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

            # === Track first appearance
            for vid in traci.vehicle.getIDList():
                if vid not in vehicle_start_times:
                    try:
                        vehicle_type = traci.vehicle.getTypeID(vid)
                        dep_time = traci.vehicle.getDeparture(vid)
                    except traci.TraCIException:
                        dep_time = step
                        vehicle_type = "unknown"
                    detection_time = step
                    vehicle_start_times[vid] = (detection_time, vehicle_type)
                    start_comparison_log.append({
                        "vehicle_id": vid,
                        "vehicle_type": vehicle_type,
                        "departure_time": dep_time,
                        "detection_time": detection_time,
                        "delta": detection_time - dep_time
                    })

            # === Green light logic
            for vid in list(pending_detections.keys()):
                detected_step, lane_id = pending_detections[vid]
                if step - detected_step < DELAY_TIME:
                    continue
                try:
                    position = traci.vehicle.getLanePosition(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    vehicle_type = traci.vehicle.getTypeID(vid)

                    for tls_id in all_tls_ids:
                        if lane_id in traci.trafficlight.getControlledLanes(tls_id):
                            if tls_id in tls_cooldowns and step - tls_cooldowns[tls_id] < COOL_OFF_PERIOD:
                                continue
                            logics = traci.trafficlight.getAllProgramLogics(tls_id)
                            if not logics:
                                continue
                            logic = logics[0]
                            controlled_lanes = traci.trafficlight.getControlledLanes(tls_id)
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
                                        "yes", phase_index, f"green granted after {DELAY_TIME}s", step - detected_step
                                    ])
                                    break
                            else:
                                log_writer.writerow([
                                    step, vid, vehicle_type, tls_id, lane_id, position, speed,
                                    "no", -1, f"{vehicle_type} detected, no green phase", step - detected_step
                                ])
                            break
                    del pending_detections[vid]
                except Exception as e:
                    print(f"Error processing {vid}: {e}")
                    continue

            # === Wait times
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

            # === Log speeds at 3 points per lane
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
                except Exception:
                    continue

            # === Arrival tracking
            for vid in traci.simulation.getArrivedIDList():
                if vid in vehicle_start_times and vid not in vehicle_end_times:
                    end_time = step
                    start_time, vehicle_type = vehicle_start_times[vid]
                    trip_writer.writerow([vid, vehicle_type, start_time, end_time, end_time - start_time])
                    vehicle_end_times.add(vid)

            # === Termination condition: all buses done
            bus_ids = [vid for vid, (start, vtype) in vehicle_start_times.items() if vtype == "bus"]
            if bus_ids and all(vid in vehicle_end_times for vid in bus_ids):
                print("✅ All buses have completed their routes.")
                break

            step += 1
            if step > 20000:
                print("⏱️ Max simulation step reached.")
                break

    except KeyboardInterrupt:
        print("\n🛑 Simulation interrupted by user.")
    finally:
        traci.close()
        log_file.close()
        trip_file.close()
        wait_file.close()
        speed_file.close()

        unfinished = [vid for vid in bus_ids if vid not in vehicle_end_times]
        if unfinished:
            print(f"⚠️ Unfinished buses: {unfinished}")
        print("✅ Simulation complete. All logs saved.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python updated_induction_loop.py <your_config.sumocfg>")
    else:
        run_simulation(sys.argv[1])
