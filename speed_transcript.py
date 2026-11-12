"""One-off helper: download the audio of a section of a YouTube stream and transcribe it
with timestamps, so the best moments can be picked for a campaign batch.

python speed_transcript.py URL START_SECONDS [END_SECONDS]
Writes out/transcript.txt
"""
import subprocess
import sys
from pathlib import Path

url, start = sys.argv[1], int(sys.argv[2])
end = sys.argv[3] if len(sys.argv) > 3 else "inf"
out = Path("out")
out.mkdir(exist_ok=True)
audio = out / "segment.m4a"
subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bestaudio[ext=m4a]/bestaudio",
                "--download-sections", f"*{start}-{end}", "-o", str(audio), url], check=True)

from faster_whisper import WhisperModel  # noqa: E402

model = WhisperModel("base.en", device="cpu", compute_type="int8")
segs, info = model.transcribe(str(audio), vad_filter=True)
lines = []
for s in segs:
    t = start + s.start
    lines.append(f"[{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}] {s.text.strip()}")
(out / "transcript.txt").write_text("\n".join(lines))
print(f"{len(lines)} lines, {info.duration / 60:.1f} minutes of audio")
