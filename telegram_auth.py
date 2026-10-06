import hashlib, hmac, json, os, time
from urllib.parse import parse_qsl

class TelegramAuthError(ValueError): pass

def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400):
    if not init_data or not bot_token:
        raise TelegramAuthError("Telegram authorization is required")
    values=dict(parse_qsl(init_data,keep_blank_values=True))
    received=values.pop("hash",None)
    if not received: raise TelegramAuthError("Telegram hash is missing")
    check="\n".join(f"{k}={values[k]}" for k in sorted(values))
    secret=hmac.new(b"WebAppData",bot_token.encode(),hashlib.sha256).digest()
    calculated=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated,received):
        raise TelegramAuthError("Invalid Telegram signature")
    auth_date=int(values.get("auth_date",0))
    if not auth_date or abs(int(time.time())-auth_date)>max_age:
        raise TelegramAuthError("Telegram authorization expired")
    try: user=json.loads(values.get("user","{}"))
    except json.JSONDecodeError as exc: raise TelegramAuthError("Invalid Telegram user") from exc
    if not user.get("id"): raise TelegramAuthError("Telegram user is missing")
    return user

def allowed_user(user):
    raw=os.getenv("TELEGRAM_ALLOWED_USER_IDS","").strip()
    if not raw:return True
    allowed={int(x.strip()) for x in raw.split(",") if x.strip()}
    return int(user["id"]) in allowed
