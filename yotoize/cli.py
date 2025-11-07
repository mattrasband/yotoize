"""Command-line interface for yotoize."""

import sys
import traceback
import click
import json
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from .audio_loader import AudioLoader
from .chapter_detector import extract_chapters, Chapter


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@click.command()
@click.argument('audio_file', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), help='Output file for chapter data (JSON)')
@click.option('--verbose', '-v', is_flag=True,
              help='Show detailed error messages and tracebacks')
@click.option('--split', '-s', type=click.Path(), 
              help='Split audio into chapter files in the specified directory')
@click.option('--format', '-f', default='mp3',
              type=click.Choice(['m4b', 'm4a', 'mp3', 'wav']),
              help='Output format for split files: m4b, m4a, mp3, or wav (default: m4b)')
def main(audio_file: str, output: Optional[str], verbose: bool, 
         split: Optional[str], format: str):
    """Extract chapters from audiobook files (mp3, m4b) using embedded metadata.
    
    AUDIO_FILE: Path to the audio file to analyze
    """
    audio_path = Path(audio_file)
    
    # Validate audio file
    try:
        loader = AudioLoader(str(audio_path))
        duration = loader.get_duration()
        print(f"Audio file: {audio_path.name}")
        print(f"Format: {loader.format}")
        print(f"Duration: {format_time(duration)}")
        print()
    except Exception as e:
        click.echo(f"Error loading audio file: {e}", err=True)
        if verbose:
            traceback.print_exc()
        sys.exit(1)
    
    # Extract chapters from metadata
    try:
        click.echo("Extracting chapters from file metadata...", err=True)
        sys.stderr.flush()
        chapters = extract_chapters(str(audio_path))
        
        if not chapters:
            click.echo("No chapters found in file metadata.", err=True)
            sys.exit(1)
            
    except KeyboardInterrupt:
        click.echo("\nOperation cancelled by user.", err=True)
        sys.stderr.flush()
        sys.exit(130)
    except Exception as e:
        click.echo(f"Error extracting chapters: {type(e).__name__}: {e}", err=True)
        if verbose:
            traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)
    
    # Display results
    print(f"\nFound {len(chapters)} chapters:\n")
    has_titles = any(ch.title for ch in chapters)
    
    if has_titles:
        print(f"{'Chapter':<10} {'Start':<12} {'End':<12} {'Duration':<12} {'Title':<40}")
        print("-" * 100)
        for i, chapter in enumerate(chapters, 1):
            start_str = format_time(chapter.start_time)
            end_str = format_time(chapter.end_time) if chapter.end_time else "N/A"
            duration_str = format_time(chapter.duration) if chapter.duration else "N/A"
            title_str = chapter.title[:37] + "..." if chapter.title and len(chapter.title) > 40 else (chapter.title or "")
            
            print(f"{i:<10} {start_str:<12} {end_str:<12} {duration_str:<12} {title_str:<40}")
    else:
        print(f"{'Chapter':<10} {'Start':<12} {'End':<12} {'Duration':<12}")
        print("-" * 50)
        for i, chapter in enumerate(chapters, 1):
            start_str = format_time(chapter.start_time)
            end_str = format_time(chapter.end_time) if chapter.end_time else "N/A"
            duration_str = format_time(chapter.duration) if chapter.duration else "N/A"
            
            print(f"{i:<10} {start_str:<12} {end_str:<12} {duration_str:<12}")
    
    # Save to file if requested
    if output:
        output_path = Path(output)
        chapter_data = {
            'audio_file': str(audio_path),
            'duration': duration,
            'chapters': [
                {
                    'number': i,
                    'start_time': ch.start_time,
                    'end_time': ch.end_time,
                    'duration': ch.duration,
                    'confidence': ch.confidence,
                    'title': ch.title
                }
                for i, ch in enumerate(chapters, 1)
            ]
        }
        
        with open(output_path, 'w') as f:
            json.dump(chapter_data, f, indent=2)
        
        print(f"\nChapter data saved to: {output_path}")
    
    # Split audio into chapter files if requested
    if split:
        split_audio_by_chapters(
            audio_path, chapters, Path(split), format, verbose
        )


