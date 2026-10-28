"""Local visual speaker estimation and a bounded, per-frame camera path.

Landmarks are inferred by MediaPipe. Speaker scores below are our heuristic,
not a trained audiovisual speaker-identification model.
"""
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time
import urllib.request
import wave


MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
MODEL_PATH = Path(__file__).resolve().parents[1] / 'data/models/face_landmarker.task'
SAMPLE_FPS = 6


def prepare_model(cancel=None):
    """Fetch a versioned public model; no user video leaves the laptop."""
    if MODEL_PATH.is_file() and hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256:
        return MODEL_PATH
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=MODEL_PATH.parent, suffix='.part', delete=False) as output:
            temporary = Path(output.name)
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(MODEL_URL, timeout=20) as response:
                while chunk := response.read(65536):
                    if cancel and cancel.is_set():
                        from .media import Cancelled
                        raise Cancelled()
                    size += len(chunk)
                    if size > 8 * 1024**2:
                        raise RuntimeError('Ukuran model pelacak tidak valid.')
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError('Checksum model pelacak tidak cocok. Unduhan tidak digunakan.')
        temporary.replace(MODEL_PATH)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return MODEL_PATH


@dataclass
class Face:
    box: tuple
    aperture: float
    spread: float
    track: int = -1

    @property
    def center(self):
        x, y, w, h = self.box
        return x + w / 2, y + h / 2


def _overlap(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    w = max(0., min(a[0]+a[2], b[0]+b[2])-x)
    h = max(0., min(a[1]+a[3], b[1]+b[3])-y)
    area = w*h
    return area / max(a[2]*a[3]+b[2]*b[3]-area, .000001)


def _assign(faces, previous, timestamp, next_id):
    # Global greedy matching uses geometry, never the detector's face ordering.
    pairs = []
    for index, face in enumerate(faces):
        for identity, (other, seen) in previous.items():
            if timestamp-seen > .7:
                continue
            distance = math.dist(face.center, other.center)
            scale = max(face.box[2], other.box[2], .03)
            overlap = _overlap(face.box, other.box)
            size_ratio = face.box[2] / max(other.box[2], .001)
            if .45 < size_ratio < 2.2 and (overlap > .12 or distance < min(.12, scale*.65)):
                pairs.append((distance/scale + (1-overlap), index, identity))
    used_faces, used_ids = set(), set()
    for _, index, identity in sorted(pairs):
        if index not in used_faces and identity not in used_ids:
            faces[index].track = identity
            used_faces.add(index)
            used_ids.add(identity)
    previous = {key: value for key, value in previous.items() if timestamp-value[1] <= .7}
    for face in faces:
        if face.track < 0:
            face.track, next_id = next_id, next_id+1
        previous[face.track] = face, timestamp
    return previous, next_id


def _observations(result, width, height):
    faces = []
    for landmarks in result.face_landmarks:
        if len(landmarks) < 400:
            continue
        xs, ys = [p.x for p in landmarks], [p.y for p in landmarks]
        left, top = max(0., min(xs)), max(0., min(ys))
        right, bottom = min(1., max(xs)), min(1., max(ys))
        if right-left < .025 or bottom-top < .025:
            continue
        def distance(a, b):
            return math.hypot((landmarks[a].x-landmarks[b].x)*width,
                              (landmarks[a].y-landmarks[b].y)*height)
        mouth_width = max(1., distance(78, 308))
        opening = sum(distance(a, b) for a, b in [(13, 14), (82, 87), (312, 317)]) / (3*mouth_width)
        spread = mouth_width / max(1., distance(33, 263))
        faces.append(Face((left, top, right-left, bottom-top), min(opening, 1.5), min(spread, 2.)))
    return faces


def _audio_energy(source, start, duration, cancel, command):
    import numpy as np
    # A temporary WAV bounds memory to the selected clip, and command() handles
    # cancellation/timeouts. The original audio is untouched.
    with tempfile.TemporaryDirectory(prefix='openclip-camera-') as temporary:
        path = Path(temporary) / 'speech.wav'
        command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(start), '-i', str(source),
                 '-t', str(duration), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                 '-threads', '1', str(path)], cancel, timeout=120)
        with wave.open(str(path), 'rb') as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), '<i2').astype(np.float32) / 32768
    count = max(1, math.ceil(duration*SAMPLE_FPS))
    energy = []
    for i in range(count):
        a, b = round(max(0, i/SAMPLE_FPS-.04)*16000), round(min(duration, (i+1)/SAMPLE_FPS+.04)*16000)
        block = samples[a:b]
        energy.append(float(np.sqrt(np.mean(block*block))) if len(block) else 0.)
    return np.asarray(energy)


