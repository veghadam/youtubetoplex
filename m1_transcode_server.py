#!/usr/bin/env python3
"""M1 Mac remote transcode server for ChannelHoarder.

Receives a source video (AV1/VP9+Opus), transcodes it to H.264+AAC using the
Apple VideoToolbox hardware encoder (h264_videotoolbox), and returns the
transcoded mp4. Run on the M1 Max:

    pip install fastapi uvicorn python-multipart
    python3 m1_transcode_server.py            # or: uvicorn m1_transcode_server:app

Requires ffmpeg built with VideoToolbox support:
    brew install ffmpeg     # (formulae ffmpeg includes h264_videotoolbox)

Security: the endpoint requires the token set in TRANSCODE_TOKEN (or the
--token arg). Requests without it are rejected.
"""

import argparse
import asyncio
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

app = FastAPI(title="M1 Transcode Server", version="1.0.0")

TOKEN = os.environ.get("TRANSCODE_TOKEN", "")

TEMP_ROOT = Path(os.environ.get("TRANSCODE_TEMP_DIR", tempfile.gettempdir()))

_FFMPEG = shutil.which("ffmpeg")
for _cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
    if Path(_cand).exists():
        _FFMPEG = _cand
        break
if not _FFMPEG:
    _FFMPEG = "ffmpeg"


def _check_token(authorization: Optional[str]) -> None:
    if not TOKEN:
        raise HTTPException(status_code=500, detail="Server not configured with a token")
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing token")


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def _transcode(src: Path, dst: Path, request: Request) -> None:
    """Transcode src -> dst with the Apple hardware encoder.

    If the client disconnects mid-encode (e.g. the downloader was restarted or
    a download was cancelled), kill the ffmpeg subprocess so the hardware
    encoder is not left busy with an orphaned job.
    """
    # -tag:v avc1 so QuickTime/Plex label it as H.264
    cmd = [
        _FFMPEG, "-y",
        "-i", str(src),
        "-c:v", "h264_videotoolbox",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-tag:v", "avc1",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _cancel_if_disconnected() -> None:
        while True:
            try:
                if await request.is_disconnected():
                    proc.kill()
                    return
            except Exception:
                return
            await asyncio.sleep(1.0)

    watcher = asyncio.ensure_future(_cancel_if_disconnected())
    try:
        _, stderr = await proc.communicate()
    finally:
        watcher.cancel()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-2000:])


@app.post("/transcode")
async def transcode(
    request: Request,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
    background_tasks: BackgroundTasks = None,
):
    _check_token(authorization)

    try:
        with tempfile.NamedTemporaryFile(
            dir=TEMP_ROOT, prefix="ch_in_", suffix=".mkv", delete=False
        ) as src:
            src_path = Path(src.name)
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                src.write(chunk)

        dst_path = Path(tempfile.NamedTemporaryFile(
            dir=TEMP_ROOT, prefix="ch_out_", suffix=".mp4", delete=False
        ).name)

        try:
            await _transcode(src_path, dst_path, request)
        except Exception as e:
            dst_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Transcode failed: {e}")

        background_tasks.add_task(_remove_file, dst_path)

        return FileResponse(
            dst_path,
            media_type="video/mp4",
            filename=Path(file.filename or "video.mp4").name,
        )
    finally:
        src_path.unlink(missing_ok=True)


@app.get("/health")
async def health():
    return {"status": "ok", "encoder": "h264_videotoolbox"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M1 transcode server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=TOKEN or None,
                        help="Auth token (or set TRANSCODE_TOKEN)")
    args = parser.parse_args()

    if args.token:
        TOKEN = args.token
    elif not TOKEN:
        # Generate a throwaway token if none provided so the server can start
        # but still reject unauthenticated clients meaningfully.
        TOKEN = secrets.token_urlsafe(32)
        print(f"WARNING: no token set. Generated: {TOKEN}")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
