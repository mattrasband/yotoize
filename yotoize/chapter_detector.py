"""Chapter extraction from audio file metadata."""

import json
import subprocess
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from .ffmpeg_tools import ffprobe_executable


@dataclass
class Chapter:
    """Represents a chapter from audio file metadata."""
    start_time: float
    end_time: Optional[float]
    confidence: float
    title: Optional[str] = None
    
    @property
    def duration(self) -> Optional[float]:
        """Get chapter duration in seconds."""
        if self.end_time is None:
            return None
        return self.end_time - self.start_time


def extract_chapters(audio_path: str) -> List[Chapter]:
    """Extract chapters from file metadata using ffprobe.
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        List of chapters, or empty list if no chapters found
    """
    try:
        # Use ffprobe to get chapter information
        cmd = [
            ffprobe_executable(),
            '-v', 'error',
            '-show_chapters',
            '-of', 'json',
            audio_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        
        data = json.loads(result.stdout)
        
        if 'chapters' not in data or not data['chapters']:
            return []
        
        chapters = []
        for chapter_data in data['chapters']:
            start_time = float(chapter_data.get('start_time', 0))
            end_time = float(chapter_data.get('end_time', 0))
            
            # Extract title from tags if available
            title = None
            if 'tags' in chapter_data and 'title' in chapter_data['tags']:
                title = chapter_data['tags']['title']
            
            chapters.append(Chapter(
                start_time=start_time,
                end_time=end_time,
                confidence=1.0,  # Metadata is 100% reliable
                title=title
            ))
        
        return chapters
        
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        # ffprobe failed or no chapters found
        return []
