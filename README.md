# Yotoize

Extract and split audiobook chapters from embedded metadata in MP3 and M4B files.

## Installation

### Prerequisites

**FFmpeg** (required for audio processing):

- **macOS:**

  ```bash
  brew install ffmpeg
  ```

- **Linux (Ubuntu/Debian):**

  ```bash
  sudo apt update && sudo apt install ffmpeg
  ```

- **Windows:**
  Download from [ffmpeg.org](https://ffmpeg.org/download.html) or use Chocolatey:

  ```bash
  choco install ffmpeg
  ```

**uv** (Python package manager):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or via Homebrew on macOS:

```bash
brew install uv
```

### Install Yotoize

```bash
# Install yotoize
uv pip install -e .
```

## Quick Start

```bash
# Extract chapters and display them
yotoize audiobook.m4b

# Extract chapters and split into separate files
yotoize audiobook.m4b --split ./chapters

# Save chapter data to JSON
yotoize audiobook.m4b --output chapters.json

# Split into MP3 files instead of M4B
yotoize audiobook.m4b --split ./chapters --format mp3
```

## How It Works

Yotoize extracts chapter information from embedded metadata in audio files using `ffprobe`. If your audiobook file has chapter markers embedded (which many commercial audiobooks do), it will extract:

- Chapter start and end times
- Chapter titles
- Chapter durations

Then, if requested, it splits the audio file into separate chapter files using `ffmpeg`, preserving:

- Original audio quality and channel layout (stereo, 5.1, etc.)
- Chapter titles as file metadata
- Sequential numbering for easy sorting

## Usage

### Basic Usage

```bash
yotoize <audio_file>
```

Displays detected chapters with their start times, end times, durations, and titles.

### Main Options

#### File Operations
- `--output, -o`: Save chapter data to a JSON file
- `--split, -s`: Split audio into chapter files in the specified directory. **Automatically creates a subfolder** named after the input file (with extension and common patterns removed)
- `--format, -f`: Output format for split files (`m4b`, `m4a`, `mp3`, or `wav`). Default: auto-detect based on input
- `--dry-run`: Preview what would happen without actually splitting
- `--skip-existing`: Skip chapters that already exist in output directory

#### Chapter Selection & Filtering
- `--chapters`: Chapter range to process (e.g., `"1,3,5-7"` or `"1-10"`)
- `--min-duration`: Minimum chapter duration in seconds
- `--max-duration`: Maximum chapter duration in seconds
- `--title-pattern`: Regex pattern to match chapter titles
- `--select-interactive, -i`: Interactive chapter selection mode
- `--merge`: Merge chapters (e.g., `--merge "1-3" --merge "5-7"`)

#### Chapter Editing
- `--rename`: Rename chapters (format: `"number:new title"`, e.g., `--rename "1:Introduction"`)
- `--rename-interactive`: Interactive chapter renaming mode

#### Metadata & Cover Art
- `--preserve-metadata`: Preserve metadata (artist, album, year, genre) in split files
- `--extract-cover`: Extract cover art from source file
- `--embed-cover`: Embed cover art in split files
- `--cover-path`: Path to save/extract cover art

#### Audio Quality
- `--bitrate`: Audio bitrate (e.g., `"192k"`, `"256k"`)
- `--codec`: Audio codec (e.g., `"aac"`, `"libmp3lame"`)

#### Filename Customization
- `--filename-pattern`: Custom filename pattern (default: `"{number:02d} - {title}"`)
  
  Supported placeholders:
  - `{number}` - Chapter number (1-based)
  - `{number:02d}` - Chapter number with zero-padding
  - `{title}` - Chapter title
  - `{artist}` - Artist from metadata
  - `{album}` - Album from metadata
  - `{year}` - Year from metadata

#### Advanced Features
- `--remove-silence`: Remove silence at chapter boundaries
- `--silence-threshold`: Silence threshold in dB (default: -50)
- `--silence-duration`: Minimum silence duration in seconds (default: 0.5)
- `--playlist`: Generate M3U playlist file
- `--playlist-name`: Name for playlist file
- `--statistics`: Show chapter statistics
- `--validate`: Validate chapters for issues (overlaps, gaps, etc.)
- `--parallel`: Process chapters in parallel for faster splitting
- `--max-workers`: Maximum parallel workers (default: 4)
- `--log`: Save operation log to file
- `--config`: Load configuration from TOML or JSON file
- `--verbose, -v`: Show detailed error messages and tracebacks

### Batch Processing

Process multiple files at once:

```bash
# Process multiple files
yotoize batch file1.m4b file2.m4b file3.m4b --output-dir ./output

# Process all files in a directory
yotoize batch --batch-dir ./audiobooks --output-dir ./output --format mp3

# With additional options
yotoize batch --batch-dir ./audiobooks --output-dir ./output --preserve-metadata --playlist --parallel
```

### Examples

```bash
# Extract and display chapters
yotoize audiobook.m4b

# Extract chapters and save metadata to JSON
yotoize audiobook.m4b --output chapters.json

# Split into M4A files
yotoize audiobook.m4b --split ./chapters --format m4a

# Split into MP3 files with verbose output
yotoize audiobook.m4b --split ./chapters --format mp3 --verbose

# Note: Creates subfolder automatically
# Input: "Harry Potter (Full-Cast Edition).m4b"
# Output: ./chapters/Harry Potter/00 - Chapter 1.mp3

# Split with custom filename pattern
yotoize audiobook.m4b --split ./chapters --filename-pattern "{number:03d} - {title}"

# Split only chapters 1-5 and 10-15
yotoize audiobook.m4b --split ./chapters --chapters "1-5,10-15"

# Split with metadata preservation and cover art
yotoize audiobook.m4b --split ./chapters --preserve-metadata --embed-cover

# Split with silence removal
yotoize audiobook.m4b --split ./chapters --remove-silence

# Generate playlist
yotoize audiobook.m4b --split ./chapters --playlist --playlist-name "My Audiobook"

# Show statistics
yotoize audiobook.m4b --statistics

# Validate chapters
yotoize audiobook.m4b --validate

# Interactive chapter selection
yotoize audiobook.m4b --split ./chapters --select-interactive

# Rename chapters
yotoize audiobook.m4b --split ./chapters --rename "1:Introduction" --rename "2:Getting Started"

# Merge chapters 1-3 into a single file
yotoize audiobook.m4b --split ./chapters --merge "1-3"

# Process with config file
yotoize audiobook.m4b --config myconfig.toml --split ./chapters

# Generate a default config file first
yotoize config myconfig.toml
# Then edit it and use it
yotoize audiobook.m4b --config myconfig.toml --split ./chapters

# Dry run to preview
yotoize audiobook.m4b --split ./chapters --dry-run

# Parallel processing for faster splitting
yotoize audiobook.m4b --split ./chapters --parallel --max-workers 8
```

## Configuration Files

Yotoize supports configuration files in TOML or JSON format. This allows you to save common settings and reuse them across operations.

### Example TOML Config (`config.toml`)

```toml
[split]
format = "mp3"
filename_pattern = "{number:02d} - {title}"
preserve_metadata = true
embed_cover = true
playlist = true
skip_existing = false
remove_silence = false
parallel = false
max_workers = 4

[filter]
# chapters = "1-10"
# min_duration = 60.0
# max_duration = 3600.0
# title_pattern = "Chapter.*"
# merge = ["1-3", "5-7"]

[rename]
# 1 = "Introduction"
# 2 = "Getting Started"

[output]
statistics = false
validate = false
verbose = false
```

### Example JSON Config (`config.json`)

```json
{
  "split": {
    "format": "mp3",
    "filename_pattern": "{number:02d} - {title}",
    "preserve_metadata": true,
    "embed_cover": true,
    "playlist": true
  },
  "filter": {
    "chapters": "1-10",
    "min_duration": 60.0
  },
  "output": {
    "statistics": true,
    "validate": true
  }
}
```

### Using Config Files

Yotoize automatically searches for config files in standard locations if `--config` is not provided:

**Search order:**
1. Current directory: `yotoize.toml`, `.yotoize.toml`
2. User config directory:
   - **Linux**: `~/.config/yotoize/config.toml` or `~/.config/yotoize/yotoize.toml`
   - **macOS**: `~/Library/Application Support/yotoize/config.toml` or `~/Library/Application Support/yotoize/yotoize.toml`
   - **Windows**: `%APPDATA%\yotoize\config.toml` or `%APPDATA%\yotoize\yotoize.toml`

```bash
# Generate a default config file
yotoize config                    # Creates yotoize.toml in current directory
yotoize config --user             # Creates config.toml in user config directory
yotoize config myconfig.toml     # Creates myconfig.toml
yotoize config config.json --format json  # Creates config.json

# Open config file in editor ($EDITOR must be set)
yotoize config --editor           # Opens existing config file (searches standard locations)
yotoize config --user --editor    # Creates and opens config.toml in user config directory
yotoize config --editor myconfig.toml  # Opens myconfig.toml

# Config file will be automatically found if placed in standard location
# No need to specify --config if using yotoize.toml in current directory
yotoize audiobook.m4b --split ./chapters

# Or explicitly specify config file
yotoize audiobook.m4b --config config.toml --split ./chapters

# Config values override defaults but can be overridden by command-line options
yotoize audiobook.m4b --config config.toml --split ./chapters --format m4b
```

### Config File Structure

The config file uses dot notation for nested keys:

- `split.format` - Output format
- `split.filename_pattern` - Filename pattern
- `split.preserve_metadata` - Preserve metadata (boolean)
- `split.embed_cover` - Embed cover art (boolean)
- `split.playlist` - Generate playlist (boolean)
- `split.skip_existing` - Skip existing files (boolean)
- `split.remove_silence` - Remove silence (boolean)
- `split.parallel` - Parallel processing (boolean)
- `split.max_workers` - Max parallel workers (integer)
- `filter.chapters` - Chapter range string
- `filter.min_duration` - Minimum duration (float)
- `filter.max_duration` - Maximum duration (float)
- `filter.title_pattern` - Title regex pattern
- `filter.merge` - List of merge ranges
- `rename.<number>` - Chapter rename mapping
- `output.statistics` - Show statistics (boolean)
- `output.validate` - Validate chapters (boolean)
- `output.verbose` - Verbose output (boolean)

See `config.example.toml` and `config.example.json` in the repository for complete examples.

## Output Format

### Console Output

When chapters are detected, they're displayed in a table:

```
Found 19 chapters:

Chapter     Start        End          Duration     Title
----------------------------------------------------------------------------------------------------
1            00:00:00     00:01:04     00:01:04     Opening Credits
2            00:01:04     00:33:50     00:32:46     Chapter 1 - The Boy Who Lived
3            00:33:50     00:59:18     00:25:28     Chapter 2 - The Vanishing Glass
...
```

### JSON Output

When using `--output`, the JSON file contains:

```json
{
  "audio_file": "path/to/audiobook.m4b",
  "duration": 31287.582,
  "metadata": {
    "title": "Book Title",
    "artist": "Author Name",
    "album": "Book Title",
    "year": "2023"
  },
  "chapters": [
    {
      "number": 1,
      "start_time": 0.0,
      "end_time": 63.531,
      "duration": 63.531,
      "confidence": 1.0,
      "title": "Opening Credits"
    },
    {
      "number": 2,
      "start_time": 63.531,
      "end_time": 1970.531,
      "duration": 1907.0,
      "confidence": 1.0,
      "title": "Chapter 1 - The Boy Who Lived"
    }
  ]
}
```

### Split Files

When using `--split`, yotoize automatically creates a subfolder in the specified directory, named after the input file. The folder name is cleaned up by:

- Removing the file extension (`.m4b`, `.mp3`, etc.)
- Removing common patterns like "(Full-Cast Edition)", "(Unabridged)", "(Narrated by...)", etc.
- Cleaning up extra whitespace

**Example:**
- Input file: `Harry Potter and the Sorcerer's Stone (Full-Cast Edition).m4b`
- Output directory specified: `./output`
- Created folder: `./output/Harry Potter and the Sorcerer's Stone/`
- Chapter files: `./output/Harry Potter and the Sorcerer's Stone/00 - Opening Credits.mp3`, etc.

Files are named sequentially with zero-padding:

- `00 - Opening Credits.m4b`
- `01 - Chapter 1 - The Boy Who Lived.m4b`
- `02 - Chapter 2 - The Vanishing Glass.m4b`
- ...

Each file includes:

- Title metadata matching the filename
- Original audio quality and channel layout preserved
- Proper sequential numbering for file browser sorting
- Additional metadata (artist, album, etc.) if `--preserve-metadata` is used
- Cover art if `--embed-cover` is used

### Playlist Files

When using `--playlist`, an M3U playlist file is generated:

```
#EXTM3U
#EXTINF:63,Opening Credits
00 - Opening Credits.mp3
#EXTINF:1907,Chapter 1 - The Boy Who Lived
01 - Chapter 1 - The Boy Who Lived.mp3
...
```

## Requirements

- Python 3.10 to 3.13 (Python 3.14+ not yet supported by dependencies)
- uv (for dependency management)
- FFmpeg and FFprobe (required for audio processing)

### Installing FFmpeg

**macOS:**

```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian):**

```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) or use Chocolatey:

