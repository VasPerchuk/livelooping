"""Download a bounded HTTPS score into a song folder only after format validation."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_BYTES = 16 * 1024 * 1024
SUFFIXES = {".mid", ".midi", ".gp", ".gp3", ".gp4", ".gp5", ".gpx"}
VALIDATOR = Path(__file__).resolve().parents[1] / "tools" / "music_assets.py"


class DownloadError(ValueError):
    pass


def public_https(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise DownloadError("download URL must be public HTTPS without credentials")
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror as exc:
        raise DownloadError(f"cannot resolve download host: {exc}") from exc
    if not addresses or any(
        not ipaddress.ip_address(record[4][0]).is_global for record in addresses
    ):
        raise DownloadError("download host resolves to a private or non-public address")


class SafeRedirects(HTTPRedirectHandler):
    def __init__(self):
        self.count = 0

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 5:
            raise DownloadError("too many redirects")
        target = urljoin(request.full_url, newurl)
        public_https(target)
        return super().redirect_request(request, fp, code, msg, headers, target)


def download(url, song_dir, filename):
    song_dir = Path(song_dir).resolve(strict=True)
    if not song_dir.is_dir():
        raise DownloadError("song directory does not exist")
    if filename != Path(filename).name or Path(filename).suffix.lower() not in SUFFIXES:
        raise DownloadError("filename must be a simple supported music filename")
    target = song_dir / filename
    if target.exists():
        raise DownloadError(f"refusing to overwrite existing asset: {target}")
    public_https(url)
    # No environment proxy or non-HTTPS redirect; no HTML/script is executed.
    opener = build_opener(ProxyHandler({}), SafeRedirects())
    pending = None
    try:
        with opener.open(Request(url, headers={"User-Agent": "livelooping-asset/1"}),
                         timeout=30) as response:
            public_https(response.geturl())
            if response.headers.get_content_type() in ("text/html", "application/xhtml+xml"):
                raise DownloadError("server returned HTML rather than music")
            advertised = response.headers.get("Content-Length")
            if advertised and int(advertised) > MAX_BYTES:
                raise DownloadError("download exceeds 16 MiB limit")
            with tempfile.NamedTemporaryFile(dir=song_dir, prefix=".pending-", suffix=target.suffix,
                                             delete=False) as staged:
                pending = Path(staged.name)
                total = 0
                while chunk := response.read(min(65536, MAX_BYTES + 1 - total)):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise DownloadError("download exceeds 16 MiB limit")
                    staged.write(chunk)
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "validate", "--path", str(pending)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError(f"validator failed: {result.stderr.strip()}") from exc
        if result.returncode or report.get("status") != "valid":
            raise DownloadError(f"invalid or unsupported music file: {report.get('error')}")
        if report.get("metadata", {}).get("note_count", 0) == 0:
            raise DownloadError("music file contains no analyzable notes")
        if target.exists():
            raise DownloadError(f"asset appeared while downloading: {target}")
        os.rename(pending, target)
        pending = None
        return {"path": str(target), "url": response.geturl(), "validation": report}
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="public HTTPS direct-download URL")
    parser.add_argument("--song-dir", type=Path, required=True, help="existing song directory")
    parser.add_argument("--name", required=True, help="new filename with supported extension")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(download(args.url, args.song_dir, args.name), ensure_ascii=False))
    except (DownloadError, OSError, HTTPError, URLError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
