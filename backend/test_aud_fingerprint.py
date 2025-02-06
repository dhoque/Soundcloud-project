import audresample
import audfactory
import librosa
import numpy as np

# Function to extract an audio fingerprint
def extract_fingerprint(audio_path):
    """ Extracts an audio fingerprint using AUD """
    y, sr = librosa.load(audio_path, sr=44100)
    
    # Resample audio for fingerprinting
    y_resampled = audresample.resample(y, sr, 16000)

    # Compute fingerprint
    fingerprint = audfactory.fingerprint(y_resampled, 16000)
    
    print(f"🔍 Fingerprint extracted for {audio_path}")
    return fingerprint

# Test with a segment
audio_file = "audio_segments/dj_set_segment_0_20.wav"
fingerprint = extract_fingerprint(audio_file)

print(f"📌 Extracted Fingerprint: {fingerprint}")