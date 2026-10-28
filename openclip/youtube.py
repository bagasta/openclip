"""Opt-in YouTube session access; cookie contents never enter project metadata."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse


SESSION_DIR = Path(__file__).resolve().parents[1] / 'data' / 'youtube-sessions'
BROWSER_DIR = SESSION_DIR.parent / 'youtube-browser'
BROWSERS = {'chrome', 'chromium', 'firefox', 'brave', 'edge', 'vivaldi', 'opera'}
BROWSER_COMMANDS = {
    'chrome': ('Chrome', ('google-chrome', 'google-chrome-stable')),
    'chromium': ('Chromium', ('chromium', 'chromium-browser')),
    'brave': ('Brave', ('brave-browser', 'brave')),
    'edge': ('Edge', ('microsoft-edge', 'microsoft-edge-stable')),
    'vivaldi': ('Vivaldi', ('vivaldi', 'vivaldi-stable')),
    'opera': ('Opera', ('opera',)),
}
MAX_COOKIE_BYTES = 2 * 1024**2
DOMAINS = {'youtube.com', 'youtu.be', 'youtube-nocookie.com'}


class AuthError(ValueError):
    pass


def youtube_domain(host):
    host = host.lower().removeprefix('.').rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in DOMAINS)


def is_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in {'https', 'http'} and youtube_domain(parsed.hostname or '')
    except ValueError:
        return False


def cookie_path(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('ID proyek tidak valid.')
    return SESSION_DIR / (job_id + '.txt')


def validate_choice(source, url, profile='default'):
    if source not in BROWSERS | {'none', 'file'}:
        raise ValueError('Pilihan akses YouTube tidak valid.')
    if source != 'none' and not is_url(url):
        raise ValueError('Cookie hanya dapat dipakai untuk tautan YouTube.')
    if profile not in {'default', 'openclip'} or (profile == 'openclip' and source not in BROWSER_COMMANDS):
        raise ValueError('Profil sesi YouTube tidak valid.')


def browser_command(browser):
    if browser not in BROWSER_COMMANDS:
        raise ValueError('Pilih browser yang tersedia untuk login YouTube.')
    return next((path for name in BROWSER_COMMANDS[browser][1] if (path := shutil.which(name))), None)


def login_browsers():
    return [{'id': browser, 'name': label} for browser, (label, _) in BROWSER_COMMANDS.items()
            if browser_command(browser)]


def browser_profile(browser):
    if browser not in BROWSER_COMMANDS:
        raise ValueError('Browser untuk profil OpenClip tidak valid.')
    return BROWSER_DIR / browser


def open_login(browser):
    command = browser_command(browser)
    if not command:
        raise ValueError('Browser tidak ditemukan. Pilih Chrome/Chromium yang terpasang atau gunakan file cookies.txt.')
    profile = browser_profile(browser)
    BROWSER_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    BROWSER_DIR.chmod(0o700)
    profile.mkdir(exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    subprocess.Popen([command, '--user-data-dir=' + str(profile), '--no-first-run',
                      '--no-default-browser-check', '--new-window', 'https://www.youtube.com/signin'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {'browser': browser, 'profile': 'openclip'}


def runtime_options():
    # The Python package does not enable Node automatically, even when it is installed.
    for runtime in ('deno', 'node'):
        if path := shutil.which(runtime):
            return {'js_runtimes': {runtime: {'path': path}}}
    raise ValueError('Unduhan YouTube memerlukan Node.js 22+ atau Deno 2.3+. Pasang salah satunya lalu jalankan ulang OpenClip.')


def filter_cookie_file(data):
    if len(data) > MAX_COOKIE_BYTES:
        raise ValueError('File cookie maksimal 2 MB.')
    try:
        lines = data.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError:
        raise ValueError('File cookie harus berupa teks UTF-8 berformat Netscape.') from None
    if not lines or lines[0].strip() not in {'# Netscape HTTP Cookie File', '# HTTP Cookie File'}:
        raise ValueError('Pilih file cookies.txt berformat Netscape, bukan JSON atau HTML.')
    result = ['# Netscape HTTP Cookie File']
    for line in lines[1:]:
        if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue
        fields = line.removeprefix('#HttpOnly_').split('\t')
        if len(fields) != 7:
            raise ValueError('Format baris cookie tidak valid. Ekspor ulang sebagai Netscape cookies.txt.')
        domain, subdomains, path, secure, expires, name, value = fields
        if not youtube_domain(domain):
            continue
        if (subdomains not in {'TRUE', 'FALSE'} or secure not in {'TRUE', 'FALSE'}
                or (domain.startswith('.') != (subdomains == 'TRUE'))
                or not path.startswith('/') or not name
                or (expires and not re.fullmatch(r'[0-9]{1,12}', expires))
                or any(ord(char) < 32 or ord(char) == 127 for char in ''.join(fields))):
            raise ValueError('Format cookie YouTube tidak valid. Ekspor ulang file cookies.txt.')
        result.append(line)
    if len(result) == 1:
        raise ValueError('File tidak berisi cookie YouTube. Ekspor cookie dari youtube.com setelah login.')
    return '\n'.join(result) + '\n'


def remove(job_id):
    cookie_path(job_id).unlink(missing_ok=True)


def save_choice(job_id, source, url, data=None, profile='default'):
    validate_choice(source, url, profile)
    target = cookie_path(job_id)
    if source != 'file':
        remove(job_id)
        return
    if data is None:
        if not target.is_file():
            raise ValueError('Pilih file cookies.txt terlebih dahulu.')
        return
    content = filter_cookie_file(data)
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    SESSION_DIR.chmod(0o700)
    # mkstemp creates the temporary file with mode 0600 before any secret is written.
    descriptor, temporary = tempfile.mkstemp(dir=SESSION_DIR, prefix='.cookie-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
            output.write(content)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def options(job):
    source = job.get('youtube_auth', 'none')
    profile = job.get('youtube_profile', 'default')
    validate_choice(source, job.get('url', ''), profile)
    if source in BROWSERS:
        if profile == 'openclip':
            return {'cookiesfrombrowser': (source, str(browser_profile(source)))}
        return {'cookiesfrombrowser': (source,)}
    if source == 'file':
        path = cookie_path(job['id'])
        if not path.is_file():
            raise AuthError('File cookie YouTube tidak ditemukan. Pilih ulang di Akses YouTube lalu simpan.')
        return {'cookiefile': str(path)}
    return {}


def prepare_session(downloader):
    """Called only by a user-queued download, before making network requests."""
    try:
        jar = downloader.cookiejar
        for cookie in list(jar):
            if not youtube_domain(cookie.domain):
                jar.clear(cookie.domain, cookie.path, cookie.name)
        jar.clear_expired_cookies()
        if not len(jar):
            raise AuthError('Tidak ada cookie YouTube aktif. Login YouTube di browser yang dipilih atau unggah file cookie baru, lalu coba lagi.')
    except AuthError:
        raise
    except Exception:
        # Provider exceptions can contain paths or cookie contents. Keep them private.
        raise AuthError('Cookie YouTube tidak dapat dibaca. Login lewat tombol Login YouTube, lalu tutup jendela browser itu sebelum mencoba lagi. Jika profil terkunci atau gagal dekripsi, gunakan file cookies.txt dari profil yang benar.') from None
    finally:
        # yt-dlp should neither export the browser jar nor overwrite the uploaded file.
        downloader.params['cookiefile'] = None


def warning_code(message):
    lower = str(message).lower()
    if any(text in lower for text in ('challenge solving failed', 'signature solving failed', 'javascript runtime', 'ejs challenge', 'challenge solver')):
        return 'javascript'
    if 'po token' in lower or 'sabr' in lower:
        return 'stream_access'
    return None


def download_error(error, authenticated=False, issues=()):
    message = re.sub(r'\x1b\[[0-9;]*m', '', str(error))
    lower = message.lower().replace('’', "'")
    if 'not a bot' in lower or 'confirm you' in lower or 'login_required' in lower or 'sign in' in lower:
        detail = ('Sesi yang dipilih belum diterima atau sudah kedaluwarsa. Login ulang di browser atau ganti file cookie.'
                  if authenticated else 'Pilih browser yang sudah login atau unggah file cookies.txt pada Akses YouTube.')
        return AuthError('YouTube meminta verifikasi login. ' + detail + ' Simpan akses, lalu klik Lanjutkan / coba lagi. Jika tetap ditolak, unggah file video langsung.')
    if 'cookie' in lower and any(word in lower for word in ('load', 'decrypt', 'database', 'copy', 'keyring')):
        return AuthError('Cookie browser tidak dapat dibaca. Pilih browser/profil yang sudah login, atau gunakan file cookies.txt melalui Akses YouTube.')
    if 'requested format is not available' in lower or 'only images are available' in lower:
        detail = (' Pemrosesan JavaScript YouTube gagal; format video belum terbaca.' if 'javascript' in issues
                  else ' YouTube masih membatasi akses stream pada sesi ini.' if 'stream_access' in issues else '')
        return ValueError('Format video/audio yang sesuai belum tersedia dari YouTube.' + detail + ' Ini berbeda dari error login. Pastikan video dapat diputar di jendela Login YouTube, tutup jendela itu, lalu coba lagi. Jika tetap gagal, unggah video langsung.')
    return ValueError(message[-1800:])
