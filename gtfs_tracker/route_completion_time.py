import pandas as pd
from datetime import datetime
import argparse

def compute_trip_times(csv_file, start_stop_id, end_stop_id):
    df = pd.read_csv(csv_file)

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    df = df[df["stop_id"].isin([start_stop_id, end_stop_id])]   # Get needed stop ids

    df = df.sort_values(by=["trip_id", "timestamp"])

    results = []
    for trip_id, group in df.groupby("trip_id"):    # Group by trip_id
        group = group.sort_values("timestamp")

        start_rows = group[group["stop_id"] == start_stop_id]
        end_rows = group[group["stop_id"] == end_stop_id]

        if start_rows.empty or end_rows.empty:
            continue

        for _, start_row in start_rows.iterrows():
            possible_ends = end_rows[end_rows["timestamp"] > start_row["timestamp"]]
            if not possible_ends.empty:
                end_row = possible_ends.iloc[0]

                duration = end_row["timestamp"] - start_row["timestamp"]

                results.append({
                    "trip_id": trip_id,
                    "vehicle_id": start_row["vehicle_id"],
                    "start_stop_id": start_stop_id,
                    "end_stop_id": end_stop_id,
                    "start_time": start_row["timestamp"],
                    "end_time": end_row["timestamp"],
                    "duration": round(duration.total_seconds() / 60, 2) # in minutes
                })
                break  

    result_df = pd.DataFrame(results)

    print(result_df)
    out_path = csv_file.replace(".csv", "_trip_durations.csv")
    result_df.to_csv(out_path, index=False)
    print(f"\nSaved summary to: {out_path}")

    if not result_df.empty:
        avg_duration = result_df["duration"].mean()
        print(f"\nAverage trip duration: {round(avg_duration, 2)} minutes")
    else:
        print("\nNo valid trips found between the specified stop IDs.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to route CSV file")
    parser.add_argument("--start", required=True, type=int, help="Start stop_id")
    parser.add_argument("--end", required=True, type=int, help="End stop_id")
    args = parser.parse_args()

    compute_trip_times(args.file, args.start, args.end)
