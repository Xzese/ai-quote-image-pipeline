import json
import dotenv
import os
import re
from transformers import AutoTokenizer
from openai import OpenAI

# =========================
# Settings
# =========================
RESET_PROMPTS_AND_HASHTAGS = False
MAX_PROMPT_RETRIES = 5
MAX_HASHTAG_RETRIES = 5

MODEL_NAME = "qwen/qwen3.5-9b"

dotenv.load_dotenv()

quotes_file_path = os.getenv("QUOTES_FILE_PATH")
if not quotes_file_path:
    raise ValueError("QUOTES_FILE_PATH is not set")

tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

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


# Optional reset step
if RESET_PROMPTS_AND_HASHTAGS:
    print("RESET_PROMPTS_AND_HASHTAGS is True. Clearing prompts and hashtags...")
    for item in quote_data:
        if "prompt" in item:
            item["prompt"] = ""
        if "hashtags" in item:
            item["hashtags"] = ""

    with open(quotes_file_path, "w", encoding="utf-8") as json_file:
        json.dump(quote_data, json_file, ensure_ascii=False, indent=2)

    print("Reset complete.")


for x in range(len(quote_data)):
    try:
        print(f"Generating for Item {x}")
        item = quote_data[x]

        # Generate prompt only if missing or blank
        if "prompt" not in item or is_blank(item["prompt"]):
            prompt_accepted = False

            for attempt in range(1, MAX_PROMPT_RETRIES + 1):
                chat_message = (
                    "Generate an image generation prompt for a diffusion model that matches "
                    "the tone of the following quote. Do not mention the author. Do not mention "
                    "text overlays. Do not include any people. Return only the prompt in curly braces. "
                    "The prompt must be at or below 50 tokens.\n"
                    f'Quote: "{item["content"]}"\n'
                    f'Author: "{item["author"]}"'
                )

                completion = client.chat.completions.create(
                    model=MODEL_NAME,
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
                    ],
                    temperature=0.7,
                    stream=False,
                    max_tokens=150,
                    extra_body= {
                        "chat_template_kwargs": {
                            "enable_thinking": False
                            }
                        }
                )

                text_response = completion.choices[0].message.content or ""
                prompt = extract_braced_content(text_response)

                if not prompt:
                    prompt = extract_fallback_text(text_response)

                if not prompt:
                    print(f"Item {x}, prompt attempt {attempt}: no valid prompt found")
                    print("RAW MODEL OUTPUT:")
                    print("-" * 60)
                    print(text_response)
                    print("-" * 60)
                    continue

                number_of_tokens = len(tokenizer.tokenize(prompt))

                if number_of_tokens <= 50 and '"' not in prompt:
                    item["prompt"] = prompt
                    print(f"Prompt Accepted for Item {x}: {prompt}")

                    with open(quotes_file_path, "w", encoding="utf-8") as json_file:
                        json.dump(quote_data, json_file, ensure_ascii=False, indent=2)

                    prompt_accepted = True
                    break
                else:
                    print(
                        f"Item {x}, prompt attempt {attempt}: rejected "
                        f"(tokens={number_of_tokens}, contains_double_quote={'\"' in prompt}) "
                        f"response={text_response!r}"
                    )

            if not prompt_accepted:
                print(f"Prompt generation failed for Item {x} after {MAX_PROMPT_RETRIES} attempts")

        # Generate hashtags only if missing or blank
        if "hashtags" not in item or is_blank(item["hashtags"]):
            hashtags_accepted = False

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

                completion = client.chat.completions.create(
                    model=MODEL_NAME,
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
                    ],
                    temperature=0.7,
                    stream=False,
                    max_tokens=150,
                    extra_body= {
                        "chat_template_kwargs": {
                            "enable_thinking": False
                            }
                        }
                )

                text_response = completion.choices[0].message.content or ""
                hashtags = extract_braced_content(text_response)

                if not hashtags:
                    hashtags = extract_fallback_text(text_response)

                if not hashtags:
                    print(f"Item {x}, hashtag attempt {attempt}: empty model response")
                    continue

                number_of_hashtags = hashtags.count("#")

                # Ask for <=20, but accept <=30
                if number_of_hashtags <= 30:
                    item["hashtags"] = hashtags
                    print(f"Hashtags Accepted for Item {x}: {hashtags}")

                    with open(quotes_file_path, "w", encoding="utf-8") as json_file:
                        json.dump(quote_data, json_file, ensure_ascii=False, indent=2)

                    hashtags_accepted = True
                    break
                else:
                    print(
                        f"Item {x}, hashtag attempt {attempt}: rejected "
                        f"(hashtags={number_of_hashtags}) response={text_response!r}"
                    )

            if not hashtags_accepted:
                print(f"Hashtag generation failed for Item {x} after {MAX_HASHTAG_RETRIES} attempts")

    except Exception as error:
        print(f"An error occurred with item {x}: {error}")
        continue