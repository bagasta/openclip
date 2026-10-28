import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gc
import json
import logging
import math
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import subprocess
import zipfile
import hashlib

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import media
from . import router
from . import youtube
from .planning import candidates, parse_srt, select_clips, srt_text

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
PROJECTS = DATA / 'projects'
PROJECTS.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
JOBS = {}
CANCEL = {}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='openclip-worker')
ACTIVE = {'queued', 'probing', 'downloading', 'transcribing', 'ranking', 'rendering'}
LOG = logging.getLogger('openclip')
DISK_RESERVE = 512*1024**2


def require_space(extra=0, operation='pemrosesan'):
    free = shutil.disk_usage(DATA).free
    needed = max(0, extra) + DISK_RESERVE
    if free < needed:
        raise ValueError(f'Ruang disk tersedia {free/1024**3:.2f} GB; diperlukan minimal {needed/1024**3:.2f} GB untuk {operation}, termasuk cadangan 512 MB. Kosongkan ruang lalu pilih Lanjutkan / coba lagi.')


def cloud_config(config):
    config = config.copy()
    config.update(asr_provider='openrouter', ranker='openrouter')
    for key in ('openrouter_budget', 'model', 'ollama_model'):
        config.pop(key, None)
    defaults = router.public_settings()
    config.setdefault('openrouter_asr_model', defaults['asr_model'])
    config.setdefault('openrouter_chat_model', defaults['chat_model'])
    return config


def now():
    return datetime.now(timezone.utc).isoformat()


def save(job):
    folder = PROJECTS / job['id']
    folder.mkdir(exist_ok=True)
    temporary = folder / 'project.tmp'
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2))
    temporary.replace(folder / 'project.json')


def update(job_id, **fields):
    with LOCK:
        JOBS[job_id].update(fields, updated_at=now())
        save(JOBS[job_id])


def read_job(job_id):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, 'Proyek tidak ditemukan.')
        return deepcopy(JOBS[job_id])


def add_warning(job_id, message):
    with LOCK:
        if message not in JOBS[job_id]['warnings']:
            JOBS[job_id]['warnings'].append(message)
            save(JOBS[job_id])


def store_clip(job_id, clip):
    with LOCK:
        clips = JOBS[job_id]['clips']
        index = next((i for i, x in enumerate(clips) if x['id'] == clip['id']), None)
        if index is None:
            clips.append(clip)
        else:
            clips[index] = clip
        save(JOBS[job_id])


