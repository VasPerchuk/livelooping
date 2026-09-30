import io
from email.message import Message
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from download_asset import DownloadError, download, public_https


def midi_note():
    events = b"\x00\x90\x3c\x64\x81\x70\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(events)) + events


class Response(io.BytesIO):
    def __init__(self, contents, content_type="application/octet-stream"):
        super().__init__(contents)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def geturl(self):
        return "https://example.org/song.mid"


class DownloaderTest(unittest.TestCase):
    def test_reject_non_https_without_network(self):
        with self.assertRaisesRegex(DownloadError, "HTTPS"):
            public_https("file:///private/song.mid")

    def test_accept_valid_music_and_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            song_dir = Path(folder)
            opener = type("Opener", (), {"open": lambda self, *a, **kw: Response(midi_note())})()
            with patch("download_asset.public_https"), patch("download_asset.build_opener", return_value=opener):
                result = download("https://example.org/song.mid", song_dir, "song.mid")
            self.assertEqual(result["validation"]["status"], "valid")
            self.assertEqual((song_dir / "song.mid").read_bytes(), midi_note())
            with self.assertRaisesRegex(DownloadError, "overwrite"):
                download("https://example.org/song.mid", song_dir, "song.mid")

    def test_invalid_download_never_committed(self):
        with tempfile.TemporaryDirectory() as folder:
            song_dir = Path(folder)
            opener = type("Opener", (), {
                "open": lambda self, *a, **kw: Response(b"<html>error</html>", "application/octet-stream")
            })()
            with patch("download_asset.public_https"), patch("download_asset.build_opener", return_value=opener):
                with self.assertRaisesRegex(DownloadError, "invalid or unsupported"):
                    download("https://example.org/song.mid", song_dir, "song.mid")
            self.assertEqual(list(song_dir.iterdir()), [])

    def test_reject_html_content_type_before_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            song_dir = Path(folder)
            opener = type("Opener", (), {
                "open": lambda self, *a, **kw: Response(b"<html></html>", "text/html")
            })()
            with patch("download_asset.public_https"), patch("download_asset.build_opener", return_value=opener):
                with self.assertRaisesRegex(DownloadError, "HTML"):
                    download("https://example.org/song.mid", song_dir, "song.mid")
            self.assertEqual(list(song_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
