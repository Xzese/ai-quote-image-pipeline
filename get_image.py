import json
import dotenv
import os
import requests
import io
import base64
import textwrap
from PIL import Image, ImageDraw, ImageFont

def overlay_text_on_image(image_path, output_path, quote, author):
    image = Image.open(image_path)
    draw = ImageDraw.Draw(image)
    text = quote
    font_size = 36
    font_path = "GreatVibes-Regular.ttf"
    font = ImageFont.truetype(font_path, font_size)
    text_position = (50,50)
    text_color = (255, 255, 255)  # White Color
    border_color = (0, 0, 0)      # Black Color
    max_width = image.width - 50

    x, y = text_position
    offsets = [-1,0,1]
    for offset_x in offsets:
        for offset_y in offsets:
            draw.text((x + offset_x, y + offset_y), text, font=font, fill=border_color)
    draw.text(text_position, text, font=font, fill=text_color)
    image.save(output_path)

dotenv.load_dotenv()

quotes_file_path = os.getenv('QUOTES_FILE_PATH')
endpoint_url = "http://127.0.0.1:7860/"

with open(quotes_file_path, 'r') as json_file:
    quote_data = json.load(json_file)

width = 512
height = 512

for x in range(len(quote_data)):
    try:
        if 'prompt' in quote_data[x] and not os.path.isfile("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png"):
            payload = {
                "prompt": quote_data[x]['prompt'] + " Must have a positive, high energy atmosphere.",
                "steps": 30,
                "width": width,
                "height": height,
                "cfg_scale": 12,
                "seed": -1
            }
            print("Generating for quote " + str(x))
            response = requests.post(endpoint_url + "sdapi/v1/txt2img", json=payload)
            if response.status_code == 200:
                image_base64 = response.json()['images'][0]
                image_binary = base64.b64decode(image_base64)
                image_data = io.BytesIO(image_binary) 
                image = Image.open(image_data)
                image.save("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png")
                print("Image Generated for quote " + str(x))
            else:
                print(f"Failed to retrieve data. HTTP Status code: {response.status_code}")
        elif not os.path.isfile("output/images_text_overlay/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png") and os.path.isfile("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png"):
            continue
            #overlay_text_on_image("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", "output/images_text_overlay/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", quote_data[x]['content'], quote_data[x]['author'])
            #print("Added Overlay for quote "+ str(x))
    except Exception as error:
        print(f"An error occured with item {x}: {error}")
        continue