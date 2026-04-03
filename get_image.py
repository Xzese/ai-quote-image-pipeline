import copy
import json
import dotenv
import os
import requests
import time
import urllib.parse
import textwrap
import signal
from PIL import Image, ImageDraw, ImageFont

FONT_FILE = "Alegreya-VariableFont.ttf"
WIDTH = 1024
HEIGHT = 1024
STEPS = 10
CFG = 1
POLL_INTERVAL = 2
TIMEOUT_SECONDS = 300

STOP_REQUESTED = False
CANCEL_SENT = False


def request_stop(signum=None, frame=None):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print("\nStop requested. Cancelling ComfyUI work safely...")


def interrupt_comfyui(session, comfyui_url):
    response = session.post(f"{comfyui_url}/interrupt", timeout=30)
    response.raise_for_status()


def clear_comfyui_queue(session, comfyui_url):
    response = session.post(
        f"{comfyui_url}/queue",
        json={"clear": True},
        timeout=30,
    )
    response.raise_for_status()


def cancel_comfyui_work(session, comfyui_url):
    global CANCEL_SENT

    if CANCEL_SENT:
        return

    CANCEL_SENT = True

    try:
        interrupt_comfyui(session, comfyui_url)
        print("Sent ComfyUI interrupt")
    except Exception as e:
        print(f"Failed to interrupt ComfyUI: {e}")

    try:
        clear_comfyui_queue(session, comfyui_url)
        print("Cleared ComfyUI pending queue")
    except Exception as e:
        print(f"Failed to clear ComfyUI queue: {e}")


def overlay_text_on_image(image_path, output_path, quote, author):
    try:
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        image_width, image_height = image.size

        max_width = int(0.82 * image_width)

        font_size = 72
        wrapped_text = quote
        text_width = 0
        text_height = 0

        while font_size >= 10:
            font = ImageFont.truetype(FONT_FILE, font_size, encoding="unicode")

            quote_length = max(1, len(quote))
            raw_width = max(font.getlength(quote), 1)
            char_width = max(1, int(quote_length / (raw_width / max_width)))

            wrapped_lines = textwrap.wrap(quote, width=char_width) or [quote]
            longest_line_width = max(font.getlength(line) for line in wrapped_lines) or 1
            adjusted_width = max(1, int((max_width / longest_line_width) * char_width))
            wrapped_text = textwrap.fill(quote, width=adjusted_width)

            text_bound = draw.textbbox((0, 0), wrapped_text, font=font)
            text_width = text_bound[2] - text_bound[0]
            text_height = text_bound[3] - text_bound[1]

            if text_height < 0.6 * image_height:
                break

            font_size -= 1

        if font_size < 10:
            raise ValueError("Could not fit quote text on image")

        text_color = (255, 255, 255)
        outline_color = (0, 0, 0)
        offsets = (-2, 0, 2)

        text_x = (image_width - text_width) / 2
        text_y = ((image_height - text_height) / 2) - 40

        for offset_x in offsets:
            for offset_y in offsets:
                if offset_x == 0 and offset_y == 0:
                    continue
                draw.text((text_x + offset_x, text_y + offset_y), wrapped_text, font=font, fill=outline_color)

        draw.text((text_x, text_y), wrapped_text, font=font, fill=text_color)

        author_font_size = 72
        author_text = "—" + author
        author_font = ImageFont.truetype(FONT_FILE, author_font_size)

        while author_font.getlength(author_text) > max_width * 0.8 and author_font_size >= 10:
            author_font_size -= 1
            author_font = ImageFont.truetype(FONT_FILE, author_font_size)

        author_x = image_width * 0.9 - author_font.getlength(author_text)
        author_y = text_y + text_height + 25

        for offset_x in offsets:
            for offset_y in offsets:
                if offset_x == 0 and offset_y == 0:
                    continue
                draw.text((author_x + offset_x, author_y + offset_y), author_text, font=author_font, fill=outline_color)

        draw.text((author_x, author_y), author_text, font=author_font, fill=text_color)

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