def download(job, cancel, upgrade=False):
    import yt_dlp
    folder = PROJECTS / job['id']
    ceiling = int(job['config'].get('source_resolution', 2160))
    prefix = job['source_upgrade_prefix'] if upgrade else 'source'
    video_format = f'bv[height<={ceiling}]'
    combined_format = f'b[height<={ceiling}]'
    last_update = [0.0]
    issues = set()
    def progress(data):
        if cancel.is_set():
            raise media.Cancelled()
        remaining = max(0, (data.get('total_bytes') or 0)-(data.get('downloaded_bytes') or 0))
        require_space(remaining, 'menyelesaikan unduhan')
        if time.monotonic()-last_update[0] > 1:
            size = data.get('downloaded_bytes', 0)/1024**2
            total = (data.get('total_bytes') or data.get('total_bytes_estimate') or 0)/1024**2
            update(job['id'], message=f'Mengunduh sumber: {size:.1f}' + (f' / {total:.1f}' if total else '') + ' MB')
            last_update[0] = time.monotonic()
    def postprocess(data):
        if cancel.is_set():
            raise media.Cancelled()
        if data.get('status') == 'started' and 'merger' in data.get('postprocessor', '').lower():
            paths = data.get('info_dict', {}).get('__files_to_merge') or []
            size = sum(Path(path).stat().st_size for path in paths if Path(path).is_file())
            require_space(size+32*1024**2, 'menggabungkan video dan audio')
            update(job['id'], message='Menggabungkan video dan audio. File unduhan yang selesai dipertahankan sampai penggabungan berhasil.')
    class Logger:
        def debug(self, message):
            pass
        def warning(self, message):
            if code := youtube.warning_code(message):
                issues.add(code)
        def error(self, message):
            # The worker logs a sanitized exception once, without session details.
            pass
    options = {'format': f'{video_format}+ba[ext=m4a]/{video_format}+ba/{combined_format}',
               'format_sort': ['hdr:SDR', 'res', 'vcodec:h264', 'br'], 'merge_output_format': 'mp4',
               'outtmpl': str(folder / f'{prefix}.%(ext)s'), 'noplaylist': True, 'quiet': True,
               'no_warnings': False, 'logger': Logger(), 'continuedl': True,
               'socket_timeout': 60, 'retries': 5, 'fragment_retries': 5,
               'progress_hooks': [progress], 'postprocessor_hooks': [postprocess],
               'match_filter': lambda info, **kw: 'Video maksimal 2 jam.' if info.get('duration', 0) and info['duration'] > 7200 else None}
    session_options = youtube.options(job)
    options.update(session_options)
    if youtube.is_url(job['url']):
        options.update(youtube.runtime_options())
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            if session_options:
                youtube.prepare_session(downloader)
            info = downloader.extract_info(job['url'], download=True)
    except (media.Cancelled, youtube.AuthError):
        raise
    except yt_dlp.utils.DownloadError as exc:
        if youtube.is_url(job['url']):
            update(job['id'], download_diagnostics=sorted(issues))
            raise youtube.download_error(exc, bool(session_options), issues) from None
        raise
    paths = [p for p in folder.glob(prefix+'.*') if p.suffix.lower() in {'.mp4', '.webm', '.mkv', '.mov', '.m4v'}]
    if not paths:
        raise ValueError('Tidak ada video terunduh. Coba unggah file langsung.')
    source = max(paths, key=lambda p: p.stat().st_size)
    if upgrade:
        metadata = media.probe(source)
        if abs(metadata['duration']-job['metadata']['duration']) > 1:
            raise ValueError('Durasi sumber baru berbeda lebih dari satu detik. Sumber lama dipertahankan agar subtitle dan potongan tidak bergeser.')
        if job['metadata']['has_audio'] and not metadata['has_audio']:
            raise ValueError('Sumber baru tidak mempunyai audio. Sumber lama dipertahankan.')
        if metadata['height'] < job['metadata']['height']:
            add_warning(job['id'], 'Unduhan baru beresolusi lebih rendah daripada sumber tersimpan. Sumber lama yang lebih tinggi dipertahankan untuk render.')
            return folder/job['source_file']
        if metadata['height'] == job['metadata']['height']:
            add_warning(job['id'], f"Unduhan baru tetap {metadata['height']}p. Sumber belum menyediakan resolusi lebih tinggi; ekspor memakai kualitas sumber yang tersedia.")
    update(job['id'], source_file=source.name, name=str(info.get('title') or job['name'])[:180])
    return source


