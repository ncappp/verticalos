#!/usr/bin/env python3
"""FaxClip screen agent (Mac, USB). Streams the phone screen to the FaxClip server only while
the owner has the Screen window open, and executes owner taps/keys/text. Never touches the
publishing queue; the server refuses input while a publication is running."""

import argparse, fcntl, json, os, re, shlex, struct, subprocess, tempfile, threading, time
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

SERVER = os.getenv('FAXCLIP_SCREEN_SERVER', 'https://verticalos-rxdl.onrender.com')
VERSION = 'SCREEN_AGENT_V1'
KEYS = {'back': 4, 'home': 3, 'recents': 187, 'enter': 66, 'delete': 67}


class Phone:
    def __init__(self, adb, serial, cfg):
        self.adb = adb
        self.serial = serial
        self.h = {'X-Device-ID': cfg['device_id'], 'Authorization': 'Bearer ' + cfg['device_token']}
        self.w = 0
        self.hgt = 0
        self.elements = []
        self.last_frame = 0
        self.tmp = Path(tempfile.mkdtemp(prefix='faxclip-screen-'))

    def run(self, *args, binary=False, timeout=25):
        r = subprocess.run([self.adb, '-s', self.serial, *args], capture_output=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError((r.stderr or b'').decode('utf-8', 'replace')[:200] or 'adb error')
        return r.stdout if binary else r.stdout.decode('utf-8', 'replace')

    def sh(self, cmd, timeout=25):
        return self.run('shell', cmd, timeout=timeout)

    def frame(self):
        png = self.run('exec-out', 'screencap', '-p', binary=True, timeout=20)
        if png[:8] != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('screencap failed')
        self.w, self.hgt = struct.unpack('>II', png[16:24])
        src = self.tmp / 's.png'
        dst = self.tmp / 's.jpg'
        src.write_bytes(png)
        subprocess.run(
            [
                'sips',
                '-s',
                'format',
                'jpeg',
                '-s',
                'formatOptions',
                '55',
                '-Z',
                '960',
                str(src),
                '--out',
                str(dst),
            ],
            capture_output=True,
            timeout=20,
            check=True,
        )
        body = dst.read_bytes()
        requests.post(
            SERVER + '/api/bridge/screen/frame',
            data=body,
            headers={
                **self.h,
                'Content-Type': 'image/jpeg',
                'X-Screen-Width': str(self.w),
                'X-Screen-Height': str(self.hgt),
            },
            timeout=20,
        ).raise_for_status()
        self.last_frame = time.time()

    def ensure_size(self):
        if not self.w:
            m = re.search(r'(\d+)x(\d+)', self.sh('wm size'))
            if m:
                self.w, self.hgt = int(m.group(1)), int(m.group(2))

    def dump(self):
        self.sh('uiautomator dump /sdcard/faxclip-screen.xml', timeout=30)
        xml = self.sh('cat /sdcard/faxclip-screen.xml')
        root = ET.fromstring(xml[xml.find('<') :])
        out = []
        for n in root.iter('node'):
            t = n.get('text', '')
            d = n.get('content-desc', '')
            c = n.get('clickable') == 'true'
            m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', n.get('bounds', ''))
            if not m or not (t or d or c):
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if x2 <= x1 or y2 <= y1:
                continue
            out.append(
                {
                    'text': t,
                    'desc': d,
                    'cls': n.get('class', '').split('.')[-1],
                    'clickable': c,
                    'cx': (x1 + x2) // 2,
                    'cy': (y1 + y2) // 2,
                }
            )
        self.elements = out[:150]
        return self.elements

    def execute(self, c):
        a = c.get('action')
        self.ensure_size()
        msg = 'Готово'
        extra = {}
        if a == 'tap':
            self.sh(f"input tap {int(c['x'] * self.w)} {int(c['y'] * self.hgt)}")
            msg = 'Нажатие'
        elif a == 'swipe':
            self.sh(
                f"input swipe {int(c['x1'] * self.w)} {int(c['y1'] * self.hgt)} {int(c['x2'] * self.w)} {int(c['y2'] * self.hgt)} {int(c['ms'])}"
            )
            msg = 'Жест'
        elif a == 'key':
            self.sh(f"input keyevent {KEYS[c['key']]}")
            msg = 'Кнопка'
        elif a == 'wake':
            self.sh('input keyevent 224')
            time.sleep(0.4)
            self.sh('wm dismiss-keyguard')
            msg = 'Телефон разбужен'
        elif a == 'text':
            t = c.get('text', '')
            if not all(32 <= ord(ch) < 127 for ch in t):
                return False, 'Пока можно вводить только латиницу и цифры. Кириллицу введите на телефоне.', {}
            self.sh('input text ' + shlex.quote(t.replace(' ', '%s')))
            msg = 'Текст введён'
        elif a == 'elements':
            els = self.dump()
            extra = {'elements': [{k: e[k] for k in ('text', 'desc', 'cls', 'clickable')} for e in els]}
            msg = f'Элементов: {len(els)}'
        elif a == 'tap_index':
            i = int(c['index'])
            if not self.elements:
                self.dump()
            if i > len(self.elements):
                return False, 'Нет элемента с таким номером — обновите список', {}
            e = self.elements[i - 1]
            self.sh(f"input tap {e['cx']} {e['cy']}")
            msg = f'Нажат элемент {i}'
        elif a == 'refresh':
            msg = 'Обновлено'
        return True, msg, extra

    def loop(self, stop):
        while not stop.is_set():
            try:
                r = requests.get(SERVER + '/api/bridge/screen/poll', headers=self.h, timeout=20)
                if r.status_code in (401, 403):
                    time.sleep(60)
                    continue
                r.raise_for_status()
                x = r.json()
                acted = False
                for c in x.get('commands', []):
                    try:
                        ok, msg, extra = self.execute(c)
                    except Exception as e:
                        ok, msg, extra = False, 'Телефон не выполнил команду: ' + str(e)[:120], {}
                    requests.post(
                        SERVER + '/api/bridge/screen/result',
                        headers=self.h,
                        json={
                            'id': c.get('id'),
                            'ok': ok,
                            'message': msg,
                            'action': c.get('action'),
                            **extra,
                        },
                        timeout=20,
                    )
                    acted = True
                if x.get('active'):
                    if acted:
                        time.sleep(0.5)
                    if acted or time.time() - self.last_frame > 1.5:
                        try:
                            self.frame()
                        except Exception:
                            pass
                    time.sleep(0.6)
                else:
                    time.sleep(4)
            except Exception:
                time.sleep(5)


def configs(root):
    out = {}
    for f in (root / '.faxclip-cloud/devices').glob('*.json'):
        try:
            x = json.loads(f.read_text())
            if (
                x.get('server') == SERVER
                and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', x.get('serial', ''))
                and x.get('device_id')
                and x.get('device_token')
            ):
                out[x['serial']] = x
        except Exception:
            continue
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    a = p.parse_args()
    root = Path(a.root)
    adb = str(root / 'platform-tools/adb')
    lock = open(Path.home() / '.faxclip-screen-agent.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Экран FaxClip уже запущен.')
    print(VERSION, 'started', flush=True)
    threads = {}
    while True:
        try:
            listing = subprocess.run([adb, 'devices'], capture_output=True, text=True, timeout=30).stdout
            phones = {
                l.split()[0]
                for l in listing.splitlines()[1:]
                if len(l.split()) > 1 and l.split()[1] == 'device'
            }
            for serial, cfg in configs(root).items():
                key = (serial, cfg['device_id'])
                alive = key in threads and threads[key][0].is_alive()
                if serial in phones and not alive:
                    stop = threading.Event()
                    ph = Phone(adb, serial, cfg)
                    t = threading.Thread(target=ph.loop, args=(stop,), daemon=True)
                    t.start()
                    threads[key] = (t, stop)
                if serial not in phones and alive:
                    threads[key][1].set()
                    threads.pop(key)
        except Exception:
            pass
        time.sleep(15)


if __name__ == '__main__':
    main()
