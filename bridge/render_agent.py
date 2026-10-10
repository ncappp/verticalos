#!/usr/bin/env python3
"""FaxClip render agent (Mac). Takes assembly jobs from the FaxClip server, picks a random take
for every scene, glues them into a 1080x1920 video with ffmpeg, draws the text plate and music,
and uploads the finished MP4. Never touches phones or the publishing queue directly."""

import argparse, fcntl, json, os, random, re, shutil, subprocess, tempfile, time, hashlib
from pathlib import Path
import requests

SERVER = os.getenv('FAXCLIP_RENDER_SERVER', 'https://verticalos-rxdl.onrender.com')
VERSION = 'RENDER_AGENT_V2'
VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.webm', '.mkv')
AUDIO_EXT = ('.mp3', '.m4a', '.aac', '.wav', '.ogg')
FONT_FILES = {
    'Arial Bold': ['Arial Bold.ttf'],
    'Arial': ['Arial.ttf'],
    'Helvetica': ['Helvetica.ttc'],
    'Impact': ['Impact.ttf'],
    'Georgia': ['Georgia.ttf'],
    'Verdana Bold': ['Verdana Bold.ttf'],
    'Trebuchet MS Bold': ['Trebuchet MS Bold.ttf'],
    'Courier New Bold': ['Courier New Bold.ttf'],
}
FONT_DIRS = [
    '/System/Library/Fonts/Supplemental',
    '/Library/Fonts',
    '/System/Library/Fonts',
    str(Path.home() / 'Library/Fonts'),
    '/usr/share/fonts/truetype/msttcorefonts',
    '/usr/share/fonts/msttcore',
    '/usr/share/fonts',
]
LINUX_ALIASES = {
    'Arial Bold.ttf': ['arialbd.ttf', 'LiberationSans-Bold.ttf'],
    'Arial.ttf': ['arial.ttf', 'LiberationSans-Regular.ttf'],
    'Impact.ttf': ['impact.ttf'],
    'Georgia.ttf': ['georgia.ttf'],
    'Verdana Bold.ttf': ['verdanab.ttf'],
    'Trebuchet MS Bold.ttf': ['trebucbd.ttf'],
    'Courier New Bold.ttf': ['courbd.ttf'],
    'Helvetica.ttc': ['LiberationSans-Regular.ttf'],
}


class Fail(Exception):
    pass


def ffmpeg_bin():
    for p in (shutil.which('ffmpeg'), '/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg'):
        if p and os.path.exists(p):
            return p
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def font_path(name):
    names = FONT_FILES.get(name, ['Arial Bold.ttf']) + FONT_FILES['Arial Bold']
    for n in names:
        for cand in [n] + LINUX_ALIASES.get(n, []):
            for d in FONT_DIRS:
                p = Path(d) / cand
                if p.exists():
                    return str(p)
                if d == '/usr/share/fonts':
                    hit = next(Path(d).rglob(cand), None) if Path(d).exists() else None
                    if hit:
                        return str(hit)
    raise Fail('Не найден шрифт ' + name)


class Source:
    def __init__(self, spec, cache):
        self.type = spec['type']
        self.url = spec['url']
        self.cache = cache
        if self.type == 'local':
            self.root = (Path.home() / 'Downloads' / self.url).resolve()
            if not str(self.root).startswith(str((Path.home() / 'Downloads').resolve())):
                raise Fail('Папка должна быть внутри «Загрузки»')

    def _ylist(self, path):
        items = []
        off = 0
        while True:
            r = requests.get(
                'https://cloud-api.yandex.net/v1/disk/public/resources',
                params={'public_key': self.url, 'path': '/' + path.strip('/'), 'limit': 200, 'offset': off},
                timeout=30,
            )
            if r.status_code == 404:
                raise Fail('Нет папки на Яндекс.Диске: ' + path)
            r.raise_for_status()
            e = r.json().get('_embedded') or {}
            b = e.get('items') or []
            items += b
            off += len(b)
            if len(b) < 200 or off >= e.get('total', 0):
                return items

    def files(self, folder, exts):
        if self.type == 'local':
            d = (self.root / folder).resolve()
            if not str(d).startswith(str(self.root)):
                raise Fail('Неверная папка')
            if not d.is_dir():
                raise Fail(f'Нет папки «{folder}» в {self.root}')
            return sorted(
                str(p)
                for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in exts and not p.name.startswith('.')
            )
        return [
            i for i in self._ylist(folder) if i.get('type') == 'file' and i['name'].lower().endswith(exts)
        ]

    def folders(self):
        if self.type == 'local':
            if not self.root.is_dir():
                raise Fail(f'Нет папки {self.root}. Создайте её и разложите сцены по подпапкам.')
            return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith('.'))
        return [i['name'] for i in self._ylist('/') if i.get('type') == 'dir']

    def fetch(self, item):
        if self.type == 'local':
            return item
        key = hashlib.sha256(
            (self.url + item['path'] + str(item.get('md5') or item.get('size'))).encode()
        ).hexdigest()[:24]
        dst = self.cache / (key + Path(item['name']).suffix.lower())
        if not dst.exists():
            href = item.get('file')
            if not href:
                raise Fail('Яндекс.Диск не дал ссылку на файл ' + item['name'])
            tmp = dst.with_suffix('.part')
            with requests.get(href, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, 'wb') as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch)
            tmp.rename(dst)
        return str(dst)

    def label(self, item):
        return Path(item).name if isinstance(item, str) else item['name']


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise Fail('ffmpeg: ' + (r.stderr or '')[-400:].replace('\n', ' '))
    return r


