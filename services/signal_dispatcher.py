"""
VcanTrade AI - Signal Dispatcher
HTTP server that receives trade signals from Cloud Scanner and dispatches to Local Executor.

Architecture:
- Runs on local laptop alongside main.py
- Listens for incoming signals from cloud (port 17199)
- Validates signal data
- Forwards to main application via Qt signals
- Provides health check endpoint
"""

import asyncio
import logging
import json
import time
from datetime import datetime
from typing import Optional, Callable
from secrets import compare_digest

import aiohttp
from aiohttp import web

import config
from services.swarm_incubation import SwarmIncubationTracker

logger = logging.getLogger(__name__)


class SignalDispatcher:
    """
    HTTP server that receives trade signals from Cloud Scanner
    and dispatches them to the local trading engine.
    """
    
    def __init__(self):
        self.app = web.Application()
        self.app.router.add_post('/api/signal', self.handle_signal)
        self.app.router.add_get('/api/handshake', self.handle_handshake)
        self.app.router.add_get('/api/health', self.health_check)
        self.app.router.add_get('/api/status', self.status_check)
        
        self.latest_signal: Optional[dict] = None
        self.signal_count = 0
        self.last_signal_time: Optional[datetime] = None
        self.last_handshake_time: Optional[datetime] = None
        self.incubation = SwarmIncubationTracker()
        
        # Callback for when signal is received
        self.on_signal_received: Optional[Callable] = None
        self.on_handshake_received: Optional[Callable] = None
        
        logger.info(f"Signal Dispatcher initialized on port {config.LOCAL_LISTENER_PORT}")

    def _confidence_score(self, confidence) -> float:
        try:
            value = float(confidence or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))

    def _extract_api_key(self, request: web.Request, data: dict) -> str:
        header_name = config.SIGNAL_API_HEADER
        auth_header = (request.headers.get("Authorization", "") or "").strip()
        bearer_key = ""
        if auth_header.lower().startswith("bearer "):
            bearer_key = auth_header[7:].strip()

        return str(
            request.headers.get(header_name, "")
            or bearer_key
            or data.get("api_key", "")
            or ""
        ).strip()

    def _is_authorized(self, request: web.Request, data: dict) -> bool:
        expected_key = config.SIGNAL_API_KEY
        if not expected_key:
            return True
        provided_key = self._extract_api_key(request, data)
        return bool(provided_key) and compare_digest(provided_key, expected_key)
    
    async def handle_signal(self, request: web.Request) -> web.Response:
        """Handle incoming trade signal from Cloud Scanner."""
        try:
            data = await request.json()

            if not self._is_authorized(request, data):
                logger.warning(
                    "Rejected unauthorized signal from %s",
                    request.remote or "unknown",
                )
                return web.json_response(
                    {"status": "unauthorized", "message": "Invalid API key"},
                    status=401
                )
            
            # Validate required fields
            required_fields = ["ticker", "action", "confidence", "reason"]
            missing = [f for f in required_fields if f not in data]
            if missing:
                logger.error(f"Invalid signal data - missing fields: {missing}")
                return web.json_response(
                    {"status": "error", "message": f"Missing fields: {missing}"},
                    status=400
                )
            
            # Validate confidence threshold.
            confidence = data.get("confidence", 0.0)
            confidence_score = self._confidence_score(confidence)
            incubation_floor = float(getattr(config, "SWARM_INCUBATION_FLOOR", 60.0))
            high_priority_threshold = float(getattr(config, "SWARM_HIGH_PRIORITY_THRESHOLD", 85.0))
            if confidence_score < incubation_floor:
                logger.warning(
                    "Signal below incubation floor: %.1f%% < %.1f%%",
                    confidence_score,
                    incubation_floor,
                )
                return web.json_response(
                    {"status": "rejected", "message": "Below confidence threshold"},
                    status=422
                )
            
            # Add metadata
            data.pop("api_key", None)
            data["received_at"] = datetime.utcnow().isoformat()
            data["local_status"] = "received"
            data["source_ip"] = request.remote or "unknown"
            
            # Store signal
            self.latest_signal = data
            self.signal_count += 1
            self.last_signal_time = datetime.utcnow()

            logger.info(
                f"[SAT] Signal received: {data['action']} {data['ticker']} "
                f"(confidence: {confidence_score:.1f}%, source: {data['source_ip']})"
            )

            route, incubation_meta = self.incubation.process_signal(data, confidence_score)
            data["incubation_route"] = route
            data["incubation_meta"] = incubation_meta

            if route == "incubating":
                logger.info(
                    "[INCUBATION] Muted %s %s at %.1f%%. Paper simulation opened; no UI alert or execution callback.",
                    data["action"],
                    data["ticker"],
                    confidence_score,
                )
                return web.json_response({
                    "status": "incubating",
                    "message": "Signal muted into swarm incubation; no desktop alert or execution triggered.",
                    "signal_id": self.signal_count,
                    "threshold": high_priority_threshold,
                    "incubation": incubation_meta,
                })

            if route == "promoted":
                logger.info(
                    "[INCUBATION] Promoted %s %s after positive local expectancy: %s",
                    data["action"],
                    data["ticker"],
                    incubation_meta.get("expectancy"),
                )

            # Trigger callback (connected to main app)
            if self.on_signal_received:
                try:
                    logger.info(
                        "[DISPATCHER] Forwarding %s %s to main execution callback (route=%s, confidence=%.1f%%)",
                        data["action"],
                        data["ticker"],
                        route,
                        confidence_score,
                    )
                    self.on_signal_received(data)
                except Exception as e:
                    logger.error(f"Signal callback failed: {e}")
            
            return web.json_response({
                "status": "accepted",
                "message": f"Signal queued for execution: {data['action']} {data['ticker']}",
                "signal_id": self.signal_count,
                "incubation_route": route,
            })
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON in signal request")
            return web.json_response(
                {"status": "error", "message": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.error(f"Signal handler error: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )

    async def handle_handshake(self, request: web.Request) -> web.Response:
        """Confirm the external brain can reach the local listener."""
        try:
            request_data = {}
            if not self._is_authorized(request, request_data):
                logger.warning(
                    "Rejected unauthorized handshake from %s",
                    request.remote or "unknown",
                )
                return web.json_response(
                    {"status": "unauthorized", "message": "Invalid API key"},
                    status=401
                )

            metadata = {
                "status": "Lion is Listening",
                "received_at": datetime.utcnow().isoformat(),
                "source_ip": request.remote or "unknown",
                "brain": str(request.query.get("brain", "external")).strip() or "external",
            }
            self.last_handshake_time = datetime.utcnow()

            logger.info(
                "[HANDSHAKE] Handshake accepted from %s (%s)",
                metadata["brain"],
                metadata["source_ip"],
            )

            if self.on_handshake_received:
                try:
                    self.on_handshake_received(metadata)
                except Exception as exc:
                    logger.error(f"Handshake callback failed: {exc}")

            return web.json_response({"status": "Lion is Listening"})
        except Exception as exc:
            logger.error(f"Handshake handler error: {exc}")
            return web.json_response(
                {"status": "error", "message": str(exc)},
                status=500
            )
    
    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "healthy",
            "service": "Signal Dispatcher",
            "timestamp": datetime.utcnow().isoformat(),
            "auth_enabled": bool(config.SIGNAL_API_KEY),
            "public_signal_url": config.PUBLIC_SIGNAL_URL or None,
        })
    
    async def status_check(self, request: web.Request) -> web.Response:
        """Status check with signal statistics."""
        return web.json_response({
            "status": "running",
            "signal_count": self.signal_count,
            "latest_signal": self.latest_signal,
            "last_signal_time": self.last_signal_time.isoformat() if self.last_signal_time else None,
            "last_handshake_time": self.last_handshake_time.isoformat() if self.last_handshake_time else None,
            "confidence_threshold": config.SWARM_CONFIDENCE_THRESHOLD,
            "incubation_floor": getattr(config, "SWARM_INCUBATION_FLOOR", 60.0),
            "high_priority_threshold": getattr(config, "SWARM_HIGH_PRIORITY_THRESHOLD", 85.0),
            "incubation_open_count": len(self.incubation.state.get("open", [])),
            "listener_host": config.LOCAL_LISTENER_HOST,
            "listener_port": config.LOCAL_LISTENER_PORT,
            "auth_enabled": bool(config.SIGNAL_API_KEY),
            "public_signal_url": config.PUBLIC_SIGNAL_URL or None,
        })
    
    async def start_server(self):
        """Start the HTTP server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, config.LOCAL_LISTENER_HOST, config.LOCAL_LISTENER_PORT)
        await site.start()
        
        logger.info(
            "Signal Dispatcher listening on %s:%s | auth=%s | public=%s",
            config.LOCAL_LISTENER_HOST,
            config.LOCAL_LISTENER_PORT,
            "ON" if config.SIGNAL_API_KEY else "OFF",
            config.PUBLIC_SIGNAL_URL or "not-set",
        )
        return runner
    
    def set_signal_callback(self, callback: Callable):
        """Set callback for when signal is received."""
        self.on_signal_received = callback

    def set_handshake_callback(self, callback: Callable):
        """Set callback for when handshake is received."""
        self.on_handshake_received = callback


class AsyncSignalDispatcher:
    """
    Persistent async HTTP client for dispatching trade signals.
    Uses a shared aiohttp.ClientSession with connection pooling to avoid
    blocking the event loop during signal forwarding.
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize_session(self):
        """Instantiates a single persistent, thread-safe TCP connector pool."""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=5.0)
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            logger.info("[BRIDGE] Persistent aiohttp ClientSession initialized securely.")

    async def close_session(self):
        """Gracefully drains network handles before application tear-down."""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def dispatch(
        self,
        signal_payload: dict,
        target_url: str,
        headers: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> tuple[bool, Optional[int], str]:
        """
        Forwards a signal payload to a target URL without blocking execution.

        Returns:
            (success: bool, status_code: int|None, body_or_error: str)
        """
        await self.initialize_session()
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        payload = dict(signal_payload)
        payload["dispatched_at"] = time.time()
        try:
            async with self.session.post(
                target_url,
                json=payload,
                headers=headers or {},
                timeout=client_timeout,
            ) as response:
                status = int(response.status)
                body = await response.text()
                if status == 200:
                    logger.info(
                        "[DISPATCH] Signal acknowledged by execution gateway. URL: %s",
                        target_url,
                    )
                    return True, status, body
                else:
                    logger.error(
                        "[BRIDGE] Target route returned critical error status: %s | %s",
                        status,
                        target_url,
                    )
                    return False, status, body
        except aiohttp.ClientConnectorError as ce:
            logger.error(
                "[BRIDGE] Transport failure routing signal over local loopback: %s | %s",
                ce,
                target_url,
            )
            return False, None, f"connection refused: {ce}"
        except asyncio.TimeoutError:
            logger.error(
                "[BRIDGE] Dispatch timeout after %.1fs | %s",
                timeout,
                target_url,
            )
            return False, None, f"timeout after {timeout:.1f}s"
        except Exception as e:
            logger.exception(
                "[BRIDGE] Unhandled payload translation block inside async pipeline: %s | %s",
                e,
                target_url,
            )
            return False, None, f"{type(e).__name__}: {e}"
