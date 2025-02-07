import os
import subprocess
import yt_dlp
import time
import hmac
import hashlib
import base64
import requests
import json
import shutil
import unicodedata
import concurrent.futures

from fuzzywuzzy import fuzz

from flask import Flask, request, jsonify
from pydub import AudioSegment
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy


app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tracklists.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Tracklist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    youtube_url = db.Column(db.String, unique=True, nullable=False)
    tracks = db.Column(db.Text, nullable=False)  # Store tracklist as JSON

    def __repr__(self):
        return f"<Tracklist {self.youtube_url}>"


# Load environment variables
load_dotenv()

# Retrieve ACRCloud credentials
ACR_ACCESS_KEY = os.getenv("ACR_ACCESS_KEY", "")
ACR_ACCESS_SECRET = os.getenv("ACR_ACCESS_SECRET", "")
ACR_HOST = os.getenv("ACR_HOST", "identify-us-west-2.acrcloud.com")

# Define storage directories
AUDIO_STORAGE_DIR = "audio_files"
SEGMENTS_DIR = "audio_segments"
os.makedirs(AUDIO_STORAGE_DIR, exist_ok=True)
os.makedirs(SEGMENTS_DIR, exist_ok=True)


def fix_encoding(text):
    """ Fixes character encoding issues in track titles and artist names """
    try:
        text = text.encode("latin1").decode("utf-8")  # Fix incorrect encoding
    except UnicodeEncodeError:
        pass  # If the conversion fails, just keep the original
    return unicodedata.normalize("NFKC", text)


def cleanup_audio_files():
    """ Deletes all locally stored audio files after database update. """
    print("🗑️ Cleaning up audio files...")

    # Remove entire audio storage directories
    if os.path.exists(AUDIO_STORAGE_DIR):
        shutil.rmtree(AUDIO_STORAGE_DIR)
        print(f"✅ Deleted folder: {AUDIO_STORAGE_DIR}")

    if os.path.exists(SEGMENTS_DIR):
        shutil.rmtree(SEGMENTS_DIR)
        print(f"✅ Deleted folder: {SEGMENTS_DIR}")

    # Recreate empty directories for next use
    os.makedirs(AUDIO_STORAGE_DIR, exist_ok=True)
    os.makedirs(SEGMENTS_DIR, exist_ok=True)


