"""Command-line interface for yotoize."""

import sys
import traceback
import click
import json
import re
import subprocess
import math
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from .audio_loader import AudioLoader
from .chapter_detector import extract_chapters, Chapter
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
from . import __version__


class ConfiguredCommand(click.Command):
    """Resolve config defaults before invoking the processing callback."""

    def invoke(self, ctx):
        path = ctx.params.get('config') or find_config_file()
        if path:
            try:
                config = Config(Path(path))
                sections = {'split': {
                    'format', 'filename_pattern', 'bitrate', 'codec', 'preserve_metadata',
                    'extract_cover', 'embed_cover', 'cover_path', 'playlist', 'playlist_name',
                    'skip_existing', 'remove_silence', 'silence_threshold', 'silence_duration',
                    'parallel', 'max_workers'},
                    'filter': {'chapters', 'min_duration', 'max_duration', 'title_pattern', 'merge'},
                    'output': {'statistics', 'validate', 'log', 'verbose'}}
                defaults = {}
                if not isinstance(config.data, dict):
                    raise ValueError('Config must be an object')
                for section, values in config.data.items():
                    if section == 'rename':
                        if not isinstance(values, dict):
                            raise ValueError('rename must be a table')
                        defaults['rename'] = [f'{number}:{title}' for number, title in values.items()]
                        continue
                    if section not in sections or not isinstance(values, dict):
                        raise ValueError(f'Unknown or invalid config section: {section}')
                    for key, value in values.items():
                        if key not in sections[section]:
                            raise ValueError(f'Unknown config key: {section}.{key}')
                        defaults[key] = value
                for param in self.params:
                    if param.name in defaults:
                        value = param.process_value(ctx, defaults[param.name])
                        if ctx.get_parameter_source(param.name) == click.core.ParameterSource.DEFAULT:
                            ctx.params[param.name] = value
                click.echo(f'Using config file: {Path(path).resolve()}', err=True)
            except (ValueError, TypeError, OSError) as exc:
                raise click.ClickException(f'Invalid configuration: {exc}') from exc
        return super().invoke(ctx)


def chapter_output_paths(chapters, output_dir, output_format, pattern, metadata):
    paths = []
    seen = set()
    for number, chapter in enumerate(chapters, 1):
        if (chapter.end_time is None or not math.isfinite(chapter.start_time)
                or not math.isfinite(chapter.end_time) or chapter.start_time < 0
                or chapter.end_time <= chapter.start_time):
            raise ValueError(f'Chapter {number} has invalid boundaries')
        name = format_filename(pattern, chapter, number, len(chapters), metadata)
        if not name or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError(f'Invalid output filename: {name!r}')
        key = name.casefold()
        if key in seen:
            raise ValueError(f'Duplicate output filename: {name}. Include {{number}} in the pattern.')
        seen.add(key)
        paths.append(output_dir / f'{name}.{output_format}')
    return paths


