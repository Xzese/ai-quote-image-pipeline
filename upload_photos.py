#!/usr/bin/env python3
import os
import requests
import dotenv
import datetime

dotenv.load_dotenv()

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
            return False

def create_media_container(image_url, caption):
    if len(os.getenv('IG_BUSINESS_USER_ID')) == 0:
        if not business_id_check():
            print("No Valid Business ID")
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
            print(response.json())
            return response.json()
        else:
            # Print the error message if the request was not successful
            print("Error Update IG Stats:", response.text)
            return None
    else:
        return "No Valid Token"
    
def publish_media_container(creation_id):
    if len(os.getenv('IG_BUSINESS_USER_ID')) == 0:
        if not business_id_check():
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
            print(response.json())
        else:
            # Print the error message if the request was not successful
            print("Error Update IG Stats:", response.text)
            return None

        return "IG Stats Updated Successfully"
    else:
        return "No Valid Token"