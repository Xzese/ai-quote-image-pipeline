import json
import dotenv
import os
import requests
from transformers import AutoTokenizer
from openai import OpenAI

dotenv.load_dotenv()

quotes_file_path = os.getenv('QUOTES_FILE_PATH')
tokenizer = AutoTokenizer.from_pretrained('bert-base-uncased')
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

with open(quotes_file_path, 'r') as json_file:
    quote_data = json.load(json_file)

with open("output/quotes.json.backup", 'r') as old_json_file:
    old_quote_data = json.load(old_json_file)

for x in range(len(quote_data)):
    prompt_accepted = False
    try:
        if 'prompt' not in quote_data[x]:
            while prompt_accepted != True:
                chat_message = "Generate an image generation prompt for a diffuser model that would match the tone of the following quote overlayed on top of the image. Do not mention the author or that the image will have a quote overlayed. Do not include any people in the image. The prompt cannot be longer than 50 tokens.\nQuote:\""+quote_data[x]['content']+"\"\nAuthor:\""+quote_data[x]['author']+"\""
                print("Generating for Item " + str(x))
                completion = client.chat.completions.create(
                model="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
                messages=[
                    {"role": "system", "content": f"Answer as though you are an expert at creating image generation prompts. Please format the reply with the prompt contained withing curly braces. The prompt must be at or below 50 tokens."},
                    {"role": "user", "content": chat_message}
                ],
                temperature=0.7,
                stream=False,
                max_tokens=150
                )
                text_response = completion.choices[0].message.content
                start_char = text_response.find("{")
                end_char = text_response.find("}")
                if start_char != -1 and end_char != -1:
                    prompt = text_response[start_char+1:end_char]
                    number_of_tokens = len(tokenizer.tokenize(prompt)) + 1
                if number_of_tokens <= 75 and prompt.find("\"") == -1:
                    quote_data[x]['prompt'] = prompt
                    print("Prompt Accepted for Item " + str(x))
                    prompt_accepted = True
                    with open(quotes_file_path, 'w') as json_file:
                        json.dump(quote_data, json_file)
    except Exception as error:
        print(f"An error occured with item {x}: {error}")
        continue