def queue_prompt(session, comfyui_url, workflow):
    response = session.post(
        f"{comfyui_url}/prompt",
        json={"prompt": workflow},
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["prompt_id"]


def get_history(session, comfyui_url, prompt_id):
    response = session.get(f"{comfyui_url}/history/{prompt_id}", timeout=300)
    response.raise_for_status()
    return response.json()


def wait_for_image(session, comfyui_url, prompt_id, timeout_seconds=TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL):
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        if STOP_REQUESTED:
            raise KeyboardInterrupt("Stop requested while waiting for image")

        history = get_history(session, comfyui_url, prompt_id)

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                images = node_output.get("images", [])
                if images:
                    return images[0]

        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out waiting for image for prompt_id {prompt_id}")


def download_image(session, comfyui_url, image_info, save_path):
    params = {
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    }

    response = session.get(
        f"{comfyui_url}/view?{urllib.parse.urlencode(params)}",
        timeout=300,
    )
    response.raise_for_status()

    temp_path = save_path + ".part"
    with open(temp_path, "wb") as f:
        f.write(response.content)

    os.replace(temp_path, save_path)


def build_workflow(base_workflow, prompt_text, width=WIDTH, height=HEIGHT, steps=STEPS, cfg=CFG, seed=None):
    workflow = copy.deepcopy(base_workflow)

    if seed is None or seed == -1:
        seed = int(time.time_ns() % 9007199254740991)

    workflow["57:27"]["inputs"]["text"] = prompt_text
    workflow["57:13"]["inputs"]["width"] = width
    workflow["57:13"]["inputs"]["height"] = height
    workflow["57:3"]["inputs"]["steps"] = steps
    workflow["57:3"]["inputs"]["cfg"] = cfg
    workflow["57:3"]["inputs"]["seed"] = seed

    return workflow


def has_non_blank_prompt(item):
    return "prompt" in item and str(item["prompt"]).strip()


signal.signal(signal.SIGINT, request_stop)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, request_stop)

dotenv.load_dotenv()

quotes_file_path = os.getenv("QUOTES_FILE_PATH")
output_image_path = os.getenv("OUTPUT_IMAGE_PATH")
overlay_image_path = os.getenv("OVERLAY_OUTPUT_PATH")
comfyui_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8000")
workflow_file_path = os.getenv(
    "COMFYUI_WORKFLOW_PATH",
    r"C:\Users\apple\Documents\Coding\instagram-ai-images\image_z_image_turbo.json",
)

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

os.makedirs(output_image_path, exist_ok=True)
os.makedirs(overlay_image_path, exist_ok=True)

session = requests.Session()
jobs = []

try:
    # Phase 1: queue all missing generations
    for x, item in enumerate(quote_data):
        if STOP_REQUESTED:
            cancel_comfyui_work(session, comfyui_url)
            break

        try:
            if not has_non_blank_prompt(item):
                print(f"Skipping item {x} - no prompt")
                continue

            output_png = os.path.join(output_image_path, f'{item["_id"]}{WIDTH}x{HEIGHT}.png')
            output_jpg = os.path.join(overlay_image_path, f'{item["_id"]}{WIDTH}x{HEIGHT}.jpeg')

            png_exists = os.path.isfile(output_png) and os.path.getsize(output_png) > 0

            if not png_exists:
                prompt_text = item["prompt"] + " Must have a positive, high energy atmosphere."

                workflow = build_workflow(
                    base_workflow=base_workflow,
                    prompt_text=prompt_text,
                    width=WIDTH,
                    height=HEIGHT,
                    steps=STEPS,
                    cfg=CFG,
                    seed=-1,
                )

                prompt_id = queue_prompt(session, comfyui_url, workflow)
                jobs.append((x, item, prompt_id, output_png, output_jpg))
                print(f"Queued generation for item {x} with prompt_id {prompt_id}")
            else:
                jobs.append((x, item, None, output_png, output_jpg))
                print(f"Image already exists for item {x}")

        except Exception as error:
            print(f"An error occurred while queueing item {x}: {error}")

    # Phase 2: collect generated images
    for x, item, prompt_id, output_png, output_jpg in jobs:
        if STOP_REQUESTED:
            cancel_comfyui_work(session, comfyui_url)
            break

        try:
            if prompt_id is not None:
                image_info = wait_for_image(session, comfyui_url, prompt_id)
                download_image(session, comfyui_url, image_info, output_png)
                print(f"Image generated for item {x}")

            if STOP_REQUESTED:
                cancel_comfyui_work(session, comfyui_url)
                break

            if not os.path.isfile(output_jpg) and os.path.isfile(output_png):
                image_overlay = overlay_text_on_image(
                    output_png,
                    output_jpg,
                    item["content"],
                    item["author"],
                )

                if image_overlay:
                    print(f"Added overlay for item {x}")
                else:
                    print(f"Failed to add overlay for item {x}")
            else:
                print(f"Overlay already exists for item {x}")

        except KeyboardInterrupt:
            request_stop()
            cancel_comfyui_work(session, comfyui_url)
            break
        except Exception as error:
            print(f"An error occurred while processing item {x}: {error}")

except KeyboardInterrupt:
    request_stop()
    cancel_comfyui_work(session, comfyui_url)

finally:
    session.close()
    if STOP_REQUESTED:
        print("Stopped safely.")