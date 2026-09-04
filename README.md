# Yotoize

Extract and split audiobook chapters from embedded metadata in MP3 and M4B/M4A files.

Yotoize reads the chapter markers publishers embed in audiobook files (via `ffprobe`) and
splits the file into one audio file per chapter (via `ffmpeg`). It does no audio analysis —
if the file has no embedded chapters, yotoize can't help.

## Requirements

A [prebuilt binary](#prebuilt-binary) needs nothing else — it carries its own copy of
FFmpeg. Installing [from source](#from-source) needs:

- FFmpeg, including `ffprobe` — both must be on your `PATH`
- Python 3.10–3.13 (3.14+ is not supported by the current dependency set) and
  [uv](https://docs.astral.sh/uv/)

### Installing FFmpeg

```bash
# macOS
brew install ffmpeg

# Linux (Ubuntu/Debian)
sudo apt update && sudo apt install ffmpeg

# Windows
choco install ffmpeg    # or download from https://ffmpeg.org/download.html
```

### Installing uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or on macOS: brew install uv
```

## Installation

### Prebuilt binary

Each release on the [releases page](https://github.com/mattrasband/yotoize/releases) ships
a self-contained bundle with FFmpeg included — no Python, no uv, no separate FFmpeg
install.

| Download | For | Size |
| --- | --- | --- |
| `yotoize-macos-arm64.tar.gz` | Apple Silicon Macs (M1 and later) | ~65 MB (~150 MB unpacked) |
| `yotoize-macos-x86_64.tar.gz` | Intel Macs | ~65 MB (~150 MB unpacked) |
| `yotoize-windows-x86_64.zip` | Windows (64-bit) | ~75 MB (~200 MB unpacked) |

FFmpeg is most of that. Each archive has a matching `.sha256` if you want to verify the
download.

Each archive unpacks to a `yotoize/` **folder**, not a lone file — the executable needs the
`_internal/` directory beside it, so move or copy the whole folder rather than pulling the
binary out of it.

**macOS.** The binaries are unsigned and un-notarized, so Gatekeeper will refuse to run
them until you clear the quarantine flag:

```bash
tar -xzf yotoize-macos-arm64.tar.gz
xattr -dr com.apple.quarantine yotoize
./yotoize/yotoize --version
```

To get a `yotoize` command, move the folder somewhere permanent and symlink the executable
onto your `PATH`:

```bash
mv yotoize ~/.local/share/yotoize
ln -s ~/.local/share/yotoize/yotoize /usr/local/bin/yotoize
```

**Windows.** Unzip and run `yotoize\yotoize.exe` from a terminal. SmartScreen may warn on
first run because the binary is unsigned — choose "More info" → "Run anyway".

**Using a different FFmpeg.** The bundled copy takes precedence. To point a release build
at your own FFmpeg instead, set `YOTOIZE_FFMPEG` and `YOTOIZE_FFPROBE` to the executables
you want:

```bash
YOTOIZE_FFMPEG=/opt/homebrew/bin/ffmpeg YOTOIZE_FFPROBE=/opt/homebrew/bin/ffprobe \
  yotoize process audiobook.m4b --split ./out
```

**Licensing.** yotoize is MIT. The bundled FFmpeg is a separate program under its own
license — GPL v3 for the macOS builds, LGPL for the Windows build. Each archive carries
`FFMPEG-NOTICE.txt` with the exact build, its source, and where to obtain FFmpeg's
corresponding source. Installing from source pulls in no FFmpeg and so carries none of
this.

### From source

**As a standalone tool** (recommended — puts `yotoize` on your `PATH`):

```bash
git clone https://github.com/mattrasband/yotoize
cd yotoize
uv tool install .
```

If uv warns that the tool directory isn't on your `PATH`, run `uv tool update-shell` and
restart your shell.

**For development / running from a checkout:**

```bash
git clone https://github.com/mattrasband/yotoize
cd yotoize
uv sync
uv run yotoize --version
```

With this setup, prefix every command below with `uv run`.

> `uv pip install -e .` only works if a virtualenv already exists and is active. Use
> `uv sync` (which creates `.venv` for you) or `uv tool install .` instead.

## Quick Start

**All chapter work goes through the `process` subcommand.** `yotoize <file>` on its own
will fail with `Error: No such command`.

```bash
# Show the chapters in a file
yotoize process audiobook.m4b

# Split into per-chapter files under ./chapters
yotoize process audiobook.m4b --split ./chapters

# Split to MP3 instead of the auto-detected format
yotoize process audiobook.m4b --split ./chapters --format mp3

# Dump chapter data as JSON
yotoize process audiobook.m4b --output chapters.json
```

The three commands are:

| Command | What it does |
| --- | --- |
| `yotoize process <file>` | Inspect / split a single audiobook. This is the main command. |
| `yotoize config [file]` | Generate or edit a config file. |
| `yotoize batch ...` | Multi-file processing — **currently broken**, see [Known issues](#known-issues). |

## How It Works

`ffprobe -show_chapters` reads the chapter markers embedded in the file. For each chapter
yotoize gets a start time, end time, and (usually) a title. When `--split` is given, each
chapter is re-encoded with `ffmpeg` into its own file, carrying the chapter title as the
`title` tag.

Note that splitting **re-encodes** rather than stream-copies (default 192k AAC for
m4b/m4a, 192k libmp3lame for mp3, pcm_s16le for wav). Use `--bitrate` / `--codec` to
change that.

## Usage

```
yotoize process [OPTIONS] AUDIO_FILE
```

With no other options, this prints a table of the detected chapters and exits.

### File operations

| Option | Description |
| --- | --- |
| `--output`, `-o PATH` | Write chapter data to a JSON file |
| `--split`, `-s DIR` | Split into chapter files. **Creates a subfolder** inside `DIR` named after the input file (see [Split output](#split-output)) |
| `--format`, `-f` | `m4b`, `m4a`, `mp3`, or `wav`. Default: derived from the input file (`.m4b`/`.m4a` → `m4b`, everything else → `mp3`) |
| `--dry-run` | Print the files that would be created without writing anything |
| `--skip-existing` | Skip chapters whose output file already exists |

### Chapter selection & filtering

| Option | Description |
| --- | --- |
| `--chapters RANGE` | Chapters to process, 1-based (`"1,3,5-7"` or `"1-10"`) |
| `--min-duration SECS` | Drop chapters shorter than this |
| `--max-duration SECS` | Drop chapters longer than this |
| `--title-pattern REGEX` | Keep only chapters whose title matches (case-insensitive search) |
| `--select-interactive`, `-i` | Prompt for a chapter selection before processing |
| `--merge RANGE` | Merge a range into one file; repeatable (`--merge "1-3" --merge "5-7"`). The merged title becomes `"<first title> - <last title>"` |

Filters are applied in order: `--merge` first, then the filters above, then renames.

### Chapter renaming

| Option | Description |
| --- | --- |
| `--rename "N:Title"` | Rename chapter N; repeatable. Numbering is 1-based and applies *after* merging and filtering |
| `--rename-interactive` | Prompt for renames one at a time |

### Metadata & cover art

| Option | Description |
| --- | --- |
| `--preserve-metadata` | Copy artist/album/year/genre/track/comment onto the split files |
| `--extract-cover` | Pull cover art out of the source file |
| `--embed-cover` | Embed the cover art into the split files (implies extraction) |
| `--cover-path PATH` | Where to write the extracted cover. Defaults to `cover.jpg` in the output folder when `--split` is used, otherwise next to the input file |

`--preserve-metadata` is also what makes the `{artist}`, `{album}`, `{year}`, and `{genre}`
filename placeholders resolve, and what populates the `metadata` object in `--output` JSON.
Without one of `--preserve-metadata`, `--extract-cover`, or `--embed-cover`, no metadata is
read at all and those placeholders expand to empty strings.

### Audio quality

| Option | Description |
| --- | --- |
| `--bitrate` | e.g. `"192k"`, `"256k"`. Default 192k; ignored for `wav` |
| `--codec` | e.g. `"aac"`, `"libmp3lame"`, `"pcm_s16le"` |

### Filename pattern

`--filename-pattern` (default `"{number:02d} - {title}"`) supports:

- `{number}` — chapter number, 1-based
- `{number:0Nd}` — zero-padded chapter number (`{number:02d}` → `01`, `02`, …)
- `{title}` — chapter title, falling back to `Chapter N`
- `{artist}`, `{album}`, `{year}`, `{genre}` — from file metadata (requires `--preserve-metadata`)

Characters illegal in filenames (`<>:"/\|?*`) are replaced with `_`, and titles are
truncated at 200 characters.

### Advanced

| Option | Description |
| --- | --- |
| `--remove-silence` | Trim silence at chapter boundaries |
| `--silence-threshold DB` | Silence threshold in dB (default `-50`) |
| `--silence-duration SECS` | Minimum silence run to detect (default `0.5`) |
| `--playlist` | Write an M3U playlist alongside the split files (requires `--split`) |
| `--playlist-name NAME` | Playlist filename without extension. Default: the album name, else `playlist` |
| `--statistics` | Print chapter count and shortest/longest/average durations |
| `--validate` | Warn about overlapping chapters, gaps > 1s, and missing end times |
| `--parallel` | Split chapters concurrently |
| `--max-workers N` | Worker count for `--parallel` (default `4`) |
| `--log PATH` | Write a timestamped operation log |
| `--config PATH` | Load a config file (must exist) |
| `--verbose`, `-v` | Show tracebacks and per-chapter progress instead of a progress bar |

### Examples

```bash
# Inspect
yotoize process audiobook.m4b
yotoize process audiobook.m4b --statistics --validate

# Split with metadata and cover art
yotoize process audiobook.m4b --split ./out --preserve-metadata --embed-cover

# Only chapters 1-5 and 10-15, as MP3, three digits of padding
yotoize process audiobook.m4b --split ./out --chapters "1-5,10-15" \
  --format mp3 --filename-pattern "{number:03d} - {title}"

# Merge the front matter into one file and rename it
yotoize process audiobook.m4b --split ./out --merge "1-3" --rename "1:Front Matter"

# Preview first, then run it for real in parallel
yotoize process audiobook.m4b --split ./out --dry-run
yotoize process audiobook.m4b --split ./out --parallel --max-workers 8

# Playlist + log
yotoize process audiobook.m4b --split ./out --playlist --playlist-name "My Audiobook" --log run.log
```

## Output

### Console

```
Found 3 chapters:

Chapter    Start        End          Duration     Title
----------------------------------------------------------------------------------------------------
1          00:00:00     00:00:10     00:00:10     Opening Credits
2          00:00:10     00:00:20     00:00:10     Chapter One
3          00:00:20     00:00:30     00:00:10     Chapter Two
```

### JSON (`--output`)

```json
{
  "audio_file": "Test Book (Unabridged).m4b",
  "duration": 30.023219954648525,
  "metadata": {
    "title": "Test Book",
    "artist": "Test Author",
    "album": "Test Book"
  },
  "chapters": [
    {
      "number": 1,
      "start_time": 0.0,
      "end_time": 10.0,
      "duration": 10.0,
      "confidence": 1.0,
      "title": "Opening Credits"
    }
  ]
}
```

`metadata` is `{}` unless `--preserve-metadata` (or a cover flag) is passed. `confidence`
is always `1.0` — chapters come from metadata, so there is nothing to estimate.

### Split output

`--split DIR` writes into a subfolder of `DIR` named after the input file, with the
extension and common edition suffixes stripped — `(Full-Cast Edition)`, `(Unabridged)`,
`(Abridged)`, `(Narrated by …)`, `(Read by …)`, `(Audible …)`, `(… Edition)`, and trailing
`- Unabridged` / `- Full Cast`.

```
Input:  Harry Potter and the Sorcerer's Stone (Full-Cast Edition).m4b
Run:    yotoize process "…(Full-Cast Edition).m4b" --split ./out --format mp3

./out/Harry Potter and the Sorcerer's Stone/
├── 01 - Opening Credits.mp3
├── 02 - Chapter 1 - The Boy Who Lived.mp3
├── 03 - Chapter 2 - The Vanishing Glass.mp3
└── …
```

Numbering starts at `01`.

### Playlist (`--playlist`)

```
#EXTM3U
#EXTINF:10,Opening Credits
01 - Opening Credits.mp3
#EXTINF:10,Chapter One
02 - Chapter One.mp3
```

## Configuration files

> **Only four settings are read from config files today:** `split.format`,
> `split.filename_pattern`, `split.bitrate`, and `split.codec`. Every other key —
> including the booleans in the generated default config and everything under
> `[filter]`, `[rename]`, and `[output]` — is parsed but **silently ignored**. Pass those
> as command-line flags. See [Known issues](#known-issues).

### Generating one

```bash
yotoize config                            # ./yotoize.toml
yotoize config --user                     # config.toml in the user config dir
yotoize config myconfig.toml              # a specific path
yotoize config config.json --format json  # JSON instead of TOML
yotoize config --force                    # overwrite an existing file
yotoize config --editor                   # open the found config in $EDITOR
```

### Where it's looked up

If `--config` isn't given, yotoize searches, in order, and uses the first hit:

1. `./yotoize.toml`, `./.yotoize.toml`, `./yotoize.json`, `./.yotoize.json`
2. In the user config directory: `config.toml`, `yotoize.toml`, `config.json`, `yotoize.json`

The user config directory is:

- **macOS:** `~/Library/Application Support/yotoize/`
- **Linux:** `$XDG_CONFIG_HOME/yotoize/`, else `~/.config/yotoize/`
- **Windows:** `%APPDATA%\yotoize\`

The path in use is echoed to stderr on every run. Command-line flags always win over
config values.

### What actually takes effect

```toml
[split]
format = "mp3"                              # applied
filename_pattern = "{number:02d} - {title}" # applied
bitrate = "192k"                            # applied
codec = "libmp3lame"                        # applied
```

`config.example.toml` and `config.example.json` in the repo show the full intended schema,
but treat everything outside those four keys as aspirational for now.

## Known issues

- **`yotoize batch` crashes.** It calls the processing function without the `rename_map`
  and `rename_interactive` arguments and dies with
  `TypeError: process_single_file() missing 2 required positional arguments`. Loop over
  `yotoize process` in a shell instead:

  ```bash
  for f in ./audiobooks/*.m4b; do
    yotoize process "$f" --split ./out --format mp3 --preserve-metadata --skip-existing
  done
  ```

- **Most config keys are ignored** — see the note in [Configuration files](#configuration-files).
- **Bare `yotoize <file>` doesn't work** despite the group being set up to try; use
  `yotoize process <file>`. A few of the tool's own hint messages still print the bare form.
- **Splitting always re-encodes**, so you lose a generation of quality even when the
  output format matches the input.

## Limitations

- **Requires embedded chapter metadata.** Files without chapter markers produce
  `No chapters found in file metadata.` and a non-zero exit.
- **No audio analysis or AI detection.** Chapter boundaries come only from the publisher's
  markers, which makes this fast and exact — but useless on unmarked files.

## Releasing

Pushing a `v*` tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml).
For each of macOS arm64, macOS x86_64, and Windows x64 it downloads static FFmpeg and
FFprobe builds, bundles them into a PyInstaller `--onedir` build, smoke tests the result by
splitting a generated chaptered file with the binary it just built, and attaches the
archive to the GitHub release for that tag:

```bash
git tag v0.3.0
git push origin v0.3.0
```

The workflow can also be run manually from the Actions tab against a tag that already
exists, which re-uploads the binaries to the matching release.

The FFmpeg builds come from [martin-riedl.de](https://ffmpeg.martin-riedl.de/) (macOS, both
arches) and [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) (Windows, LGPL).
Neither is pinned to an exact version, so a release picks up whatever those publish that
day, and an outage on either breaks the build.

> `yotoize --version` reports a hardcoded `0.2.0` from `yotoize/cli.py`, independent of the
> tag you release and of the `0.1.0` in `pyproject.toml`. Worth reconciling before cutting
> a real release.

## License

MIT
