"""Metadata extraction and preservation utilities."""

import subprocess
import json
from pathlib import Path
from typing import Dict, Optional, Any
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.id3 import ID3NoHeaderError

from .ffmpeg_tools import ffprobe_executable


class MetadataExtractor:
    """Extract metadata from audio files."""
    
    def __init__(self, audio_path: str):
        """Initialize metadata extractor.
        
        Args:
            audio_path: Path to audio file
        """
        self.audio_path = Path(audio_path)
        self.format = self.audio_path.suffix.lower()
    
    def extract_all_metadata(self) -> Dict[str, Any]:
        """Extract all available metadata from the audio file.
        
        Returns:
            Dictionary containing metadata fields
        """
        metadata = {}
        
        if self.format == '.mp3':
            metadata.update(self._extract_mp3_metadata())
        elif self.format in ['.m4b', '.m4a']:
            metadata.update(self._extract_m4b_metadata())
        
        # Also try ffprobe for additional metadata
        ffprobe_metadata = self._extract_ffprobe_metadata()
        if ffprobe_metadata:
            metadata.update(ffprobe_metadata)
        
        return metadata
    
    def _extract_mp3_metadata(self) -> Dict[str, Any]:
        """Extract metadata from MP3 file."""
        metadata = {}
        try:
            audio_file = MP3(str(self.audio_path))
            
            # Extract common ID3 tags
            if audio_file.tags:
                tags = audio_file.tags
                
                # Title
                if 'TIT2' in tags:
                    metadata['title'] = str(tags['TIT2'][0])
                elif 'TIT1' in tags:
                    metadata['title'] = str(tags['TIT1'][0])
                
                # Artist
                if 'TPE1' in tags:
                    metadata['artist'] = str(tags['TPE1'][0])
                elif 'TPE2' in tags:
                    metadata['artist'] = str(tags['TPE2'][0])
                
                # Album
                if 'TALB' in tags:
                    metadata['album'] = str(tags['TALB'][0])
                
                # Year
                if 'TDRC' in tags:
                    year = tags['TDRC'][0]
                    if hasattr(year, 'year'):
                        metadata['year'] = str(year.year)
                    else:
                        metadata['year'] = str(year)
                elif 'TYER' in tags:
                    metadata['year'] = str(tags['TYER'][0])
                
                # Genre
                if 'TCON' in tags:
                    metadata['genre'] = str(tags['TCON'][0])
                
                # Track number
                if 'TRCK' in tags:
                    metadata['track'] = str(tags['TRCK'][0])
                
                # Album artist
                if 'TPE2' in tags:
                    metadata['albumartist'] = str(tags['TPE2'][0])
                
                # Comment
                if 'COMM' in tags:
                    metadata['comment'] = str(tags['COMM'][0].text)
                
        except ID3NoHeaderError:
            pass
        except Exception:
            pass
        
        return metadata
    
    def _extract_m4b_metadata(self) -> Dict[str, Any]:
        """Extract metadata from M4B/M4A file."""
        metadata = {}
        try:
            audio_file = MP4(str(self.audio_path))
            
            if audio_file.tags:
                tags = audio_file.tags
                
                # Title
                if '\xa9nam' in tags:
                    metadata['title'] = str(tags['\xa9nam'][0])
                
                # Artist
                if '\xa9ART' in tags:
                    metadata['artist'] = str(tags['\xa9ART'][0])
                
                # Album
                if '\xa9alb' in tags:
                    metadata['album'] = str(tags['\xa9alb'][0])
                
                # Year
                if '\xa9day' in tags:
                    year = tags['\xa9day'][0]
                    if isinstance(year, str):
                        metadata['year'] = year[:4] if len(year) >= 4 else year
                    else:
                        metadata['year'] = str(year)
                
                # Genre
                if '\xa9gen' in tags:
                    metadata['genre'] = str(tags['\xa9gen'][0])
                
                # Track number
                if 'trkn' in tags:
                    track_data = tags['trkn'][0]
                    if isinstance(track_data, tuple):
                        metadata['track'] = str(track_data[0])
                    else:
                        metadata['track'] = str(track_data)
                
                # Album artist
                if 'aART' in tags:
                    metadata['albumartist'] = str(tags['aART'][0])
                
                # Comment
                if '\xa9cmt' in tags:
                    metadata['comment'] = str(tags['\xa9cmt'][0])
                
        except Exception:
            pass
        
        return metadata
    
    def _extract_ffprobe_metadata(self) -> Dict[str, Any]:
        """Extract metadata using ffprobe."""
        metadata = {}
        try:
            cmd = [
                ffprobe_executable(),
                '-v', 'error',
                '-show_entries', 'format_tags',
                '-of', 'json',
                str(self.audio_path)
            ]
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True
            )
            
            data = json.loads(result.stdout)
            if 'format' in data and 'tags' in data['format']:
                tags = data['format']['tags']
                
                # Map common tag names
                tag_mapping = {
                    'title': 'title',
                    'TITLE': 'title',
                    'artist': 'artist',
                    'ARTIST': 'artist',
                    'album': 'album',
                    'ALBUM': 'album',
                    'date': 'year',
                    'DATE': 'year',
                    'year': 'year',
                    'YEAR': 'year',
                    'genre': 'genre',
                    'GENRE': 'genre',
                    'track': 'track',
                    'TRACK': 'track',
                    'album_artist': 'albumartist',
                    'ALBUM_ARTIST': 'albumartist',
                    'comment': 'comment',
                    'COMMENT': 'comment',
                }
                
                for tag_key, metadata_key in tag_mapping.items():
                    if tag_key in tags and metadata_key not in metadata:
                        metadata[metadata_key] = tags[tag_key]
                
        except Exception:
            pass
        
        return metadata
    
    def extract_cover_art(self, output_path: Optional[Path] = None) -> Optional[Path]:
        """Extract cover art from audio file.
        
        Args:
            output_path: Optional path to save cover art. If None, saves to same directory as audio file.
        
        Returns:
            Path to extracted cover art file, or None if not found
        """
        try:
            if self.format == '.mp3':
                return self._extract_mp3_cover(output_path)
            elif self.format in ['.m4b', '.m4a']:
                return self._extract_m4b_cover(output_path)
        except Exception:
            pass
        
        return None
    
    def _extract_mp3_cover(self, output_path: Optional[Path] = None) -> Optional[Path]:
        """Extract cover art from MP3 file."""
        try:
            audio_file = MP3(str(self.audio_path))
            
            if audio_file.tags:
                # Look for APIC (album art) frames
                for key in audio_file.tags.keys():
                    if key.startswith('APIC'):
                        apic = audio_file.tags[key]
                        if apic and apic.data:
                            if output_path is None:
                                output_path = self.audio_path.parent / 'cover.jpg'
                            
                            output_path.write_bytes(apic.data)
                            return output_path
        except Exception:
            pass
        
        return None
    
    def _extract_m4b_cover(self, output_path: Optional[Path] = None) -> Optional[Path]:
        """Extract cover art from M4B/M4A file."""
        try:
            audio_file = MP4(str(self.audio_path))
            
            if audio_file.tags and 'covr' in audio_file.tags:
                cover_data = audio_file.tags['covr'][0]
                
                if output_path is None:
                    # Determine extension from cover data
                    ext = 'jpg'  # Default
                    if hasattr(cover_data, 'imageformat'):
                        if cover_data.imageformat == 'PNG':
                            ext = 'png'
                    output_path = self.audio_path.parent / f'cover.{ext}'
                
                output_path.write_bytes(bytes(cover_data))
                return output_path
        except Exception:
            pass
        
        return None


