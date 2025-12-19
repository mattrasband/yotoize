"""Playlist generation utilities."""

from pathlib import Path
from typing import List, Optional
from .chapter_detector import Chapter


def generate_m3u_playlist(
    chapters: List[Chapter],
    output_dir: Path,
    output_format: str,
    filename_pattern: str = "{number:02d} - {title}",
    metadata: Optional[dict] = None,
    playlist_name: Optional[str] = None,
) -> Path:
    """Generate M3U playlist file for chapters.
    
    Args:
        chapters: List of chapters
        output_dir: Directory where chapter files are located
        output_format: File format extension (mp3, m4b, etc.)
        filename_pattern: Pattern used to generate filenames
        metadata: Optional metadata dictionary
        playlist_name: Name for playlist file (without extension)
    
    Returns:
        Path to generated playlist file
    """
    from .utils import format_filename
    
    if playlist_name is None:
        if metadata and 'album' in metadata:
            playlist_name = metadata['album']
        else:
            playlist_name = 'playlist'
    
    playlist_path = output_dir / f"{playlist_name}.m3u"
    
    lines = ['#EXTM3U']
    
    for i, chapter in enumerate(chapters, 1):
        # Format filename
        filename = format_filename(filename_pattern, chapter, i, len(chapters), metadata)
        filename = f"{filename}.{output_format}"
        
        # Get duration in seconds
        duration = int(chapter.duration) if chapter.duration else 0
        
        # Build EXTINF line
        title = chapter.title or f"Chapter {i}"
        extinf = f"#EXTINF:{duration},{title}"
        
        lines.append(extinf)
        lines.append(filename)
    
    # Write playlist file
    with open(playlist_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
        f.write('\n')
    
    return playlist_path
