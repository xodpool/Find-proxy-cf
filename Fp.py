import argparse
import csv
import json
import os
import re
import requests
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipaddress import ip_network

REQUEST_URL = "https://speed.cloudflare.com/cdn-cgi/trace"
TIMEOUT = 1
MAX_DURATION = 2
BATCH_SIZE = 1000
DEFAULT_PORT = 443

def get_asn_info(asn):
    url = f"https://api.bgpview.io/asn/{asn}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json().get('data', {})

def load_locations():
    if not os.path.exists("locations.json"):
        response = requests.get("https://speed.cloudflare.com/locations")
        response.raise_for_status()
        locations = response.json()
        with open("locations.json", "w") as file:
            json.dump(locations, file)
    else:
        with open("locations.json", "r") as file:
            locations = json.load(file)
    return locations

def create_location_map(locations):
    return {loc['iata']: loc for loc in locations}

def prepare_output_file(out_file):
    if os.path.exists(out_file):
        os.remove(out_file)

def fetch_cidr_blocks_from_asn(asn):
    url = f"https://api.bgpview.io/asn/{asn}/prefixes"
    response = requests.get(url)
    response.raise_for_status()
    prefixes = response.json().get('data', {}).get('ipv4_prefixes', [])
    return [prefix['prefix'] for prefix in prefixes]

def generate_ips(cidr):
    return [str(ip) for ip in ip_network(cidr, strict=False).hosts()]

def process_ip(ip, location_map, port=DEFAULT_PORT):
    start = time.time()
    try:
        with socket.create_connection((ip, port), timeout=TIMEOUT) as sock:
            tcp_duration = time.time() - start
            response = requests.get(REQUEST_URL, timeout=TIMEOUT)
            response_time = time.time() - start
            if response_time > MAX_DURATION:
                raise Exception("Request took too long")
            if "uag=Mozilla/5.0" in response.text:
                match = re.search(r'colo=([A-Z]+)', response.text)
                if match:
                    data_center = match.group(1)
                    loc = location_map.get(data_center, {})
                    print(f"Valid IP {ip}, Location {loc.get('city', 'Unknown')}, Latency {tcp_duration:.2f} ms")
                    return {
                        'ip': ip,
                        'port': port,
                        'data_center': data_center,
                        'region': loc.get('region', ''),
                        'city': loc.get('city', ''),
                        'latency': f"{tcp_duration:.2f} ms",
                    }
    except Exception as e:
        pass
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asn', required=True, help='Comma-separated ASN numbers')
    args = parser.parse_args()

    asns = args.asn.split(',')
    locations = load_locations()
    location_map = create_location_map(locations)

    for asn in asns:
        asn_info = get_asn_info(asn)
        out_file = f"{asn_info.get('name', 'asn')}.csv"
        prepare_output_file(out_file)

        cidr_blocks = fetch_cidr_blocks_from_asn(asn)
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            for cidr in cidr_blocks:
                ips = generate_ips(cidr)
                futures.extend(executor.submit(process_ip, ip, location_map, DEFAULT_PORT) for ip in ips)

            with open(out_file, 'w', newline='') as csvfile:
                fieldnames = ['ip', 'port', 'data_center', 'region', 'city', 'latency']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        writer.writerow(result)

if __name__ == '__main__':
    main()
