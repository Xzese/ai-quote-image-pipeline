#!/usr/bin/env python3
import os
import dotenv
import json
import random
import time

from upload_photo.upload_photo import post_random_photo 

dotenv.load_dotenv()

def get_random_photo():
    quotes_file_path = os.getenv('QUOTES_FILE_PATH')
    with open(quotes_file_path, 'r') as json_file:
        quote_data = json.load(json_file)
    while True:
        quote_choice = random.randint(0,len(quote_data)-1)
        file_path = os.path.join("output","images_text_overlay",quote_data[quote_choice]['_id']+"1024x1024.jpeg")
        caption = quote_data[quote_choice]['hashtags']
        return file_path, caption

while True:
    try:
        file_path, caption = get_random_photo()
        post_random_photo(file_path, caption)
        break
    except:
        print("Trying again for another photo as failed")


time.sleep(60*60*3)