def _speaker_scores(frames, energy, segments, start):
    import numpy as np
    histories = defaultdict(list)
    for i, frame in enumerate(frames):
        for face in frame['faces']:
            histories[face.track].append((i, face.aperture, face.spread))
    peak = float(np.percentile(energy, 90)) if len(energy) else 0.
    floor = float(np.percentile(energy, 15)) if len(energy) else 0.
    threshold = max(.0008, min(floor*2.5, peak*.25))
    # Word timestamps gate pauses; segment ranges handle imported SRT too.
    intervals = []
    for segment in segments:
        for word in segment.get('words') or [segment]:
            a, b = word['start']-start-.12, word['end']-start+.15
            if b >= 0 and a <= len(frames)/SAMPLE_FPS:
                intervals.append((a, b))
    speech = []
    for i, frame in enumerate(frames):
        rms = energy[min(i, len(energy)-1)] if len(energy) else 0.
        voiced = rms > threshold and (not intervals or any(a <= frame['time'] <= b for a, b in intervals))
        speech.append(bool(voiced))
        frame['scores'] = {}
    for identity, history in histories.items():
        indices = np.array([row[0] for row in history])
        aperture = np.array([row[1] for row in history])
        spread = np.array([row[2] for row in history])
        for j, index in enumerate(indices):
            window = (indices >= index-3) & (indices <= index+3)
            values, widths, positions = aperture[window], spread[window], indices[window]
            score = 0.
            if len(values) >= 3 and speech[index]:
                adjacent = np.diff(positions) == 1
                movement = float(np.mean(np.abs(np.diff(values))[adjacent])) if adjacent.any() else 0.
                motion = min(1., float(np.std(values))*8 + movement*5 + float(np.std(widths))*4)
                sync = 0.
                audio = energy[np.minimum(positions, len(energy)-1)] if len(energy) else np.zeros(len(values))
                if len(values) >= 5 and np.std(values) > .005 and np.std(audio) > .0005:
                    sync = max(0., float(np.corrcoef(values, audio)[0, 1]))
                # Correlation is only supporting evidence: vowels and acoustic
                # amplitude do not have a one-to-one relation to lip opening.
                score = motion*(.8+.2*sync)
            frames[int(index)]['scores'][identity] = round(score, 4)
    return speech


def _camera_path(frames, speech, duration):
    points, active, pending, since, switched = [], None, None, 0., -10.
    switches, ambiguous = 0, 0
    for i, frame in enumerate(frames):
        timestamp, faces = frame['time'], frame['faces']
        visible = {face.track: face for face in faces}
        cut = frame['cut']
        if cut:
            active, pending, switched = None, None, timestamp-1.
        scores = frame['scores']
        winner = max(visible, key=lambda key: scores.get(key, 0.), default=None)
        best = scores.get(winner, 0.)
        old_active = active
        if active is None and visible:
            active = winner if best >= .12 else max(faces, key=lambda face: face.box[2]*face.box[3]).track
        elif active not in visible and len(visible) == 1:
            active = next(iter(visible))
        elif speech[i] and winner is not None and winner != active and best >= .12 and best > scores.get(active, 0.)*1.35+.035:
            if pending != winner:
                pending, since = winner, timestamp
            if timestamp-since >= .32 and timestamp-switched >= .75:
                active, switched, pending = winner, timestamp, None
        else:
            pending = None
        if len(visible) > 1 and speech[i] and best < .12:
            ambiguous += 1
        if old_active is not None and active != old_active and not cut:
            switches += 1
        if active in visible:
            x, y = visible[active].center
        elif points:
            x, y = points[-1]['x'], points[-1]['y']
        else:
            x, y = .5, .5
        if points and not cut:
            previous = points[-1]
            # A small dead zone prevents face jitter; a bounded pan reaches the
            # next speaker without jumping across the frame in one sample.
            dx, dy = x-previous['x'], y-previous['y']
            if abs(dx) < .008:
                dx = 0.
            if abs(dy) < .008:
                dy = 0.
            x = previous['x'] + max(-.13, min(.13, dx*.7))
            y = previous['y'] + max(-.08, min(.08, dy*.6))
        points.append({'time': round(timestamp, 5), 'x': round(x, 5), 'y': round(y, 5),
                       'speaker': active if active in visible else None, 'cut': cut})
    if points:
        points.append(dict(points[-1], time=round(duration, 5), cut=False))
    return points, switches, ambiguous


