import datetime
from matplotlib import pyplot as plt
from collections import deque
import pandas as pd
import geopandas as gpd
import contextily as ctx
from shapely.geometry import Point
from scipy.interpolate import interp1d

class Trajectory:
    def __init__(self, trajectory_id: str, coordinates: list[tuple[float, float]]=None, timestamps: list[datetime.datetime]=None, max_waypoints: int = 1000):
        """
        Initializes the Trajectory object.
        
        Args:
            trajectory_id (str): Unique identifier for the trajectory.
            max_waypoints (int): maximum waypoint to store in this trajectory.
            coordinates (list of tuples): List of (latitude, longitude) pairs. TODO: expand to 3D trajectories
            timestamps (list of datetime): Timestamps for each coordinate. 
            frequency (str): The frequency of the trajectory data points if timestamps are not provided.
        """
        self.trajectory_id = trajectory_id
        self.max_waypoints = max_waypoints
        self.coordinates = deque(coordinates if coordinates is not None else [], maxlen=max_waypoints)
        self.timestamps = deque(timestamps if timestamps is not None else [], maxlen=max_waypoints)
    
        # Create a GeoDataFrame TODO: expand to support cartisan x y z trajectories
        # Handle empty initialization case
        self.gdf = None
        if len(self.coordinates) != 0:
            self.gdf = gpd.GeoDataFrame(
                {"trajectory_id": [self.trajectory_id] * len(self.coordinates), "timestamp": self.timestamps},
                geometry=[Point(lon, lat) for lat, lon in self.coordinates],
                crs="EPSG:4326",
            )
            self.gdf = self.gdf.to_crs(epsg=3857)  # Convert to meters (EPSG:3857)

    def __len__(self) -> int:
        return len(self.timestamps)


    def empty(self) -> bool:
        return len(self.coordinates) == 0
    
    def avg_waypoint_interval(self) -> datetime.timedelta | None:
        if len(self.timestamps) < 2: return None
        timespan = self.timestamps[-1] - self.timestamps[0] 
        return timespan / (len(self.timestamps) -1)

    def add_waypoint(self, latitude:float, longitude:float, timestamp:datetime):
        """
        Add a new point (latitude, longitude) to the trajectory.
        
        Args:
            latitude (float): Latitude of the new waypoint.
            longitude (float): Longitude of the new waypoint.
            timestamp (datetime)
        """
        # Ensure new waypoint is from different timestamp to avoid interpolation div by 0
        if not self.empty() and self.timestamps[-1] == timestamp:
            return 

        # Add the new point to the coordinates and timestamp list
        self.coordinates.append((latitude, longitude))
        self.timestamps.append(timestamp)

        # Update GeoDataFrame
        new_point = Point(longitude, latitude)
        new_row = gpd.GeoDataFrame(
            {"trajectory_id": [self.trajectory_id], "timestamp": [timestamp]},
            geometry=[new_point],
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        if self.gdf is None:
            self.gdf = new_row
        else:
            self.gdf = pd.concat([self.gdf, new_row], ignore_index=True).iloc[-self.max_waypoints:]  # Trim to max length
    

    def interpolate(self, target_timestamps):
        """
        Interpolate the trajectory to match the target timestamps.
        
        Args:
            target_timestamps (pd.DatetimeIndex): Target timestamps to interpolate to.
            
        Returns:
            gpd.GeoDataFrame: Interpolated trajectory.
        """
        # Handle single-point trajectories by returning a constant trajectory
        if len(self.timestamps) == 1:
            lat_const = self.gdf.geometry.y.iloc[0]
            lon_const = self.gdf.geometry.x.iloc[0]
            return gpd.GeoDataFrame(
                {"trajectory_id": [self.trajectory_id] * len(target_timestamps),
                "timestamp": target_timestamps},
                geometry=gpd.points_from_xy([lon_const] * len(target_timestamps), [lat_const] * len(target_timestamps)),
                crs="EPSG:3857"
            ).to_crs(epsg=3857) 
        
        interp_lat = interp1d(self.gdf.timestamp.astype(int), self.gdf.geometry.y, kind="linear", fill_value="extrapolate")
        interp_lon = interp1d(self.gdf.timestamp.astype(int), self.gdf.geometry.x, kind="linear", fill_value="extrapolate")

        interpolated_lat = interp_lat(target_timestamps.astype(int))
        interpolated_lon = interp_lon(target_timestamps.astype(int))

        # Create the interpolated GeoDataFrame
        interpolated_gdf = gpd.GeoDataFrame(
            {"trajectory_id": [self.trajectory_id] * len(target_timestamps), "timestamp": target_timestamps},
            geometry=gpd.points_from_xy(interpolated_lon, interpolated_lat),
            crs="EPSG:3857"
        ).to_crs(epsg=3857) 

        return interpolated_gdf

    def compute_ate(self, other_trajectory):
        """
        Compute the Average Trajectory Error (ATE) between two trajectories using Euclidean distance.
        
        Args:
            other_trajectory (Trajectory): Another Trajectory object to compare with.
            
        Returns:
            float: Average Trajectory Error (ATE) in meters.
        """
        # Interpolate both trajectories to match the same timestamps
        common_timestamps = pd.concat([self.gdf.timestamp, other_trajectory.gdf.timestamp]).drop_duplicates().sort_values()

        interpolated_self = self.interpolate(common_timestamps)
        interpolated_other = other_trajectory.interpolate(common_timestamps)

        # Calculate ATE (Euclidean distance)
        distance = interpolated_self.geometry.distance(interpolated_other.geometry)
        ate = distance.mean()
        
        return ate
    
    def visualize(self, output_dir=None, map_bg=False):
        
        print("save fig {}".format(self.trajectory_id))
        fig, ax = plt.subplots(figsize=(8,6))
        self.gdf.plot(ax=ax, markersize=10)
        plt.xlabel("Latitude")
        plt.ylabel("Longitude")            
        if map_bg:
            ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)
        if output_dir is None:
            plt.show()
        else:
            plt.savefig("{}/{}.png".format(output_dir, self.trajectory_id))

    def __repr__(self):
        return f"Trajectory(id={self.trajectory_id}, length={len(self.coordinates)})"
    
if __name__ == "__main__":
    time = datetime.datetime.now()
    traj = Trajectory("traj_1", [(37.7749, -122.4194), (37.7752, -122.4190), (37.7758, -122.4186)], 
                        [time, time+datetime.timedelta(0,3), time + datetime.timedelta(0,6)])
    traj.visualize()