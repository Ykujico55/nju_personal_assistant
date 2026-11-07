import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_assistant.api.middleware.request_id import RequestIdMiddleware


def test_untrusted_header_is_not_a_diagnostic_identifier():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/check")
    async def check():
        return {"ok": True}

    with TestClient(app) as client:
        value = client.get("/check", headers={"X-Request-ID": "unsafe/private-secret"})
        assert re.fullmatch(r"req_[0-9a-f]{32}", value.headers["X-Request-ID"])
        valid = "req_" + "a" * 32
        assert (
            client.get("/check", headers={"X-Request-ID": valid}).headers["X-Request-ID"] == valid
        )