def analyse(source, start, duration, metadata, segments, cancel, command, progress=None):
    import cv2
    import mediapipe as mp
    import numpy as np
    from .media import Cancelled
    cv2.setNumThreads(1)
    if progress:
        progress('Menyiapkan pelacak pembicara lokal.')
    model_path = prepare_model(cancel)
    energy = _audio_energy(source, start, duration, cancel, command) if metadata['has_audio'] else np.zeros(math.ceil(duration*SAMPLE_FPS))
    width = min(640, metadata['width'])
    height = max(2, round(metadata['height']/metadata['width']*width/2)*2)
    if height > 640:
        width, height = max(2, round(width*640/height/2)*2), 640
    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=6,
        min_face_detection_confidence=.45, min_face_presence_confidence=.45,
        min_tracking_confidence=.45)
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(start), '-i', str(source),
            '-t', str(duration), '-vf', f'fps={SAMPLE_FPS},scale={width}:{height}', '-threads', '1',
            '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    frames, previous, previous_gray, next_id = [], {}, None, 0
    # Stream frames directly from FFmpeg, with a watchdog for stalled input.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending_bytes, last_data = bytearray(), time.monotonic()
        try:
            with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
                ended = False
                while not ended:
                    if cancel.is_set():
                        raise Cancelled()
                    ready = selector.select(timeout=.2)
                    if not ready:
                        if time.monotonic()-last_data > 45:
                            raise RuntimeError('Decoder pelacak kamera berhenti merespons.')
                        continue
                    raw = os.read(process.stdout.fileno(), 262144)
                    if not raw:
                        ended = True
                    else:
                        last_data = time.monotonic()
                        pending_bytes.extend(raw)
                    frame_size = width*height*3
                    while len(pending_bytes) >= frame_size:
                        if cancel.is_set():
                            raise Cancelled()
                        frame = np.frombuffer(bytes(pending_bytes[:frame_size]), np.uint8).reshape(height, width, 3)
                        del pending_bytes[:frame_size]
                        timestamp = len(frames)/SAMPLE_FPS
                        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (32, 18)).astype(np.float32)/255
                        cut = previous_gray is None or float(np.mean(np.abs(gray-previous_gray))) > .24
                        previous_gray = gray
                        if cut:
                            previous = {}
                        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
                        result = landmarker.detect_for_video(image, round(timestamp*1000))
                        faces = _observations(result, width, height)
                        previous, next_id = _assign(faces, previous, timestamp, next_id)
                        frames.append({'time': timestamp, 'faces': faces, 'cut': cut})
                        if progress and len(frames) % (SAMPLE_FPS*5) == 0:
                            progress(f'Melacak pembicara {timestamp:.0f}/{duration:.0f} detik.')
            process.wait(timeout=5)
            if process.returncode:
                log.seek(0)
                raise RuntimeError(log.read().decode(errors='replace')[-1200:] or 'Decoder pelacak gagal.')
            if pending_bytes:
                raise RuntimeError('Frame pelacak kamera tidak lengkap.')
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    if not frames:
        raise RuntimeError('Tidak ada frame video untuk pelacakan kamera.')
    speech = _speaker_scores(frames, energy, segments, start)
    points, switches, ambiguous = _camera_path(frames, speech, duration)
    detected = sum(bool(frame['faces']) for frame in frames)
    warnings = []
    if detected < len(frames)*.8:
        warnings.append('Wajah tertutup atau tidak terlihat pada sebagian gambar; kamera menahan posisi terakhir saat target hilang.')
    if ambiguous > max(3, sum(speech)*.25):
        warnings.append('Sebagian percakapan memiliki gerakan bibir yang ambigu; kamera mempertahankan target sampai pergantian cukup jelas.')
    return {'method': 'mediapipe-lip-motion-audio-v1', 'sample_fps': SAMPLE_FPS,
            'frames': len(frames), 'face_coverage': round(detected/len(frames), 3),
            'speaker_switches': switches, 'ambiguous_frames': ambiguous,
            'warnings': warnings, 'points': points if detected else []}


def camera_commands(points, width, height):
    """sendcmd evaluates each short interval every output frame, without a
    deeply nested FFmpeg expression or a huge shell argument."""
    lines = []
    for a, b in zip(points, points[1:]):
        if b['time'] <= a['time']:
            continue
        x, y = a['x'], a['y']
        dx, dy = (0., 0.) if b['cut'] else (b['x']-x, b['y']-y)
        interval = f"{a['time']:.5f}-{b['time']:.5f}"
        lines.append(f"{interval} [expr] crop@camera x 'clip(({x:.6f}+{dx:.6f}*TI)*W-{width}/2,0,W-{width})', "
                     f"[expr] crop@camera y 'clip(({y:.6f}+{dy:.6f}*TI)*H-{height}*.38,0,H-{height})';")
    return '\n'.join(lines) + '\n'
