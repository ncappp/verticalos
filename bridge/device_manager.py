#!/usr/bin/env python3
"""Authorized USB devices + owner-issued setup files. Never guesses between multiple phones."""

import argparse, fcntl, hashlib, json, os, re, subprocess, threading, time
from pathlib import Path
import requests
from adb_control import ADB
from pc_bridge import Bridge

SERVER = 'https://verticalos-rxdl.onrender.com'


def choose_serial(setup, phones, mapped):
    explicit = setup.get('serial', '')
    if explicit:
        return explicit if explicit in phones else None
    candidates = [s for s in phones if s not in mapped]
    return candidates[0] if len(candidates) == 1 else None


def valid_setup(x):
    return (
        isinstance(x, dict)
        and x.get('format') == 'FAXCLIP_DEVICE_SETUP_V1'
        and x.get('server') == SERVER
        and isinstance(x.get('device_id'), str)
        and re.fullmatch(r'[a-f0-9-]{36}', x['device_id'])
        and isinstance(x.get('code'), str)
        and re.fullmatch(
            r'[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{5}){2}', x['code']
        )
        and isinstance(x.get('serial', ''), str)
        and (not x.get('serial') or re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', x['serial']))
    )


def save_config(file, data):
    tmp = file.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, file)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    args = p.parse_args()
    root = Path(args.root)
    adbpath = root / 'platform-tools/adb'
    private = root / '.faxclip-cloud'
    configs = private / 'devices'
    configs.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(configs, 0o700)
    manager_lock = open(private / 'device-manager.lock', 'a')
    try:
        fcntl.flock(manager_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Менеджер уже запущен для этой папки. Второй экземпляр не запускается.')
    try:
        with requests.get(SERVER + '/api/health', timeout=30) as r:
            if not r.ok or r.json().get('device_setup') != 1:
                raise SystemExit('Сервер ещё не обновлён для раздела Устройства. Менеджер не запущен.')
    except requests.RequestException:
        raise SystemExit('Сервер недоступен. Менеджер не запущен.')
    downloads = Path.home() / 'Downloads'
    workers = {}
    announced = set()
    reported_offline = set()

    def note(key, text):
        if key not in announced:
            print(text, flush=True)
            announced.add(key)

    def worker(serial, settings, stop):
        b = None
        try:
            b = Bridge(
                SERVER,
                {'serial': serial, 'adb': str(adbpath), **settings, 'mode': 'ACCESSIBILITY_PUBLISH'},
                private / 'work',
            )
            b.controller_stop = stop
            b.run()
        except Exception:
            note('worker-' + serial, 'Менеджер: один из мостов остановлен. Другие устройства не отключены.')
        finally:
            if b:
                b.phone_lock.close()

    print(
        'FaxClip: менеджер устройств запущен. Добавляйте устройства в Mini App → Устройства; файлы подключения сохраняйте в Downloads этого Mac.',
        flush=True,
    )
    try:
        while True:
            if (private / 'manager-stop').exists():
                raise KeyboardInterrupt
            listing = subprocess.run(
                [str(adbpath), 'devices'], capture_output=True, text=True, timeout=30
            ).stdout
            phones = [
                x.split()[0]
                for x in listing.splitlines()[1:]
                if len(x.split()) > 1 and x.split()[1] == 'device'
            ]
            settings = {}
            for file in configs.glob('*.json'):
                try:
                    x = json.loads(file.read_text())
                    serial = x['serial']
                    if x.get('server') != SERVER or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', serial):
                        continue
                    if serial in phones:
                        h = {'X-Device-ID': x['device_id'], 'Authorization': 'Bearer ' + x['device_token']}
                        with requests.get(
                            SERVER + '/api/bridge/jobs/00000000-0000-0000-0000-000000000000/media',
                            headers=h,
                            timeout=15,
                        ) as r:
                            if r.status_code in (401, 403):
                                if serial in workers:
                                    workers[serial][1].set()
                                note(
                                    'expired-' + serial,
                                    'Сервер отклонил одно из подключений. В Устройствах скачайте новый файл подключения; код вводить не нужно.',
                                )
                                continue
                            if r.status_code != 409:
                                continue
                    settings[serial] = x
                except Exception:
                    continue
            for serial, x in settings.items():
                if serial in phones:
                    reported_offline.discard(serial)
                    continue
                if serial in reported_offline:
                    continue
                try:
                    h = {'X-Device-ID': x['device_id'], 'Authorization': 'Bearer ' + x['device_token']}
                    with requests.post(
                        SERVER + '/api/bridge/disconnect', headers=h, json={}, timeout=10
                    ) as r:
                        if r.ok:
                            reported_offline.add(serial)
                except requests.RequestException:
                    pass
            for file in downloads.glob('faxclip-connect-*.json'):
                try:
                    if file.stat().st_size > 4096:
                        continue
                    setup = json.loads(file.read_text())
                    if not valid_setup(setup):
                        continue
                    # A previously configured device supplies the exact serial for a renewal.
                    if not setup.get('serial'):
                        for conf in configs.glob('*.json'):
                            saved = json.loads(conf.read_text())
                            if saved.get('device_id') == setup['device_id']:
                                setup['serial'] = saved.get('serial', '')
                                break
                    serial = choose_serial(setup, phones, settings)
                    if not serial:
                        note(
                            'ambiguous-' + file.name,
                            'Файл ожидает телефон: нужен один новый авторизованный USB-телефон или точный USB serial. Между несколькими телефонами менеджер не выбирает случайно.',
                        )
                        continue
                    if serial in workers:
                        thread, stop = workers[serial]
                        stop.set()
                        thread.join(timeout=1)
                        if thread.is_alive():
                            continue
                        del workers[serial]
                    with requests.post(
                        SERVER + '/api/bridge/pair', json={'code': setup['code']}, timeout=30
                    ) as r:
                        if not r.ok:
                            note(
                                'pair-failed-' + file.name,
                                'Файл подключения отклонён или истёк. Скачайте новый файл в Устройствах. Токены не выводятся.',
                            )
                            continue
                        paired = r.json()
                    if (
                        paired.get('device_id') != setup['device_id']
                        or paired.get('mode') != 'ACCESSIBILITY_PUBLISH'
                        or not isinstance(paired.get('device_token'), str)
                    ):
                        continue
                    x = {
                        'server': SERVER,
                        'serial': serial,
                        'device_id': paired['device_id'],
                        'device_token': paired['device_token'],
                    }
                    save_config(configs / (hashlib.sha256(serial.encode()).hexdigest() + '.json'), x)
                    settings[serial] = x
                    file.unlink()  # only this consumed owner-issued setup file, never arbitrary downloads
                    note(
                        'paired-' + x['device_id'],
                        'Устройство подключено. Конфигурация сохранена; одноразовый файл подключения удалён.',
                    )
                except Exception:
                    continue
            for serial, x in settings.items():
                if serial not in phones:
                    continue
                if serial in workers and workers[serial][0].is_alive():
                    continue
                try:
                    adb = ADB(serial, str(adbpath))
                    battery = adb.shell('dumpsys', 'battery')
                    m = re.search(r'level:\s*(\d+)', battery)
                    cap = adb.shell(
                        'am',
                        'broadcast',
                        '-a',
                        'com.faxclip.access.CONTROL',
                        '-p',
                        'com.faxclip.access',
                        '--es',
                        'cmd',
                        'verification_capabilities',
                    )
                    package = adb.shell('dumpsys', 'package', 'com.zhiliaoapp.musically')
                    ready = 'FRESH_CLIP_TIMESTAMP_V1' in cap and bool(
                        re.search(r'versionName=44\.6\.4(?:\s|$)', package)
                    )
                    h = {'X-Device-ID': x['device_id'], 'Authorization': 'Bearer ' + x['device_token']}
                    requests.post(
                        SERVER + '/api/bridge/heartbeat',
                        headers=h,
                        json={
                            'battery': int(m.group(1)) if m else 0,
                            'mode': 'ACCESSIBILITY_PUBLISH' if ready else 'REGISTERED_ONLY',
                            'helper_version': 14 if ready else 0,
                            'app_version': '44.6.4' if ready else '',
                        },
                        timeout=15,
                    )
                    if not ready:
                        note(
                            'not-ready-' + serial,
                            'Устройство зарегистрировано и доступно, но совместимый адаптер публикации не готов. Задания на нём не запускаются.',
                        )
                        continue
                    adb.ready()
                    stop = threading.Event()
                    thread = threading.Thread(target=worker, args=(serial, x, stop), daemon=True)
                    workers[serial] = (thread, stop)
                    thread.start()
                except Exception:
                    continue
            time.sleep(5)
    except KeyboardInterrupt:
        print(
            'Менеджер останавливается: текущие операции должны завершиться безопасно; новые задания не запускаются.',
            flush=True,
        )
        for thread, stop in workers.values():
            stop.set()
        for thread, stop in workers.values():
            thread.join()


if __name__ == '__main__':
    main()
