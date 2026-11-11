"""
Кадрирование горизонтального видео в 9:16.

Клип анализируется плотно (ANALYSIS_FPS кадров в секунду): на каждом кадре ищем лица,
подпись спикера и «отпечаток» картинки для поиска склеек. Клип делится на планы,
и для каждого плана выбирается раскладка по большинству кадров:
- single — один спикер (или двое рядом): окно 9:16 по медианному положению лица;
- split  — два спикера далеко друг от друга: левый сверху, правый снизу;
- fit    — кадр целиком по центру на размытом фоне: перебивки без людей, общие планы,
           неуверенные находки, подпись спикера в кадре (чтобы её не резать).

Всё рендерится одним проходом ffmpeg: позиции окон — функции времени t,
раскладки накладываются друг на друга только на своих планах.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import structlog

from config.settings import settings
from services.video.aspect import TARGET_HEIGHT, TARGET_WIDTH, DisplayGeometry
from services.video.ffmpeg_paths import get_ffmpeg_binary

logger = structlog.get_logger(__name__)

HALF_HEIGHT = TARGET_HEIGHT // 2
_HALF_ASPECT = TARGET_WIDTH / HALF_HEIGHT  # 1080×960

ANALYSIS_FPS = 4
ANALYSIS_WIDTH = 640
MIN_SHOT_SEC = 1.2  # короче — микрокадр: сливаем с соседним планом
LAYOUT_MAJORITY = 0.6  # доля кадров плана, нужная для single/split

# YuNet (opencv_zoo, 2023mar): нейросетевой детектор, видит лица в профиль и на крупных планах.
_YUNET_MODEL = Path(__file__).resolve().parents[2] / "models" / "face_detection_yunet_2023mar.onnx"
_detector: cv2.FaceDetectorYN | None = None


def _get_detector() -> cv2.FaceDetectorYN:
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            str(_YUNET_MODEL), "", (ANALYSIS_WIDTH, ANALYSIS_WIDTH), score_threshold=settings.face_min_score
        )
    return _detector


@dataclass
class Face:
    cx: float
    cy: float
    w: float
    h: float


@dataclass
class FrameInfo:
    t: float
    faces: list[Face]
    signature: np.ndarray
    plate: bool


@dataclass
class Shot:
    start: float
    end: float
    layout: str = "fit"  # single | split | fit
    # single: [лицо]; split: [левый, правый]
    faces: list[Face] = field(default_factory=list)
    reason: str = ""


@dataclass
class ReframePlan:
    shots: list[Shot]

    @property
    def split_ranges(self) -> list[tuple[float, float]]:
        return self._ranges("split")

    def _ranges(self, layout: str) -> list[tuple[float, float]]:
        return [(s.start, s.end) for s in self.shots if s.layout == layout]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"split_ranges": self.split_ranges}), encoding="utf-8")


def load_split_ranges(clip_path: Path) -> list[tuple[float, float]]:
    """Интервалы разделённого экрана клипа (для размещения субтитров по шву)."""
    path = layout_path_for(clip_path)
    if not path.exists():
        return []
    try:
        return [tuple(r) for r in json.loads(path.read_text(encoding="utf-8"))["split_ranges"]]
    except Exception:
        return []


def layout_path_for(clip_path: Path) -> Path:
    return clip_path.with_suffix(".layout.json")


# ---------- анализ кадра ----------


def detect_faces_bgr(image: np.ndarray, scale_to_display: float) -> list[Face]:
    """Лица на кадре анализа; координаты пересчитаны в пиксели исходного видео."""
    height, width = image.shape[:2]
    detector = _get_detector()
    detector.setInputSize((width, height))
    _, detections = detector.detect(image)
    if detections is None:
        return []
    faces = [
        Face(
            cx=(d[0] + d[2] / 2) * scale_to_display,
            cy=(d[1] + d[3] / 2) * scale_to_display,
            w=d[2] * scale_to_display,
            h=d[3] * scale_to_display,
        )
        for d in detections
        if d[3] >= height * settings.face_min_height_ratio
    ]
    if not faces:
        return []
    # Мелкие лица на заднем плане (зрители, отражения, постеры) не в счёт
    biggest = max(f.h for f in faces)
    return [f for f in faces if f.h >= biggest * 0.5]


def detect_name_plate_bgr(image: np.ndarray) -> bool:
    """
    Подпись спикера, вшитая при монтаже внизу кадра. Признак — строки белого текста:
    в такой строке много переходов «светлое/тёмное», у белой одежды и бликов их почти нет.
    На MEDIA MIX 69 (1080p): 109–128 текстовых строк с подписью, 0–3 без неё.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, _ = gray.shape
    band = gray[int(height * 0.62) : int(height * 0.98), :]
    bright = (band > 200).astype(np.int8)
    transitions = np.abs(np.diff(bright, axis=1)).sum(axis=1)
    text_rows = int((transitions >= 16).sum())
    return text_rows >= settings.plate_min_text_rows * height / 1080


