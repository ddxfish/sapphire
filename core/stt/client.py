import requests
from typing import Optional
import os
import sys
import config

class WhisperClient:
    """Client for interacting with the speech-to-text transcription server."""
    
    def __init__(self, server_url: str = config.STT_SERVER_URL):
        """Initialize the WhisperClient with server URL from config."""
        self.server_url = server_url

    def transcribe_file(self, audio_file: str) -> Optional[str]:
        """
        Send an audio file to the server for transcription.
        Args: audio_file (str): Path to the audio file   
        Returns: Optional[str]: Transcribed text, or None if transcription failed
        """
        try:
            with open(audio_file, 'rb') as f:
                response = requests.post(f"{self.server_url}/transcribe", 
                                        files={'audio': f})
                response.raise_for_status()
                return response.json()["text"]
        except Exception as e:
            print(f"Transcription error: {e}")
            return None