def probe(ff, path):
    r = subprocess.run([ff, '-hide_banner', '-i', path], capture_output=True, text=True, timeout=60)
    m = re.search(r'Duration: (\d+):(\d+):(\d+\.?\d*)', r.stderr)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
    return dur, bool(re.search(r'Stream #.*Audio:', r.stderr)), bool(re.search(r'Stream #.*Video:', r.stderr))


def hexrgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def plate_png(text, st, pl, w, h, out):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    if not text.strip():
        img.save(out)
        return
    font = ImageFont.truetype(font_path(st['font']), st['size'])
    d = ImageDraw.Draw(img)
    pad = pl['padding'] if pl['enabled'] else 0
    maxw = w - 2 * 72 - 2 * pad
    lines = []
    for para in text.replace('\r', '').split('\n'):
        cur = ''
        for word in para.split():
            t = (cur + ' ' + word).strip()
            if d.textlength(t, font=font) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    lines = [l for l in lines if l is not None]
    if len(lines) > st['max_lines']:
        lines = lines[: st['max_lines']]
        l = lines[-1]
        while l and d.textlength(l + '…', font=font) > maxw:
            l = l[:-1]
        lines[-1] = l.rstrip() + '…'
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * st['line_spacing'])
    bh = lh * len(lines) - (lh - (asc + desc))
    widths = [d.textlength(l, font=font) for l in lines]
    bw = int(max(widths or [0]))
    y = {'top': int(h * 0.12), 'center': (h - bh) // 2, 'bottom': int(h * 0.80) - bh}[pl['position']] + int(
        h * pl['offset'] / 100
    )
    y = max(pad + 20, min(h - bh - pad - 20, y))
    if pl['enabled'] and pl['opacity'] > 0:
        x0 = {'left': 72, 'center': (w - bw) // 2, 'right': w - 72 - bw}[st['align']] - pad
        d.rounded_rectangle(
            [x0, y - pad, x0 + bw + 2 * pad, y + bh + pad],
            radius=pl['radius'],
            fill=hexrgb(pl['color']) + (int(255 * pl['opacity'] / 100),),
        )
    for i, l in enumerate(lines):
        lw = widths[i]
        x = {'left': 72 + pad, 'center': (w - lw) / 2, 'right': w - 72 - pad - lw}[st['align']]
        d.text(
            (x, y + i * lh),
            l,
            font=font,
            fill=hexrgb(st['color']) + (255,),
            stroke_width=st['stroke_width'],
            stroke_fill=hexrgb(st['stroke_color']) + (255,),
        )
    img.save(out)


def banner_xy(pos, mx, my):
    """ffmpeg overlay x/y expressions for a banner position like 'bottom-right' or 'center'."""
    v, hz = {
        'top-left': ('top', 'left'),
        'top': ('top', 'center'),
        'top-right': ('top', 'right'),
        'left': ('middle', 'left'),
        'center': ('middle', 'center'),
        'right': ('middle', 'right'),
        'bottom-left': ('bottom', 'left'),
        'bottom': ('bottom', 'center'),
        'bottom-right': ('bottom', 'right'),
    }.get(pos, ('bottom', 'center'))
    x = {'left': str(int(mx)), 'center': '(W-w)/2', 'right': f'W-w-{int(mx)}'}[hz]
    y = {'top': str(int(my)), 'middle': '(H-h)/2', 'bottom': f'H-h-{int(my)}'}[v]
    return x, y


def fetch_banner(b, cache, headers):
    """Banner images are cached on the Mac by checksum; downloaded from FaxClip once."""
    sha = re.sub(r'[^0-9a-f]', '', str(b.get('sha256', '')))[:64]
    path = cache / 'banners' / (sha or b['banner_id'])
    if path.exists() and sha and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
        return str(path)
    r = requests.get(SERVER + '/api/bridge/banners/' + b['banner_id'], headers=headers or {}, timeout=60)
    if r.status_code == 404:
        raise Fail('Баннер удалён из FaxClip — уберите его из рецепта')
    r.raise_for_status()
    if sha and hashlib.sha256(r.content).hexdigest() != sha:
        raise Fail('Баннер скачался с ошибкой, повторим позже')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return str(path)


def render(job, ff, cache, renew, headers=None):
    p = job['payload']
    src = Source(p['source'], cache)
    W, H, FPS = p['video']['w'], p['video']['h'], p['video']['fps']
    tmp = Path(tempfile.mkdtemp(prefix='faxclip-render-'))
    try:
        parts = []
        used = []
        total = 0
        cap = p['timing']['max_total']
        for i, s in enumerate(p['scenes']):
            items = src.files(s['folder'], VIDEO_EXT)
            if not items:
                raise Fail(f'В папке «{s["folder"]}» нет видео')
            item = random.choice(items)
            path = src.fetch(item)
            used.append(f"{s['folder']}/{src.label(item)}")
            dur, has_a, has_v = probe(ff, path)
            if not has_v:
                raise Fail('Файл без видео: ' + src.label(item))
            t = min([x for x in (dur or 9999, s['max_sec'] or 9999, max(0.5, cap - total)) if x > 0])
            out = tmp / f'p{i}.mp4'
            vf = f'scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p'
            cmd = [ff, '-y', '-hide_banner', '-loglevel', 'error', '-t', f'{t:.3f}', '-i', path]
            if not has_a:
                cmd += ['-f', 'lavfi', '-t', f'{t:.3f}', '-i', 'anullsrc=r=44100:cl=stereo']
            cmd += [
                '-vf',
                vf,
                '-map',
                '0:v:0',
                '-map',
                '0:a:0' if has_a else '1:a:0',
                '-c:v',
                'libx264',
                '-preset',
                'veryfast',
                '-crf',
                '20',
                '-c:a',
                'aac',
                '-ar',
                '44100',
                '-ac',
                '2',
                '-b:a',
                '160k',
                '-shortest',
                str(out),
            ]
            run(cmd)
            parts.append(out)
            total += t
            renew()
            if total >= cap - 0.05:
                break
        lst = tmp / 'list.txt'
        lst.write_text(''.join(f"file '{x}'\n" for x in parts))
        joined = tmp / 'joined.mp4'
        run(
            [
                ff,
                '-y',
                '-hide_banner',
                '-loglevel',
                'error',
                '-f',
                'concat',
                '-safe',
                '0',
                '-i',
                str(lst),
                '-c',
                'copy',
                str(joined),
            ]
        )
        png = tmp / 'plate.png'
        plate_png(p.get('text') or '', p['text_style'], p['plate'], W, H, str(png))
        a = p['audio']
        tm = p['timing']
        music = None
        if a.get('music_folder'):
            ms = src.files(a['music_folder'], AUDIO_EXT)
            if ms:
                mi = random.choice(ms)
                music = src.fetch(mi)
                used.append(f"{a['music_folder']}/{src.label(mi)}")
        end = tm['text_end'] if tm['text_end'] > tm['text_start'] else total + 1
        cmd = [
            ff,
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            str(joined),
            '-loop',
            '1',
            '-i',
            str(png),
        ]
        if music:
            cmd += ['-stream_loop', '-1', '-i', music]
        fc = [f"[0:v][1:v]overlay=0:0:shortest=1:enable='between(t,{tm['text_start']},{end})'[v0]"]
        last = 'v0'
        idx = 3 if music else 2
        for k, b in enumerate(p.get('banners') or []):
            img = fetch_banner(b, cache, headers)
            cmd += ['-loop', '1', '-i', img]
            bw = max(2, int(W * b['width_pct'] / 100) // 2 * 2)
            x, y = banner_xy(b['position'], b['margin_x'], b['margin_y'])
            b_end = b['end'] if b['end'] > b['start'] else total + 1
            fc.append(
                f"[{idx}:v]scale={bw}:-2,format=rgba,colorchannelmixer=aa={b['opacity'] / 100:.2f}[bn{k}];"
                f"[{last}][bn{k}]overlay=x={x}:y={y}:shortest=1:enable='between(t,{b['start']},{b_end})'[vb{k}]"
            )
            last = f'vb{k}'
            idx += 1
        fc.append(f'[{last}]null[v]')
        ov = (a['original_volume'] / 100) if a['original'] else 0
        if music:
            fc.append(
                f"[0:a]volume={ov}[a0];[2:a]volume={a['music_volume'] / 100}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            )
        else:
            fc.append(f"[0:a]volume={ov}[a]")
        final = tmp / 'final.mp4'
        cmd += [
            '-filter_complex',
            ';'.join(fc),
            '-map',
            '[v]',
            '-map',
            '[a]',
            '-t',
            f'{total:.3f}',
            '-c:v',
            'libx264',
            '-preset',
            'medium',
            '-crf',
            '21',
            '-pix_fmt',
            'yuv420p',
            '-c:a',
            'aac',
            '-b:a',
            '160k',
            '-movflags',
            '+faststart',
            str(final),
        ]
        run(cmd, timeout=1800)
        renew()
        return final, used, total, tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def scan(job, cache):
    p = job['payload']
    src = Source(p['source'], cache)
    items = []
    try:
        items.append({'path': '/', 'folders': src.folders()[:60]})
    except Fail as e:
        return {'ok': False, 'error': str(e), 'items': []}
    ok = True
    for f in [s['folder'] for s in p['scenes']] + ([p['music_folder']] if p.get('music_folder') else []):
        try:
            v = len(src.files(f, VIDEO_EXT))
            a = len(src.files(f, AUDIO_EXT))
            items.append({'path': f, 'videos': v, 'audio': a})
            ok = ok and (a > 0 if f == p.get('music_folder') else v > 0)
        except Fail as e:
            items.append({'path': f, 'error': str(e)})
            ok = False
    return {'ok': ok, 'items': items}


def creds(root):
    for f in sorted((root / '.faxclip-cloud/devices').glob('*.json')):
        try:
            x = json.loads(f.read_text())
            if x.get('server') == SERVER and x.get('device_id') and x.get('device_token'):
                return {'X-Device-ID': x['device_id'], 'Authorization': 'Bearer ' + x['device_token']}
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()
    root = Path(a.root)
    lock = open(Path.home() / '.faxclip-render-agent.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Склейка FaxClip уже запущена.')
    cache = (
        Path.home() / 'Library/Caches/FaxClip Render'
        if os.uname().sysname == 'Darwin'
        else Path.home() / '.cache/faxclip-render'
    )
    cache.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_bin()
    print(VERSION, 'started', 'ffmpeg=' + str(ff), flush=True)
    while True:
        h = creds(root)
        if not h:
            time.sleep(60)
            continue
        try:
            r = requests.post(
                SERVER + '/api/bridge/render/claim',
                headers=h,
                json={'version': VERSION, 'ffmpeg': bool(ff)},
                timeout=30,
            )
            if r.status_code in (401, 403):
                time.sleep(60)
                continue
            r.raise_for_status()
            job = r.json().get('job')
            if not job:
                if a.once:
                    return
                time.sleep(15)
                continue
            jid = job['id']
            base = SERVER + '/api/bridge/render/' + jid
            try:
                if job['kind'] == 'scan':
                    requests.post(
                        base + '/scan-result', headers=h, json=scan(job, cache), timeout=30
                    ).raise_for_status()
                    continue
                if not ff:
                    raise Fail('На Mac нет ffmpeg. Запустите установщик склейки ещё раз.')
                last = [time.time()]

                def renew():
                    if time.time() - last[0] > 60:
                        last[0] = time.time()
                        try:
                            requests.post(base + '/renew', headers=h, timeout=20)
                        except Exception:
                            pass

                print('render', jid, flush=True)
                final, used, total, tmp = render(job, ff, cache, renew, h)
                try:
                    with open(final, 'rb') as f:
                        up = requests.post(
                            base + '/result',
                            headers=h,
                            files={'file': ('video.mp4', f, 'video/mp4')},
                            data={
                                'meta': json.dumps(
                                    {'files': used, 'duration': round(total, 2)}, ensure_ascii=False
                                )
                            },
                            timeout=600,
                        )
                    up.raise_for_status()
                    print('done', jid, flush=True)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            except Exception as e:
                msg = (
                    str(e)
                    if isinstance(e, Fail)
                    else 'Ошибка на Mac: ' + type(e).__name__ + ' ' + str(e)[:200]
                )
                print('fail', jid, msg, flush=True)
                try:
                    requests.post(base + '/fail', headers=h, json={'error': msg[:480]}, timeout=20)
                except Exception:
                    pass
        except Exception as e:
            print('net', type(e).__name__, flush=True)
            time.sleep(20)
        if a.once:
            return


if __name__ == '__main__':
    main()
