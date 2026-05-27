import asyncio
import json

import services.signal_dispatcher as signal_dispatcher


def test_status_check_exposes_live_timing_fields():
    signal_dispatcher.config.LOCAL_LISTENER_HOST = "127.0.0.1"
    signal_dispatcher.config.LOCAL_LISTENER_PORT = 17199
    signal_dispatcher.config.SWARM_CONFIDENCE_THRESHOLD = 70.0
    signal_dispatcher.config.SIGNAL_API_KEY = ""
    signal_dispatcher.config.PUBLIC_SIGNAL_URL = ""

    dispatcher = signal_dispatcher.SignalDispatcher()

    response = asyncio.run(dispatcher.status_check(None))
    payload = json.loads(response.text)

    assert payload["status"] == "running"
    assert "started_at" in payload
    assert isinstance(payload["uptime_seconds"], int)
    assert payload["uptime_seconds"] >= 0
    assert "seconds_since_last_signal" in payload
    assert "seconds_since_last_handshake" in payload
