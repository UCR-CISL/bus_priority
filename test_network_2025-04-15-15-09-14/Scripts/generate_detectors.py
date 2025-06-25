import os
import sys
import gzip
import shutil
import sumolib

def decompress_network_file(network_file):
    """
    Decompress the network file if it's in .gz format.
    """
    if network_file.endswith('.gz'):
        decompressed_file = network_file[:-3]
        with gzip.open(network_file, 'rb') as f_in:
            with open(decompressed_file, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        print(f"Decompressed to: {decompressed_file}")
        return decompressed_file
    return network_file

def generate_detectors(network_file):
    """
    Generate induction loop detectors on lanes leading into traffic lights.
    """
    net = sumolib.net.readNet(network_file)

    with open("detectors.add.xml", "w") as detectors_file:
        detectors_file.write('<additional>\n')

        added_lanes = set()

        for edge in net.getEdges():
            for lane in edge.getLanes():
                for conn in lane.getOutgoing():
                    if conn.getTLSID():  # This connection is controlled by a traffic light
                        lane_id = lane.getID()
                        if lane_id in added_lanes:
                            continue
                        added_lanes.add(lane_id)

                        pos = lane.getLength() - 1
                        if pos <= 0:
                            continue

                        detector_id = f"det_{lane_id}"
                        detectors_file.write(
                            f'    <inductionLoop id="{detector_id}" lane="{lane_id}" pos="{pos}" freq="60" file="detector_output.xml"/>\n'
                        )

        detectors_file.write('</additional>\n')
        print("Detectors written to detectors.add.xml")

def main():
    if len(sys.argv) != 2:
        print("Usage: python generate_detectors.py <network_file.net.xml(.gz)>")
        sys.exit(1)

    network_file = sys.argv[1]
    decompressed_net_path = decompress_network_file(network_file)
    generate_detectors(decompressed_net_path)

if __name__ == "__main__":
    main()




