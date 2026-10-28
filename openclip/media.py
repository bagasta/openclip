"""FFmpeg rendering and local speaker framing, implemented independently."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .planning import words_for_clip, segment_captions_for_clip, srt_text


class Cancelled(Exception):
    pass


def command(args, cancel=None, cwd=None, timeout=1800):
    # A log file avoids filling a PIPE while a long render is running.
    import tempfile
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(args, cwd=cwd, stdout=log, stderr=log, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel and cancel.is_set():
                    raise Cancelled('Dibatalkan pengguna.')
                if time.monotonic()-started > timeout:
                    raise RuntimeError('Pemrosesan melewati batas waktu.')
                time.sleep(.15)
            log.seek(0)
            output = log.read().decode(errors='replace')
            if process.returncode:
                raise RuntimeError(output[-1800:] or 'Perintah media gagal.')
            return output
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()


def probe(path):
    data = json.loads(command(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], timeout=30))
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if not video:
        raise ValueError('File tidak mempunyai stream video.')
    duration = float(data['format'].get('duration') or video.get('duration') or 0)
    if not 1 <= duration <= 7200:
        raise ValueError('Durasi video harus antara 1 detik dan 2 jam.')
    width, height = int(video['width']), int(video['height'])
    rotation = next((x.get('rotation', 0) for x in video.get('side_data_list', []) if 'rotation' in x), 0)
    if int(rotation) % 180:
        width, height = height, width
    return {'duration': duration, 'width': width, 'height': height, 'has_audio': any(s['codec_type'] == 'audio' for s in data['streams'])}


def transcribe(source, folder, language, model_name, cancel, update):
    from faster_whisper import WhisperModel
    audio = folder / 'audio.wav'
    command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source), '-vn', '-ar', '16000', '-ac', '1', '-threads', '2', str(audio)], cancel)
    update(17, 'Memuat model Whisper lokal. Unduhan pertama memerlukan internet.')
    model_dir = Path(__file__).resolve().parents[1] / 'data' / 'models'
    model = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=2, num_workers=1, download_root=str(model_dir))
    parts, info = model.transcribe(str(audio), language=None if language == 'auto' else language, beam_size=1, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    segments = []
    try:
        for part in parts:
            if cancel.is_set():
                raise Cancelled()
            words = [{'start': w.start, 'end': w.end, 'word': w.word.strip()} for w in part.words or [] if w.end > w.start]
            segments.append({'start': part.start, 'end': part.end, 'text': part.text.strip(), 'words': words})
            update(min(54, 20+part.end/max(info.duration, 1)*34), f'Transkripsi {part.end:.0f}/{info.duration:.0f} detik')
    finally:
        del model
        audio.unlink(missing_ok=True)
    if not segments:
        raise ValueError('Tidak ada ujaran terdeteksi. Gunakan video dengan percakapan atau sertakan SRT.')
    return segments, info.language


def ass_text(words, width, height, segment_captions=None):
    def stamp(t):
        cs = max(0, round(t*100)); s, cs = divmod(cs, 100); m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f'{h}:{m:02}:{s:02}.{cs:02}'
    def clean(text):
        return text.replace('\\', '').replace('{', '').replace('}', '').replace('\n', ' ')
    size = round(width * .065)
    font = 'Luckiest Guy' if (Path(__file__).parent/'static/fonts/LuckiestGuy-Regular.ttf').exists() else 'DejaVu Sans'
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,-1,0,0,100,100,0,0,1,{max(3,round(width*.006))},0,2,32,32,{round(height*.16)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    groups, group = [], []
    for word in words:
        if group and (len(' '.join(w['word'] for w in group)) + len(word['word']) > 22 or word['start']-group[-1]['end'] > .7):
            groups.append(group); group = []
        group.append(word)
        if len(group) >= 3 or word['word'].endswith(('.', '?', '!')):
            groups.append(group); group = []
    if group:
        groups.append(group)
    lines = []
    for group in groups:
        for i, word in enumerate(group):
            next_time = group[i+1]['start'] if i+1 < len(group) else word['end']
            text = ' '.join(('{\\c&H0000E5FF&}' if j == i else '{\\c&H00FFFFFF&}') + clean(item['word']).upper() for j,item in enumerate(group))
            lines.append((word['start'], max(word['end'],next_time), text))
    for segment in segment_captions or []:
        text = clean(segment['text']).upper()
        # A segment can contain a complete sentence. Use readable wrapping and
        # a smaller font, and leave every word white because its time is unknown.
        segment_size = max(round(width*.038), min(size, round((width-64)*4/max(len(text)*.55, 1))))
        lines.append((segment['start'], segment['end'], f'{{\\fs{segment_size}\\c&H00FFFFFF&}}' + text))
    return header + '\n'.join(f'Dialogue: 0,{stamp(a)},{stamp(b)},Default,,0,0,0,,{text}'
                              for a, b, text in sorted(lines, key=lambda item: item[0])) + '\n'


def face_positions(source, start, duration, metadata, cancel):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    width = 320
    height = max(2, round(metadata['height']/metadata['width']*width/2)*2)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    process = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(start), '-i', str(source), '-t', str(duration), '-vf', f'fps=0.5,scale={width}:{height}', '-threads', '1', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    positions, previous, index = [], .5, 0
    try:
        while True:
            if cancel.is_set():
                raise Cancelled()
            raw = process.stdout.read(width*height*3)
            if len(raw) != width*height*3:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
            faces = detector.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), scaleFactor=1.12, minNeighbors=4, minSize=(24, 24))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2]*f[3])
                value = (x+w/2)/width
                previous = value if not positions else .6*previous+.4*value
                positions.append((index*2, round(previous, 4)))
            index += 1
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        process.stdout.close()
    return positions


def render(source, folder, clip, config, segments, metadata, cancel, progress=None):
    import uuid
    version = uuid.uuid4().hex[:8]
    target = folder / f"{clip['id']}-{version}"
    target.mkdir()
    start, end = clip['start'], clip['end']
    duration = end-start
    sizes = {'9:16': (1080, 1920), '1:1': (1080, 1080), '16:9': (1920, 1080)} if config.get('resolution',720) == 1080 else {'9:16': (720, 1280), '1:1': (720, 720), '16:9': (1280, 720)}
    width, height = sizes[config['aspect']]
    high_quality = config.get('encoding_quality', 'high') == 'high'
    crf, preset = (18, 'medium') if high_quality else (23, 'veryfast')
    focal = float(clip.get('focal', .5))
    position = str(focal)
    framing = config['framing']
    actual_framing = framing
    camera = None
    if framing == 'auto':
        from . import tracking
        camera = tracking.analyse(source, start, duration, metadata, segments, cancel, command, progress)
        if camera['points']:
            position = str(camera['points'][0]['x'])
            actual_framing = 'speaker'
            (target / 'camera.cmd').write_text(tracking.camera_commands(camera['points'], width, height))
        else:
            actual_framing = 'center_fallback'
            position = '.5'
    if framing == 'fit':
        video_filter = f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1'
    elif actual_framing == 'speaker':
        first = camera['points'][0]
        video_filter = (f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                        f"sendcmd=f=camera.cmd,crop@camera={width}:{height}:"
                        f"x='min(max(iw*{first['x']}-ow/2,0),iw-ow)':"
                        f"y='min(max(ih*{first['y']}-oh*.38,0),ih-oh)',setsar=1")
    else:
        video_filter = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}:x='min(max(iw*({position})-ow/2,0),iw-ow)':y='(ih-oh)/2',setsar=1"
    words = words_for_clip(segments, start, end)
    segment_captions = segment_captions_for_clip(segments, start, end)
    if config['captions'] and (words or segment_captions):
        (target / 'captions.ass').write_text(ass_text(words, width, height, segment_captions))
        video_filter += ',ass=captions.ass:fontsdir=' + str(Path(__file__).parent/'static/fonts')
    # Input seeking and finite duration keep CPU/disk cost proportional to each clip.
    output = target / 'clip.mp4'
    if progress:
        progress('Gerak kamera siap. Merender video dan subtitle.')
    command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2', '-ss', str(start), '-i', str(source), '-t', str(duration), '-map', '0:v:0', '-map', '0:a:0?', '-vf', video_filter, '-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-threads', '2', '-filter_threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k' if high_quality else '128k', '-movflags', '+faststart', str(output)], cancel, cwd=target)
    command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(min(1, duration/2)), '-i', str(output), '-frames:v', '1', '-vf', 'scale=360:-2', '-threads', '1', str(target / 'poster.jpg')], cancel, timeout=60)
    groups, group = [], []
    for word in words:
        if group and (len(group) >= 6 or word['start']-group[-1]['end'] > .7):
            groups.append({'start': group[0]['start'], 'end': group[-1]['end'], 'text': ' '.join(w['word'] for w in group)})
            group = []
        group.append(word)
    if group:
        groups.append({'start': group[0]['start'], 'end': group[-1]['end'], 'text': ' '.join(w['word'] for w in group)})
    groups.extend(segment_captions)
    groups.sort(key=lambda item: item['start'])
    (target / 'captions.srt').write_text(srt_text(groups))
    scale = min(width/metadata['width'], height/metadata['height']) if framing == 'fit' else max(width/metadata['width'], height/metadata['height'])
    quality = {'profile': 'high' if high_quality else 'standard', 'crf': crf, 'preset': preset,
               'scaler': 'lanczos', 'upscale_factor': round(scale, 3),
               'source_crop': {'width': round(metadata['width'] if framing == 'fit' else width/scale),
                               'height': round(metadata['height'] if framing == 'fit' else height/scale)}}
    (target / 'timeline.json').write_text(json.dumps({'source_range': {'start': start, 'end': end}, 'aspect': config['aspect'], 'framing': actual_framing, 'camera': camera, 'render_quality': quality, 'source_file': source.name, 'caption_timing': config.get('timing', 'asr'), 'tracks': [{'type': 'video', 'start': 0, 'end': duration}, {'type': 'caption', 'words': words, 'segments': segment_captions}]}, ensure_ascii=False, indent=2))
    return {'version': version, 'framing': actual_framing, 'duration': round(duration, 3), 'width': width, 'height': height, 'bytes': output.stat().st_size,
            'tracking': {key: value for key, value in camera.items() if key != 'points'} if camera else None,
            'quality': quality}