def process(job_id, rerender=None, camera_only=False, export_only=False):
    cancel = CANCEL[job_id]
    folder = PROJECTS / job_id
    try:
        if cancel.is_set():
            raise media.Cancelled()
        job = read_job(job_id)
        config = cloud_config(job['config'])
        update(job_id, config=config)
        camera_only = camera_only or (not rerender and job.get('pending_reframe', False))
        export_only = export_only or (not rerender and job.get('pending_export_quality', False))
        render_only = bool(rerender or camera_only or export_only)
        source = folder / job['source_file'] if job.get('source_file') else None
        upgrading = export_only and job.get('pending_source_upgrade', False)
        if source is None or upgrading:
            update(job_id, status='downloading', progress=3, message=f"Mengunduh sumber video terbaik hingga {config.get('source_resolution',2160)}p.", error=None)
            source = download(job, cancel, upgrade=upgrading)
            if upgrading:
                update(job_id, pending_source_upgrade=False)
        update(job_id, status='probing', progress=8, message='Memeriksa video dan ruang penyimpanan.', error=None)
        metadata = media.probe(source)
        billing = router.Usage(job.get('openrouter_cost', 0), lambda cost: update(job_id, openrouter_cost=cost))
        if not metadata['has_audio'] and not (folder / 'transcript.json').exists() and not (folder / 'input.srt').exists():
            raise ValueError('Video tidak mempunyai audio. Sertakan file SRT untuk memilih momen.')
        require_space()
        update(job_id, metadata=metadata)
        transcript_file = folder / 'transcript.json'
        asr_model = config['openrouter_asr_model']
        signature = f"openrouter|{asr_model}|{config['language']}"
        cached = json.loads(transcript_file.read_text()) if transcript_file.exists() else None
        legacy = f"local|base|{config['language']}"
        if cached and (render_only or cached.get('signature', legacy) == signature):
            transcript = cached
            segments = transcript['segments']
        elif render_only:
            raise ValueError('Transkrip tersimpan diperlukan untuk render ulang. Jalankan pemrosesan sumber terlebih dahulu.')
        elif (folder / 'input.srt').exists():
            segments = parse_srt((folder / 'input.srt').read_text(), metadata['duration'])
            transcript = {'language': config['language'], 'timing': 'srt-estimated-words', 'segments': segments}
        else:
            update(job_id, status='transcribing', progress=12, message='Menyiapkan audio untuk transkripsi OpenRouter.')
            progress_update = lambda p, m: update(job_id, progress=round(p), message=m)
            segments, language = router.transcribe(source, folder, config['language'], asr_model, metadata['duration'], cancel, progress_update, billing)
            has_words = any(s.get('words') for s in segments)
            has_segments = any(s.get('text') and not s.get('words') for s in segments)
            timing = 'openrouter-mixed-timestamps' if has_words and has_segments else 'openrouter-word-timestamps' if has_words else 'openrouter-segment-timestamps'
            transcript = {'language': language, 'timing': timing, 'signature': signature, 'segments': segments}
            gc.collect()
        transcript_file.write_text(json.dumps(transcript, ensure_ascii=False))
        (folder / 'transcript.srt').write_text(srt_text(segments))
        config['timing'] = transcript['timing']
        update(job_id, language=transcript['language'], transcript_timing=transcript['timing'])
        coarse = [s for s in segments if s.get('text') and not s.get('words')]
        if coarse:
            seconds = sum(s['end']-s['start'] for s in coarse)
            add_warning(job_id, f'{len(coarse)} segmen ({seconds:.1f} detik ujaran) hanya memiliki timestamp segmen dari provider. Subtitle bagian ini tampil per segmen tanpa highlight kata; bagian lainnya tetap mengikuti timestamp kata.')
        if cancel.is_set():
            raise media.Cancelled()
        if rerender:
            chosen = [rerender]
        elif camera_only or export_only:
            chosen = deepcopy(job['clips'])
            if camera_only:
                for clip in chosen:
                    clip.update(manual_focal=False, camera_mode='auto')
        else:
            update(job_id, status='ranking', progress=56, message='Mencari rentang ujaran dan mengurutkan kandidat.')
            pool = candidates(segments, config['min_duration'], config['max_duration'], config['prompt'])
            update(job_id, message='OpenRouter menilai kandidat dan menyusun judul dari transkrip.')
            pool = router.rank(pool, config['openrouter_chat_model'], config['prompt'], config['clip_count'], billing)
            chosen = select_clips(pool, config['clip_count'])
            if not chosen:
                raise ValueError('Tidak ada kandidat klip yang valid.')
            if len(chosen) < config['clip_count']:
                add_warning(job_id, f'Hanya {len(chosen)} klip tanpa tumpang tindih berlebihan yang cocok dengan durasi pilihan.')
            for i, clip in enumerate(chosen):
                clip.update(id=f'clip{i+1}', focal=.5, status='queued')
            update(job_id, clips=chosen)
        for i, clip in enumerate(chosen):
            if cancel.is_set():
                raise media.Cancelled()
            require_space(operation='merender klip berikutnya')
            update(job_id, status='rendering', progress=60+round(37*i/len(chosen)), message=f'Menyiapkan kamera klip {i+1}/{len(chosen)}.')
            old = next((deepcopy(c) for c in job['clips'] if c['id'] == clip['id']), deepcopy(clip)) if render_only else deepcopy(clip)
            clip['status'] = 'rendering'
            store_clip(job_id, clip)
            try:
                render_config = config.copy()
                mode = clip.get('camera_mode')
                if mode == 'auto':
                    render_config['framing'] = 'auto'
                elif mode == 'fit':
                    render_config['framing'] = 'fit'
                elif clip.get('manual_focal') or mode == 'manual':
                    render_config['framing'] = 'center'
                artifact = media.render(source, folder, clip, render_config, segments, metadata, cancel,
                                        lambda message: update(job_id, message=f'Klip {i+1}/{len(chosen)} · {message}'))
                clip.update(status='complete', artifact=artifact)
                store_clip(job_id, clip)
                if artifact['framing'] == 'center_fallback':
                    add_warning(job_id, 'Wajah tidak terdeteksi pada sebagian klip. Crop tengah digunakan; posisi dapat diatur lalu dirender ulang.')
                for warning in (artifact.get('tracking') or {}).get('warnings', []):
                    add_warning(job_id, warning)
                quality = artifact.get('quality') or {}
                if quality.get('upscale_factor', 1) > 1.25:
                    area = quality['source_crop']
                    add_warning(job_id, f"Area sumber {area['width']}×{area['height']} diperbesar {quality['upscale_factor']:.2f}× untuk hasil {artifact['width']}×{artifact['height']}. Detail tetap dibatasi sumber; gunakan sumber resolusi lebih tinggi untuk hasil lebih tajam.")
            except Exception:
                if render_only:
                    old['status'] = 'complete' if old.get('artifact') else 'error'
                    store_clip(job_id, old)
                raise
        update(job_id, status='complete', progress=100, message='Klip siap ditinjau dan diunduh.', error=None, pending_reframe=False, pending_export_quality=False, pending_source_upgrade=False)
    except media.Cancelled:
        update(job_id, status='cancelled', message='Pemrosesan dibatalkan. Hasil yang selesai tetap tersedia.')
    except Exception as exc:
        if cancel.is_set():
            update(job_id, status='cancelled', message='Pemrosesan dibatalkan.')
        else:
            LOG.exception('Project %s failed', job_id)
            update(job_id, status='error', message='Pemrosesan berhenti.', error=str(exc)[-1800:])


