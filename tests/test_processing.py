# /// script
# requires-python = ">=3.10"
# dependencies = ["click>=8.1.7", "mutagen>=1.47.0", "tqdm>=4.66.0", "tomli-w>=1.0.0", "tomli>=2.0.0; python_version < '3.11'"]
# ///
"""Behavioral regressions using a two-second synthetic audiobook."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from mutagen.mp4 import MP4, MP4Cover

from yotoize.cli import cli


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'Book.m4b'
        metadata = self.root / 'chapters.txt'
        metadata.write_text(';FFMETADATA1\n' + ''.join(
            f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={i * 1000}\nEND={(i + 1) * 1000}\ntitle=Same Title\n'
            for i in range(2)), encoding='utf-8')
        self.ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                    '-i', str(metadata), '-map_metadata', '1', '-c:a', 'aac', str(self.source))
        self.runner = CliRunner()
        self.config = self.root / 'settings.json'
        self.config.write_text('{}', encoding='utf-8')
        self.out = self.root / 'out'

    def ffmpeg(self, *args):
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', *args], check=True, capture_output=True)

    def run_process(self, *args):
        return self.runner.invoke(cli, ['process', str(self.source), '--config', str(self.config), *args])

    def split(self, *args):
        return self.run_process('--split', str(self.out), '--format', 'mp3', *args)

    def test_successful_split_and_playlist(self):
        result = self.split('--playlist')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(list((self.out / 'Book').glob('*.mp3'))), 2)
        self.assertTrue((self.out / 'Book/playlist.m3u').exists())

    def test_wav_split(self):
        result = self.run_process('--split', str(self.out), '--format', 'wav')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(list((self.out / 'Book').glob('*.wav'))), 2)

    def test_encoding_failure_is_nonzero_and_preserves_previous_output(self):
        self.assertEqual(self.split().exit_code, 0)
        before = {p.name: p.read_bytes() for p in (self.out / 'Book').iterdir()}
        for extra in [[], ['--parallel']]:
            result = self.split('--codec', 'invalid_codec', '--playlist', *extra)
            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertIn('invalid_codec', result.output)
            self.assertFalse((self.out / 'Book/playlist.m3u').exists())
            self.assertEqual(before, {p.name: p.read_bytes() for p in (self.out / 'Book').iterdir()})

    def test_collision_rejected_before_encoding(self):
        result = self.split('--filename-pattern', '{title}')
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn('Duplicate output filename', result.output)
        self.assertFalse(self.out.exists())

    def test_resume_checks_existing_audio(self):
        self.assertEqual(self.split().exit_code, 0)
        target = next((self.out / 'Book').glob('*.mp3'))
        previous = target.stat().st_mtime_ns
        self.assertEqual(self.split('--skip-existing').exit_code, 0)
        self.assertEqual(target.stat().st_mtime_ns, previous)
        target.write_bytes(b'broken')
        result = self.split('--skip-existing')
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertEqual(target.read_bytes(), b'broken')

    def add_cover(self):
        cover = self.root / 'fixture.jpg'
        self.ffmpeg('-f', 'lavfi', '-i', 'color=red:s=32x32', '-frames:v', '1', str(cover))
        book = MP4(self.source)
        book['covr'] = [MP4Cover(cover.read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
        book.save()

    def test_dry_run_writes_nothing(self):
        self.add_cover()
        before = set(self.root.rglob('*'))
        result = self.split('--dry-run', '--embed-cover', '--playlist',
                            '--output', str(self.root / 'chapters.json'), '--log', str(self.root / 'run.log'))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(before, set(self.root.rglob('*')))

    def test_cover_embedding_into_new_directory(self):
        self.add_cover()
        for fmt in ['mp3', 'm4b', 'm4a']:
            result = self.run_process('--split', str(self.out / fmt), '--format', fmt, '--embed-cover')
            self.assertEqual(result.exit_code, 0, result.output)
            target = next((self.out / fmt / 'Book').glob(f'*.{fmt}'))
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(target)],
                                   check=True, capture_output=True, text=True)
            streams = json.loads(probe.stdout)['streams']
            self.assertEqual(sum(s['codec_type'] == 'audio' for s in streams), 1)
            self.assertTrue(any(s.get('disposition', {}).get('attached_pic') for s in streams))

    def test_config_applies_flags_filters_rename_and_cli_precedence(self):
        self.config.write_text(json.dumps({'split': {'playlist': True, 'filename_pattern': 'custom-{number}'},
                                          'filter': {'chapters': '2'}, 'rename': {'1': 'Selected'},
                                          'output': {'statistics': True}}))
        result = self.split('--filename-pattern', '{number:02d} - {title}')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((self.out / 'Book/01 - Selected.mp3').exists())
        self.assertTrue((self.out / 'Book/playlist.m3u').exists())
        self.assertIn('Total chapters: 1', result.output)

    def test_bad_config_fails_clearly(self):
        for data in [{'split': {'max_workers': 0}}, {'split': {'typo': True}}, {'split': {'format': 'invalid'}}]:
            self.config.write_text(json.dumps(data))
            result = self.split()
            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertFalse(self.out.exists())

    def test_cli_can_disable_config_flags(self):
        self.config.write_text(json.dumps({'split': {'playlist': True, 'embed_cover': True}}))
        result = self.split('--no-playlist', '--no-embed-cover')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse((self.out / 'Book/playlist.m3u').exists())
        self.assertNotIn('No cover art', result.output)

    def test_batch_applies_config_and_reports_failure(self):
        self.config.write_text(json.dumps({'split': {'codec': 'invalid_codec'}}))
        with patch('yotoize.cli.find_config_file', return_value=self.config):
            result = self.runner.invoke(cli, ['batch', str(self.source), '--output-dir', str(self.out)])
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn('Processed 0/1', result.output)

    def test_cover_write_failure_is_reported(self):
        self.add_cover()
        target = self.root / 'directory.jpg'
        target.mkdir()
        result = self.split('--embed-cover', '--cover-path', str(target))
        self.assertNotEqual(result.exit_code, 0, result.output)

    def test_empty_selection_is_failure(self):
        result = self.split('--title-pattern', 'absent')
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertFalse(self.out.exists())

    def test_batch_uses_one_book_subdirectory(self):
        with patch('yotoize.cli.find_config_file', return_value=None):
            result = self.runner.invoke(cli, ['batch', str(self.source), '--output-dir', str(self.out), '--format', 'mp3'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(list((self.out / 'Book').glob('*.mp3'))), 2)
        self.assertFalse((self.out / 'Book/Book').exists())

    def test_batch_rejects_colliding_book_folders(self):
        other = self.root / 'Book (Unabridged).m4b'
        shutil.copyfile(self.source, other)
        result = self.runner.invoke(cli, ['batch', str(self.source), str(other), '--output-dir', str(self.out)])
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn('same output folder', result.output)
        self.assertFalse(self.out.exists())

    def test_batch_continues_after_invalid_input(self):
        broken = self.root / 'Broken.m4b'
        broken.write_bytes(b'not audio')
        with patch('yotoize.cli.find_config_file', return_value=None):
            result = self.runner.invoke(cli, ['batch', str(broken), str(self.source),
                                              '--output-dir', str(self.out), '--format', 'mp3'])
        self.assertNotEqual(result.exit_code, 0, result.output)
        self.assertIn('Processed 1/2', result.output)
        self.assertEqual(len(list((self.out / 'Book').glob('*.mp3'))), 2)


if __name__ == '__main__':
    unittest.main()
