import requests
import time
import hashlib
import hmac
import base64
import json

# Replace these with your ACRCloud project credentials
ACCESS_KEY = "GET FROM ENV"
ACCESS_SECRET = "GET FROM ENV"
AUDIO_FILE_PATH = ""  # Update this with your actual file path
HOST = "identify-us-west-2.acrcloud.com"  # Adjust if your region is different

def get_signature():
    timestamp = str(int(time.time()))
    http_method = "POST"
    http_uri = "/v1/identify"
    data_type = "audio"
    signature_version = "1"

    # Corrected string_to_sign format
    string_to_sign = f"{http_method}\n{http_uri}\n{ACCESS_KEY}\n{data_type}\n{signature_version}\n{timestamp}"

    # HMAC-SHA1 encoding
    sign = hmac.new(
        ACCESS_SECRET.encode("utf-8"),  # Secret key
        string_to_sign.encode("utf-8"),  # Data to sign
        hashlib.sha1
    ).digest()

    signature = base64.b64encode(sign).decode()

    return signature, timestamp

def recognize_music():
    url = f"https://{HOST}/v1/identify"
    
    sign, timestamp = get_signature()

    # Required form data
    data = {
        "access_key": ACCESS_KEY,
        "sample_bytes": str(len(open(AUDIO_FILE_PATH, "rb").read())),
        "timestamp": timestamp,
        "signature": sign,
        "signature_version": "1",
        "data_type": "audio"
    }

    files = {
        "sample": open(AUDIO_FILE_PATH, "rb")
    }

    response = requests.post(url, data=data, files=files)
    response_json = response.json()

    # Extract relevant info
    if "metadata" in response_json and "music" in response_json["metadata"]:
        song = response_json["metadata"]["music"][0]  # First recognized song
        title = song.get("title", "Unknown Title")
        artist = song["artists"][0]["name"] if "artists" in song else "Unknown Artist"
        
        # Extract streaming links if available
        external_metadata = song.get("external_metadata", {})
        spotify_link = external_metadata.get("spotify", {}).get("track", {}).get("id")
        deezer_link = external_metadata.get("deezer", {}).get("track", {}).get("id")
        youtube_link = external_metadata.get("youtube", {}).get("vid")

        # Convert IDs to full URLs
        spotify_url = f"https://open.spotify.com/track/{spotify_link}" if spotify_link else None
        deezer_url = f"https://www.deezer.com/track/{deezer_link}" if deezer_link else None
        youtube_url = f"https://www.youtube.com/watch?v={youtube_link}" if youtube_link else None

        # Print the results
        print("\n=== SONG INFO ===")
        print(f"🎵 Title: {title}")
        print(f"🎤 Artist: {artist}")
        if spotify_url:
            print(f"🎧 Spotify: {spotify_url}")
        if deezer_url:
            print(f"🎧 Deezer: {deezer_url}")
        if youtube_url:
            print(f"📺 YouTube: {youtube_url}")
        print("=================")

    else:
        print("❌ No song identified.")

    return response_json

# Run recognition
result = recognize_music()