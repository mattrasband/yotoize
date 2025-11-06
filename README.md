# Chapterize

Extract and split audiobook chapters from embedded metadata in MP3 and M4B files.

## Installation

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install chapterize
uv pip install -e .
```

## Quick Start

```bash
# Extract chapters and display them
chapterize audiobook.m4b

# Extract chapters and split into separate files
chapterize audiobook.m4b --split ./chapters

# Save chapter data to JSON
chapterize audiobook.m4b --output chapters.json

# Split into MP3 files instead of M4B
chapterize audiobook.m4b --split ./chapters --format mp3
```

## How It Works

Chapterize extracts chapter information from embedded metadata in audio files using `ffprobe`. If your audiobook file has chapter markers embedded (which many commercial audiobooks do), it will extract:

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
chapterize <audio_file>
```

Displays detected chapters with their start times, end times, durations, and titles.

### Options

- `--output, -o`: Save chapter data to a JSON file
- `--split, -s`: Split audio into chapter files in the specified directory
- `--format, -f`: Output format for split files (`m4b`, `m4a`, `mp3`, or `wav`). Default: `m4b`
- `--verbose, -v`: Show detailed error messages and tracebacks

### Examples

```bash
# Extract and display chapters
chapterize audiobook.m4b

# Extract chapters and save metadata to JSON
chapterize audiobook.m4b --output chapters.json

# Split into M4A files
chapterize audiobook.m4b --split ./chapters --format m4a

# Split into MP3 files with verbose output
chapterize audiobook.m4b --split ./chapters --format mp3 --verbose
```

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

When using `--split`, files are named sequentially with zero-padding:

- `00 - Opening Credits.m4b`
- `01 - Chapter 1 - The Boy Who Lived.m4b`
- `02 - Chapter 2 - The Vanishing Glass.m4b`
- ...

Each file includes:

- Title metadata matching the filename
- Original audio quality and channel layout preserved
- Proper sequential numbering for file browser sorting

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