@asynccontextmanager
async def lifespan(app):
    for path in PROJECTS.glob('*/project.json'):
        try:
            job = json.loads(path.read_text())
            if job['status'] in ACTIVE:
                job.update(status='interrupted', message='Server berhenti sebelum job selesai. Tekan Lanjutkan.', error=None)
            config = cloud_config(job['config'])
            if config != job['config']:
                job['config'] = config
                save(job)
            JOBS[job['id']] = job
            CANCEL[job['id']] = threading.Event()
        except (ValueError, KeyError):
            LOG.warning('Unreadable project %s', path)
    yield
    for event in CANCEL.values():
        event.set()
    POOL.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='OpenClip Local', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_requests(request: Request, call_next):
    if request.method in {'POST', 'PATCH', 'DELETE', 'PUT'}:
        origin = request.headers.get('origin')
        if origin and origin not in {'http://127.0.0.1:7860', 'http://localhost:7860'}:
            return JSONResponse({'detail': 'Permintaan harus berasal dari OpenClip lokal.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path == '/' or request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get('/api/health')
def health():
    return {'app': 'openclip-local', 'root': str(ROOT), 'ffmpeg': bool(shutil.which('ffmpeg')),
            'free_gb': round(shutil.disk_usage(DATA).free/1024**3, 1), 'ai_provider': 'openrouter', 'worker_limit': 1}


@app.get('/api/youtube/browsers')
def youtube_browsers():
    return {'browsers': youtube.login_browsers()}


class YoutubeLogin(BaseModel):
    browser: str = Field(max_length=20)


@app.post('/api/youtube/login')
def youtube_login(body: YoutubeLogin):
    with LOCK:
        if any(j['status'] in ACTIVE and j.get('youtube_profile') == 'openclip'
               and j.get('youtube_auth') == body.browser for j in JOBS.values()):
            raise HTTPException(409, 'Tunggu unduhan yang memakai sesi browser ini selesai.')
        try:
            return youtube.open_login(body.browser)
        except (ValueError, OSError):
            raise HTTPException(422, 'Browser tidak dapat dijalankan. Pilih browser lain yang terpasang atau gunakan file cookies.txt.') from None


@app.get('/api/projects')
def projects():
    with LOCK:
        return sorted(deepcopy(list(JOBS.values())), key=lambda x: x['created_at'], reverse=True)


class RouterSettings(BaseModel):
    api_key: str = Field(default='', max_length=512)
    asr_model: str = Field(default='openai/whisper-large-v3', max_length=120)
    chat_model: str = Field(default='google/gemini-2.5-flash', max_length=120)


@app.get('/api/settings/openrouter')
def get_router_settings():
    return router.public_settings()


@app.post('/api/settings/openrouter')
def set_router_settings(body: RouterSettings):
    with LOCK:
        if any(j['status'] in ACTIVE and (j['config'].get('asr_provider') == 'openrouter' or j['config']['ranker'] == 'openrouter') for j in JOBS.values()):
            raise HTTPException(409, 'Tunggu job OpenRouter selesai sebelum mengganti pengaturan.')
    return router.save_settings(body.api_key, body.asr_model, body.chat_model)


@app.get('/api/projects/{job_id}')
def project(job_id: str):
    return read_job(job_id)


@app.post('/api/projects', status_code=201)
async def create_project(video: UploadFile | None = File(None), subtitle: UploadFile | None = File(None), url: str = Form(''), prompt: str = Form(''), language: str = Form('auto'), min_duration: float = Form(45), max_duration: float = Form(180), clip_count: int = Form(3), aspect: str = Form('9:16'), framing: str = Form('auto'), captions: bool = Form(True), resolution: int = Form(1080), source_resolution: int = Form(2160), encoding_quality: str = Form('high'), youtube_auth: str = Form('none'), youtube_cookies: UploadFile | None = File(None), youtube_profile: str = Form('default')):
    if not math.isfinite(min_duration) or not math.isfinite(max_duration) or not 5 <= min_duration <= max_duration <= 240:
        raise HTTPException(422, 'Durasi harus 5 sampai 240 detik, minimum tidak boleh melebihi maksimum.')
    if language not in {'auto', 'id', 'en', 'ja', 'ko', 'zh', 'es', 'fr', 'de'} or aspect not in {'9:16', '1:1', '16:9'} or framing not in {'auto', 'center', 'fit'} or not 1 <= clip_count <= 5 or resolution not in {720, 1080}:
        raise HTTPException(422, 'Pengaturan tidak valid.')
    if not router.public_settings()['key_set']:
        raise HTTPException(422, 'Simpan API key di Pengaturan AI terlebih dahulu.')
    if source_resolution not in {720, 1080, 2160} or encoding_quality not in {'high', 'standard'}:
        raise HTTPException(422, 'Kualitas sumber atau ekspor tidak valid.')
    if not video or not video.filename:
        from urllib.parse import urlparse
        parsed = urlparse(url.strip())
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(422, 'Unggah video atau masukkan URL http/https yang valid.')
        if parsed.hostname.lower() in {'localhost', '127.0.0.1', '::1'}:
            raise HTTPException(422, 'Untuk video lokal, gunakan unggah file.')
    try:
        youtube.validate_choice(youtube_auth, '' if video and video.filename else url.strip(), youtube_profile)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    job_id = uuid.uuid4().hex
    folder = PROJECTS / job_id
    folder.mkdir()
    source_file = None
    try:
        if video and video.filename:
            extension = Path(video.filename).suffix.lower()
            if extension not in {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}:
                raise HTTPException(422, 'Format video: MP4, MOV, MKV, WEBM, AVI, atau M4V.')
            source_file = 'source' + extension
            total = 0
            try:
                require_space(video.size or 0, 'menyalin file unggahan')
            except ValueError as exc:
                raise HTTPException(507, str(exc)) from None
            with (folder / source_file).open('wb') as output:
                while chunk := await video.read(1024*1024):
                    total += len(chunk)
                    try:
                        require_space(len(chunk), 'menyalin file unggahan')
                    except ValueError as exc:
                        raise HTTPException(507, str(exc)) from None
                    output.write(chunk)
            if total == 0:
                raise HTTPException(422, 'File video kosong.')
        if subtitle and subtitle.filename:
            data = await subtitle.read(4*1024**2+1)
            if len(data) > 4*1024**2:
                raise HTTPException(413, 'SRT maksimal 4 MB.')
            (folder / 'input.srt').write_text(data.decode('utf-8-sig'))
        cookie_data = await youtube_cookies.read(youtube.MAX_COOKIE_BYTES+1) if youtube_cookies and youtube_cookies.filename else None
        try:
            youtube.save_choice(job_id, youtube_auth, url.strip(), cookie_data, youtube_profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    except Exception:
        shutil.rmtree(folder)
        youtube.remove(job_id)
        raise
    job = {'id': job_id, 'name': Path(video.filename).name if source_file else url.strip(), 'url': url.strip(), 'source_file': source_file,
           'youtube_auth': youtube_auth, 'youtube_profile': youtube_profile,
           'created_at': now(), 'updated_at': now(), 'status': 'queued', 'progress': 0, 'message': 'Menunggu giliran pemrosesan.', 'error': None, 'warnings': [], 'clips': [],
           'config': {'prompt': prompt[:2000], 'language': language, 'ranker': 'openrouter', 'min_duration': min_duration, 'max_duration': max_duration, 'clip_count': clip_count, 'aspect': aspect, 'framing': framing, 'captions': captions, 'resolution': resolution, 'source_resolution': source_resolution, 'encoding_quality': encoding_quality, 'asr_provider': 'openrouter', 'openrouter_asr_model': router.settings()['asr_model'], 'openrouter_chat_model': router.settings()['chat_model']}}
    with LOCK:
        JOBS[job_id] = job
        CANCEL[job_id] = threading.Event()
        save(job)
    POOL.submit(process, job_id)
    return deepcopy(job)


@app.post('/api/projects/{job_id}/cancel')
def cancel_project(job_id: str):
    job = read_job(job_id)
    if job['status'] in ACTIVE:
        CANCEL[job_id].set()
        update(job_id, message='Pembatalan diminta. Menunggu operasi aktif berhenti.')
    return read_job(job_id)


@app.post('/api/projects/{job_id}/resume')
def resume_project(job_id: str):
    job = read_job(job_id)
    if job['status'] in ACTIVE:
        raise HTTPException(409, 'Proyek masih diproses.')
    CANCEL[job_id].clear()
    update(job_id, status='queued', progress=0, error=None, warnings=[], message='Menunggu giliran.')
    POOL.submit(process, job_id)
    return read_job(job_id)


@app.post('/api/projects/{job_id}/youtube-auth')
async def set_youtube_auth(job_id: str, youtube_auth: str = Form('none'), youtube_cookies: UploadFile | None = File(None), youtube_profile: str = Form('default')):
    read_job(job_id)
    data = await youtube_cookies.read(youtube.MAX_COOKIE_BYTES+1) if youtube_cookies and youtube_cookies.filename else None
    with LOCK:
        job = read_job(job_id)
        if job['status'] in ACTIVE:
            raise HTTPException(409, 'Tunggu proses aktif berhenti sebelum mengganti akses YouTube.')
        try:
            youtube.save_choice(job_id, youtube_auth, job.get('url', ''), data, youtube_profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        update(job_id, youtube_auth=youtube_auth, youtube_profile=youtube_profile)
    # Saving a choice does not read browser cookies or resume any job.
    return read_job(job_id)


class QualitySettings(BaseModel):
    min_duration: float = Field(default=45, ge=5, le=240)
    max_duration: float = Field(default=180, ge=5, le=240)
    resolution: int = 1080


@app.post('/api/projects/{job_id}/quality')
def reprocess_quality(job_id: str, body: QualitySettings):
    job = read_job(job_id)
    if job['status'] in ACTIVE:
        raise HTTPException(409, 'Tunggu proses aktif selesai.')
    if body.min_duration > body.max_duration or body.resolution not in {720,1080}:
        raise HTTPException(422, 'Pengaturan kualitas tidak valid.')
    cloud = router.settings()
    if not cloud.get('api_key'):
        raise HTTPException(422, 'API key OpenRouter belum disimpan di Pengaturan AI.')
    config = cloud_config(job['config']); config.update(body.model_dump(), openrouter_asr_model=cloud['asr_model'], openrouter_chat_model=cloud['chat_model'])
    update(job_id, config=config, pending_reframe=False, pending_export_quality=False, pending_source_upgrade=False)
    return resume_project(job_id)


@app.post('/api/projects/{job_id}/reframe')
def reframe_project(job_id: str):
    # Queue one render operation; this path never calls STT or a text model.
    with LOCK:
        job = read_job(job_id)
        if job['status'] in ACTIVE:
            raise HTTPException(409, 'Tunggu proses aktif selesai.')
        if not job['clips'] or not (PROJECTS/job_id/'transcript.json').is_file() or not job.get('source_file'):
            raise HTTPException(409, 'Sumber, transkrip, dan potongan klip harus sudah tersedia.')
        if not (PROJECTS/job_id/job['source_file']).is_file():
            raise HTTPException(409, 'Sumber video tersimpan tidak ditemukan.')
        config = job['config'].copy()
        config['framing'] = 'auto'
        update(job_id, config=config, status='queued', progress=0, error=None, warnings=[],
               pending_reframe=True, pending_export_quality=False, pending_source_upgrade=False,
               message='Render kamera pembicara masuk antrean. Transkrip dan rentang klip dipakai kembali.')
        CANCEL[job_id].clear()
        POOL.submit(process, job_id, camera_only=True)
    return read_job(job_id)


class ExportQualitySettings(BaseModel):
    resolution: int = 1080
    source_resolution: int = 2160
    upgrade_source: bool = True


@app.post('/api/projects/{job_id}/export-quality')
def export_quality(job_id: str, body: ExportQualitySettings):
    if body.resolution not in {720, 1080} or body.source_resolution not in {720, 1080, 2160}:
        raise HTTPException(422, 'Resolusi sumber atau hasil tidak valid.')
    with LOCK:
        job = read_job(job_id)
        if job['status'] in ACTIVE:
            raise HTTPException(409, 'Tunggu proses aktif selesai.')
        if not job['clips'] or not (PROJECTS/job_id/'transcript.json').is_file() or not job.get('source_file'):
            raise HTTPException(409, 'Sumber, transkrip, dan potongan harus sudah tersedia.')
        if not (PROJECTS/job_id/job['source_file']).is_file():
            raise HTTPException(409, 'Sumber video tersimpan tidak ditemukan.')
        if body.upgrade_source and not job.get('url'):
            raise HTTPException(422, 'Proyek unggahan memakai file asli. Nonaktifkan unduh ulang sumber untuk render dari file tersebut.')
        upgrade = bool(body.upgrade_source and job['metadata']['height'] < body.source_resolution)
        config = job['config'].copy()
        config.update(resolution=body.resolution, source_resolution=body.source_resolution, encoding_quality='high')
        update(job_id, config=config, status='queued', progress=0, error=None, warnings=[],
               pending_reframe=False, pending_export_quality=True, pending_source_upgrade=upgrade,
               source_upgrade_prefix='source-hq-'+uuid.uuid4().hex[:8],
               message='Render kualitas tinggi masuk antrean. Potongan, judul, dan subtitle dipakai kembali.')
        CANCEL[job_id].clear()
        POOL.submit(process, job_id, export_only=True)
    return read_job(job_id)


def export_project_files(job_id):
    job = read_job(job_id)
    generation = hashlib.sha256(json.dumps([(c['id'],c.get('artifact',{}).get('version')) for c in job['clips']]).encode()).hexdigest()[:8]
    destination = ROOT/'exports'/job_id/generation
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for clip in job['clips']:
        if not clip.get('artifact'):
            continue
        origin = PROJECTS/job_id/f"{clip['id']}-{clip['artifact']['version']}"
        slug = re.sub(r'[^\w -]', '', clip['title']).strip()[:80] or clip['id']
        for source_name, suffix in [('clip.mp4','.mp4'),('captions.srt','.srt'),('timeline.json','.json')]:
            shutil.copy2(origin/source_name, destination/f"{clip['id']}-{slug}{suffix}")
        count += 1
    if not count:
        raise HTTPException(409, 'Belum ada hasil yang selesai dirender.')
    return destination, count


@app.post('/api/projects/{job_id}/save-exports')
def save_exports(job_id: str):
    path, count = export_project_files(job_id)
    return {'path': str(path), 'clips': count}


@app.post('/api/projects/{job_id}/open-exports')
def open_exports(job_id: str):
    path, count = export_project_files(job_id)
    if not shutil.which('xdg-open'):
        raise HTTPException(400, 'Pembuka folder tidak tersedia. Lokasi hasil: ' + str(path))
    subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'path': str(path), 'clips': count}


@app.get('/api/projects/{job_id}/download.zip')
def download_zip(job_id: str):
    path, count = export_project_files(job_id)
    archive = path.parent/f'{path.name}.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as output:
        for file in path.iterdir():
            if file.is_file():
                output.write(file,file.name)
    return FileResponse(archive, filename='OpenClip-hasil.zip', media_type='application/zip')


@app.delete('/api/projects/{job_id}')
def delete_project(job_id: str):
    job = read_job(job_id)
    if job['status'] in ACTIVE:
        raise HTTPException(409, 'Batalkan dan tunggu job berhenti sebelum menghapus.')
    with LOCK:
        shutil.rmtree(PROJECTS / job_id)
        youtube.remove(job_id)
        del JOBS[job_id]
        CANCEL.pop(job_id, None)
    return {'deleted': job_id}


@app.get('/api/projects/{job_id}/source')
def source_video(job_id: str):
    job = read_job(job_id)
    path = PROJECTS / job_id / (job.get('source_file') or 'missing')
    if not path.is_file():
        raise HTTPException(404, 'Sumber belum tersedia.')
    return FileResponse(path)


@app.get('/api/projects/{job_id}/transcript')
def transcript(job_id: str):
    read_job(job_id)
    path = PROJECTS / job_id / 'transcript.json'
    if not path.exists():
        raise HTTPException(404, 'Transkrip belum tersedia.')
    return json.loads(path.read_text())


@app.get('/api/projects/{job_id}/transcript.srt')
def transcript_download(job_id: str):
    read_job(job_id)
    path = PROJECTS / job_id / 'transcript.srt'
    if not path.exists():
        raise HTTPException(404, 'Transkrip belum tersedia.')
    return FileResponse(path, filename='transcript.srt', media_type='application/x-subrip')


class EditClip(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    focal: float = Field(default=.5, ge=0, le=1)
    title: str = Field(default='', max_length=100)
    camera_mode: str = 'auto'


@app.post('/api/projects/{job_id}/clips/{clip_id}/render')
def edit_clip(job_id: str, clip_id: str, edit: EditClip):
    job = read_job(job_id)
    if job['status'] in ACTIVE:
        raise HTTPException(409, 'Tunggu pemrosesan proyek selesai.')
    clip = next((x for x in job['clips'] if x['id'] == clip_id), None)
    if not clip:
        raise HTTPException(404, 'Klip tidak ditemukan.')
    if edit.camera_mode not in {'auto', 'manual', 'fit'}:
        raise HTTPException(422, 'Mode kamera tidak valid.')
    if not all(math.isfinite(x) for x in [edit.start, edit.end, edit.focal]) or edit.end-edit.start < 2 or edit.end-edit.start > 240 or edit.end > job['metadata']['duration']+.05:
        raise HTTPException(422, 'Rentang klip harus 2 sampai 240 detik dan berada di dalam sumber.')
    clip.update(start=edit.start, end=edit.end, focal=edit.focal, title=edit.title or clip['title'], manual_focal=edit.camera_mode == 'manual', camera_mode=edit.camera_mode)
    update(job_id, status='queued', progress=0, pending_reframe=False, pending_export_quality=False, pending_source_upgrade=False, message='Render ulang masuk antrean.')
    CANCEL[job_id].clear()
    POOL.submit(process, job_id, clip)
    return read_job(job_id)


@app.get('/api/projects/{job_id}/clips/{clip_id}/{kind}')
def artifact(job_id: str, clip_id: str, kind: str, download: bool = False):
    job = read_job(job_id)
    clip = next((x for x in job['clips'] if x['id'] == clip_id), None)
    if not clip or not clip.get('artifact'):
        raise HTTPException(404, 'Hasil klip belum tersedia.')
    files = {'video': ('clip.mp4', 'video/mp4'), 'poster': ('poster.jpg', 'image/jpeg'), 'srt': ('captions.srt', 'application/x-subrip'), 'timeline': ('timeline.json', 'application/json')}
    if kind not in files:
        raise HTTPException(404)
    name, mime = files[kind]
    path = PROJECTS / job_id / f"{clip_id}-{clip['artifact']['version']}" / name
    if not path.is_file():
        raise HTTPException(404, 'File hasil tidak ditemukan.')
    slug = re.sub(r'[^\w -]', '', clip['title']).strip()[:80] or clip_id
    filename = f'{slug}{Path(name).suffix}' if download else None
    return FileResponse(path, media_type=mime, filename=filename)


app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


@app.get('/')
def index():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')
