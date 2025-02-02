#!/usr/bin/env python3
import os
import requests
import dotenv
import datetime
import json
import random
import datetime
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

dotenv.load_dotenv()

def add_to_log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.getenv('LOG_FILE'), 'a') as f:
        f.write(f'[{timestamp}] {message}\n')

def business_id_check():
    #get Business Account ID if missing
    if len(os.getenv('IG_BUSINESS_USER_ID')) == 0:
        endpoint_url = 'https://graph.facebook.com/v19.0/me/accounts'
        params = {
            'fields': 'instagram_business_account{id,username}',
            'access_token': os.getenv('ACCESS_TOKEN')
        }
        response = requests.get(endpoint_url, params=params)

        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            # Parse the JSON response
            ig_business_account = response.json()
            os.environ['IG_BUSINESS_USER_ID'] = ig_business_account['data'][0]['instagram_business_account']['id']
            dotenv.set_key('.env',"IG_BUSINESS_USER_ID", os.environ["IG_BUSINESS_USER_ID"])
            return True
        else:
            # Print the error message if the request was not successful
            print("Error Update User ID:", response.text)
            add_to_log("Error Update User ID:" + response.text)
            return False

def create_media_container(image_url, caption):
    if len(os.getenv('IG_BUSINESS_USER_ID')) == 0:
        if not business_id_check():
            print("No Valid Business ID")
            add_to_log("No Valid Business ID")
            return
    if os.getenv('ACCESS_TOKEN') is not None and os.getenv('ACCESS_TOKEN') != '' and os.getenv('ACCESS_TOKEN_EXPIRY') is not None and os.getenv('ACCESS_TOKEN_EXPIRY') != '' and datetime.datetime.strptime(os.getenv('ACCESS_TOKEN_EXPIRY'), '%Y-%m-%d %H:%M:%S.%f') > datetime.datetime.now():
        endpoint_url = 'https://graph.facebook.com/v20.0/' + os.getenv('IG_BUSINESS_USER_ID') + '/media'
        params = {
            'image_url': image_url,
            'caption':caption,
            'access_token': os.getenv('ACCESS_TOKEN')
        }
        # Send a GET request to the endpoint URL with the parameters
        response = requests.post(endpoint_url, params=params)
        
        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            print("Created media container: " + response.json()['id'])
            add_to_log("Created media container: " + response.json()['id'])
            return response.json()['id']
        else:
            # Print the error message if the request was not successful
            add_to_log("Error Creating Media Container:" + response.text)
            print("Error Creating Media Container:", response.text)
            return None
    else:
        return "No Valid Token"
    
def publish_media_container(creation_id):
    if len(os.getenv('IG_BUSINESS_USER_ID')) == 0:
        if not business_id_check():
            add_to_log("No Valid Business ID")
            print("No Valid Business ID")
            return
    if os.getenv('ACCESS_TOKEN') is not None and os.getenv('ACCESS_TOKEN') != '' and os.getenv('ACCESS_TOKEN_EXPIRY') is not None and os.getenv('ACCESS_TOKEN_EXPIRY') != '' and datetime.datetime.strptime(os.getenv('ACCESS_TOKEN_EXPIRY'), '%Y-%m-%d %H:%M:%S.%f') > datetime.datetime.now():
        endpoint_url = 'https://graph.facebook.com/v20.0/' + os.getenv('IG_BUSINESS_USER_ID') + '/media_publish'
        params = {
            'creation_id': creation_id,
            'access_token': os.getenv('ACCESS_TOKEN')
        }
        # Send a GET request to the endpoint URL with the parameters
        response = requests.post(endpoint_url, params=params)
        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            add_to_log("Published media container: " + creation_id)
            print("Published media container: " + creation_id)
            return response.json()
        else:
            # Print the error message if the request was not successful
            add_to_log("Error publishing media container:" + response.text)
            print("Error publishing media container:", response.text)
            send_email_alert("[Instagram AI Image] Facebook Token Issue", "There has been an Error:  " + response.text)
            return None
    else:
        add_to_log("No Valid Facebook Token")
        print("No Valid Facebook Token")
        send_email_alert("[Instagram AI Image] Facebook Token Expired", "There has been an Error: Your Facebook Token Expired on " + os.getenv('ACCESS_TOKEN_EXPIRY'))
        return "No Valid Token"
    
def upload_to_imgbb(image_path):
    if os.getenv('IMGBB_API_KEY') is not None and os.getenv('IMGBB_API_KEY') != '':

        with open(image_path, 'rb') as image_file:
            binary_data = image_file.read()
        
        endpoint_url = 'https://api.imgbb.com/1/upload'

        params = {
            'key': os.getenv('IMGBB_API_KEY'),
            'expiration':300
        }

        form_data = {
            'image': binary_data
        }
        # Send a GET request to the endpoint URL with the parameters
        response = requests.post(endpoint_url, params=params, files=form_data)
        
        # Check if the request was successful (status code 200)
        if response.status_code == 200:
            add_to_log("Uploaded image to imgbb: " + image_path)
            print("Uploaded image to imgbb: " + image_path)
            return response.json()['data']['image']['url']
        else:
            # Print the error message if the request was not successful
            add_to_log("Error Uploading Image: " + response.text)
            print("Error Uploading Image: ", response.text)
            return None
    else:
        send_email_alert("[Instagram AI Image] No Valid IMGBB API Key", "There has been an Error: No Valid IMGBB API Key")
        return "No Valid IMGBB API Key"

def post_random_photo():
    quotes_file_path = os.getenv('QUOTES_FILE_PATH')
    with open(quotes_file_path, 'r') as json_file:
        quote_data = json.load(json_file)
    while True:
        quote_choice = random.randint(0,len(quote_data)-1)
        file_path = os.path.join("output","images_text_overlay",quote_data[quote_choice]['_id']+"1024x1024.jpeg")
        if os.path.isfile(file_path):
            upload_url = upload_to_imgbb(file_path)
            container_id = create_media_container(upload_url, quote_data[quote_choice]['hashtags'])
            response = publish_media_container(container_id)
            if response == "No Valid Token" or response == None:
                break
            if 'post_count' not in quote_data[quote_choice]:
                quote_data[quote_choice]['post_count'] = '1'
            else:
                quote_data[quote_choice]['post_count'] = str(int(quote_data[quote_choice]['post_count'])+1)
            add_to_log("Posted image ID: "+ quote_data[quote_choice]['_id'])
            print("Posted image ID: "+ quote_data[quote_choice]['_id'])
            break
        else:
            add_to_log("Image file path not valid: " + file_path)
            print("Image file path not valid: " + file_path)
    with open(quotes_file_path, 'w') as json_file:
        json.dump(quote_data, json_file)

def send_email_alert(subject, body):
    try:
        # Connect to SMTP server
        server = smtplib.SMTP(os.getenv('SMTP_SERVER'), os.getenv('SMTP_PORT'))
        server.starttls()
        server.login(os.getenv('SENDER_EMAIL'), os.getenv('SENDER_PASSWORD'))

        # Compose email message
        msg = MIMEMultipart()
        msg['From'] = os.getenv('SENDER_EMAIL')
        msg['To'] = os.getenv('RECIPIENT_EMAIL')
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Send email
        server.sendmail(os.getenv('SENDER_EMAIL'), os.getenv('RECIPIENT_EMAIL'), msg.as_string())

        # Close connection
        server.quit()

        print("Email alert sent with subject: " + subject)
        add_to_log("Email alert sent with subject: " + subject)
    except Exception as e:
        print('Error sending email notification:', e)

post_random_photo()

time.sleep(60*60*3)