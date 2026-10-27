"""
Кадрирование горизонтального видео в 9:16 по монтажным планам.

Клип делится на планы (по сменам сцены). На каждом плане ищем лица:
- один человек (или двое рядом) — обычный кадр 9:16 по центру лица;
- два человека далеко друг от друга — разделённый экран: левый сверху, правый снизу.

Всё рендерится одним проходом ffmpeg: позиции кадра — функции времени t,
разделённый экран накладывается поверх обычного только на своих планах.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
import structlog

from config.settings import settings
from services.video.aspect import TARGET_HEIGHT, TARGET_WIDTH, DisplayGeometry
from services.video.ffmpeg import run_ffmpeg

logger = structlog.get_logger(__name__)

HALF_HEIGHT = TARGET_HEIGHT // 2
_HALF_ASPECT = TARGET_WIDTH / HALF_HEIGHT  # 1080×960

# YuNet (opencv_zoo, 2023mar): нейросетевой детектор, видит лица в профиль и на крупных планах.
# Каскады Хаара на студийном видео находили лица в решётке на стене и пропускали крупные планы.
_YUNET_MODEL = Path(__file__).resolve().parents[2] / "models" / "face_detection_yunet_2023mar.onnx"
_DETECT_WIDTH = 640
_detector: cv2.FaceDetectorYN | None = None


def _get_detector() -> cv2.FaceDetectorYN:
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            str(_YUNET_MODEL), "", (_DETECT_WIDTH, _DETECT_WIDTH), score_threshold=0.75
        )
    return _detector


@dataclass
class Face:
    cx: float
    cy: float
    w: float
    h: float


@dataclass
class Shot:
    start: float
    end: float
    layout: str = "single"  # single | split
    # single: центр кадра; split: [левый, правый]
    faces: list[Face] = field(default_factory=list)
    # Верх плашки с именем гостя (доля высоты кадра), если она видна на плане
    plate_top: float | None = None


@dataclass
class ReframePlan:
    shots: list[Shot]

    @property
    def split_ranges(self) -> list[tuple[float, float]]:
        return [(s.start, s.end) for s in self.shots if s.layout == "split"]

    @property
    def plate_shots(self) -> list[Shot]:
        return [s for s in self.shots if s.layout == "single" and s.plate_top is not None]

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


# ---------- детекция ----------


def detect_name_plate(image_path: Path) -> float | None:
    """
    Плашка с именем гостя, вшитая при монтаже в левый нижний угол.
    Признак — строки белого текста: в такой строке много переходов «светлое/тёмное»,
    у белой одежды или бликов их почти нет. Возвращает верх плашки (доля высоты) или None.
    На MEDIA MIX 69: на кадрах с плашкой 109–128 текстовых строк, без плашки 0–3.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape
    top = int(height * 0.66)
    band = image[top : int(height * 0.98), : int(width * 0.6)]
    bright = (band > 200).astype(np.int8)
    transitions = np.abs(np.diff(bright, axis=1)).sum(axis=1)
    text_rows = np.nonzero(transitions >= 20)[0]
    if len(text_rows) < settings.plate_min_text_rows:
        return None
    return (top + int(text_rows[0])) / height


def detect_faces(image_path: Path) -> list[Face]:
    image = cv2.imread(str(image_path))
    if image is None:
        return []
    height, width = image.shape[:2]
    ratio = _DETECT_WIDTH / width
    small = cv2.resize(image, (_DETECT_WIDTH, int(round(height * ratio))))
    detector = _get_detector()
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, detections = detector.detect(small)
    if detections is None:
        return []

    faces = [
        Face(
            cx=(d[0] + d[2] / 2) / ratio,
            cy=(d[1] + d[3] / 2) / ratio,
            w=d[2] / ratio,
            h=d[3] / ratio,
        )
        for d in detections
    ]
    # Мелкие лица на заднем плане (зрители, отражения) не в счёт
    biggest = max(f.h for f in faces)
    return [f for f in faces if f.h >= biggest * 0.55 and f.h >= height * 0.05]


def classify_faces(faces: list[Face], frame_width: float) -> tuple[str, list[Face]]:
    if not faces:
        return "none", []
    if len(faces) == 1:
        return "single", faces
    if len(faces) > 2:
        # Общий план студии: разделённый экран на троих не собрать — кадр по самому крупному лицу
        return "single", [max(faces, key=lambda f: f.h)]
    left = min(faces, key=lambda f: f.cx)
    right = max(faces, key=lambda f: f.cx)
    if right.cx - left.cx >= frame_width * settings.split_min_distance_ratio:
        return "split", [left, right]
    # Двое рядом помещаются в один кадр 9:16 — центр между ними
    mid = Face((left.cx + right.cx) / 2, (left.cy + right.cy) / 2, max(left.w, right.w), max(left.h, right.h))
    return "single", [mid]


