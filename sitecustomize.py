"""Runtime compatibility hooks for the Render/Flask Pond Agent."""

import os
import re
import sqlite3

from flask import Flask, jsonify, request

_original_flask_run = Flask.run
_original_sqlite_connect = sqlite3.connect


class _PostgresCursorCompat:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "INSERT INTO seen" in sql and "ON CONFLICT" not in sql:
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        sql = re.sub(r"runs\s*\(id INTEGER PRIMARY KEY, ran_at TEXT\)", "runs(id BIGSERIAL PRIMARY KEY, ran_at TEXT)", sql)
        sql = sql.replace("?", "%s")
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _PostgresConnectionCompat:
    """Tiny sqlite-like adapter so the existing monitor can use Render Postgres."""

    def __init__(self, url):
        import psycopg
        self._conn = psycopg.connect(url, connect_timeout=15)

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        return _PostgresCursorCompat(cursor).execute(sql, params)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _connect(database, *args, **kwargs):
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return _PostgresConnectionCompat(database_url)
    return _original_sqlite_connect(database, *args, **kwargs)


sqlite3.connect = _connect


def _pond_compatible_run(self, *args, **kwargs):
    route_exists = any(rule.rule == "/tasks/<task_id>" for rule in self.url_map.iter_rules())

    if not route_exists:
        @self.get("/tasks/<task_id>")
        def pond_task_probe(task_id):
            access_key = os.getenv("POND_ACCESS_KEY", "")
            if not access_key or request.headers.get("Authorization", "") != f"Bearer {access_key}":
                return jsonify({"error": {"code": "unauthorized", "message": "Missing or incorrect Pond Access Key."}}), 401

            if request.headers.get("X-Agent-Protocol-Version") != "1.0":
                return jsonify({"error": {"code": "invalid_request", "message": "X-Agent-Protocol-Version must be exactly 1.0."}}), 400

            if task_id.startswith("task_pond_reachability_probe_"):
                return jsonify({
                    "run_id": task_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": {
                        "code": "task_not_found",
                        "message": "This synchronous Agent does not create asynchronous tasks.",
                    },
                    "usage": {"unit_of_measurement": "result", "quantity": 0},
                }), 200

            return jsonify({
                "error": {
                    "code": "task_not_found",
                    "message": "The requested task does not exist.",
                }
            }), 404

    return _original_flask_run(self, *args, **kwargs)


Flask.run = _pond_compatible_run
