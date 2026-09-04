"""Utility functions for yotoize."""

import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from .chapter_detector import Chapter
from .ffmpeg_tools import ffmpeg_executable


def parse_chapter_range(chapter_str: str, total_chapters: int) -> List[int]:
    """Parse chapter range string into list of chapter indices (0-based).
    
    Examples:
        "1,3,5" -> [0, 2, 4]
        "1-5" -> [0, 1, 2, 3, 4]
        "1-3,5,7-9" -> [0, 1, 2, 4, 6, 7, 8]
    
    Args:
        chapter_str: String specifying chapters (e.g., "1,3,5-7")
        total_chapters: Total number of chapters
    
    Returns:
        List of 0-based chapter indices
    """
    indices = []
    parts = chapter_str.split(',')
    
    for part in parts:
        part = part.strip()
        if '-' in part:
            # Range
            start_str, end_str = part.split('-', 1)
            start = int(start_str.strip()) - 1  # Convert to 0-based
            end = int(end_str.strip())  # Keep 1-based for range
            
            if start < 0:
                start = 0
            if end > total_chapters:
                end = total_chapters
            
            indices.extend(range(start, end))
        else:
            # Single chapter
            idx = int(part.strip()) - 1  # Convert to 0-based
            if 0 <= idx < total_chapters:
                indices.append(idx)
    
    return sorted(set(indices))  # Remove duplicates and sort


def filter_chapters(
    chapters: List[Chapter],
    chapter_range: Optional[str] = None,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
    title_pattern: Optional[str] = None,
) -> List[Chapter]:
    """Filter chapters based on various criteria.
    
    Args:
        chapters: List of chapters to filter
        chapter_range: Chapter range string (e.g., "1,3,5-7")
        min_duration: Minimum chapter duration in seconds
        max_duration: Maximum chapter duration in seconds
        title_pattern: Regex pattern to match chapter titles
    
    Returns:
        Filtered list of chapters
    """
    filtered = chapters
    
    # Filter by chapter range
    if chapter_range:
        indices = parse_chapter_range(chapter_range, len(chapters))
        filtered = [chapters[i] for i in indices if 0 <= i < len(chapters)]
    
    # Filter by duration
    if min_duration is not None:
        filtered = [ch for ch in filtered if ch.duration and ch.duration >= min_duration]
    
    if max_duration is not None:
        filtered = [ch for ch in filtered if ch.duration and ch.duration <= max_duration]
    
    # Filter by title pattern
    if title_pattern:
        pattern = re.compile(title_pattern, re.IGNORECASE)
        filtered = [ch for ch in filtered if ch.title and pattern.search(ch.title)]
    
    return filtered


def format_filename(pattern: str, chapter: Chapter, chapter_num: int, total_chapters: int, 
                   metadata: Optional[dict] = None) -> str:
    """Format filename using pattern string.
    
    Supported placeholders:
        {number} - Chapter number (1-based)
        {number:02d} - Chapter number with zero-padding (e.g., 02, 03)
        {title} - Chapter title
        {artist} - Artist from metadata
        {album} - Album from metadata
        {year} - Year from metadata
    
    Args:
        pattern: Filename pattern string
        chapter: Chapter object
        chapter_num: Chapter number (1-based)
        total_chapters: Total number of chapters
        metadata: Optional metadata dictionary
    
    Returns:
        Formatted filename (without extension)
    """
    # Calculate padding needed for chapter number
    num_digits = len(str(total_chapters))
    
    # Replace placeholders
    filename = pattern
    
    # {number} or {number:02d} format
    if '{number:' in filename:
        # Extract format specifier
        match = re.search(r'\{number:(\d+)d\}', filename)
        if match:
            padding = int(match.group(1))
            # Use the matched string directly for replacement
            matched_str = match.group(0)
            filename = filename.replace(matched_str, f"{chapter_num:0{padding}d}")
    else:
        filename = filename.replace('{number}', str(chapter_num))
    
    # {title}
    if '{title}' in filename:
        title = chapter.title or f"Chapter {chapter_num}"
        # Sanitize title for filename
        title = re.sub(r'[<>:"/\\|?*]', '_', title)
        title = title.strip(' .')
        if len(title) > 200:
            title = title[:200]
        filename = filename.replace('{title}', title)
    
    # Metadata placeholders
    if metadata:
        for key in ['artist', 'album', 'year', 'genre']:
            placeholder = f"{{{key}}}"
            if placeholder in filename:
                value = metadata.get(key, '')
                # Sanitize
                value = re.sub(r'[<>:"/\\|?*]', '_', str(value))
                value = value.strip(' .')
                filename = filename.replace(placeholder, value)
    
    return filename


