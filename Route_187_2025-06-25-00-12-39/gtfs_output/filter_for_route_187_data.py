import xml.etree.ElementTree as ET

ROUTE_KEYWORDS = ['t163-b2736-sl4', 't1B3-b2739-sl4']  # route IDs for EB and WB on route 187

# Input files
routes_file = 'gtfs_output/routes.rou.xml'
stops_file = 'gtfs_output/stops_additional.xml'

# Output files
filtered_routes_file = 'gtfs_output/routes_filtered.rou.xml'
filtered_stops_file = 'gtfs_output/stops_filtered.additional.xml'

def keep_element_by_attr(elem, attr, keywords):
    val = elem.get(attr, "")
    return any(k in val for k in keywords)

def filter_routes():
    tree = ET.parse(routes_file)
    root = tree.getroot()

    for child in list(root):
        if child.tag in ['route', 'vehicle']:
            if not keep_element_by_attr(child, 'id', ROUTE_KEYWORDS):
                root.remove(child)

    tree.write(filtered_routes_file, encoding='utf-8', xml_declaration=True)
    print(f"Filtered routes saved to: {filtered_routes_file}")

def filter_stops():
    tree = ET.parse(stops_file)
    root = tree.getroot()

    for child in list(root):
        if child.tag == 'busStop':
            if not keep_element_by_attr(child, 'id', ROUTE_KEYWORDS):
                root.remove(child)

    tree.write(filtered_stops_file, encoding='utf-8', xml_declaration=True)
    print(f"Filtered stops saved to: {filtered_stops_file}")

if __name__ == '__main__':
    filter_routes()
    filter_stops()