def frame_signature(image: np.ndarray) -> np.ndarray:
    """Уменьшенная картинка, нормированная по яркости и контрасту: склейки видны и в тёмных сценах."""
    small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 18), interpolation=cv2.INTER_AREA)
    small = small.astype(np.float32)
    return (small - small.mean()) / (small.std() + 4.0)


def _analyze_frame(image: np.ndarray, t: float, scale_to_display: float) -> FrameInfo:
    return FrameInfo(
        t=t,
        faces=detect_faces_bgr(image, scale_to_display),
        signature=frame_signature(image),
        plate=detect_name_plate_bgr(image),
    )


async def analyze_clip(
    video_path: Path,
    *,
    start: float,
    duration: float,
    display: DisplayGeometry,
) -> list[FrameInfo]:
    """Декодирует клип ANALYSIS_FPS кадров/с в память и анализирует каждый кадр."""
    width = ANALYSIS_WIDTH
    height = int(round(display.display_height * width / display.display_width / 2)) * 2
    frame_bytes = width * height * 3
    scale_to_display = display.display_width / width
    proc = await asyncio.create_subprocess_exec(
        get_ffmpeg_binary(),
        "-v", "error",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video_path),
        "-an",
        "-vf", f"fps={ANALYSIS_FPS},scale={width}:{height}",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    frames: list[FrameInfo] = []
    index = 0
    try:
        while True:
            try:
                raw = await proc.stdout.readexactly(frame_bytes)
            except asyncio.IncompleteReadError:
                break
            image = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            t = index / ANALYSIS_FPS
            frames.append(await asyncio.to_thread(_analyze_frame, image, t, scale_to_display))
            index += 1
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return frames


# ---------- планы и раскладки ----------


def _dominant_cx(frame: FrameInfo) -> float | None:
    if not frame.faces:
        return None
    return max(frame.faces, key=lambda f: f.h).cx


def find_cuts(frames: list[FrameInfo], frame_width: float) -> list[float]:
    """Склейки: резкая смена структуры картинки или скачок главного лица, который держится."""
    cuts: list[float] = []
    for i in range(1, len(frames)):
        prev, cur = frames[i - 1], frames[i]
        diff = float(np.abs(cur.signature - prev.signature).mean())
        jump = False
        a, b = _dominant_cx(prev), _dominant_cx(cur)
        if a is not None and b is not None and abs(a - b) > frame_width * 0.2:
            nxt = _dominant_cx(frames[i + 1]) if i + 1 < len(frames) else b
            jump = nxt is not None and abs(nxt - a) > frame_width * 0.2
        count_change = (len(prev.faces) == 0) != (len(cur.faces) == 0) and diff > settings.cut_threshold * 0.6
        if diff > settings.cut_threshold or jump or count_change:
            cuts.append((prev.t + cur.t) / 2)
    return cuts


def _segments(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0, *cuts, duration]
    segs = [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]
    # Микрокадры сливаем с соседом: короткий кусок первым — к следующему, иначе — к предыдущему
    merged: list[list[float]] = []
    for a, b in segs:
        if merged and b - a < MIN_SHOT_SEC:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    if len(merged) > 1 and merged[0][1] - merged[0][0] < MIN_SHOT_SEC:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return [(a, b) for a, b in merged]


