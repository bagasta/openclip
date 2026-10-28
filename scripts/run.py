#!/usr/bin/env python3
"""Install locally and run only the OpenClip server belonging to this folder."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.venv'
PID = ROOT / 'data' / 'server.pid'
URL = 'http://127.0.0.1:7860'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if args.stop:
        if not PID.exists():
            print('OpenClip tidak memiliki server aktif yang tercatat.'); return
        pid = int(PID.read_text())
        proc = Path(f'/proc/{pid}')
        try:
            cmd = (proc / 'cmdline').read_bytes().decode().replace('\0', ' ')
            cwd = (proc / 'cwd').resolve()
            if cwd != ROOT or 'openclip.app:app' not in cmd:
                raise RuntimeError('PID bukan server OpenClip dari folder ini.')
            os.kill(pid, signal.SIGTERM)
            print('Perintah berhenti dikirim ke OpenClip.')
        except FileNotFoundError:
            print('Server sudah berhenti.')
        PID.unlink(missing_ok=True)
        return
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        sys.exit('FFmpeg dan ffprobe diperlukan. Ubuntu: sudo apt install ffmpeg')
    uv = shutil.which('uv')
    if not ENV.exists():
        if uv:
            subprocess.run([uv, 'venv', '--python', sys.executable, str(ENV)], check=True)
        else:
            subprocess.run([sys.executable, '-m', 'venv', str(ENV)], check=True)
    stamp = ENV / 'openclip-requirements.txt'
    req = (ROOT / 'requirements.txt').read_text()
    if not stamp.exists() or stamp.read_text() != req:
        # These wheels share the cv2 namespace. Remove the previous headless
        # distribution before installing MediaPipe's OpenCV dependency.
        legacy_cv = [name for name, pattern in [('opencv-python-headless', 'opencv_python_headless-*.dist-info'),
                                               ('opencv-python', 'opencv_python-*.dist-info')]
                     if any(ENV.glob('lib/python*/site-packages/' + pattern))]
        if legacy_cv:
            uninstall = [uv, 'pip', 'uninstall', '--python', str(ENV / 'bin/python')] if uv else [str(ENV / 'bin/python'), '-m', 'pip', 'uninstall', '-y']
            subprocess.run(uninstall + legacy_cv, check=True)
        command = [uv, 'pip', 'install', '--python', str(ENV / 'bin/python')] if uv else [str(ENV / 'bin/python'), '-m', 'pip', 'install']
        if legacy_cv and uv:
            command += ['--reinstall-package', 'opencv-contrib-python']
        subprocess.run(command + ['-r', str(ROOT / 'requirements.txt')], check=True)
        if legacy_cv and not uv:
            subprocess.run(command + ['--force-reinstall', '--no-deps', 'opencv-contrib-python==4.14.0.94'], check=True)
        stamp.write_text(req)
    sys.path.insert(0, str(ROOT))
    from openclip.tracking import prepare_model
    print('Menyiapkan model kamera pembicara lokal (unduhan pertama sekitar 3.8 MB).', flush=True)
    prepare_model()
    if args.setup:
        print('Dependensi siap. Jalankan: make dev'); return
    try:
        with urllib.request.urlopen(URL + '/api/health', timeout=2) as response:
            running = json.load(response)
        if running.get('app') == 'openclip-local' and running.get('root') == str(ROOT):
            print(f'OpenClip sudah berjalan: {URL}'); return
        sys.exit('Port 7860 dipakai aplikasi lain.')
    except urllib.error.URLError:
        pass
    (ROOT / 'data').mkdir(exist_ok=True)
    process = subprocess.Popen([str(ENV / 'bin/python'), '-m', 'uvicorn', 'openclip.app:app', '--host', '127.0.0.1', '--port', '7860', '--no-access-log'], cwd=ROOT)
    PID.write_text(str(process.pid))
    print(f'OpenClip: {URL}\nCtrl+C untuk berhenti.', flush=True)
    try:
        process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=10)
    finally:
        PID.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
