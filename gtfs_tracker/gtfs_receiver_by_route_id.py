import argparse
from datetime import datetime
import random
import time
from matplotlib import pyplot as plt
import copy
import requests
import threading
import os
import geopandas as gpd
import contextily as ctx
import csv
from lib.gtfs.proto import gtfs_realtime_pb2
from lib.trajectory import Trajectory
from collections import OrderedDict


class GTFSReceiver(object):
    def __init__(self, gtfs_src, gtfs_params, route_id_filter=None, verbose=True):
        self._gtfs_src = gtfs_src
        self._gtfs_params = gtfs_params
        self._route_id_filter = route_id_filter
        self._verbose = verbose
        self._running = False
        self._gtfs_thread = None

        self._trajectories = OrderedDict()
        self._trajectories_lock = threading.Lock()
        self._latest_gtfs_proto = None
        self._gtfs_proto_lock = threading.Lock()

    def trajectory_list(self) -> tuple[list[str], list[Trajectory]]:
        traj_ids = []
        traj_list = []
        with self._trajectories_lock:
            for vid in self._trajectories:
                traj_ids.append(copy.deepcopy(vid))
                traj_list.append(copy.deepcopy(self._trajectories[vid]))
        return traj_ids, traj_list

    def update_gtfs_proto(self, gtfs_proto):
        updated = False
        with self._gtfs_proto_lock:
            if self._latest_gtfs_proto is None or self._latest_gtfs_proto.header.timestamp != gtfs_proto.header.timestamp:
                self._latest_gtfs_proto = gtfs_proto
                updated = True
                if self._verbose:
                    print(f"Updating latest GTFS record to {gtfs_proto.header.timestamp}")
        return updated

    def get_latest_gtfs_proto(self):
        with self._gtfs_proto_lock:
            return copy.deepcopy(self._latest_gtfs_proto) if self._latest_gtfs_proto else None

    def busy_pull_latest_gtfs_protobuf(self):
        while self._running:
            success = False
            for attempt in range(3):
                try:
                    r = requests.get(url=self._gtfs_src, params=self._gtfs_params, timeout=10)
                    if r.status_code != 200:
                        if self._verbose:
                            print(f"[WARNING] Non-200 response: {r.status_code}. Retrying...")
                        time.sleep(2 ** attempt)
                        continue

                    gtfs_proto_str = r.content
                    gtfs_proto = gtfs_realtime_pb2.FeedMessage()

                    try:
                        gtfs_proto.ParseFromString(gtfs_proto_str)
                    except Exception as parse_err:
                        if self._verbose:
                            print(f"[ERROR] Failed to parse GTFS proto: {parse_err}")
                        break

                    updated = self.update_gtfs_proto(gtfs_proto)
                    if updated:
                        count = 0
                        for entity in gtfs_proto.entity:
                            if not entity.HasField('vehicle'):
                                continue

                            # Filter by route ID if specified
                            if self._route_id_filter:
                                route_id = entity.vehicle.trip.route_id
                                if route_id != self._route_id_filter:
                                    continue

                            vid, lat, lon, ts = self.parse_gtfs_entity(entity)
                            new_waypoint = self.log_trajectory(vid, lat, lon, ts)
                            if new_waypoint:
                                self.log_csv_by_route(entity)
                                count += 1
                        if self._verbose:
                            print(f"Added {count} waypoints")
                    success = True
                    break

                except requests.exceptions.Timeout:
                    if self._verbose:
                        print(f"[WARNING] GTFS feed timeout on attempt {attempt+1}. Retrying...")
                    time.sleep(2 ** attempt)
                except requests.exceptions.RequestException as e:
                    if self._verbose:
                        print(f"[ERROR] Request failed: {e}")
                    break

            if not success:
                if self._verbose:
                    print("[ERROR] All attempts to retrieve GTFS feed failed. Skipping this cycle.")

            time.sleep(1)

    def parse_gtfs_entity(self, gtfs_entity_proto) -> tuple[str, float, float, datetime]:
        v = gtfs_entity_proto.vehicle
        vid = v.vehicle.id
        lat = v.position.latitude
        lon = v.position.longitude
        ts = v.timestamp
        return vid, lat, lon, datetime.fromtimestamp(ts)

    def log_trajectory(self, vid: str, lat: float, lon: float, ts: datetime):
        with self._trajectories_lock:
            new_waypoint = False
            if vid not in self._trajectories:
                self._trajectories[vid] = Trajectory(vid)
            if self._trajectories[vid].empty() or self._trajectories[vid].timestamps[-1] != ts:
                self._trajectories[vid].add_waypoint(lat, lon, ts)
                if self._verbose:
                    print(f"Adding to {vid} waypoint ({lat},{lon}) at {ts} with avg interval: {self._trajectories[vid].avg_waypoint_interval()}")
                new_waypoint = True
            return new_waypoint

    def log_csv_by_route(self, entity):
        v = entity.vehicle
        position = v.position
        trip = v.trip
        ts = v.timestamp

        row = {
            "timestamp": datetime.fromtimestamp(ts).isoformat(),
            "vehicle_id": v.vehicle.id,
            "latitude": position.latitude,
            "longitude": position.longitude,
            "trip_id": trip.trip_id,
            "route_id": trip.route_id,
            "stop_id": v.stop_id,
            "current_status": v.current_status,
            "current_stop_sequence": v.current_stop_sequence
        }

        route_id = trip.route_id or "unknown_route"
        os.makedirs("gtfs_logs", exist_ok=True)
        log_file = f"gtfs_logs/Route_{route_id}.csv"

        write_header = not os.path.exists(log_file)
        with open(log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def start(self):
        print(f"Serving GTFS receiver at {self._gtfs_src}")
        if self._route_id_filter:
            print(f"Filtering for route_id: {self._route_id_filter}")
        self._running = True
        self._gtfs_thread = threading.Thread(target=self.busy_pull_latest_gtfs_protobuf)
        self._gtfs_thread.start()

    def stop(self):
        self._running = False
        if self._gtfs_thread is not None:
            self._gtfs_thread.join()
            print("GTFS pull stopped")

    def visualize_trajectories(self, output_dir='./'):
        print("Plotting GTFS traces...")
        fig, ax = plt.subplots(figsize=(8, 6))
        vids = list(self._trajectories.keys())
        for vid in vids:
            color = (random.random(), random.random(), random.random())
            self._trajectories[vid].gdf.plot(ax=ax, color=color, markersize=10, label=vid)
        plt.xlabel("Latitude")
        plt.ylabel("Longitude")
        plt.title("GTFS Track")
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)
        plt.savefig(f"{output_dir}/GTFS_at_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")


if __name__ == "__main__":
    FOOTHILL_TRANSIT_GTFS_RT_SRC = "https://foothilltransit.rideralerts.com/myStop/GTFS-Realtime.ashx"
    FOOTHILL_TRANSIT_PARAMS = {"Type": "VehiclePosition", "Debug": False}

    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=str, default=FOOTHILL_TRANSIT_GTFS_RT_SRC)
    parser.add_argument("--route_id", type=str, default=None, help="Filter to only include this route ID")
    args = parser.parse_args()

    gtfs_receiver = GTFSReceiver(
        gtfs_src=args.src,
        gtfs_params=FOOTHILL_TRANSIT_PARAMS,
        route_id_filter=args.route_id
    )

    gtfs_receiver.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        gtfs_receiver.stop()
        gtfs_receiver.visualize_trajectories()