def detect_silence(audio_path: Path, start_time: float, end_time: float, 
                  threshold: float = -50.0, duration: float = 0.5) -> Tuple[float, float]:
    """Detect silence at the beginning and end of an audio segment.
    
    Args:
        audio_path: Path to audio file
        start_time: Start time of segment
        end_time: End time of segment
        threshold: Silence threshold in dB (default: -50dB)
        duration: Minimum duration of silence to detect (default: 0.5s)
    
    Returns:
        Tuple of (trim_start, trim_end) - additional time to trim from start and end
    """
    try:
        import subprocess
        
        # Use ffmpeg silencedetect filter
        cmd = [
            ffmpeg_executable(),
            '-ss', str(start_time),
            '-i', str(audio_path),
            '-t', str(end_time - start_time),
            '-af', f'silencedetect=noise={threshold}dB:d={duration}',
            '-f', 'null',
            '-'
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        trim_start = 0.0
        trim_end = 0.0
        
        # Parse silencedetect output
        stderr = result.stderr
        silence_start_times = []
        silence_end_times = []
        
        for line in stderr.split('\n'):
            if 'silence_start' in line:
                match = re.search(r'silence_start: ([\d.]+)', line)
                if match:
                    silence_start_times.append(float(match.group(1)))
            elif 'silence_end' in line:
                match = re.search(r'silence_end: ([\d.]+)', line)
                if match:
                    silence_end_times.append(float(match.group(1)))
        
        # Check for silence at the beginning
        if silence_start_times and silence_start_times[0] < duration:
            trim_start = silence_end_times[0] if silence_end_times else duration
        
        # Check for silence at the end
        segment_duration = end_time - start_time
        if silence_end_times and silence_end_times[-1] > segment_duration - duration:
            trim_end = segment_duration - silence_start_times[-1] if silence_start_times else duration
        
        return (trim_start, trim_end)
        
    except Exception:
        # If detection fails, return no trimming
        return (0.0, 0.0)


def validate_chapters(chapters: List[Chapter]) -> List[str]:
    """Validate chapters for issues like overlaps, gaps, missing end times.
    
    Args:
        chapters: List of chapters to validate
    
    Returns:
        List of warning/error messages
    """
    warnings = []
    
    for i, chapter in enumerate(chapters):
        # Check for missing end time
        if chapter.end_time is None:
            warnings.append(f"Chapter {i+1} has no end time")
            continue
        
        # Check for invalid duration
        if chapter.duration and chapter.duration <= 0:
            warnings.append(f"Chapter {i+1} has invalid duration: {chapter.duration}")
        
        # Check for overlaps with next chapter
        if i < len(chapters) - 1:
            next_chapter = chapters[i + 1]
            if next_chapter.start_time < chapter.end_time:
                overlap = chapter.end_time - next_chapter.start_time
                warnings.append(
                    f"Chapters {i+1} and {i+2} overlap by {overlap:.2f} seconds"
                )
            elif next_chapter.start_time > chapter.end_time:
                gap = next_chapter.start_time - chapter.end_time
                if gap > 1.0:  # Only warn about gaps > 1 second
                    warnings.append(
                        f"Gap of {gap:.2f} seconds between chapters {i+1} and {i+2}"
                    )
    
    return warnings


def rename_chapters(chapters: List[Chapter], rename_map: Dict[int, str]) -> List[Chapter]:
    """Rename chapters based on a mapping.
    
    Args:
        chapters: List of chapters
        rename_map: Dictionary mapping chapter numbers (1-based) to new titles
    
    Returns:
        List of chapters with renamed titles
    """
    renamed = []
    for i, chapter in enumerate(chapters):
        chapter_num = i + 1
        if chapter_num in rename_map:
            # Create new chapter with renamed title
            renamed_chapter = Chapter(
                start_time=chapter.start_time,
                end_time=chapter.end_time,
                confidence=chapter.confidence,
                title=rename_map[chapter_num]
            )
            renamed.append(renamed_chapter)
        else:
            renamed.append(chapter)
    
    return renamed


def interactive_chapter_renaming(chapters: List[Chapter]) -> Dict[int, str]:
    """Interactive chapter renaming.
    
    Args:
        chapters: List of chapters
    
    Returns:
        Dictionary mapping chapter numbers to new titles
    """
    rename_map = {}
    
    print("\nInteractive Chapter Renaming:")
    print("Enter chapter numbers and new titles (e.g., '1:New Title' or 'q' to finish)")
    
    while True:
        try:
            entry = input("\nRename (number:title or 'q' to finish): ").strip()
            
            if entry.lower() == 'q':
                break
            
            if ':' not in entry:
                print("Invalid format. Use 'number:title'")
                continue
            
            chapter_num_str, new_title = entry.split(':', 1)
            chapter_num = int(chapter_num_str.strip())
            
            if 1 <= chapter_num <= len(chapters):
                rename_map[chapter_num] = new_title.strip()
                print(f"Chapter {chapter_num} will be renamed to: {new_title.strip()}")
            else:
                print(f"Invalid chapter number. Must be between 1 and {len(chapters)}")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            break
    
    return rename_map


def calculate_statistics(chapters: List[Chapter]) -> dict:
    """Calculate statistics about chapters.
    
    Args:
        chapters: List of chapters
    
    Returns:
        Dictionary with statistics
    """
    durations = [ch.duration for ch in chapters if ch.duration]
    
    if not durations:
        return {
            'total_chapters': len(chapters),
            'total_duration': 0.0,
            'average_duration': 0.0,
            'shortest_chapter': None,
            'longest_chapter': None,
        }
    
    total_duration = sum(durations)
    avg_duration = total_duration / len(durations)
    
    # Find shortest and longest chapters
    shortest_idx = durations.index(min(durations))
    longest_idx = durations.index(max(durations))
    
    return {
        'total_chapters': len(chapters),
        'total_duration': total_duration,
        'average_duration': avg_duration,
        'shortest_chapter': {
            'number': shortest_idx + 1,
            'duration': min(durations),
            'title': chapters[shortest_idx].title,
        },
        'longest_chapter': {
            'number': longest_idx + 1,
            'duration': max(durations),
            'title': chapters[longest_idx].title,
        },
    }
