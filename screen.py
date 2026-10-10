"""Remote phone screen over the Mac USB bridge. State is in memory (single gunicorn worker);
frames are ephemeral by design. Owner endpoints are Telegram-authenticated by the global guard,
bridge endpoints by bridge_guard (X-Device-ID + Bearer)."""

import threading, time, uuid
from flask import request, jsonify, g, Response

ACTIONS = {"tap", "swipe", "text", "key", "wake", "elements", "tap_index", "refresh"}
KEYS = {"back", "home", "recents", "enter", "delete"}
INPUT_ACTIONS = {"tap", "swipe", "text", "key", "tap_index", "elements"}
_lock = threading.Lock()
_state = {}  # device_id -> dict


def _st(did):
    return _state.setdefault(
        did,
        {
            "viewer_until": 0,
            "agent_seen": 0,
            "frame": None,
            "frame_seq": 0,
            "frame_at": 0,
            "w": 0,
            "h": 0,
            "queue": [],
            "results": {},
            "elements": [],
            "elements_at": 0,
        },
    )


def register_screen(app, conn, now):
    def busy(did):
        with conn() as c:
            return bool(
                c.execute(
                    "select 1 from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING')", (did,)
                ).fetchone()
            )

    def device_ok(did):
        with conn() as c:
            return bool(
                c.execute("select 1 from devices where id=? and status!='REVOKED'", (did,)).fetchone()
            )

    # ----- owner -----
    @app.post('/api/devices/<did>/screen/open')
    def screen_open(did):
        if not device_ok(did):
            return jsonify(error='Устройство не найдено'), 404
        with _lock:
            s = _st(did)
            s["viewer_until"] = time.time() + 25
        return jsonify(ok=True)

    @app.post('/api/devices/<did>/screen/close')
    def screen_close(did):
        with _lock:
            s = _st(did)
            s["viewer_until"] = 0
        return jsonify(ok=True)

    @app.get('/api/devices/<did>/screen')
    def screen_state(did):
        with _lock:
            s = _st(did)
            t = time.time()
            results = sorted(s["results"].values(), key=lambda r: r["at"])[-5:]
            out = dict(
                agent_online=t - s["agent_seen"] < 15,
                frame_seq=s["frame_seq"],
                frame_age=round(t - s["frame_at"], 1) if s["frame_at"] else None,
                width=s["w"],
                height=s["h"],
                pending=len(s["queue"]),
                results=results,
                elements=s["elements"],
                elements_age=round(t - s["elements_at"], 1) if s["elements_at"] else None,
            )
        out["busy"] = busy(did)
        return jsonify(out)

    @app.get('/api/devices/<did>/screen.jpg')
    def screen_frame(did):
        with _lock:
            frame = _st(did)["frame"]
        if not frame:
            return jsonify(error='Кадра ещё нет'), 404
        return Response(frame, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.post('/api/devices/<did>/screen/command')
    def screen_command(did):
        if not device_ok(did):
            return jsonify(error='Устройство не найдено'), 404
        x = request.get_json(silent=True) or {}
        a = x.get('action')
        if a not in ACTIONS:
            return jsonify(error='Неизвестное действие'), 400
        cmd = {"id": str(uuid.uuid4()), "action": a}
        try:
            if a == "tap":
                cmd.update(x=float(x["x"]), y=float(x["y"]))
            if a == "swipe":
                cmd.update(
                    x1=float(x["x1"]),
                    y1=float(x["y1"]),
                    x2=float(x["x2"]),
                    y2=float(x["y2"]),
                    ms=int(max(80, min(3000, int(x.get("ms", 300))))),
                )
            if a == "tap_index":
                cmd.update(index=int(x["index"]))
        except (KeyError, TypeError, ValueError):
            return jsonify(error='Неверные координаты'), 400
        for k in ("x", "y", "x1", "y1", "x2", "y2"):
            if k in cmd and not 0 <= cmd[k] <= 1:
                return jsonify(error='Координаты вне экрана'), 400
        if a == "tap_index" and cmd["index"] < 1:
            return jsonify(error='Номер элемента должен быть от 1'), 400
        if a == "key":
            if x.get("key") not in KEYS:
                return jsonify(error='Неизвестная кнопка'), 400
            cmd["key"] = x["key"]
        if a == "text":
            t = str(x.get("text", ""))
            if not t or len(t) > 500:
                return jsonify(error='Введите текст (до 500 символов)'), 400
            cmd["text"] = t
        if a in INPUT_ACTIONS and busy(did):
            return jsonify(
                error='Сейчас телефон публикует видео. Смотреть можно, нажимать нельзя — дождитесь окончания.'
            ), 409
        with _lock:
            s = _st(did)
            if len(s["queue"]) >= 20:
                return jsonify(error='Слишком много команд подряд, подождите'), 429
            s["queue"].append(cmd)
            s["viewer_until"] = max(s["viewer_until"], time.time() + 25)
        return jsonify(ok=True, id=cmd["id"])

    # ----- installer for the Mac screen agent (public software only, no tokens) -----
    import hashlib, os, shlex
    from flask import send_from_directory

    @app.get('/screen-install.py')
    def screen_installer():
        return send_from_directory(
            os.path.join(app.root_path, 'bridge'), 'screen_install.py', mimetype='text/plain'
        )

    @app.get('/api/screen-install-command')
    def screen_install_command():
        with open(os.path.join(app.root_path, 'bridge', 'screen_install.py'), 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        boot = (
            "import requests,hashlib; r=requests.get('https://verticalos-rxdl.onrender.com/screen-install.py',timeout=60); r.raise_for_status(); s=r.content; hashlib.sha256(s).hexdigest()=="
            + repr(sha)
            + " or __import__('sys').exit('Checksum mismatch'); exec(compile(s,'screen_install.py','exec'))"
        )
        return jsonify(
            command='"$HOME/Downloads/faxclip-telegram-bridge-v14/.venv/bin/python" -c ' + shlex.quote(boot),
            sha256=sha,
        )

    # ----- bridge (Mac screen agent) -----
    @app.get('/api/bridge/screen/poll')
    def bridge_screen_poll():
        did = g.bridge_device['id']
        with _lock:
            s = _st(did)
            s["agent_seen"] = time.time()
            cmds, s["queue"] = s["queue"], []
            active = time.time() < s["viewer_until"]
        return jsonify(active=active, commands=cmds)

    @app.post('/api/bridge/screen/frame')
    def bridge_screen_frame():
        did = g.bridge_device['id']
        body = request.get_data(cache=False)
        if not body.startswith(b'\xff\xd8') or len(body) > 1500 * 1024:
            return jsonify(error='JPEG up to 1.5 MB required'), 400
        try:
            w = int(request.headers.get('X-Screen-Width', '0'))
            h = int(request.headers.get('X-Screen-Height', '0'))
        except ValueError:
            w = h = 0
        with _lock:
            s = _st(did)
            s.update(frame=body, frame_at=time.time(), w=w, h=h, agent_seen=time.time())
            s["frame_seq"] += 1
        return jsonify(ok=True)

    @app.post('/api/bridge/screen/result')
    def bridge_screen_result():
        did = g.bridge_device['id']
        x = request.get_json(silent=True) or {}
        cid = str(x.get('id', ''))[:40]
        with _lock:
            s = _st(did)
            s["results"][cid] = {
                "id": cid,
                "ok": bool(x.get("ok")),
                "message": str(x.get("message", ""))[:300],
                "action": str(x.get("action", ""))[:20],
                "at": time.time(),
            }
            if len(s["results"]) > 30:
                for k in sorted(s["results"], key=lambda k: s["results"][k]["at"])[:-30]:
                    s["results"].pop(k, None)
            if isinstance(x.get("elements"), list):
                els = []
                for i, e in enumerate(x["elements"][:150], 1):
                    if isinstance(e, dict):
                        els.append(
                            {
                                "index": i,
                                "text": str(e.get("text", ""))[:80],
                                "desc": str(e.get("desc", ""))[:80],
                                "cls": str(e.get("cls", ""))[:40],
                                "clickable": bool(e.get("clickable")),
                            }
                        )
                s["elements"] = els
                s["elements_at"] = time.time()
        return jsonify(ok=True)
