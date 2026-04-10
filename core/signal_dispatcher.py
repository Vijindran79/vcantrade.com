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

import logging
import json
from datetime import datetime
from typing import Optional, Callable

from aiohttp import web

import config

logger = logging.getLogger(__name__)


class SignalDispatcher:
    """
    HTTP server that receives trade signals from Cloud Scanner
    and dispatches them to the local trading engine.
    """
    
    def __init__(self):
        self.app = web.Application()
        self.app.router.add_post('/api/signal', self.handle_signal)
        self.app.router.add_get('/api/health', self.health_check)
        self.app.router.add_get('/api/status', self.status_check)
        
        self.latest_signal: Optional[dict] = None
        self.signal_count = 0
        self.last_signal_time: Optional[datetime] = None
        
        # Callback for when signal is received
        self.on_signal_received: Optional[Callable] = None
        
        logger.info(f"Signal Dispatcher initialized on port {config.LOCAL_LISTENER_PORT}")
    
    async def handle_signal(self, request: web.Request) -> web.Response:
        """Handle incoming trade signal from Cloud Scanner."""
        try:
            data = await request.json()
            
            # Validate required fields
            required_fields = ["ticker", "action", "confidence", "reason"]
            missing = [f for f in required_fields if f not in data]
            if missing:
                logger.error(f"Invalid signal data - missing fields: {missing}")
                return web.json_response(
                    {"status": "error", "message": f"Missing fields: {missing}"},
                    status=400
                )
            
            # Validate confidence threshold
            confidence = data.get("confidence", 0.0)
            if confidence < config.SWARM_CONFIDENCE_THRESHOLD:
                logger.warning(
                    f"Signal below confidence threshold: {confidence} < {config.SWARM_CONFIDENCE_THRESHOLD}"
                )
                return web.json_response(
                    {"status": "rejected", "message": "Below confidence threshold"},
                    status=422
                )
            
            # Add metadata
            data["received_at"] = datetime.utcnow().isoformat()
            data["local_status"] = "received"
            
            # Store signal
            self.latest_signal = data
            self.signal_count += 1
            self.last_signal_time = datetime.utcnow()
            
            logger.info(
                f"📡 Signal received: {data['action']} {data['ticker']} "
                f"(confidence: {confidence:.2f})"
            )
            
            # Trigger callback (connected to main app)
            if self.on_signal_received:
                try:
                    self.on_signal_received(data)
                except Exception as e:
                    logger.error(f"Signal callback failed: {e}")
            
            return web.json_response({
                "status": "accepted",
                "message": f"Signal queued for execution: {data['action']} {data['ticker']}",
                "signal_id": self.signal_count
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
    
    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "healthy",
            "service": "Signal Dispatcher",
            "timestamp": datetime.utcnow().isoformat()
        })
    
    async def status_check(self, request: web.Request) -> web.Response:
        """Status check with signal statistics."""
        return web.json_response({
            "status": "running",
            "signal_count": self.signal_count,
            "latest_signal": self.latest_signal,
            "last_signal_time": self.last_signal_time.isoformat() if self.last_signal_time else None,
            "confidence_threshold": config.SWARM_CONFIDENCE_THRESHOLD,
            "listener_port": config.LOCAL_LISTENER_PORT
        })
    
    async def start_server(self):
        """Start the HTTP server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.LOCAL_LISTENER_PORT)
        await site.start()
        
        logger.info(f"Signal Dispatcher listening on port {config.LOCAL_LISTENER_PORT}")
        return runner
    
    def set_signal_callback(self, callback: Callable):
        """Set callback for when signal is received."""
        self.on_signal_received = callback
