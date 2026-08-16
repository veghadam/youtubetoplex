import sys

path = '/app/app/services/ytdlp_service.py'
with open(path, 'r') as f:
    lines = f.readlines()

new_method = '''    def _probe_codec(self, filepath):
        """Probe a file to determine video and audio codecs using ffprobe."""
        import json
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_name,codec_type", "-of", "json", filepath],
                capture_output=True, text=True,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            info = json.loads(result.stdout)
            streams = info.get("streams", [])
            vcodec = ""
            acodec = ""
            for s in streams:
                if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg",):
                    vcodec = s["codec_name"]
                elif s.get("codec_type") == "audio":
                    acodec = s["codec_name"]
            return vcodec, acodec
        except Exception:
            return "", ""

    def run(self, info):
        filename = info["filepath"]
        vcodec, acodec = self._probe_codec(filename)
        needs_video = vcodec in ("av1", "vp9")
        needs_audio = acodec in ("opus",)
        if not needs_video and not needs_audio:
            self.to_screen(f"Video ({vcodec}) and audio ({acodec}) already native-friendly; skipping transcoding")
            return [], info

        self.to_screen(f"Transcoding: video={vcodec} -> libx264, audio={acodec} -> aac")
        ext = info.get("ext", "mp4")
        fd, outpath = tempfile.mkstemp(suffix=f".{ext}", dir=str(Path(filename).parent))
        os.close(fd)

        opts = []
        if needs_video:
            opts.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p"])
        else:
            opts.extend(["-c:v", "copy"])
        if needs_audio:
            opts.extend(["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"])
        else:
            opts.extend(["-c:a", "copy"])

        self.run_ffmpeg(filename, outpath, opts)
        Path(filename).unlink()
        info["filepath"] = outpath
        info["format"] = info["ext"] = "mp4"
        self.to_screen(f"Transcoded file: {outpath}")
        return [filename], info

'''

# Lines 34-72 (1-indexed) = indices 33-71 (0-indexed)
# Replace those lines
new_lines = lines[:33] + [new_method] + lines[72:]

with open(path, 'w') as f:
    f.writelines(new_lines)
print(f'Replaced lines 34-72 with new method ({len(new_method.splitlines())} lines)')
