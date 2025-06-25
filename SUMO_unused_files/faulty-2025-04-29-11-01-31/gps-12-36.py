import os
import sys
import csv
import math
from datetime import datetime

# --- Check SUMO_HOME ---
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare environment variable 'SUMO_HOME'")

import traci

# --- Check for SUMO config file ---
if len(sys.argv) < 2:
    print("Usage: python script.py <your_config.sumocfg>")
    sys.exit(1)

sumo_config_path = sys.argv[1]

# --- SUMO GUI Launch Config file ---
Sumo_config = [
    'sumo-gui',
    '-c', sumo_config_path,
    '--step-length', '0.05',
    '--delay', '1000',
    '--lateral-resolution', '0.1'
]

# --- Try to Start SUMO ---
try:
    traci.start(Sumo_config)
except Exception as e:
    print(f"[ERROR] Failed to start SUMO: {e}")
    sys.exit(1)

# --- Parameters ---
DISTANCE_THRESHOLD = 100  # Distance from TLS in meters
PRIORITY_DURATION = 2  # How often we can overwrite the TLS
POST_EXIT_WAIT = 30  # Finish simulation 30 seconds after last bus exits
last_priority_times = {}
last_bus_exit_time = None
bus_in_range_times = {}  # Track when each bus first gets in range of a TLS
COMM_DELAY = 9  # Communication delay in seconds

# --- CSV Setup: Priority Events ---
csv_file = "bus_priority_log.csv"
csv_headers = [
    "sim_time", "vehicle_id", "tls_id", "lane_id",
    "position", "speed", "priority_granted", "phase_index", "note"
]
csv_fh = open(csv_file, mode='w', newline='')
csv_writer = csv.DictWriter(csv_fh, fieldnames=csv_headers)
csv_writer.writeheader()

# --- CSV Setup: Trip Times ---
bus_start_times = {}
bus_end_times = {}
trip_log_file = "bus_trip_times.csv"
trip_log_headers = ["vehicle_id", "start_time", "end_time", "duration"]
trip_log_rows = []

# --- Helper Functions ---

def get_tls_ahead(vehicle_id):
    current_edge = traci.vehicle.getRoadID(vehicle_id)
    if current_edge.startswith(":"):  # incoming edge
        return None
    for tls_id in traci.trafficlight.getIDList():
        controlled_links = traci.trafficlight.getControlledLinks(tls_id)
        if not controlled_links:
            continue
        for links in controlled_links:
            for link in links:
                from_edge = link[0].split("_")[0]
                if from_edge == current_edge:
                    return tls_id
    return None

def is_near_tls(vehicle_id, tls_id):
    lane_id = traci.vehicle.getLaneID(vehicle_id)
    pos = traci.vehicle.getLanePosition(vehicle_id)
    lane_length = traci.lane.getLength(lane_id)
    return (lane_length - pos) <= DISTANCE_THRESHOLD

def should_set_priority(tls_id, sim_time):
    last_time = last_priority_times.get(tls_id, -999)
    return (sim_time - last_time) > PRIORITY_DURATION

def get_green_phase_for_lane(tls_id, lane_id):
    program = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)[0]
    controlled_links_nested = traci.trafficlight.getControlledLinks(tls_id)
    flat_links = [link for group in controlled_links_nested for link in group]

    for phase_index, phase in enumerate(program.phases):
        for link_index, link in enumerate(flat_links):
            from_lane = link[0]
            if from_lane == lane_id and phase.state[link_index] == 'G':
                return phase_index
    return None

