import json
import dotenv
import os
import requests
import time
import urllib.parse
import textwrap
from PIL import Image, ImageDraw, ImageFont

def convert_png_to_jpg(png_image_path, jpg_image_path, quality=90):
    image = Image.open(png_image_path)

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        image = background
    else:
        image = image.convert("RGB")

    image.save(jpg_image_path, "JPEG", quality=quality)
    print("Image converted to JPEG")


def overlay_text_on_image(image_path, output_path, quote, author):
    try:
        text_size_accepted = False
        font_file = "Alegreya-VariableFont.ttf"

        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        image_width, image_height = image.size

        font_size = 72
        author_font_size = 72

        while text_size_accepted is False:
            font = ImageFont.truetype(font_file, font_size, encoding="unicode")

            max_width = int(0.82 * image_width)

            char_width = len(quote) / (font.getlength(quote) / max_width)
            wrapped_text_list = textwrap.wrap(quote, width=max(1, int(char_width)))

            if not wrapped_text_list:
                wrapped_text_list = [quote]

            char_width = (max_width / max(font.getlength(line) for line in wrapped_text_list)) * char_width
            wrapped_text = textwrap.fill(quote, width=max(1, int(char_width)))

            text_bound = draw.textbbox((0, 0), wrapped_text, font=font)
            text_width = text_bound[2] - text_bound[0]
            text_height = text_bound[3] - text_bound[1]

            if text_height < 0.6 * image_height:
                text_size_accepted = True
            else:
                font_size -= 1
                if font_size < 10:
                    raise ValueError("Could not fit quote text on image")

        text_color = (255, 255, 255)
        outline_color = (0, 0, 0)
        offsets = [-2, 0, 2]

        text_x = (image_width - text_width) / 2
        text_y = ((image_height - text_height) / 2) - 40

        for offset_x in offsets:
            for offset_y in offsets:
                draw.text((text_x + offset_x, text_y + offset_y), wrapped_text, font=font, fill=outline_color)

        draw.text((text_x, text_y), wrapped_text, font=font, fill=text_color)

        font = ImageFont.truetype(font_file, author_font_size)

        while font.getlength("—" + author) > max_width * 0.8:
            author_font_size -= 1
            font = ImageFont.truetype(font_file, author_font_size)

        author_text = "—" + author
        author_x = image_width * 0.9 - font.getlength(author_text)
        author_y = text_y + text_height + 25

        for offset_x in offsets:
            for offset_y in offsets:
                draw.text((author_x + offset_x, author_y + offset_y), author_text, font=font, fill=outline_color)

        draw.text((author_x, author_y), author_text, font=font, fill=text_color)

        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background
        else:
            image = image.convert("RGB")

        image.save(output_path, "JPEG", quality=90)
        return True

    except Exception as e:
        print(f"Error overlaying text on image: {e}")
        return False


def queue_prompt(comfyui_url, workflow):
    response = requests.post(
        f"{comfyui_url}/prompt",
        json={"prompt": workflow},
        timeout=300
    )
    response.raise_for_status()
    data = response.json()
    return data["prompt_id"]


def get_history(comfyui_url, prompt_id):
    response = requests.get(f"{comfyui_url}/history/{prompt_id}", timeout=300)
    response.raise_for_status()
    return response.json()


def wait_for_image(comfyui_url, prompt_id, timeout_seconds=300, poll_interval=2):
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        history = get_history(comfyui_url, prompt_id)

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})

            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    return images[0]

        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out waiting for image for prompt_id {prompt_id}")


def download_image(comfyui_url, image_info, save_path):
    params = {
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output")
    }

    response = requests.get(
        f"{comfyui_url}/view?{urllib.parse.urlencode(params)}",
        timeout=300
    )
    response.raise_for_status()

    with open(save_path, "wb") as f:
        f.write(response.content)


def build_workflow(base_workflow, prompt_text, width=1024, height=1024, steps=10, cfg=1, seed=None):
    workflow = json.loads(json.dumps(base_workflow))

    if seed is None or seed == -1:
        seed = int(time.time() * 1000) % 9007199254740991

    # Your workflow node IDs
    workflow["57:27"]["inputs"]["text"] = prompt_text
    workflow["57:13"]["inputs"]["width"] = width
    workflow["57:13"]["inputs"]["height"] = height
    workflow["57:3"]["inputs"]["steps"] = steps
    workflow["57:3"]["inputs"]["cfg"] = cfg
    workflow["57:3"]["inputs"]["seed"] = seed

    return workflow


dotenv.load_dotenv()

quotes_file_path = os.getenv("QUOTES_FILE_PATH")
output_image_path = os.getenv("OUTPUT_IMAGE_PATH")
overlay_image_path = os.getenv("OVERLAY_OUTPUT_PATH")

# Default ComfyUI local address
comfyui_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8000")

# Use a raw string or double backslashes on Windows
workflow_file_path = r"C:\Users\apple\Documents\Coding\instagram-ai-images\image_z_image_turbo.json"

if not quotes_file_path:
    raise ValueError("QUOTES_FILE_PATH is not set")

if not output_image_path:
    raise ValueError("OUTPUT_IMAGE_PATH is not set")

if not overlay_image_path:
    raise ValueError("OVERLAY_OUTPUT_PATH is not set")

with open(quotes_file_path, "r", encoding="utf-8") as json_file:
    quote_data = json.load(json_file)

with open(workflow_file_path, "r", encoding="utf-8") as workflow_file:
    base_workflow = json.load(workflow_file)

width = 1024
height = 1024

os.makedirs(output_image_path, exist_ok=True)
os.makedirs(overlay_image_path, exist_ok=True)

for x in range(len(quote_data)):
    try:
        item = quote_data[x]

        if "prompt" not in item or not str(item["prompt"]).strip():
            continue

        output_png = os.path.join(output_image_path, f'{item["_id"]}{width}x{height}.png')
        output_jpg = os.path.join(overlay_image_path, f'{item["_id"]}{width}x{height}.jpeg')

        if not os.path.isfile(output_png):
            prompt_text = item["prompt"] + " Must have a positive, high energy atmosphere."

            print("Generating for quote " + str(x))

            workflow = build_workflow(
                base_workflow=base_workflow,
                prompt_text=prompt_text,
                width=width,
                height=height,
                steps=10,
                cfg=1,
                seed=-1
            )

            prompt_id = queue_prompt(comfyui_url, workflow)
            image_info = wait_for_image(comfyui_url, prompt_id)
            download_image(comfyui_url, image_info, output_png)

            print("Image generated for quote " + str(x))

        if not os.path.isfile(output_jpg) and os.path.isfile(output_png):
            image_overlay = overlay_text_on_image(
                output_png,
                output_jpg,
                item["content"],
                item["author"]
            )

            if image_overlay:
                print("Added overlay for quote " + str(x))
            else:
                print("Failed to add overlay for quote " + str(x))

    except Exception as error:
        print(f"An error occurred with item {x}: {error}")
        continue