# ---------- планы ----------


async def _scene_cuts(video_path: Path, start: float, duration: float) -> list[float]:
    stderr = await run_ffmpeg(
        [
            "-ss", f"{start:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(video_path),
            "-an",
            "-vf", f"scale=320:-2,select='gt(scene,{settings.reframe_scene_threshold})',showinfo",
            "-f", "null", "-",
        ],
        label="reframe_scenes",
        timeout=max(60.0, duration * 2),
    )
    cuts = sorted({round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", stderr)})
    return [c for c in cuts if 0.0 < c < duration]


def _shots_from_cuts(cuts: list[float], duration: float, min_len: float = 0.8) -> list[Shot]:
    bounds = [0.0, *cuts, duration]
    shots: list[Shot] = []
    for a, b in zip(bounds, bounds[1:]):
        if shots and b - a < min_len:
            shots[-1].end = b  # слишком короткий план приклеиваем к предыдущему
            continue
        shots.append(Shot(start=a, end=b))
    if len(shots) > 1 and shots[0].end - shots[0].start < min_len:
        shots[1].start = 0.0
        shots.pop(0)
    return shots


async def _inspect_frame(
    video_path: Path, timestamp: float, work_dir: Path, idx: int
) -> tuple[list[Face], float | None]:
    """Лица и плашка с именем на одном кадре."""
    frame = work_dir / f"_reframe_{idx}.jpg"
    try:
        await run_ffmpeg(
            ["-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(frame)],
            label="reframe_frame",
            timeout=45,
        )
        faces = await asyncio.to_thread(detect_faces, frame)
        plate = await asyncio.to_thread(detect_name_plate, frame) if settings.plate_zoom_enabled else None
        return faces, plate
    finally:
        frame.unlink(missing_ok=True)


async def plan_reframe(
    video_path: Path,
    *,
    start: float,
    duration: float,
    display: DisplayGeometry,
    work_dir: Path,
) -> ReframePlan:
    cuts = await _scene_cuts(video_path, start, duration)
    shots = _shots_from_cuts(cuts, duration)
    frame_w = display.display_width

    previous: Shot | None = None
    for idx, shot in enumerate(shots):
        length = shot.end - shot.start
        samples = [0.5] if length < 4 else ([0.2, 0.5, 0.8] if length >= 6 else [0.3, 0.7])
        best_kind, best_faces = "none", []
        single_faces: list[Face] = []
        for j, ratio in enumerate(samples):
            faces, plate = await _inspect_frame(
                video_path, start + shot.start + length * ratio, work_dir, idx * 10 + j
            )
            if plate is not None:
                shot.plate_top = plate if shot.plate_top is None else min(shot.plate_top, plate)
            kind, chosen = classify_faces(faces, frame_w)
            if kind == "single":
                single_faces.append(chosen[0])
            # «Двое» важнее «одного»: на общем плане детектор иногда теряет одного из спикеров
            rank = {"none": 0, "single": 1, "split": 2}
            if rank[kind] > rank[best_kind]:
                best_kind, best_faces = kind, chosen
        if best_kind == "single" and len(single_faces) > 1:
            # Спикер двигается внутри плана — ставим кадр по среднему положению лица
            n = len(single_faces)
            best_faces = [
                Face(
                    cx=sum(f.cx for f in single_faces) / n,
                    cy=sum(f.cy for f in single_faces) / n,
                    w=max(f.w for f in single_faces),
                    h=max(f.h for f in single_faces),
                )
            ]
        if best_kind == "none":
            if previous is not None:
                shot.layout, shot.faces = previous.layout, previous.faces
            else:
                shot.layout, shot.faces = "single", [Face(frame_w / 2, display.display_height / 2, 0, 0)]
        else:
            shot.layout, shot.faces = best_kind, best_faces
        previous = shot

    # Планы без лиц в начале клипа берут раскладку первого плана с лицами
    first_found = next((s for s in shots if s.faces and s.faces[0].h > 0), None)
    if first_found:
        for s in shots:
            if s is first_found:
                break
            if not (s.faces and s.faces[0].h > 0):
                s.layout, s.faces = first_found.layout, first_found.faces

    logger.info(
        "reframe_plan",
        shots=[
            (round(s.start, 1), round(s.end, 1), s.layout + ("+plate" if s.plate_top is not None else ""))
            for s in shots
        ],
    )
    return ReframePlan(shots=shots)


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


def build_reframe_filter(plan: ReframePlan, display: DisplayGeometry) -> str:
    """filter_complex с выходом [v] размером 1080×1920."""
    W, H = display.display_width, display.display_height
    shots = plan.shots
    has_single = any(s.layout == "single" for s in shots)
    has_split = any(s.layout == "split" for s in shots)

    # Обычный кадр: масштаб по высоте до 1920, окно 1080 по горизонтали
    scale = TARGET_HEIGHT / H
    scaled_w = max(TARGET_WIDTH, _even(W * scale))
    last_x = (scaled_w - TARGET_WIDTH) / 2
    single_x: list[float] = []
    for s in shots:
        if s.layout == "single":
            last_x = _clamp(s.faces[0].cx * scale - TARGET_WIDTH / 2, 0, scaled_w - TARGET_WIDTH)
        single_x.append(last_x)

    single_chain = (
        f"scale={scaled_w}:{TARGET_HEIGHT}:flags=lanczos,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x={_piecewise(shots, single_x)}:y=0,setsar=1"
    )

    # Слой «наезд» для планов с плашкой: показываем кадр только выше её верхнего края
    plate_shots = plan.plate_shots if has_single else []
    zoom_chain = ""
    if plate_shots:
        visible = max(0.55, min(s.plate_top for s in plate_shots) - 0.02) * H
        zscale = TARGET_HEIGHT / visible
        zoom_w = max(TARGET_WIDTH, _even(W * zscale))
        zoom_h = _even(H * zscale)
        zoom_x: list[float] = []
        last_zx = (zoom_w - TARGET_WIDTH) / 2
        for s in shots:
            if s.layout == "single":
                last_zx = _clamp(s.faces[0].cx * zscale - TARGET_WIDTH / 2, 0, zoom_w - TARGET_WIDTH)
            zoom_x.append(last_zx)
        zoom_chain = (
            f"scale={zoom_w}:{zoom_h}:flags=lanczos,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x={_piecewise(shots, zoom_x)}:y=0,setsar=1"
        )

    if not has_split and not zoom_chain:
        return f"[0:v]{single_chain}[v]"

    split_graph = _split_graph(plan, display) if has_split else ""

    inputs: list[str] = []
    parts: list[str] = []
    if has_single:
        inputs.append("one")
        parts.append(f"[one]{single_chain}[single]")
    if zoom_chain:
        inputs.append("zin")
        parts.append(f"[zin]{zoom_chain}[zoom]")
    if has_split:
        inputs += ["top", "bot"]
        parts.append(split_graph)

    current = "single" if has_single else "split"
    if zoom_chain:
        ranges = [(s.start, s.end) for s in plate_shots]
        parts.append(f"[{current}][zoom]overlay=0:0:enable={_enable_expr(ranges)}[withzoom]")
        current = "withzoom"
    if has_split and has_single:
        parts.append(f"[{current}][split]overlay=0:0:enable={_enable_expr(plan.split_ranges)}[v]")
    else:
        parts.append(f"[{current}]null[v]")

    head = f"[0:v]split={len(inputs)}" + "".join(f"[{name}]" for name in inputs)
    return ";".join([head, *parts])


def _split_graph(plan: ReframePlan, display: DisplayGeometry) -> str:
    """Разделённый экран: [top] и [bot] → [split] 1080×1920."""
    W, H = display.display_width, display.display_height
    shots = plan.shots
    # Половинки 1080×960: окно вокруг лица, размер одинаковый на весь клип (crop не умеет менять w/h)
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

    def half_chain(xy: list[tuple[float, float]]) -> str:
        xs = _piecewise(shots, [p[0] for p in xy])
        ys = _piecewise(shots, [p[1] for p in xy])
        return (
            f"crop={crop_w}:{crop_h}:x={xs}:y={ys},"
            f"scale={TARGET_WIDTH}:{HALF_HEIGHT}:flags=lanczos,setsar=1"
        )

    return (
        f"[top]{half_chain(top_xy)}[t];"
        f"[bot]{half_chain(bottom_xy)}[b];"
        "[t][b]vstack=inputs=2[split]"
    )


def plan_debug(plan: ReframePlan) -> list[dict]:
    return [asdict(s) for s in plan.shots]
