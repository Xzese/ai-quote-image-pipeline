import json
import dotenv
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from transformers import AutoTokenizer
from openai import OpenAI

# =========================
# Settings
# =========================
RESET_PROMPTS_AND_HASHTAGS = False
MAX_PROMPT_RETRIES = 5
MAX_HASHTAG_RETRIES = 5
PARALLEL_WORKERS = 4  # match LM Studio parallel slots

MODEL_NAME = "qwen/qwen3.5-9b"

dotenv.load_dotenv()

quotes_file_path = os.getenv("QUOTES_FILE_PATH")
if not quotes_file_path:
    raise ValueError("QUOTES_FILE_PATH is not set")

tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

file_lock = threading.Lock()
print_lock = threading.Lock()


def log(message: str) -> None:
    with print_lock:
        print(message)


def save_json(data) -> None:
    with file_lock:
        with open(quotes_file_path, "w", encoding="utf-8") as json_file:
            json.dump(data, json_file, ensure_ascii=False, indent=2)


with open(quotes_file_path, "r", encoding="utf-8") as json_file:
    quote_data = json.load(json_file)


def extract_braced_content(text):
    if not text:
        return None

    match = re.search(r"\{([^{}]+)\}", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


def extract_fallback_text(text):
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*", "", cleaned).strip()
    cleaned = cleaned.replace("```", "").strip()
    cleaned = re.sub(r"^(prompt|answer|hashtags)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()

    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1].strip()

    return cleaned or None


def is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def call_model(messages, temperature=0.7, max_tokens=150):
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=temperature,
        stream=False,
        max_tokens=max_tokens,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False
            }
        }
    )
    return completion.choices[0].message.content or ""


def generate_prompt(item, item_index):
    for attempt in range(1, MAX_PROMPT_RETRIES + 1):
        chat_message = (
            "Generate an image generation prompt for a diffusion model that matches "
            "the tone of the following quote. Do not mention the author. Do not mention "
            "text overlays. Do not include any people. Return only the prompt in curly braces. "
            "The prompt must be at or below 50 tokens.\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        text_response = call_model(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You create concise image prompts. Return only one prompt enclosed in curly braces. "
                        "Example: {misty pine forest at dawn, soft light, calm atmosphere}. "
                        "Do not add explanations. Do not add markdown. No people. Maximum 50 tokens."
                    ),
                },
                {"role": "user", "content": chat_message},
            ]
        )

        prompt = extract_braced_content(text_response) or extract_fallback_text(text_response)

        if not prompt:
            log(f"Item {item_index}, prompt attempt {attempt}: no valid prompt found")
            log("RAW MODEL OUTPUT:")
            log("-" * 60)
            log(text_response)
            log("-" * 60)
            continue

        number_of_tokens = len(tokenizer.tokenize(prompt))

        if number_of_tokens <= 50 and '"' not in prompt:
            return prompt

        log(
            f"Item {item_index}, prompt attempt {attempt}: rejected "
            f"(tokens={number_of_tokens}, contains_double_quote={'\"' in prompt}) "
            f"response={text_response!r}"
        )

    return None


def generate_hashtags(item, item_index):
    for attempt in range(1, MAX_HASHTAG_RETRIES + 1):
        chat_message = (
            "Generate a string of 20 or fewer instagram hashtags. "
            "They must be related to the quote and/or the author. "
            "They can also reference AI art in some of them. "
            "Please return the result in the format:\n"
            "hashtags: {#hashtag1 #hashtag2 ... }\n"
            f'Quote: "{item["content"]}"\n'
            f'Author: "{item["author"]}"'
        )

        text_response = call_model(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You create Instagram hashtags. Return only the hashtags enclosed in curly braces. "
                        "Example: {#inspiration #quotes #wisdom #aiart}. "
                        "Do not add explanations. Do not add markdown. Ask for 20 or fewer hashtags."
                    ),
                },
                {"role": "user", "content": chat_message},
            ]
        )

        hashtags = extract_braced_content(text_response) or extract_fallback_text(text_response)

        if not hashtags:
            log(f"Item {item_index}, hashtag attempt {attempt}: empty model response")
            continue

        number_of_hashtags = hashtags.count("#")

        if number_of_hashtags <= 30:
            return hashtags

        log(
            f"Item {item_index}, hashtag attempt {attempt}: rejected "
            f"(hashtags={number_of_hashtags}) response={text_response!r}"
        )

    return None


def process_item(index):
    item = quote_data[index]
    changed = False

    log(f"Generating for Item {index}")

    if "prompt" not in item or is_blank(item["prompt"]):
        prompt = generate_prompt(item, index)
        if prompt:
            item["prompt"] = prompt
            changed = True
            log(f"Prompt Accepted for Item {index}: {prompt}")
        else:
            log(f"Prompt generation failed for Item {index} after {MAX_PROMPT_RETRIES} attempts")

    if "hashtags" not in item or is_blank(item["hashtags"]):
        hashtags = generate_hashtags(item, index)
        if hashtags:
            item["hashtags"] = hashtags
            changed = True
            log(f"Hashtags Accepted for Item {index}: {hashtags}")
        else:
            log(f"Hashtag generation failed for Item {index} after {MAX_HASHTAG_RETRIES} attempts")

    if changed:
        save_json(quote_data)

    return index, changed


# Optional reset step
if RESET_PROMPTS_AND_HASHTAGS:
    log("RESET_PROMPTS_AND_HASHTAGS is True. Clearing prompts and hashtags...")
    for item in quote_data:
        if "prompt" in item:
            item["prompt"] = ""
        if "hashtags" in item:
            item["hashtags"] = ""

    save_json(quote_data)
    log("Reset complete.")


def main():
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = [executor.submit(process_item, i) for i in range(len(quote_data))]

        for future in as_completed(futures):
            try:
                index, changed = future.result()
                log(f"Finished Item {index} (changed={changed})")
            except Exception as error:
                log(f"An error occurred: {error}")

    # Final save to ensure latest state is persisted
    save_json(quote_data)
    log("All done.")


if __name__ == "__main__":
    main()