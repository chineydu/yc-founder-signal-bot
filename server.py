"""Render entrypoint that explicitly loads Pond's compatibility hook.

Render currently starts the service with ``python app.py``. Pond's publisher
probes GET /tasks/{task_id}; the compatibility route lives in sitecustomize.py.
Importing it explicitly here guarantees the route is installed before Flask
starts, even when Python does not auto-load sitecustomize.
"""

import os

import sitecustomize  # noqa: F401,E402  # patches Flask.run before app startup
from app import app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
