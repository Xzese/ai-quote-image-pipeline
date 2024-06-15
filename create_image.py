import json
import dotenv
import os

dotenv.load_dotenv()

quotes_file_path = os.getenv('QUOTES_FILE_PATH')

with open(quotes_file_path, 'r') as json_file:
    data = json.load(json_file)

print(len(data))