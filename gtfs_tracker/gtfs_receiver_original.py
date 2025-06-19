# Fetch GTFS feed from a source URL

import argparse
from datetime import datetime
import random
import time
from matplotlib import pyplot as plt
import copy
import requests
import threading
import requests
import http.server
import socketserver
import json
import os
import geopandas as gpd
import contextily as ctx
from lib.gtfs.proto import gtfs_realtime_pb2
from lib.trajectory import Trajectory
from collections import OrderedDict

class GTFSReceiver(object):
    def __init__(self, gtfs_src, gtfs_params, verbose=True):
        self._gtfs_src = gtfs_src
        self._gtfs_params = gtfs_params
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
        # Update latest record when new updates come in
        updated = False
        with self._gtfs_proto_lock:
            if self._latest_gtfs_proto is None or self._latest_gtfs_proto.header.timestamp != gtfs_proto.header.timestamp:
                self._latest_gtfs_proto = gtfs_proto
                updated = True
                if self._verbose: 
                    print(f"Updating latest gtfs record to {gtfs_proto.header.timestamp}")
        return updated
    
    def get_latest_gtfs_proto(self):
        with self._gtfs_proto_lock:
            return copy.deepcopy(self._latest_gtfs_proto) if self._latest_gtfs_proto else None
    
    def busy_pull_latest_gtfs_protobuf(self):
        while(self._running): 
            r = requests.get(url=self._gtfs_src, params=self._gtfs_params)
            # Check for a valid response
            if r.status_code != 200:
                continue  # Skip this iteration if the response is not valid

            gtfs_proto_str = r.content
            gtfs_proto = gtfs_realtime_pb2.FeedMessage()

            try:
                gtfs_proto.ParseFromString(gtfs_proto_str)
            except Exception as e:
                continue  # Skip this iteration if parsing fails
            
            updated = self.update_gtfs_proto(gtfs_proto)
            if updated:
                # Add waypoint to trajectories
                count = 0
                for entity in gtfs_proto.entity:
                    if not entity.HasField('vehicle'):
                        continue
                    vid, lat, lon, ts = self.parse_gtfs_entity(entity)
                    new_waypoint = self.log_trajectory(vid,lat,lon,ts)
                    if new_waypoint:
                        count += 1
                if self._verbose:
                    print(f"Added {count} waypoints")

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
                if self._verbose: print(f"Adding to {vid} waypoint ({lat},{lon}) at {ts} with avg interval: {self._trajectories[vid].avg_waypoint_interval()}")
                new_waypoint = True
            return new_waypoint


    def start(self):
        print(f"Serving GTFS receiver at {self._gtfs_src}")
        self._running = True
        self._gtfs_thread = threading.Thread(target=self.busy_pull_latest_gtfs_protobuf)
        self._gtfs_thread.start()
        
    def stop(self):
        self._running = False
        if (self._gtfs_thread is not None):
            self._gtfs_thread.join()
            print("gtfs pull stop")

    def visualize_trajectories(self, output_dir='./'):
        # TODO: combine this with NMEA viz, creating Traj Group object
        print("Plotting GTFS traces...")
        fig, ax = plt.subplots(figsize=(8,6))
        vids = []
        for vid in self._trajectories:
            vids.append(vid)
        for vid in vids:
            color = (random.random(), random.random(), random.random()) # one random color per vehicle
            self._trajectories[vid].gdf.plot(ax=ax, color=color, markersize=10, label=vid)
        plt.xlabel("Latitude")
        plt.ylabel("Longitude")
        plt.title("GTFS Track")
        # ax.legend(loc="best")
            
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)
        plt.savefig("{}/GTFS_at_{}.png".format(output_dir, datetime.now()))


if __name__ == "__main__":
    FOOTHILL_TRANSIT_GTFS_RT_SRC = "https://foothilltransit.rideralerts.com/myStop/GTFS-Realtime.ashx"
    FOOTHILL_TRANSIT_PARAMS = {"Type": "VehiclePosition", "Debug": False } # Debug True for Json, False for protobuf

    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=str, default=FOOTHILL_TRANSIT_GTFS_RT_SRC)
    parser.add_argument("--param", type=str, default=FOOTHILL_TRANSIT_PARAMS)
    args = parser.parse_args()
    
    gtfs_receiver = GTFSReceiver(gtfs_src=args.src, gtfs_params=args.param)
    gtfs_receiver.start()
    try:
        while True:
            time.sleep(1)
    except:
        gtfs_receiver.stop()
        gtfs_receiver.visualize_trajectories()