import os
import sys
import csv
from datetime import datetime
import traci

# --- Check SUMO_HOME ---
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare environment variable 'SUMO_HOME'")

# --- Check for SUMO config file ---
if len(sys.argv) < 2:
    print("Usage: python script.py <your_config.sumocfg>")
    sys.exit(1)

sumo_config_path = sys.argv[1]

# --- SUMO GUI Launch Config ---
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
ETA_BUFFER = 5          # Number of seconds before arrival to trigger tls to turn green
COMM_DELAY = 0          # Communication delay in seconds. Changed to 0 after changing to ETA
PRIORITY_DURATION = 2   # Cooldown between granting greens back to back 
POST_EXIT_WAIT = 30     # Wait before ending the simulation after last bus

# --- CSV Setup: Trip Times ---
last_priority_times = {} # Time last priority was granted.
bus_start_times = {}
bus_end_times = {}
trip_log_rows = []

# --- CSV Logging ---
priority_log_file = "bus_priority_log.csv"
trip_log_file = "bus_trip_times.csv"
priority_log_headers = [
    "sim_time", "vehicle_id", "tls_id", "lane_id",
    "eta", "speed", "priority_granted", "phase_index", "note"
]
trip_log_headers = ["vehicle_id", "start_time", "end_time", "duration"]

priority_log = open(priority_log_file, 'w', newline='')
priority_writer = csv.DictWriter(priority_log, fieldnames=priority_log_headers)
priority_writer.writeheader()

# --- Helper Functions ---

def should_set_priority(tls_id, sim_time): # To check if enough time has passed since last green was granted
    last_time = last_priority_times.get(tls_id, -999)
    return (sim_time - last_time) > PRIORITY_DURATION

def get_green_phase_for_lane(tls_id, lane_id): # Find out the green phase index for the lane our bus is on
    try:
        program = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)[0]
        controlled_links_nested = traci.trafficlight.getControlledLinks(tls_id)
        flat_links = [link for group in controlled_links_nested for link in group]
        for phase_index, phase in enumerate(program.phases):
            for link_index, link in enumerate(flat_links):
                from_lane = link[0]
                if from_lane == lane_id and phase.state[link_index] == 'G':
                    return phase_index
        return None
    except:
        return None

def estimate_eta(vehicle_id, tls_id): # Figure out the time that bus will be at the TLS
    try:
        route = traci.vehicle.getRoute(vehicle_id) # Getting all the edges in the bus's route
        current_edge = traci.vehicle.getRoadID(vehicle_id) # Finding the current edge
        pos_on_lane = traci.vehicle.getLanePosition(vehicle_id) # Finding bus's position on the edge
        speed = traci.vehicle.getSpeed(vehicle_id) # Finding bus's speed
        tls_links = traci.trafficlight.getControlledLinks(tls_id)
        if not tls_links:
            return None

        tls_incoming_edges = set(link[0].split("_")[0] for group in tls_links for link in group)
        remaining_distance = 0
        found = False
        for edge in route:
            if edge == current_edge:
                found = True
                lane_id = traci.vehicle.getLaneID(vehicle_id)
                edge_length = traci.lane.getLength(lane_id)
                remaining_distance += edge_length - pos_on_lane
            elif found:
                remaining_distance += traci.lane.getLength(edge + "_0")
            if edge in tls_incoming_edges:
                break

        if speed <= 0.1:
            return None  # Avoid divide-by-zero

        return remaining_distance / speed

    except traci.TraCIException:
        return None

# --- Main Simulation Loop ---
try:
    last_bus_exit_time = None

    while True:
        traci.simulationStep()
        sim_time = traci.simulation.getTime()
        all_vehicle_ids = set(traci.vehicle.getIDList())
        active_buses = {vid for vid in all_vehicle_ids if traci.vehicle.getVehicleClass(vid) == "bus"}

        for bus_id in active_buses:
            try:
                if bus_id not in bus_start_times:
                    bus_start_times[bus_id] = sim_time

                lane_id = traci.vehicle.getLaneID(bus_id)
                speed = traci.vehicle.getSpeed(bus_id)
                tls_id = None
                priority_granted = False
                phase_index = None
                note = ""
                eta = None

                # Determine traffic light ahead
                current_edge = traci.vehicle.getRoadID(bus_id)
                for tid in traci.trafficlight.getIDList():
                    links = traci.trafficlight.getControlledLinks(tid)
                    for group in links:
                        for link in group:
                            if link[0].split("_")[0] == current_edge:
                                tls_id = tid
                                break
                        if tls_id:
                            break

                if tls_id:
                    eta = estimate_eta(bus_id, tls_id)
                    if eta is not None and eta <= ETA_BUFFER + COMM_DELAY:
                        if should_set_priority(tls_id, sim_time):
                            phase_index = get_green_phase_for_lane(tls_id, lane_id)
                            if phase_index is not None:
                                traci.trafficlight.setPhase(tls_id, phase_index)
                                last_priority_times[tls_id] = sim_time
                                priority_granted = True
                                note = f"Priority granted (ETA={eta:.1f}s)"
                                print(f"[{sim_time:.1f}s] Bus {bus_id} ETA {eta:.1f}s → {tls_id}: green phase set.")
                            else:
                                note = "No matching green phase"
                        else:
                            note = "Cooldown active"
                    else:
                        note = f"ETA too high ({eta:.1f}s)" if eta else "ETA unavailable"
                else:
                    note = "No TLS ahead"

                priority_writer.writerow({
                    "sim_time": round(sim_time, 2),
                    "vehicle_id": bus_id,
                    "tls_id": tls_id or "N/A",
                    "lane_id": lane_id,
                    "eta": round(eta, 2) if eta else "N/A",
                    "speed": round(speed, 2),
                    "priority_granted": "yes" if priority_granted else "no",
                    "phase_index": phase_index if phase_index is not None else "N/A",
                    "note": note
                })

            except Exception as e:
                print(f"[ERROR] Processing bus {bus_id}: {e}")
                continue

        # Check if buses finished
        seen_buses = set(bus_start_times.keys())
        finished_buses = seen_buses - active_buses - set(bus_end_times.keys())
        for bus_id in finished_buses:
            end_time = sim_time
            duration = round(end_time - bus_start_times[bus_id], 2)
            bus_end_times[bus_id] = end_time
            trip_log_rows.append({
                "vehicle_id": bus_id,
                "start_time": round(bus_start_times[bus_id], 2),
                "end_time": round(end_time, 2),
                "duration": duration
            })
            print(f"[{end_time:.1f}s] Bus {bus_id} completed. Trip time: {duration:.2f}s")
            last_bus_exit_time = sim_time

        if not active_buses:
            if last_bus_exit_time is None:
                last_bus_exit_time = sim_time
            elif sim_time - last_bus_exit_time >= POST_EXIT_WAIT:
                print("[INFO] No active buses. Ending simulation.")
                break

except KeyboardInterrupt:
    print("\n[INFO] Simulation interrupted by user.")

finally:
    # Cleanup
    try:
        traci.close()
        priority_log.close()
        with open(trip_log_file, 'w', newline='') as trip_csv:
            writer = csv.DictWriter(trip_csv, fieldnames=trip_log_headers)
            writer.writeheader()
            writer.writerows(trip_log_rows)
        print(f"[INFO] Priority log saved to: {priority_log_file}")
        print(f"[INFO] Trip times saved to: {trip_log_file}")
    except Exception as cleanup_error:
        print(f"[ERROR] Cleanup failed: {cleanup_error}")
