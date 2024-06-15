import requests
import json
import os

endpoint_url = "https://api.quotable.io/quotes"
file_path = "output/quotes2.json"
quote_list = []
total_pages = 1
current_page = 1

params = {
    "limit": 150
}

while current_page <= total_pages:
    print("Page Number: " + str(current_page))
    response = requests.get(endpoint_url, params=params)
    if response.status_code == 200:
        data = response.json()
        quote_list += data['results']
        total_pages = data['totalPages']
        current_page += 1
    else:
        print(f"Failed to retrieve data. HTTP Status code: {response.status_code}")

with open(file_path, 'w') as json_file:
    json.dump(quote_list, json_file, indent=4)