def _median_face(faces: list[Face]) -> Face:
    return Face(
        cx=float(np.median([f.cx for f in faces])),
        cy=float(np.median([f.cy for f in faces])),
        w=float(np.median([f.w for f in faces])),
        h=float(np.median([f.h for f in faces])),
    )


def classify_shot(frames: list[FrameInfo], frame_width: float) -> tuple[str, list[Face], str]:
    """Раскладка плана по большинству кадров; при сомнениях — fit (кадр целиком на размытом фоне)."""
    if not frames:
        return "fit", [], "нет кадров"
    total = len(frames)
    if sum(f.plate for f in frames) / total >= 0.3:
        return "fit", [], "подпись спикера"

    singles: list[Face] = []
    lefts: list[Face] = []
    rights: list[Face] = []
    for frame in frames:
        faces = frame.faces
        if len(faces) == 1:
            singles.append(faces[0])
        elif len(faces) == 2:
            left, right = sorted(faces, key=lambda f: f.cx)
            if right.cx - left.cx >= frame_width * settings.split_min_distance_ratio:
                lefts.append(left)
                rights.append(right)
            else:
                # Двое рядом помещаются в одно окно — центр между ними
                singles.append(Face((left.cx + right.cx) / 2, (left.cy + right.cy) / 2, max(left.w, right.w), max(left.h, right.h)))

    if len(lefts) / total >= LAYOUT_MAJORITY:
        return "split", [_median_face(lefts), _median_face(rights)], f"двое на {len(lefts)}/{total} кадров"
    if len(singles) / total >= LAYOUT_MAJORITY:
        face = _median_face(singles)
        spread = float(np.std([f.cx for f in singles]))
        if spread > frame_width * 0.12:
            return "fit", [], "спикер сильно двигается"
        return "single", [face], f"один на {len(singles)}/{total} кадров"
    return "fit", [], "нет уверенного спикера"


def build_plan(frames: list[FrameInfo], duration: float, display: DisplayGeometry) -> ReframePlan:
    frame_w = display.display_width
    shots: list[Shot] = []
    for a, b in _segments(find_cuts(frames, frame_w), duration):
        shot_frames = [f for f in frames if a <= f.t < b]
        layout, faces, reason = classify_shot(shot_frames, frame_w)
        shots.append(Shot(start=a, end=b, layout=layout, faces=faces, reason=reason))
    if not shots:
        shots = [Shot(start=0.0, end=duration, layout="fit", reason="нет кадров")]
    return ReframePlan(shots=shots)


async def plan_reframe(
    video_path: Path,
    *,
    start: float,
    duration: float,
    display: DisplayGeometry,
    work_dir: Path | None = None,
) -> ReframePlan:
    frames = await analyze_clip(video_path, start=start, duration=duration, display=display)
    plan = build_plan(frames, duration, display)
    logger.info(
        "reframe_plan",
        frames=len(frames),
        shots=[(round(s.start, 1), round(s.end, 1), s.layout, s.reason) for s in plan.shots],
    )
    return plan


# ---------- фильтр ffmpeg ----------


def _even(value: float) -> int:
    v = int(round(value))
    return v - v % 2


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _piecewise(shots: list[Shot], values: list[float]) -> str:
    """Выражение ffmpeg: значение по плану в зависимости от времени t."""
    expr = f"{values[-1]:.0f}"
    for shot, value in zip(reversed(shots[:-1]), reversed(values[:-1])):
        expr = f"if(lt(t,{shot.end:.3f}),{value:.0f},{expr})"
    return f"'{expr}'"


def _enable_expr(ranges: list[tuple[float, float]]) -> str:
    return "'" + "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in ranges) + "'"


def _fit_graph(display: DisplayGeometry) -> str:
    """[fin] → [fit]: кадр целиком по ширине 1080 поверх размытой и чуть затемнённой копии."""
    fg_h = _even(TARGET_WIDTH * display.display_height / display.display_width)
    return (
        "[fin]split=2[fgsrc][bgsrc];"
        f"[bgsrc]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},scale=270:480,boxblur=12:2,"
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT},eq=brightness=-0.08,setsar=1[bg];"
        f"[fgsrc]scale={TARGET_WIDTH}:{fg_h}:flags=lanczos,setsar=1[fg];"
        "[bg][fg]overlay=0:(H-h)/2[fit]"
    )


