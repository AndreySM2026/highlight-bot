from __future__ import annotations

from pathlib import Path

from services.video.ffmpeg import run_ffmpeg, run_ffprobe
from services.video.rotation import get_video_rotation, rotation_vf_prefix


async def normalize_video(input_path: Path, output_path: Path) -> dict:
    rotation = await get_video_rotation(input_path)
    rotate_vf = rotation_vf_prefix(rotation)
    await run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vf",
            f"{rotate_vf}fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ],
        label="normalize",
    )
    probe = await run_ffprobe(output_path)
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    return {
        "duration": float(probe.get("format", {}).get("duration", 0)),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": 30,
    }