def sanitize_filename(filename: str) -> str:
    """Sanitize a string to be used as a filename."""
    # Remove or replace invalid filename characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    return filename


def split_audio_by_chapters(audio_path: Path, chapters: List[Chapter], output_dir: Path,
                            output_format: str, verbose: bool):
    """Split audio file into separate chapter files."""
    
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    click.echo(f"\nSplitting audio into {len(chapters)} chapter files...", err=True)
    click.echo(f"Output directory: {output_dir}", err=True)
    click.echo(f"Output format: {output_format}", err=True)
    sys.stderr.flush()
    
    # Determine number of digits needed for padding based on total chapters
    num_digits = len(str(len(chapters)))
    
    for i, chapter in enumerate(chapters):
        start_time = chapter.start_time
        end_time = chapter.end_time
        
        if end_time is None:
            click.echo(f"Warning: Chapter {i+1} has no end time, skipping", err=True)
            continue
        
        duration = end_time - start_time
        
        # Generate filename: "Chapter NN - Title.m4b"
        chapter_num = f"{i:0{num_digits}d}"
        
        if chapter.title:
            # Use title in filename (sanitized)
            safe_title = sanitize_filename(chapter.title)
            filename = f"{chapter_num} - {safe_title}.{output_format}"
        else:
            filename = f"{chapter_num}.{output_format}"
        
        output_file = output_dir / filename
        
        # Build ffmpeg command for re-encoding
        # Important: Use -map 0:a to only extract audio stream (ignore video/cover art)
        cmd = [
            'ffmpeg',
            '-ss', str(start_time),  # Seek before input for better accuracy
            '-i', str(audio_path),
            '-map', '0:a',  # Only map audio stream (ignore video/cover art)
            '-t', str(duration),
            '-avoid_negative_ts', 'make_zero',
        ]
        
        # Preserve original channel layout (don't remix or downmix)
        # By default, ffmpeg preserves channel layout when re-encoding
        # We explicitly avoid any channel mixing filters to preserve stereo balance
        
        # Add codec and bitrate based on output format
        if output_format in ['m4b', 'm4a']:
            cmd.extend(['-c:a', 'aac', '-b:a', '192k'])
        elif output_format == 'mp3':
            cmd.extend(['-c:a', 'libmp3lame', '-b:a', '192k'])
        elif output_format == 'wav':
            cmd.extend(['-c:a', 'pcm_s16le'])
        
        # Add title metadata
        if chapter.title:
            metadata_title = chapter.title
        else:
            metadata_title = f"Chapter {chapter_num}"
        cmd.extend(['-metadata', f'title={metadata_title}'])
        
        cmd.extend(['-y', str(output_file)])
        
        try:
            if verbose:
                click.echo(f"Extracting chapter {i+1}/{len(chapters)}: {filename}", err=True)
                sys.stderr.flush()
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True
            )
            
            if verbose:
                click.echo(f"  ✓ Created: {output_file.name}", err=True)
                sys.stderr.flush()
            else:
                # Show progress - clear line first, then print new progress
                progress_msg = f"  [{i+1}/{len(chapters)}] {output_file.name}"
                # Clear line using ANSI escape code and print new progress
                print(f"\r{' ' * 80}\r{progress_msg}", end='', flush=True)
                
        except subprocess.CalledProcessError as e:
            click.echo(f"\nError extracting chapter {i+1}: {e}", err=True)
            click.echo(f"ffmpeg command: {' '.join(cmd)}", err=True)
            click.echo(f"Exit code: {e.returncode}", err=True)
            
            # Always show stderr - it contains useful error info
            if e.stderr:
                click.echo(f"\nffmpeg stderr:", err=True)
                click.echo(e.stderr, err=True)
            
            if e.stdout:
                click.echo(f"\nffmpeg stdout:", err=True)
                click.echo(e.stdout, err=True)
            
            sys.stderr.flush()
            continue
    
    if not verbose:
        print()  # New line after progress
    
    click.echo(f"\n✓ Successfully split into {len(chapters)} files in {output_dir}", err=True)
    sys.stderr.flush()


if __name__ == '__main__':
    main()
