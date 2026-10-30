"""Start or inspect JobRadar's isolated persistent Chrome (macOS).

Only manages its own data directory and loopback endpoint. Does not copy any
existing browser data, sign in, search, apply, or install a background service.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / '.local/state/jobradar/browser'
PROFILE = STATE / 'profile'
PORT = 19222
CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def status():
    try:
        pid = int((STATE / 'pid').read_text())
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True).strip()
        if str(CHROME) not in command or f'--user-data-dir={PROFILE}' not in command or f'--remote-debugging-port={PORT}' not in command:
            raise RuntimeError('PID does not belong to JobRadar Chrome')
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{PORT}/json/version', timeout=2) as response:
            version = json.load(response)
        from urllib.parse import urlsplit
        endpoint = urlsplit(version['webSocketDebuggerUrl'])
        if endpoint.hostname not in {'127.0.0.1', 'localhost'} or endpoint.port != PORT:
            raise RuntimeError('Unexpected browser endpoint')
        return {'ready': True, 'pid': pid, 'port': PORT, 'profile': str(PROFILE), 'browser': version['Browser'], 'login': 'not_verified'}
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        return {'ready': False, 'port': PORT, 'profile': str(PROFILE), 'reason': type(exc).__name__,
                'profile_present': PROFILE.is_dir(), 'note': 'No login verification.'}


def start():
    current = status()
    if current['ready']:
        return current
    if not CHROME.is_file():
        raise RuntimeError('Google Chrome is not installed')
    # Refuse to attach to another browser or service occupying our port.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROFILE.mkdir(exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    os.chmod(PROFILE, 0o700)
    with (STATE / 'chrome.log').open('ab') as log:
        proc = subprocess.Popen([
            str(CHROME), f'--user-data-dir={PROFILE}',
            f'--remote-debugging-port={PORT}', '--remote-debugging-address=127.0.0.1',
            f'--remote-allow-origins=http://127.0.0.1:{PORT}',
            '--no-first-run', '--no-default-browser-check',
            'https://www.zhipin.com/web/user/',
        ], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    (STATE / 'pid').write_text(str(proc.pid))
    for _ in range(40):
        current = status()
        if current['ready']:
            return current
        if proc.poll() is not None:
            raise RuntimeError('Chrome exited before its endpoint was ready')
        time.sleep(0.25)
    raise RuntimeError('Chrome endpoint not ready after 10 seconds; inspect local chrome.log')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status'])
    args = parser.parse_args()
    result = start() if args.action == 'start' else status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ready'] else 1)
