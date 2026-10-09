import json, os, sys, urllib.request

token = os.environ.get("TELEGRAM_BOT_TOKEN")
url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MINI_APP_URL", "")).rstrip("/")
if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN is required")
if not url.startswith("https://"):
    raise SystemExit("Pass an HTTPS Mini App URL")
base = f"https://api.telegram.org/bot{token}/"


def call(method, payload):
    req = urllib.request.Request(
        base + method, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        result = json.loads(r.read())
        if not result.get("ok"):
            raise RuntimeError(result)
        return result


call("setMyCommands", {"commands": [{"command": "start", "description": "Открыть VerticalOS"}]})
call(
    "setChatMenuButton",
    {"menu_button": {"type": "web_app", "text": "Открыть VerticalOS", "web_app": {"url": url}}},
)
print("Telegram bot menu configured")
