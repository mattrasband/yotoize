"""Audio file metadata utilities for mp3 and m4b formats."""

from pathlib import Path
import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4


class AudioLoader:
    """Handles basic audio file metadata."""
    
    def __init__(self, audio_path: str):
        """Initialize audio loader.
        
        Args:
            audio_path: Path to audio file (mp3 or m4b)
        """
        self.audio_path = Path(audio_path)
        if not self.audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        self.format = self.audio_path.suffix.lower()
        if self.format not in ['.mp3', '.m4b', '.m4a']:
            raise ValueError(f"Unsupported format: {self.format}. Supported: .mp3, .m4b, .m4a")
    
    def get_duration(self) -> float:
        """Get audio duration in seconds."""
        if self.format == '.mp3':
            audio_file = MP3(str(self.audio_path))
        else:  # m4b/m4a
            audio_file = MP4(str(self.audio_path))
        return audio_file.info.length
