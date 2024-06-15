import requests
import json
import dotenv
import os

dotenv.load_dotenv()

endpoint_url = "https://api.quotable.io/quotes"
quotes_file_path = os.getenv('QUOTES_FILE_PATH')
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

with open(quotes_file_path, 'w') as json_file:
    json.dump(quote_list, json_file, indent=4)