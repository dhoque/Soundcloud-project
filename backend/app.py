import os
import subprocess
import yt_dlp
import time
import hmac
import hashlib
import base64
import requests
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

def download_youtube_audio(youtube_url):
    """ Downloads YouTube audio and saves it as MP3 locally. """
    mp3_file_path = os.path.join(AUDIO_STORAGE_DIR, "dj_set.mp3")

    ydl_opts = {
        'format': 'bestaudio/best',
        'extract_audio': True,
        'audio_format': 'mp3',
        'outtmpl': mp3_file_path,
        'quiet': True
    }

    try:
        print("🔄 Downloading YouTube audio...")
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

    command = ["ffmpeg", "-i", mp3_path, "-ac", "2", "-ar", "44100", "-y", wav_file_path]

    try:
        print("🔄 Converting MP3 to WAV...")
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"✅ WAV file created: {wav_file_path}")
    except subprocess.CalledProcessError as e:
        print("❌ Error converting MP3 to WAV:", e)
        return None, str(e)

    return wav_file_path, None

def segment_audio(wav_path, segment_length=20, overlap=5):
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
        print(f"✅ Created segment: {segment_path} ({start//1000}s - {end//1000}s)")

        if end == total_length:
            break  # Stop if we've reached the end

    print(f"🎵 Total segments created: {len(segments)}")
    return segments

def get_acrcloud_signature():
    """ Generates a signature for ACRCloud authentication. """
    if not ACR_ACCESS_KEY or not ACR_ACCESS_SECRET:
        raise ValueError("ACRCloud API credentials are missing. Check your .env file.")

    timestamp = str(int(time.time()))
    string_to_sign = f"POST\n/v1/identify\n{ACR_ACCESS_KEY}\naudio\n1\n{timestamp}"

    secret_bytes = ACR_ACCESS_SECRET.encode("utf-8")
    sign = hmac.new(secret_bytes, string_to_sign.encode("utf-8"), hashlib.sha1).digest()

    return base64.b64encode(sign).decode(), timestamp

def recognize_track(file_path):
    """ Sends a WAV segment to ACRCloud and returns the recognized track. """
    url = f"https://{ACR_HOST}/v1/identify"

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
        response = requests.post(url, data=data, files={"sample": audio_file})
    
    print(f"✅ Response received from ACRCloud for {file_path}: {response.json()}")
    return response.json()

def merge_consecutive_tracks(tracklist):
    """ Merges consecutive duplicate tracks in the tracklist. """
    if not tracklist:
        return []

    merged_tracks = [tracklist[0]]  # Start with the first track

    for i in range(1, len(tracklist)):
        if tracklist[i]["title"] != merged_tracks[-1]["title"]:  # Only add if different
            merged_tracks.append(tracklist[i])

    return merged_tracks

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
    for segment in segment_paths:
        result = recognize_track(segment)
        if "metadata" in result and "music" in result["metadata"]:
            track = result["metadata"]["music"][0]
            title = track.get("title", "Unknown Title")
            artist = track["artists"][0]["name"] if "artists" in track else "Unknown Artist"

            tracklist.append({"title": title, "artist": artist})
            print(f"🎶 Recognized: {title} - {artist}")

    # Merge consecutive duplicates
    tracklist = merge_consecutive_tracks(tracklist)

    tracklist_json = json.dumps(tracklist) 
    with app.app_context():
            existing_entry = Tracklist.query.filter_by(youtube_url=youtube_url).first()
            if existing_entry:
                existing_entry.tracks = tracklist_json  # Update existing entry
            else:
                new_entry = Tracklist(youtube_url=youtube_url, tracks=tracklist_json)
                db.session.add(new_entry)

            db.session.commit()

    print("🎼 Final Cleaned Tracklist Generated:")
    for track in tracklist:
        print(f"   🎵 {track['title']} - {track['artist']}")

    return jsonify({
        "message": "Track recognition completed",
        "tracklist": tracklist
    })

if __name__ == "__main__":
    app.run(debug=True)