```bash
choco install ffmpeg
```

## Features

### Core Features
- ✅ Extract chapters from embedded metadata
- ✅ Split audio into separate chapter files
- ✅ Preserve audio quality and channel layout
- ✅ Support for MP3, M4B, M4A, and WAV formats

### Advanced Features
- ✅ **Batch Processing** - Process multiple files at once
- ✅ **Enhanced Metadata** - Preserve artist, album, year, genre
- ✅ **Cover Art** - Extract and embed cover art
- ✅ **Progress Bars** - Visual progress indicators
- ✅ **Resume/Skip** - Skip existing files
- ✅ **Custom Filenames** - Flexible filename patterns
- ✅ **Dry Run** - Preview operations
- ✅ **Chapter Filtering** - Select chapters by number, duration, or title pattern
- ✅ **Audio Quality** - Control bitrate and codec
- ✅ **Playlist Generation** - Create M3U playlists
- ✅ **Statistics** - Chapter statistics and analysis
- ✅ **Validation** - Detect overlaps, gaps, and issues
- ✅ **Config Files** - Save and reuse settings
- ✅ **Chapter Merging** - Combine multiple chapters
- ✅ **Silence Removal** - Remove silence at boundaries
- ✅ **Interactive Selection** - Select chapters interactively
- ✅ **Chapter Renaming** - Rename chapters before splitting
- ✅ **Logging** - Save operation logs
- ✅ **Format Detection** - Auto-detect best output format
- ✅ **Parallel Processing** - Faster splitting with multiple workers

## Limitations

- **Requires embedded chapter metadata**: This tool only works with audio files that have chapter markers embedded in their metadata. If your audiobook doesn't have embedded chapters, this tool won't be able to detect them.
- **No AI detection**: Unlike some other tools, this doesn't use AI to detect chapters from audio content. It relies solely on metadata.

## Why This Approach?

Many commercial audiobooks (especially from Audible, Apple Books, etc.) include chapter markers in their metadata. Extracting from metadata is:

- **Fast**: No audio processing needed
- **Accurate**: Uses the publisher's chapter markers
- **Reliable**: No guessing or approximation

If your audiobook doesn't have embedded chapters, you'll need a different tool that uses audio analysis or AI detection.

## License

MIT
