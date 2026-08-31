"""Small Pond compatibility hook for the Render/Flask process.

Pond's publisher currently probes GET /tasks/{task_id} while validating the
Access Key even when the manifest advertises synchronous execution. The
production Agent remains synchronous; this hook only exposes a safe polling
endpoint for Pond's reachability probe.
"""

import os

from flask import Flask, jsonify, request

_original_flask_run = Flask.run


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

            # Pond uses this synthetic ID to check that the endpoint is reachable.
            # The Agent itself never creates async tasks, so there is no real task
            # to return for the probe. Return a valid terminal failure rather than
            # a framework-level 404 so Pond can complete its endpoint validation.
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
