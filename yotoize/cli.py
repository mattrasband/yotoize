"""Command-line interface for yotoize."""

import sys
import traceback
import click
import json
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from .audio_loader import AudioLoader
from .chapter_detector import extract_chapters, Chapter
from .ffmpeg_tools import ffmpeg_executable
from .metadata import MetadataExtractor, build_ffmpeg_metadata_args
from .config import Config
from .config_finder import find_config_file, get_user_config_dir
from .utils import (
    filter_chapters, format_filename, validate_chapters, 
    calculate_statistics, detect_silence, parse_chapter_range,
    rename_chapters, interactive_chapter_renaming
)
from .playlist import generate_m3u_playlist
from .logger import YotoizeLogger


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def sanitize_filename(filename: str) -> str:
    """Sanitize a string to be used as a filename."""
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip(' .')
    if len(filename) > 200:
        filename = filename[:200]
    return filename


def derive_output_folder_name(audio_path: Path, base_output_dir: Path) -> Path:
    """Derive a clean folder name from the audio file and create subfolder path.
    
    Removes:
    - File extension
    - Common patterns like "(Full-Cast Edition)", "(Unabridged)", etc.
    - Extra whitespace
    
    Args:
        audio_path: Path to the input audio file
        base_output_dir: Base output directory
    
    Returns:
        Path to the output subfolder
    """
    # Get filename without extension
    stem = audio_path.stem
    
    # Remove common patterns that are often in audiobook filenames
    patterns_to_remove = [
        r'\s*\(Full-Cast Edition\)',
        r'\s*\(Unabridged\)',
        r'\s*\(Abridged\)',
        r'\s*\(Narrated by.*?\)',
        r'\s*\(Read by.*?\)',
        r'\s*\(Audible.*?\)',
        r'\s*\(.*?Edition\)',
        r'\s*-\s*Full.*?Cast',
        r'\s*-\s*Unabridged',
        r'\s*-\s*Abridged',
    ]
    
    clean_name = stem
    for pattern in patterns_to_remove:
        clean_name = re.sub(pattern, '', clean_name, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    clean_name = re.sub(r'\s+', ' ', clean_name)
    clean_name = clean_name.strip(' -_')
    
    # If name is empty after cleaning, fall back to stem
    if not clean_name:
        clean_name = stem
    
    # Sanitize for filesystem
    clean_name = sanitize_filename(clean_name)
    
    return base_output_dir / clean_name


def detect_output_format(audio_path: Path) -> str:
    """Auto-detect best output format based on input file."""
    ext = audio_path.suffix.lower()
    if ext in ['.m4b', '.m4a']:
        return 'm4b'
    elif ext == '.mp3':
        return 'mp3'
    else:
        return 'mp3'  # Default


def merge_chapters(chapters: List[Chapter], merge_ranges: List[str]) -> List[Chapter]:
    """Merge chapters based on merge ranges.
    
    Args:
        chapters: List of chapters
        merge_ranges: List of merge range strings (e.g., ["1-3", "5-7"])
    
    Returns:
        List of merged chapters
    """
    merged = []
    merged_indices = set()
    
    # Process merge ranges
    for merge_range in merge_ranges:
        indices = parse_chapter_range(merge_range, len(chapters))
        if len(indices) > 1:
            # Merge these chapters
            start_chapter = chapters[indices[0]]
            end_chapter = chapters[indices[-1]]
            
            merged_chapter = Chapter(
                start_time=start_chapter.start_time,
                end_time=end_chapter.end_time,
                confidence=start_chapter.confidence,
                title=f"{start_chapter.title or 'Chapter'} - {end_chapter.title or 'Chapter'}" if start_chapter.title or end_chapter.title else None
            )
            merged.append(merged_chapter)
            merged_indices.update(indices)
    
    # Add non-merged chapters
    for i, chapter in enumerate(chapters):
        if i not in merged_indices:
            merged.append(chapter)
    
    # Sort by start time
    merged.sort(key=lambda ch: ch.start_time)
    
    return merged


def process_single_file(
    audio_path: Path,
    output: Optional[Path],
    split: Optional[Path],
    output_format: Optional[str],
    verbose: bool,
    dry_run: bool,
    skip_existing: bool,
    filename_pattern: str,
    chapter_range: Optional[str],
    min_duration: Optional[float],
    max_duration: Optional[float],
    title_pattern: Optional[str],
    merge_ranges: Optional[List[str]],
    rename_map: Optional[Dict[int, str]],
    rename_interactive: bool,
    bitrate: Optional[str],
    codec: Optional[str],
    preserve_metadata: bool,
    extract_cover: bool,
    embed_cover: bool,
    cover_path: Optional[Path],
    remove_silence: bool,
    silence_threshold: float,
    silence_duration: float,
    playlist: bool,
    playlist_name: Optional[str],
    statistics: bool,
    validate: bool,
    log_file: Optional[Path],
    config: Optional[Config],
    parallel: bool,
    max_workers: int,
) -> bool:
    """Process a single audio file.
    
    Returns:
        True if successful, False otherwise
    """
    logger = YotoizeLogger(log_file, verbose)
    
    try:
        # Priority: 1. CLI arguments (already set), 2. Config file, 3. Auto-detect/defaults
        # Only apply config values if CLI arguments are not provided
        
        # Format: CLI > Config > Auto-detect (only if config doesn't have format)
        if output_format is None:
            if config:
                config_format = config.get('split.format')
                if config_format:
                    output_format = config_format
            
            # Only auto-detect if config didn't provide a format
            if output_format is None:
                output_format = detect_output_format(audio_path)
        
        # Filename pattern: CLI > Config > Default
        if (not filename_pattern or filename_pattern == "{number:02d} - {title}") and config:
            config_pattern = config.get('split.filename_pattern')
            if config_pattern:
                filename_pattern = config_pattern
        
        # Bitrate: CLI > Config
        if bitrate is None and config:
            bitrate = config.get('split.bitrate')
        
        # Codec: CLI > Config
        if codec is None and config:
            codec = config.get('split.codec')
        
        # Ensure output_format is set (fallback to mp3)
        if not output_format:
            output_format = 'mp3'
        
        # Validate audio file
        try:
            loader = AudioLoader(str(audio_path))
            duration = loader.get_duration()
            logger.info(f"Audio file: {audio_path.name}")
            logger.info(f"Format: {loader.format}")
            logger.info(f"Duration: {format_time(duration)}")
        except Exception as e:
            logger.error(f"Error loading audio file: {e}")
            if verbose:
                traceback.print_exc()
            return False
        
        # Extract metadata
        metadata = {}
        cover_art_path = None
        if preserve_metadata or extract_cover or embed_cover:
            extractor = MetadataExtractor(str(audio_path))
            metadata = extractor.extract_all_metadata()
            logger.debug(f"Extracted metadata: {list(metadata.keys())}")
        
        # Determine output directory early for cover art extraction
        output_dir = None
        if split:
            base_output_dir = Path(split)
            output_dir = derive_output_folder_name(audio_path, base_output_dir)
        
        # Extract cover art if requested
        if extract_cover or embed_cover:
            extractor = MetadataExtractor(str(audio_path))
            if cover_path:
                cover_output = Path(cover_path)
            elif output_dir:
                cover_output = output_dir / 'cover.jpg'
            else:
                cover_output = audio_path.parent / 'cover.jpg'
            cover_art_path = extractor.extract_cover_art(cover_output)
            if cover_art_path:
                logger.info(f"Extracted cover art: {cover_art_path}")
        
        # Extract chapters
        try:
            logger.info("Extracting chapters from file metadata...")
            chapters = extract_chapters(str(audio_path))
            
            if not chapters:
                logger.error("No chapters found in file metadata.")
                return False
        except KeyboardInterrupt:
            logger.error("\nOperation cancelled by user.")
            return False
        except Exception as e:
            logger.error(f"Error extracting chapters: {type(e).__name__}: {e}")
            if verbose:
                traceback.print_exc()
            return False
        
        # Apply merge ranges if specified
        if merge_ranges:
            chapters = merge_chapters(chapters, merge_ranges)
            logger.info(f"Merged to {len(chapters)} chapters")
        
        # Filter chapters
        original_count = len(chapters)
        chapters = filter_chapters(
            chapters,
            chapter_range=chapter_range,
            min_duration=min_duration,
            max_duration=max_duration,
            title_pattern=title_pattern,
        )
        if len(chapters) < original_count:
            logger.info(f"Filtered to {len(chapters)} chapters (from {original_count})")
        
        # Handle chapter renaming
        rename_map_local = rename_map or {}
        if rename_interactive:
            rename_map_local.update(interactive_chapter_renaming(chapters))
        
        if rename_map_local:
            chapters = rename_chapters(chapters, rename_map_local)
            logger.info(f"Renamed {len(rename_map_local)} chapters")
        
        # Validate chapters
        if validate:
            warnings = validate_chapters(chapters)
            if warnings:
                logger.warning("Chapter validation warnings:")
                for warning in warnings:
                    logger.warning(f"  - {warning}")
        
        # Calculate and display statistics
        if statistics:
            stats = calculate_statistics(chapters)
            print(f"\nStatistics:")
            print(f"  Total chapters: {stats['total_chapters']}")
            print(f"  Total duration: {format_time(stats['total_duration'])}")
            print(f"  Average duration: {format_time(stats['average_duration'])}")
            if stats['shortest_chapter']:
                print(f"  Shortest chapter: {stats['shortest_chapter']['number']} ({format_time(stats['shortest_chapter']['duration'])})")
            if stats['longest_chapter']:
                print(f"  Longest chapter: {stats['longest_chapter']['number']} ({format_time(stats['longest_chapter']['duration'])})")
        
        # Display chapters
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
        
        # Save to JSON if requested
        if output:
            output_path = Path(output)
            chapter_data = {
                'audio_file': str(audio_path),
                'duration': duration,
                'metadata': metadata,
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
        
        # Split audio if requested
        if split:
            # Use the output_dir we already derived
            if output_dir is None:
                base_output_dir = Path(split)
                output_dir = derive_output_folder_name(audio_path, base_output_dir)
            
            if dry_run:
                logger.info("DRY RUN: Would split audio into chapter files...")
                logger.info(f"Output directory: {output_dir}")
                for i, chapter in enumerate(chapters, 1):
                    filename = format_filename(filename_pattern, chapter, i, len(chapters), metadata)
                    print(f"  Would create: {output_dir / f'{filename}.{output_format}'}")
            else:
                success = split_audio_by_chapters(
                    audio_path=audio_path,
                    chapters=chapters,
                    output_dir=output_dir,
                    output_format=output_format,
                    verbose=verbose,
                    skip_existing=skip_existing,
                    filename_pattern=filename_pattern,
                    metadata=metadata,
                    bitrate=bitrate,
                    codec=codec,
                    cover_art_path=cover_art_path if embed_cover else None,
                    remove_silence=remove_silence,
                    silence_threshold=silence_threshold,
                    silence_duration=silence_duration,
                    parallel=parallel,
                    max_workers=max_workers,
                    logger=logger,
                )
                
                if success and playlist:
                    playlist_path = generate_m3u_playlist(
                        chapters, output_dir, output_format, filename_pattern, metadata, playlist_name
                    )
                    logger.info(f"Generated playlist: {playlist_path}")
        
        return True
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if verbose:
            traceback.print_exc()
        return False


def split_audio_by_chapters(
    audio_path: Path,
    chapters: List[Chapter],
    output_dir: Path,
    output_format: str,
    verbose: bool,
    skip_existing: bool,
    filename_pattern: str,
    metadata: Dict[str, Any],
    bitrate: Optional[str],
    codec: Optional[str],
    cover_art_path: Optional[Path],
    remove_silence: bool,
    silence_threshold: float,
    silence_duration: float,
    parallel: bool,
    max_workers: int,
    logger: YotoizeLogger,
) -> bool:
    """Split audio file into separate chapter files."""
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Splitting audio into {len(chapters)} chapter files...")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Output format: {output_format}")
    
    # Determine codec and bitrate
    if output_format in ['m4b', 'm4a']:
        default_codec = codec or 'aac'
        default_bitrate = bitrate or '192k'
    elif output_format == 'mp3':
        default_codec = codec or 'libmp3lame'
        default_bitrate = bitrate or '192k'
    elif output_format == 'wav':
        default_codec = codec or 'pcm_s16le'
        default_bitrate = None
    else:
        default_codec = codec or 'aac'
        default_bitrate = bitrate or '192k'
    
    # Build metadata args
    metadata_args = []
    if metadata:
        metadata_args = build_ffmpeg_metadata_args(metadata, output_format)
    
    # Process chapters
    def process_chapter(i: int, chapter: Chapter) -> tuple[int, bool, Optional[str]]:
        """Process a single chapter."""
        start_time = chapter.start_time
        end_time = chapter.end_time
        
        if end_time is None:
            return (i, False, "No end time")
        
        duration = end_time - start_time
        
        # Format filename
        filename = format_filename(filename_pattern, chapter, i + 1, len(chapters), metadata)
        output_file = output_dir / f"{filename}.{output_format}"
        
        # Skip if exists
        if skip_existing and output_file.exists():
            return (i, True, "Skipped (exists)")
        
        # Detect silence if requested
        trim_start = 0.0
        trim_end = 0.0
        if remove_silence:
            trim_start, trim_end = detect_silence(
                audio_path, start_time, end_time, silence_threshold, silence_duration
            )
            if trim_start > 0 or trim_end > 0:
                logger.debug(f"Chapter {i+1}: trimming {trim_start:.2f}s from start, {trim_end:.2f}s from end")
        
        # Adjust times for silence removal
        actual_start = start_time + trim_start
        actual_end = end_time - trim_end
        actual_duration = actual_end - actual_start
        
        # Build ffmpeg command
        cmd = [
            ffmpeg_executable(),
            '-ss', str(actual_start),
            '-i', str(audio_path),
            '-map', '0:a',
            '-t', str(actual_duration),
            '-avoid_negative_ts', 'make_zero',
        ]
        
        # Add codec
        cmd.extend(['-c:a', default_codec])
        
        # Add bitrate if applicable
        if default_bitrate:
            cmd.extend(['-b:a', default_bitrate])
        
        # Add metadata
        cmd.extend(metadata_args)
        
        # Add chapter-specific title
        chapter_title = chapter.title or f"Chapter {i+1}"
        cmd.extend(['-metadata', f'title={chapter_title}'])
        
        # Add cover art if provided
        if cover_art_path and cover_art_path.exists():
            if output_format in ['m4b', 'm4a']:
                cmd.extend(['-i', str(cover_art_path), '-map', '1', '-c:v', 'copy', '-disposition:v', '0'])
            elif output_format == 'mp3':
                cmd.extend(['-i', str(cover_art_path), '-map', '0:a', '-map', '1', '-c:v', 'copy', '-id3v2_version', '3'])
        
        cmd.extend(['-y', str(output_file)])
        
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True
            )
            return (i, True, None)
        except subprocess.CalledProcessError as e:
            error_msg = f"ffmpeg error: {e.stderr[:200] if e.stderr else str(e)}"
            return (i, False, error_msg)
    
    # Process chapters (parallel or sequential)
    if parallel and len(chapters) > 1:
        logger.info(f"Processing {len(chapters)} chapters in parallel (max {max_workers} workers)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_chapter, i, ch): (i, ch) for i, ch in enumerate(chapters)}
            
            with tqdm(total=len(chapters), desc="Splitting", disable=verbose) as pbar:
                for future in as_completed(futures):
                    i, success, msg = future.result()
                    if success:
                        pbar.update(1)
                        if verbose:
                            logger.info(f"Chapter {i+1}/{len(chapters)}: {chapters[i].title or f'Chapter {i+1}'}")
                    else:
                        logger.error(f"Chapter {i+1} failed: {msg}")
                        pbar.update(1)
    else:
        with tqdm(chapters, desc="Splitting", disable=verbose) as pbar:
            for i, chapter in enumerate(pbar):
                i_result, success, msg = process_chapter(i, chapter)
                if success:
                    if verbose:
                        logger.info(f"Chapter {i+1}/{len(chapters)}: {chapter.title or f'Chapter {i+1}'}")
                else:
                    logger.error(f"Chapter {i+1} failed: {msg}")
    
    logger.info(f"Successfully split into {len(chapters)} files in {output_dir}")
    return True


def interactive_chapter_selection(chapters: List[Chapter]) -> List[Chapter]:
    """Interactive chapter selection mode.
    
    Args:
        chapters: List of chapters to select from
    
    Returns:
        Selected chapters
    """
    print("\nInteractive Chapter Selection:")
    print("Enter chapter numbers to include (e.g., '1,3,5' or '1-5' or 'all')")
    print("Type 'q' to quit without selecting")
    
    while True:
        try:
            selection = input("\nSelection: ").strip().lower()
            
            if selection == 'q':
                return []
            elif selection == 'all':
                return chapters
            else:
                indices = parse_chapter_range(selection, len(chapters))
                if indices:
                    selected = [chapters[i] for i in indices if 0 <= i < len(chapters)]
                    print(f"\nSelected {len(selected)} chapters:")
                    for i, ch in enumerate(selected, 1):
                        title = ch.title or f"Chapter {i}"
                        duration = format_time(ch.duration) if ch.duration else "N/A"
                        print(f"  {i}. {title} ({duration})")
                    
                    confirm = input("\nConfirm selection? (y/n): ").strip().lower()
                    if confirm == 'y':
                        return selected
                else:
                    print("Invalid selection. Please try again.")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            return []


# Create main CLI group - routes to main when no subcommand provided
@click.group(invoke_without_command=True)
@click.version_option(version='0.2.0')
@click.pass_context
def cli(ctx):
    """Extract and split audiobook chapters from embedded metadata."""
    # If no subcommand was invoked and we have a file argument, call main
    if ctx.invoked_subcommand is None:
        import sys
        # Check if first non-option arg looks like a file
        args = sys.argv[1:]
        file_arg = None
        for i, arg in enumerate(args):
            if not arg.startswith('-') and not arg.startswith('--'):
                file_arg = arg
                break
        
        if file_arg:
            # Invoke main command with the arguments
            try:
                ctx.forward(main)
            except Exception:
                # If forward fails, show help
                click.echo(ctx.get_help())
        else:
            # Show help if no file argument
            click.echo(ctx.get_help())


# Add main as a subcommand (but it's also the default via invoke_without_command)
@cli.command(name='process')
@click.argument('audio_file', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), help='Output file for chapter data (JSON)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed error messages and tracebacks')
@click.option('--split', '-s', type=click.Path(), help='Split audio into chapter files in the specified directory')
@click.option('--format', '-f', type=click.Choice(['m4b', 'm4a', 'mp3', 'wav']), help='Output format (default: auto-detect)')
@click.option('--dry-run', is_flag=True, help='Preview what would happen without actually splitting')
@click.option('--skip-existing', is_flag=True, help='Skip chapters that already exist in output directory')
@click.option('--filename-pattern', default='{number:02d} - {title}', help='Filename pattern (default: "{number:02d} - {title}")')
@click.option('--chapters', help='Chapter range to process (e.g., "1,3,5-7")')
@click.option('--min-duration', type=float, help='Minimum chapter duration in seconds')
@click.option('--max-duration', type=float, help='Maximum chapter duration in seconds')
@click.option('--title-pattern', help='Regex pattern to match chapter titles')
@click.option('--merge', multiple=True, help='Merge chapters (e.g., --merge "1-3" --merge "5-7")')
@click.option('--bitrate', help='Audio bitrate (e.g., "192k", "256k")')
@click.option('--codec', help='Audio codec (e.g., "aac", "libmp3lame")')
@click.option('--preserve-metadata', is_flag=True, help='Preserve metadata (artist, album, etc.) in split files')
@click.option('--extract-cover', is_flag=True, help='Extract cover art from source file')
@click.option('--embed-cover', is_flag=True, help='Embed cover art in split files')
@click.option('--cover-path', type=click.Path(), help='Path to save/extract cover art')
@click.option('--remove-silence', is_flag=True, help='Remove silence at chapter boundaries')
@click.option('--silence-threshold', type=float, default=-50.0, help='Silence threshold in dB (default: -50)')
@click.option('--silence-duration', type=float, default=0.5, help='Minimum silence duration in seconds (default: 0.5)')
@click.option('--playlist', is_flag=True, help='Generate M3U playlist file')
@click.option('--playlist-name', help='Name for playlist file (default: album name or "playlist")')
@click.option('--statistics', is_flag=True, help='Show chapter statistics')
@click.option('--validate', is_flag=True, help='Validate chapters for issues')
@click.option('--log', type=click.Path(), help='Save operation log to file')
@click.option('--config', type=click.Path(exists=True), help='Load configuration from file')
@click.option('--parallel', is_flag=True, help='Process chapters in parallel')
@click.option('--max-workers', type=int, default=4, help='Maximum parallel workers (default: 4)')
@click.option('--select-interactive', '-i', is_flag=True, help='Interactive chapter selection mode')
@click.option('--rename', multiple=True, help='Rename chapters (format: "number:new title", e.g., "1:Introduction")')
@click.option('--rename-interactive', is_flag=True, help='Interactive chapter renaming mode')
def main(
    audio_file: str,
    output: Optional[str],
    verbose: bool,
    split: Optional[str],
    format: Optional[str],
    dry_run: bool,
    skip_existing: bool,
    filename_pattern: str,
    chapters: Optional[str],
    min_duration: Optional[float],
    max_duration: Optional[float],
    title_pattern: Optional[str],
    merge: tuple,
    bitrate: Optional[str],
    codec: Optional[str],
    preserve_metadata: bool,
    extract_cover: bool,
    embed_cover: bool,
    cover_path: Optional[str],
    remove_silence: bool,
    silence_threshold: float,
    silence_duration: float,
    playlist: bool,
    playlist_name: Optional[str],
    statistics: bool,
    validate: bool,
    log: Optional[str],
    config: Optional[str],
    parallel: bool,
    max_workers: int,
    select_interactive: bool,
    rename: tuple,
    rename_interactive: bool,
):
    """Extract chapters from audiobook files (mp3, m4b) using embedded metadata.
    
    AUDIO_FILE: Path to the audio file to analyze
    """
    # Load config - priority: 1. CLI --config, 2. Local directory, 3. Global directory
    config_obj = None
    if config:
        # Explicitly provided config file
        config_obj = Config(Path(config))
        click.echo(f"Using config file: {Path(config).resolve()}", err=True)
    else:
        # Try to find config file: local directory first, then global
        found_config = find_config_file()
        if found_config:
            try:
                config_obj = Config(found_config)
                click.echo(f"Using config file: {found_config.resolve()}", err=True)
            except Exception as e:
                if verbose:
                    click.echo(f"Warning: Could not load config from {found_config}: {e}", err=True)
    
    # Handle interactive selection mode - need to filter chapters before processing
    if select_interactive:
        # Extract chapters first to show user
        try:
            audio_path_temp = Path(audio_file)
            chapters_temp = extract_chapters(str(audio_path_temp))
            
            if not chapters_temp:
                click.echo("No chapters found in file metadata.", err=True)
                sys.exit(1)
            
            interactive_selected = interactive_chapter_selection(chapters_temp)
            if not interactive_selected:
                click.echo("No chapters selected.", err=True)
                sys.exit(0)
            
            # Convert selection to chapter range string
            selected_indices = sorted([i for i, ch in enumerate(chapters_temp) if ch in interactive_selected])
            if selected_indices:
                chapters = ','.join(str(i+1) for i in selected_indices)
            else:
                chapters = None
        except Exception as e:
            click.echo(f"Error in interactive mode: {e}", err=True)
            if verbose:
                traceback.print_exc()
            sys.exit(1)
    
    # Handle chapter renaming
    rename_map = {}
    if rename:
        for rename_entry in rename:
            if ':' in rename_entry:
                chapter_num_str, new_title = rename_entry.split(':', 1)
                try:
                    chapter_num = int(chapter_num_str.strip())
                    rename_map[chapter_num] = new_title.strip()
                except ValueError:
                    click.echo(f"Invalid rename format: {rename_entry}", err=True)
    
    # Process file
    success = process_single_file(
        audio_path=Path(audio_file),
        output=Path(output) if output else None,
        split=Path(split) if split else None,
        output_format=format,  # Pass None if not provided, let config/auto-detect handle it
        verbose=verbose,
        dry_run=dry_run,
        skip_existing=skip_existing,
        filename_pattern=filename_pattern,
        chapter_range=chapters,
        min_duration=min_duration,
        max_duration=max_duration,
        title_pattern=title_pattern,
        merge_ranges=list(merge) if merge else None,
        rename_map=rename_map if rename or rename_interactive else None,
        rename_interactive=rename_interactive,
        bitrate=bitrate,
        codec=codec,
        preserve_metadata=preserve_metadata,
        extract_cover=extract_cover,
        embed_cover=embed_cover,
        cover_path=Path(cover_path) if cover_path else None,
        remove_silence=remove_silence,
        silence_threshold=silence_threshold,
        silence_duration=silence_duration,
        playlist=playlist,
        playlist_name=playlist_name,
        statistics=statistics,
        validate=validate,
        log_file=Path(log) if log else None,
        config=config_obj,
        parallel=parallel,
        max_workers=max_workers,
    )
    
    sys.exit(0 if success else 1)


