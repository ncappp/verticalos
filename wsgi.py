from app import app, init_db  # noqa: F401  (gunicorn imports wsgi:app)

init_db()
