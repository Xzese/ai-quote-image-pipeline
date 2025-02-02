import json
from collections import Counter

# Path to the JSON file
json_file_path = r"Z:\Coding\Production\instagram-ai-images\output\quotes.json"

try:
    # Load the JSON file
    with open(json_file_path, 'r') as file:
        data = json.load(file)
    
    # Ensure the data is a list
    if not isinstance(data, list):
        raise ValueError("The JSON file does not contain an array of objects.")

    # Extract post_count values, treating missing or zero values as "0"
    post_counts = [
        int(obj.get("post_count", 0)) if obj.get("post_count") else 0
        for obj in data
    ]

    # Group and count the occurrences of each post_count
    post_count_counts = Counter(post_counts)

    # Sort the counts by post_count value in descending order
    sorted_counts = sorted(post_count_counts.items(), key=lambda x: x[0], reverse=True)

    # Print the results
    print("Counts of post_count values (sorted by post_count descending):")
    for post_count, count in sorted_counts:
        print(f"Post Count: {post_count}, Count: {count}")

except FileNotFoundError:
    print(f"Error: The file at {json_file_path} was not found.")
except json.JSONDecodeError:
    print("Error: Failed to decode JSON. Please check the file's contents.")
except ValueError as ve:
    print(f"Error: {ve}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
