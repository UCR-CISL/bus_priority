import traci
import csv

sumo_cfg = "osm.sumocfg"
output_csv = "bus_completion_times.csv"

traci.start(["sumo-gui", "-c", sumo_cfg])

departure_times = {}
arrival_times = {}

known_buses = set()
arrived_buses = set()

last_bus_exit_time = None
timer_started = False

print("🚦 Simulation started...")

while True:
    traci.simulationStep()
    current_time = traci.simulation.getTime()

    # 1. Detect new buses and log their departure
    for veh_id in traci.vehicle.getIDList():
        if veh_id not in departure_times:
            try:
                vclass = traci.vehicle.getVehicleClass(veh_id)
                if vclass == "bus":
                    known_buses.add(veh_id)
                    departure_times[veh_id] = current_time
                    print(f"🚌 Bus {veh_id} departed at {current_time:.2f}s")
            except traci.TraCIException:
                continue

    # 2. Detect bus arrivals
    for veh_id in traci.simulation.getArrivedIDList():
        if veh_id in known_buses and veh_id not in arrived_buses:
            arrival_times[veh_id] = current_time
            arrived_buses.add(veh_id)
            print(f"✅ Bus {veh_id} arrived at {current_time:.2f}s")

            # If that was the last bus, start the 5-second countdown
            if arrived_buses == known_buses:
                last_bus_exit_time = current_time
                print(f"⏳ All buses have arrived. Starting 5-second wait at {last_bus_exit_time:.2f}s.")

    # 3. Stop simulation 5 seconds after last bus arrived
    if last_bus_exit_time is not None and current_time >= last_bus_exit_time + 5:
        print(f"🛑 Stopping simulation 5 seconds after last bus exit at {current_time:.2f}s")
        break

traci.close()

# 4. Save bus timings to CSV
with open(output_csv, "w", newline="") as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(["vehicle_id", "departure_time", "arrival_time", "duration"])
    for veh_id in sorted(arrival_times):
        depart = departure_times.get(veh_id, "N/A")
        arrive = arrival_times[veh_id]
        duration = arrive - depart if depart != "N/A" else "N/A"
        writer.writerow([veh_id, depart, arrive, duration])

print(f"📄 Done! Bus data saved to {output_csv}")