@cli.command(name='config')
@click.argument('config_file', type=click.Path(), required=False)
@click.option('--format', type=click.Choice(['toml', 'json']), default='toml', help='Config file format (default: toml)')
@click.option('--force', is_flag=True, help='Overwrite existing config file')
@click.option('--user', is_flag=True, help='Create config in user config directory instead of current directory')
@click.option('-e', '--editor', is_flag=True, help='Open config file in $EDITOR after creating/finding it')
def config_cmd(config_file: Optional[str], format: str, force: bool, user: bool, editor: bool):
    """Generate or manage configuration file.
    
    If CONFIG_FILE is not provided, creates 'yotoize.toml' in the current directory
    (or 'config.toml' in the user config directory if --user is specified).
    
    If the config file already exists and --editor is specified, opens it for editing.
    """
    import os
    
    # If editor is requested and no file specified, try to find existing config
    if editor and config_file is None:
        found_config = find_config_file()
        if found_config:
            config_path = found_config
            editor_cmd = os.getenv('EDITOR')
            if not editor_cmd:
                click.echo("Error: $EDITOR environment variable is not set.", err=True)
                click.echo("Please set it, e.g.: export EDITOR=vim", err=True)
                sys.exit(1)
            
            try:
                subprocess.run([editor_cmd, str(config_path)], check=True)
                click.echo(f"✓ Opened config file: {config_path}")
            except subprocess.CalledProcessError as e:
                click.echo(f"Error: Failed to open editor: {e}", err=True)
                sys.exit(1)
            except FileNotFoundError:
                click.echo(f"Error: Editor '{editor_cmd}' not found.", err=True)
                sys.exit(1)
            return
    
    # Determine config file path
    if config_file is None:
        if user:
            # Create in user config directory
            # Use 'config.toml' to match what find_config_file checks first
            user_config_dir = get_user_config_dir()
            user_config_dir.mkdir(parents=True, exist_ok=True)
            config_file = str(user_config_dir / f'config.{format}')
        else:
            # Create in current directory
            config_file = f'yotoize.{format}'
    
    config_path = Path(config_file)
    
    # If file exists and editor is requested, just open it
    if config_path.exists() and editor and not force:
        editor_cmd = os.getenv('EDITOR')
        if not editor_cmd:
            click.echo("Error: $EDITOR environment variable is not set.", err=True)
            click.echo("Please set it, e.g.: export EDITOR=vim", err=True)
            sys.exit(1)
        
        try:
            subprocess.run([editor_cmd, str(config_path)], check=True)
            click.echo(f"✓ Opened config file: {config_path}")
        except subprocess.CalledProcessError as e:
            click.echo(f"Error: Failed to open editor: {e}", err=True)
            sys.exit(1)
        except FileNotFoundError:
            click.echo(f"Error: Editor '{editor_cmd}' not found.", err=True)
            sys.exit(1)
        return
    
    # Check if file exists
    if config_path.exists() and not force:
        click.echo(f"Error: Config file already exists: {config_path}", err=True)
        click.echo("Use --force to overwrite it, or --editor to edit it.", err=True)
        sys.exit(1)
    
    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create default config
    default_config = {
        'split': {
            'format': 'mp3',
            'filename_pattern': '{number:02d} - {title}',
            'preserve_metadata': True,
            'extract_cover': False,
            'embed_cover': True,
            'playlist': False,
            'skip_existing': False,
            'remove_silence': False,
            'silence_threshold': -50.0,
            'silence_duration': 0.5,
            'parallel': False,
            'max_workers': 4,
        },
        'filter': {},
        'rename': {},
        'output': {
            'statistics': False,
            'validate': False,
            'verbose': False,
        },
    }
    
    # Save config
    try:
        config = Config()
        config.data = default_config
        config.save(config_path, format)
        
        click.echo(f"✓ Created default config file: {config_path}")
        click.echo(f"\nYou can now customize it and use it with:")
        click.echo(f"  yotoize audiobook.m4b --config {config_path} --split ./chapters")
        click.echo(f"\nOr it will be automatically found if placed in a standard location:")
        if user:
            click.echo(f"  - User config directory: {config_path.name} (already created)")
            click.echo(f"  - Current directory: yotoize.toml")
        else:
            click.echo(f"  - Current directory: {config_path.name} (already created)")
            click.echo(f"  - User config: {get_user_config_dir() / 'config.toml'}")
        
        # Open in editor if requested
        if editor:
            editor_cmd = os.getenv('EDITOR')
            if not editor_cmd:
                click.echo("\nWarning: $EDITOR environment variable is not set.", err=True)
                click.echo("Please set it, e.g.: export EDITOR=vim", err=True)
                sys.exit(1)
            
            try:
                subprocess.run([editor_cmd, str(config_path)], check=True)
                click.echo(f"\n✓ Opened config file in editor: {editor_cmd}")
            except subprocess.CalledProcessError as e:
                click.echo(f"\nError: Failed to open editor: {e}", err=True)
                sys.exit(1)
            except FileNotFoundError:
                click.echo(f"\nError: Editor '{editor_cmd}' not found.", err=True)
                sys.exit(1)
    except ImportError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Please install the required dependencies:", err=True)
        if format == 'toml':
            click.echo("  pip install tomli tomli-w", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error creating config file: {e}", err=True)
        sys.exit(1)


@cli.command(name='batch')
@click.argument('audio_files', nargs=-1, type=click.Path(exists=True))
@click.option('--batch-dir', type=click.Path(exists=True), help='Process all audio files in directory')
@click.option('--output-dir', type=click.Path(), help='Output directory for all files')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed error messages')
@click.option('--format', '-f', type=click.Choice(['m4b', 'm4a', 'mp3', 'wav']), help='Output format')
@click.option('--filename-pattern', default='{number:02d} - {title}', help='Filename pattern')
@click.option('--preserve-metadata', is_flag=True, help='Preserve metadata')
@click.option('--extract-cover', is_flag=True, help='Extract cover art')
@click.option('--embed-cover', is_flag=True, help='Embed cover art')
@click.option('--playlist', is_flag=True, help='Generate playlists')
@click.option('--parallel', is_flag=True, help='Process files in parallel')
@click.option('--max-workers', type=int, default=2, help='Maximum parallel workers')
@click.pass_context
def batch(
    ctx: click.Context,
    audio_files: tuple,
    batch_dir: Optional[str],
    output_dir: Optional[str],
    verbose: bool,
    format: Optional[str],
    filename_pattern: str,
    preserve_metadata: bool,
    extract_cover: bool,
    embed_cover: bool,
    playlist: bool,
    parallel: bool,
    max_workers: int,
):
    """Process multiple audio files in batch."""
    # Collect files
    files_to_process = list(audio_files)
    
    if batch_dir:
        batch_path = Path(batch_dir)
        for ext in ['.mp3', '.m4b', '.m4a']:
            files_to_process.extend(batch_path.glob(f'*{ext}'))
    
    if not files_to_process:
        click.echo("No audio files found to process.", err=True)
        sys.exit(1)
    
    click.echo(f"Processing {len(files_to_process)} files...", err=True)
    
    # Process each file
    success_count = 0
    for audio_file in tqdm(files_to_process, desc="Files", disable=verbose):
        file_output_dir = None
        if output_dir:
            # Use the derive function to create clean folder names
            file_output_dir = derive_output_folder_name(Path(audio_file), Path(output_dir))
        
        success = process_single_file(
            audio_path=Path(audio_file),
            output=None,
            split=file_output_dir,
            output_format=format or 'mp3',
            verbose=verbose,
            dry_run=False,
            skip_existing=True,
            filename_pattern=filename_pattern,
            chapter_range=None,
            min_duration=None,
            max_duration=None,
            title_pattern=None,
            merge_ranges=None,
            bitrate=None,
            codec=None,
            preserve_metadata=preserve_metadata,
            extract_cover=extract_cover,
            embed_cover=embed_cover,
            cover_path=None,
            remove_silence=False,
            silence_threshold=-50.0,
            silence_duration=0.5,
            playlist=playlist,
            playlist_name=None,
            statistics=False,
            validate=False,
            log_file=None,
            config=None,
            parallel=parallel,
            max_workers=max_workers,
        )
        
        if success:
            success_count += 1
    
    click.echo(f"\nProcessed {success_count}/{len(files_to_process)} files successfully.", err=True)
    sys.exit(0 if success_count == len(files_to_process) else 1)


if __name__ == '__main__':
    cli()
