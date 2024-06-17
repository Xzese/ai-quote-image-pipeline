import json
import dotenv
import os
import requests
import io
import base64
import textwrap
from PIL import Image, ImageDraw, ImageFont

def overlay_text_on_image(image_path, output_path, quote, author):
    try:
        text_size_accepted = False
        font_file = "DancingScript-Bold.ttf"
        # Load image and prepare for drawing
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        image_width, image_height = image.size

        font_size = 48
        author_font_size = 54

        while text_size_accepted == False:
            # Load font
            font = ImageFont.truetype(font_file, font_size, encoding="unicode")

            # Wrap text
            max_width = int(0.75 * image_width)
            # Work out initial char width
            char_width = len(quote) / (font.getlength(quote) / max_width)
            wrapped_text_list = textwrap.wrap(quote, width=char_width)

            # Work out updated char width based on first line accuracy
            char_width = (max_width / max(font.getlength(line) for line in wrapped_text_list)) * char_width
            wrapped_text_list = textwrap.wrap(quote, width=char_width)
            wrapped_text = textwrap.fill(quote, width=char_width)
            
            text_bound = draw.textbbox((0,0),wrapped_text,font=font)
            text_width = text_bound[2] - text_bound[0]
            text_height = text_bound[3] - text_bound[1]
            if text_height < 0.6 * image_height:
                text_size_accepted = True
            else:
                font_size -= 1

        text_color = (255, 255, 255)
        outline_color = (0, 0, 0)
        offsets = [-1,0,1]

        # Calculate text position
        text_x = (image_width - text_width) / 2
        text_y = ((image_width - text_height) / 2) - 40

        for offset_x in offsets:
            for offset_y in offsets:
                draw.text((text_x + offset_x, text_y + offset_y), wrapped_text, font=font, fill=outline_color)
        # Draw text on image
        draw.text((text_x, text_y), wrapped_text, font=font, fill=text_color)

        font = ImageFont.truetype(font_file, author_font_size)

        while font.getlength("—" + author) > max_width * 0.8:
            author_font_size -= 1
            font = ImageFont.truetype(font_file, author_font_size)

        text_x = image_width * 0.9 - font.getlength("—" + author)
        text_y = text_y + text_height + 25

        for offset_x in offsets:
            for offset_y in offsets:
                draw.text((text_x + offset_x, text_y + offset_y), "—" + author, font=font, fill=outline_color)

        draw.text((text_x, text_y), "—" + author, font=font, fill=text_color)
        # Save the image with overlaid text
        image.save(output_path)
        return True
    except Exception as e:
        print(f"Error overlaying text on image: {e}")
        return False

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
                image_overlay = overlay_text_on_image("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", "output/images_text_overlay/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", quote_data[x]['content'], quote_data[x]['author'])
                if image_overlay:
                    print("Added overlay for quote "+ str(x))
                else:
                    print("Failed to add overlay for quote "+ str(x))
            else:
                print(f"Failed to retrieve data. HTTP Status code: {response.status_code}")
        elif not os.path.isfile("output/images_text_overlay/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png") and os.path.isfile("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png"):
            image_overlay = overlay_text_on_image("output/images/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", "output/images_text_overlay/"+ quote_data[x]['_id'] + str(width) + "x" + str(height) + ".png", quote_data[x]['content'], quote_data[x]['author'])
            if image_overlay:
                print("Added overlay for quote "+ str(x))
            else:
                print("Failed to add overlay for quote "+ str(x))
    except Exception as error:
        print(f"An error occured with item {x}: {error}")
        continue