def _single_chain(plan: ReframePlan, display: DisplayGeometry) -> str:
    W, H = display.display_width, display.display_height
    shots = plan.shots
    scale = TARGET_HEIGHT / H
    scaled_w = max(TARGET_WIDTH, _even(W * scale))
    last_x = (scaled_w - TARGET_WIDTH) / 2
    xs: list[float] = []
    for s in shots:
        if s.layout == "single":
            last_x = _clamp(s.faces[0].cx * scale - TARGET_WIDTH / 2, 0, scaled_w - TARGET_WIDTH)
        xs.append(last_x)
    return (
        f"[sin]scale={scaled_w}:{TARGET_HEIGHT}:flags=lanczos,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x={_piecewise(shots, xs)}:y=0,setsar=1[single]"
    )


def _split_graph(plan: ReframePlan, display: DisplayGeometry) -> str:
    """[top] и [bot] → [split] 1080×1920: две половинки 1080×960 вокруг лиц."""
    W, H = display.display_width, display.display_height
    shots = plan.shots
    # Размер окна одинаковый на весь клип (crop не умеет менять w/h)
    split_faces = [f for s in shots if s.layout == "split" for f in s.faces]
    face_h = sorted(f.h for f in split_faces)[len(split_faces) // 2]
    crop_h = _clamp(face_h * settings.split_face_zoom, H * 0.4, H)
    crop_w = crop_h * _HALF_ASPECT
    if crop_w > W / 2:  # окно не шире половины кадра, иначе в половинку попадёт второй спикер
        crop_w = W / 2
        crop_h = crop_w / _HALF_ASPECT
    crop_w, crop_h = _even(crop_w), _even(crop_h)

    def window(face: Face) -> tuple[float, float]:
        x = _clamp(face.cx - crop_w / 2, 0, W - crop_w)
        y = _clamp(face.cy - crop_h * 0.42, 0, H - crop_h)  # глаза чуть выше середины половинки
        return x, y

    top_xy: list[tuple[float, float]] = []
    bottom_xy: list[tuple[float, float]] = []
    last_top = last_bottom = (0.0, 0.0)
    for s in shots:
        if s.layout == "split":
            last_top, last_bottom = window(s.faces[0]), window(s.faces[1])
        top_xy.append(last_top)
        bottom_xy.append(last_bottom)

    def half(label_in: str, label_out: str, xy: list[tuple[float, float]]) -> str:
        xs = _piecewise(shots, [p[0] for p in xy])
        ys = _piecewise(shots, [p[1] for p in xy])
        return (
            f"[{label_in}]crop={crop_w}:{crop_h}:x={xs}:y={ys},"
            f"scale={TARGET_WIDTH}:{HALF_HEIGHT}:flags=lanczos,setsar=1[{label_out}]"
        )

    return f"{half('top', 't', top_xy)};{half('bot', 'b', bottom_xy)};[t][b]vstack=inputs=2[split]"


def build_reframe_filter(plan: ReframePlan, display: DisplayGeometry) -> str:
    """filter_complex с выходом [v] 1080×1920: база + раскладки поверх на своих планах."""
    layouts = {s.layout for s in plan.shots}
    inputs: list[str] = []
    parts: list[str] = []
    if "fit" in layouts:
        inputs.append("fin")
        parts.append(_fit_graph(display))
    if "single" in layouts:
        inputs.append("sin")
        parts.append(_single_chain(plan, display))
    if "split" in layouts:
        inputs += ["top", "bot"]
        parts.append(_split_graph(plan, display))

    # База — первая раскладка из fit/single/split; остальные накладываются на своих планах
    order = [name for name in ("fit", "single", "split") if name in layouts]
    current = order[0]
    for idx, name in enumerate(order[1:], start=1):
        out = "v" if idx == len(order) - 1 else f"l{idx}"
        parts.append(f"[{current}][{name}]overlay=0:0:enable={_enable_expr(plan._ranges(name))}[{out}]")
        current = out
    if len(order) == 1:
        parts.append(f"[{current}]null[v]")

    head = f"[0:v]split={len(inputs)}" + "".join(f"[{name}]" for name in inputs)
    return ";".join([head, *parts])