# --- Main Simulation Loop ---
try:
    while True:
        traci.simulationStep()
        sim_time = traci.simulation.getTime()
        all_vehicle_ids = set(traci.vehicle.getIDList())
        active_buses = {vid for vid in all_vehicle_ids if traci.vehicle.getVehicleClass(vid) == "bus"}

        for veh_id in active_buses:
            try:
                if veh_id not in bus_start_times:
                    bus_start_times[veh_id] = sim_time

                lane_id = traci.vehicle.getLaneID(veh_id)
                pos = traci.vehicle.getLanePosition(veh_id)
                speed = traci.vehicle.getSpeed(veh_id)
                tls_id = get_tls_ahead(veh_id)
                priority_granted = False
                phase_index = None
                note = ""

                if tls_id and is_near_tls(veh_id, tls_id):
                    # First time the bus is near TLS — record the time
                    if veh_id not in bus_in_range_times:
                        bus_in_range_times[veh_id] = sim_time

                    time_since_in_range = sim_time - bus_in_range_times[veh_id]

                    # Wait for the communication delay
                    if time_since_in_range >= COMM_DELAY and should_set_priority(tls_id, sim_time):
                        phase_index = get_green_phase_for_lane(tls_id, lane_id)
                        if phase_index is not None:
                            traci.trafficlight.setPhase(tls_id, phase_index)
                            last_priority_times[tls_id] = sim_time
                            priority_granted = True
                            note = "Priority granted after communication delay"
                            print(f"[{sim_time:.1f}s] Bus {veh_id} → {tls_id}: phase {phase_index} set (after delay).")
                        else:
                            note = f"No green phase for lane {lane_id}"
                            print(f"[{sim_time:.1f}s] WARNING: {note}")
                    else:
                        wait_remaining = COMM_DELAY - time_since_in_range
                        if wait_remaining > 0:
                            note = f"Waiting {wait_remaining:.1f}s for delay"
                        elif not should_set_priority(tls_id, sim_time):
                            note = "Priority cooldown active"
                else:
                    if tls_id and not is_near_tls(veh_id, tls_id):
                        note = "Bus not near TLS"
                    elif tls_id and not should_set_priority(tls_id, sim_time):
                        note = "Priority cooldown active"
                    elif not tls_id:
                        note = "No TLS ahead"

                csv_writer.writerow({
                    "sim_time": round(sim_time, 2),
                    "vehicle_id": veh_id,
                    "tls_id": tls_id or "N/A",
                    "lane_id": lane_id,
                    "position": round(pos, 2),
                    "speed": round(speed, 2),
                    "priority_granted": "yes" if priority_granted else "no",
                    "phase_index": phase_index if phase_index is not None else "N/A",
                    "note": note
                })

            except traci.TraCIException:
                continue

        seen_buses = set(bus_start_times.keys())
        finished_buses = seen_buses - active_buses - set(bus_end_times.keys())

        for bus_id in finished_buses:
            end_time = sim_time
            start_time = bus_start_times.get(bus_id, 0)
            duration = round(end_time - start_time, 2)
            bus_end_times[bus_id] = end_time
            trip_log_rows.append({
                "vehicle_id": bus_id,
                "start_time": round(start_time, 2),
                "end_time": round(end_time, 2),
                "duration": duration
            })
            print(f"[{end_time:.1f}s] Bus {bus_id} finished route. Trip time: {duration:.2f} s")
            last_bus_exit_time = sim_time

        if not active_buses:
            if last_bus_exit_time is None:
                last_bus_exit_time = sim_time
            elif sim_time - last_bus_exit_time >= POST_EXIT_WAIT:
                print(f"[INFO] No buses for {POST_EXIT_WAIT} seconds. Ending simulation.")
                break

except KeyboardInterrupt:
    print("\n[INFO] Simulation interrupted by user.")

except Exception as e:
    print(f"[ERROR] Unexpected error during simulation: {e}")

finally:
    # Cleanup
    try:
        traci.close()
        csv_fh.close()
        with open(trip_log_file, mode='w', newline='') as trip_csv:
            writer = csv.DictWriter(trip_csv, fieldnames=trip_log_headers)
            writer.writeheader()
            writer.writerows(trip_log_rows)
        print("[INFO] SUMO connection closed.")
        print(f"[INFO] Priority log saved to: {csv_file}")
        print(f"[INFO] Trip times saved to: {trip_log_file}")
    except Exception as cleanup_error:
        print(f"[ERROR] Error during cleanup: {cleanup_error}")