def build_ffmpeg_metadata_args(metadata: Dict[str, Any], format: str) -> list:
    """Build ffmpeg metadata arguments from metadata dictionary.
    
    Args:
        metadata: Dictionary of metadata fields
        format: Output format (mp3, m4b, m4a, wav)
    
    Returns:
        List of ffmpeg metadata arguments
    """
    args = []
    
    # Map metadata keys to ffmpeg metadata keys
    if format in ['m4b', 'm4a']:
        # M4B/M4A metadata keys
        mapping = {
            'title': 'title',
            'artist': 'artist',
            'album': 'album',
            'year': 'date',
            'genre': 'genre',
            'track': 'track',
            'albumartist': 'album_artist',
            'comment': 'comment',
        }
    elif format == 'mp3':
        # MP3 metadata keys
        mapping = {
            'title': 'title',
            'artist': 'artist',
            'album': 'album',
            'year': 'date',
            'genre': 'genre',
            'track': 'track',
            'albumartist': 'album_artist',
            'comment': 'comment',
        }
    else:
        # WAV and other formats have limited metadata support
        mapping = {
            'title': 'title',
            'artist': 'artist',
            'album': 'album',
            'comment': 'comment',
        }
    
    for key, ffmpeg_key in mapping.items():
        if key in metadata and metadata[key]:
            value = str(metadata[key])
            # Escape special characters in metadata values
            value = value.replace('=', '\\=').replace(';', '\\;').replace('#', '\\#')
            args.extend(['-metadata', f'{ffmpeg_key}={value}'])
    
    return args