def download_youtube_audio(youtube_url):
    """ Downloads YouTube audio using yt-dlp with authentication cookies. """
    mp3_file_path = os.path.join(AUDIO_STORAGE_DIR, "dj_set.mp3")
    cookies_file = "/etc/secrets/cookies.txt"  # Use absolute path to root directory

    if not os.path.exists(cookies_file):
        print(f"⚠️ Warning: {cookies_file} not found. Trying without cookies.")

    ydl_opts = {
        'format': 'bestaudio/best',
        'extract_audio': True,
        'audio_format': 'mp3',
        'outtmpl': mp3_file_path,
        'quiet': True,
        'cookies': cookies_file if os.path.exists(cookies_file) else None
    }

    try:
        print("🔄 Downloading YouTube audio with authentication...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])
        print(f"✅ Audio downloaded: {mp3_file_path}")
    except Exception as e:
        print("❌ Error downloading YouTube audio:", e)
        return None, str(e)

    return mp3_file_path, None


def convert_mp3_to_wav(mp3_path):
    """ Converts an MP3 file to WAV. """
    wav_file_path = mp3_path.replace(".mp3", ".wav")

    command = ["ffmpeg", "-i", mp3_path, "-ac",
               "2", "-ar", "44100", "-y", wav_file_path]

    try:
        print("🔄 Converting MP3 to WAV...")
        subprocess.run(command, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        print(f"✅ WAV file created: {wav_file_path}")
    except subprocess.CalledProcessError as e:
        print("❌ Error converting MP3 to WAV:", e)
        return None, str(e)

    return wav_file_path, None


def segment_audio(wav_path, segment_length=20, overlap=0):
    """ Splits WAV into smaller segments. """
    print("🔄 Segmenting audio into smaller chunks...")
    segments = []
    audio = AudioSegment.from_wav(wav_path)

    step = (segment_length - overlap) * 1000  # Convert to milliseconds
    total_length = len(audio)

    for start in range(0, total_length, step):
        end = min(start + (segment_length * 1000), total_length)
        segment = audio[start:end]

        segment_filename = f"{os.path.basename(wav_path).replace('.wav', '')}_segment_{start//1000}_{end//1000}.wav"
        segment_path = os.path.join(SEGMENTS_DIR, segment_filename)
        segment.export(segment_path, format="wav")
        segments.append(segment_path)
        print(
            f"✅ Created segment: {segment_path} ({start//1000}s - {end//1000}s)")

        if end == total_length:
            break  # Stop if we've reached the end

    print(f"🎵 Total segments created: {len(segments)}")
    return segments


def get_acrcloud_signature():
    """ Generates a signature for ACRCloud authentication. """
    if not ACR_ACCESS_KEY or not ACR_ACCESS_SECRET:
        raise ValueError(
            "ACRCloud API credentials are missing. Check your .env file.")

    timestamp = str(int(time.time()))
    string_to_sign = f"POST\n/v1/identify\n{ACR_ACCESS_KEY}\naudio\n1\n{timestamp}"

    secret_bytes = ACR_ACCESS_SECRET.encode("utf-8")
    sign = hmac.new(secret_bytes, string_to_sign.encode(
        "utf-8"), hashlib.sha1).digest()

    return base64.b64encode(sign).decode(), timestamp


def recognize_track(file_path, max_retries=3):
    """ Sends a WAV segment to ACRCloud and returns the recognized track. """
    url = f"https://{ACR_HOST}/v1/identify"

    for _ in range(max_retries):
        try:
            sign, timestamp = get_acrcloud_signature()
        except ValueError as e:
            print("❌ ACRCloud API error:", e)
            return {"error": str(e)}

        print(f"🎧 Sending segment to ACRCloud: {file_path}...")

        # Get file size
        file_size = os.path.getsize(file_path)

        data = {
            "access_key": ACR_ACCESS_KEY,
            "data_type": "audio",
            "signature_version": "1",
            "signature": sign,
            "timestamp": timestamp,
            "sample_bytes": str(file_size)  # Required by ACRCloud
        }

        with open(file_path, "rb") as audio_file:
            response = requests.post(url, data=data, files={
                                     "sample": audio_file})

        if response:
            print(
                f"✅ Response received from ACRCloud for {file_path}: {response.json()}")
            return response.json()

        # If no result, wait and retry
        print(f"⚠️ No result from ACRCloud for {file_path}. Retrying...")
        time.sleep(2)  # Wait before retrying
    return None


def recognize_segment_parallel(segment):
    """ Recognize track with retry logic """
    for _ in range(3):  # Retry up to 3 times
        result = recognize_track(segment)
        if "metadata" in result:
            return result
        time.sleep(2)  # Wait before retrying
    return {"error": "Failed after 3 retries"}


def merge_consecutive_tracks(tracklist):
    """ 
    Merges similar track names, preferring the highest confidence version, and removes exact duplicates. 
    If the exact same title appears multiple times, it keeps the one with the highest confidence.
    """
    if not tracklist:
        return []

    best_tracks = {}  # Store the best version of each track

    for track in tracklist:
        title, confidence = track["title"], track["confidence"]

        # If exact title is already stored, update only if new confidence is higher
        if title in best_tracks:
            if confidence > best_tracks[title]["confidence"]:
                best_tracks[title] = track
            continue  # Skip further processing

        # Find if a similar track has already been seen
        existing_key = None
        for seen_title in best_tracks.keys():
            if fuzz.partial_ratio(title.lower(), seen_title.lower()) > 80:
                existing_key = seen_title
                break

        if existing_key:
            existing_track = best_tracks[existing_key]

            # Compare confidence scores
            if confidence > existing_track["confidence"]:
                best_tracks[existing_key] = track
            elif confidence == existing_track["confidence"]:
                # Prefer "Remix" if confidence scores are the same
                if "remix" in title.lower() and "remix" not in existing_track["title"].lower():
                    best_tracks[existing_key] = track
        else:
            best_tracks[title] = track  # No duplicate found, add new entry

    return list(best_tracks.values())


@app.route("/identify", methods=["POST"])
def identify():
    data = request.get_json()
    youtube_url = data.get("youtube_url")

    if not youtube_url:
        return jsonify({"error": "YouTube URL is required"}), 400

    # Step 1: Download YouTube audio
    mp3_path, error = download_youtube_audio(youtube_url)
    if error:
        return jsonify({"error": f"Failed to extract audio: {error}"}), 500

    # Step 2: Convert MP3 to WAV
    wav_path, error = convert_mp3_to_wav(mp3_path)
    if error:
        return jsonify({"error": f"Failed to convert audio to WAV: {error}"}), 500

    # Step 3: Segment WAV into smaller parts
    segment_paths = segment_audio(wav_path)

    # Step 4: Recognize each segment using ACRCloud
    tracklist = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(recognize_segment_parallel, segment_paths)

    for result in results:
        if "metadata" in result and "music" in result["metadata"]:
            track = max(result["metadata"]["music"],
                        key=lambda t: t.get("score", 0))
            title = track.get("title", "Unknown Title")
            artist = track["artists"][0]["name"] if "artists" in track else "Unknown Artist"
            confidence = track.get("score", 0)
            title = fix_encoding(title)
            artist = fix_encoding(artist)

            # Extract streaming links if available
            external_metadata = track.get("external_metadata", {})
            spotify_link = external_metadata.get(
                "spotify", {}).get("track", {}).get("id")
            deezer_link = external_metadata.get(
                "deezer", {}).get("track", {}).get("id")
            youtube_link = external_metadata.get("youtube", {}).get("vid")

            # Convert IDs to full URLs
            spotify_url = f"https://open.spotify.com/track/{spotify_link}" if spotify_link else None
            deezer_url = f"https://www.deezer.com/track/{deezer_link}" if deezer_link else None
            youtube_url = f"https://www.youtube.com/watch?v={youtube_link}" if youtube_link else None

            tracklist.append({
                "title": title,
                "artist": artist,
                "confidence": confidence,
                "spotify": spotify_url,
                "deezer": deezer_url,
                "youtube": youtube_url
            })

            print(
                f"🎶 Recognized: {title} - {artist} | Confidence: {confidence}%")
            print(f"   🎵 Spotify: {spotify_url}")
            print(f"   🎵 Deezer: {deezer_url}")
            print(f"   🎵 YouTube: {youtube_url}")

    # Merge consecutive duplicates
    tracklist = merge_consecutive_tracks(tracklist)

    tracklist_json = json.dumps(tracklist)

    with app.app_context():
        try:
            existing_entry = Tracklist.query.filter_by(
                youtube_url=youtube_url).first()
            if existing_entry:
                existing_entry.tracks = tracklist_json  # Update existing entry
                print(f"🔄 Updating existing tracklist for {youtube_url}")
            else:
                new_entry = Tracklist(
                    youtube_url=youtube_url, tracks=tracklist_json)
                db.session.add(new_entry)
                print(f"🆕 Adding new tracklist for {youtube_url}")

            db.session.commit()
            print("✅ Database successfully updated.")

        except Exception as e:
            print(f"❌ Database update failed: {e}")

    print("🎼 Final Cleaned Tracklist Generated:")
    for track in tracklist:
        print(f"   🎵 {track['title']} - {track['artist']}")

    cleanup_audio_files()

    return jsonify({
        "message": "Track recognition completed",
        "tracklist": tracklist
    })


if __name__ == "__main__":
    app.run(debug=True)