def valid_audio_output(path, expected_duration):
    """Check the audio stream and duration before publishing or resuming."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=duration:format=duration', '-of', 'json', str(path)
        ], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if not data.get('streams'):
            return False
        duration = float(data['streams'][0].get('duration', data.get('format', {}).get('duration', 0)))
        return math.isfinite(duration) and duration > 0 and abs(duration - expected_duration) <= 0.25
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return False


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
    parallel: bool,
    max_workers: int,
) -> bool:
    """Process a single audio file.
    
    Returns:
        True if successful, False otherwise
    """
    logger = YotoizeLogger(None if dry_run else log_file, verbose)
    
    try:
        output_format = output_format or detect_output_format(audio_path)
        
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
        if not chapters:
            raise ValueError('No chapters match the selection')
        
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
        
        if split:
            chapter_output_paths(chapters, output_dir, output_format, filename_pattern, metadata)
        cover_output = None
        if extract_cover or embed_cover:
            cover_output = Path(cover_path) if cover_path else (output_dir or audio_path.parent) / 'cover.jpg'
            if not dry_run:
                cover_output.parent.mkdir(parents=True, exist_ok=True)
                cover_art_path = MetadataExtractor(str(audio_path)).extract_cover_art(cover_output)
                if cover_art_path:
                    logger.info(f'Extracted cover art: {cover_art_path}')
                else:
                    logger.warning('No cover art found in source file.')
        if dry_run:
            for label, target in [('JSON', output), ('cover', cover_output), ('log', log_file)]:
                if target:
                    logger.info(f'DRY RUN: Would write {label}: {target}')

        # Save to JSON if requested
        if output and not dry_run:
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
                if playlist:
                    logger.info('DRY RUN: Would generate a playlist in the output directory')
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
                
                if not success:
                    return False
                if playlist:
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
    
    output_files = chapter_output_paths(chapters, output_dir, output_format, filename_pattern, metadata)
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
        output_file = output_files[i]
        
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
        if actual_duration <= 0:
            return (i, False, 'Silence trimming removed the entire chapter')
        if skip_existing and output_file.exists():
            if valid_audio_output(output_file, actual_duration):
                return (i, True, 'Skipped (verified)')
            return (i, False, f'Existing output is incomplete or invalid: {output_file}; remove it or rerun without --skip-existing')
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg',
            '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-ss', str(actual_start),
            '-i', str(audio_path),
        ]
        if cover_art_path and output_format in ['mp3', 'm4a', 'm4b']:
            cmd.extend(['-i', str(cover_art_path)])
        cmd.extend([
            '-map', '0:a:0', '-map_chapters', '-1',
            '-t', str(actual_duration),
            '-avoid_negative_ts', 'make_zero',
        ])
        
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
                cmd.extend(['-map', '1:v:0', '-c:v', 'copy', '-disposition:v', 'attached_pic'])
            elif output_format == 'mp3':
                cmd.extend(['-map', '1:v:0', '-c:v', 'copy', '-disposition:v', 'attached_pic', '-id3v2_version', '3'])
        
        try:
            with tempfile.TemporaryDirectory(prefix='.yotoize-', dir=output_dir) as temp_dir:
                temp_file = Path(temp_dir) / output_file.name
                cmd.extend(['-y', str(temp_file)])
                subprocess.run(cmd, capture_output=True, check=True, text=True)
                if not valid_audio_output(temp_file, actual_duration):
                    return (i, False, 'Encoded output failed duration/audio validation')
                temp_file.replace(output_file)
            return (i, True, None)
        except subprocess.CalledProcessError as e:
            error_msg = f"ffmpeg error: {e.stderr[-2000:] if e.stderr else str(e)}"
            return (i, False, error_msg)
        except OSError as e:
            return (i, False, str(e))
    
    # Process chapters (parallel or sequential)
    failures = 0
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
                        failures += 1
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
                    failures += 1
                    logger.error(f"Chapter {i+1} failed: {msg}")
    
    logger.info(f"Completed {len(chapters) - failures}/{len(chapters)} chapters in {output_dir}; {failures} failed")
    return failures == 0


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
@click.version_option(version=__version__)
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
@cli.command(name='process', cls=ConfiguredCommand)
@click.argument('audio_file', type=click.Path(exists=True))
@click.option('--output', '-o', type=click.Path(), help='Output file for chapter data (JSON)')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed error messages and tracebacks')
@click.option('--split', '-s', type=click.Path(), help='Split audio into chapter files in the specified directory')
@click.option('--format', '-f', type=click.Choice(['m4b', 'm4a', 'mp3', 'wav']), help='Output format (default: auto-detect)')
@click.option('--dry-run', is_flag=True, help='Preview what would happen without actually splitting')
@click.option('--skip-existing/--no-skip-existing', default=False, help='Skip existing chapters only after audio/duration validation')
@click.option('--filename-pattern', default='{number:02d} - {title}', help='Filename pattern (default: "{number:02d} - {title}")')
@click.option('--chapters', help='Chapter range to process (e.g., "1,3,5-7")')
@click.option('--min-duration', type=click.FloatRange(min=0), help='Minimum chapter duration in seconds')
@click.option('--max-duration', type=click.FloatRange(min=0), help='Maximum chapter duration in seconds')
@click.option('--title-pattern', help='Regex pattern to match chapter titles')
@click.option('--merge', multiple=True, help='Merge chapters (e.g., --merge "1-3" --merge "5-7")')
@click.option('--bitrate', help='Audio bitrate (e.g., "192k", "256k")')
@click.option('--codec', help='Audio codec (e.g., "aac", "libmp3lame")')
@click.option('--preserve-metadata/--no-preserve-metadata', default=False, help='Preserve metadata (artist, album, etc.) in split files')
@click.option('--extract-cover/--no-extract-cover', default=False, help='Extract cover art from source file')
@click.option('--embed-cover/--no-embed-cover', default=False, help='Embed cover art in split files')
@click.option('--cover-path', type=click.Path(), help='Path to save/extract cover art')
@click.option('--remove-silence/--no-remove-silence', default=False, help='Remove silence at chapter boundaries')
@click.option('--silence-threshold', type=float, default=-50.0, help='Silence threshold in dB (default: -50)')
@click.option('--silence-duration', type=float, default=0.5, help='Minimum silence duration in seconds (default: 0.5)')
@click.option('--playlist/--no-playlist', default=False, help='Generate M3U playlist file')
@click.option('--playlist-name', help='Name for playlist file (default: album name or "playlist")')
@click.option('--statistics/--no-statistics', default=False, help='Show chapter statistics')
@click.option('--validate/--no-validate', default=False, help='Validate chapters for issues')
@click.option('--log', type=click.Path(), help='Save operation log to file')
@click.option('--config', type=click.Path(exists=True), help='Load configuration from file')
@click.option('--parallel/--no-parallel', default=False, help='Process chapters in parallel')
@click.option('--max-workers', type=click.IntRange(min=1), default=4, help='Maximum parallel workers (default: 4)')
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
        click.echo(f"  yotoize process audiobook.m4b --config {config_path} --split ./chapters")
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
@click.option('--parallel', is_flag=True, help='Process chapters in parallel within each file')
@click.option('--max-workers', type=click.IntRange(min=1), default=4, help='Maximum parallel workers (default: 4)')
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
    
    files_to_process = list(dict.fromkeys(Path(p).resolve() for p in files_to_process))
    if output_dir:
        destinations = [str(derive_output_folder_name(p, Path(output_dir))).casefold() for p in files_to_process]
        if len(set(destinations)) != len(destinations):
            raise click.ClickException('Multiple inputs resolve to the same output folder; process them separately.')
    click.echo(f"Processing {len(files_to_process)} files...", err=True)
    
    # Process each file
    success_count = 0
    for audio_file in tqdm(files_to_process, desc="Files", disable=verbose):
        args = [str(audio_file), '--skip-existing']
        if output_dir:
            args.extend(['--split', output_dir])
        for name in ['verbose', 'format', 'filename_pattern', 'preserve_metadata',
                     'extract_cover', 'embed_cover', 'playlist', 'parallel', 'max_workers']:
            if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                value = ctx.params[name]
                args.append('--' + name.replace('_', '-'))
                if not isinstance(value, bool):
                    args.append(str(value))
        try:
            with main.make_context('process', args, parent=ctx) as process_ctx:
                main.invoke(process_ctx)
            success = True
        except SystemExit as exc:
            success = exc.code == 0
        except click.ClickException as exc:
            exc.show()
            success = False
        
        if success:
            success_count += 1
    
    click.echo(f"\nProcessed {success_count}/{len(files_to_process)} files successfully.", err=True)
    sys.exit(0 if success_count == len(files_to_process) else 1)


if __name__ == '__main__':
    cli()
