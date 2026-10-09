"""Standalone background worker: `python worker.py` (set FAXCLIP_WORKER=off on the web service)."""

import logging

from app import init_db  # noqa: F401  registers every job handler
import jobs

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
    jobs.loop()
