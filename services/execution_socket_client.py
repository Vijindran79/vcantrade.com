"""
Raw socket client for Side-by-Side Execution Strategy.

Sends clean JSON packets to port 5555 for trade execution:
- BUY_SIM, SELL_SIM, FLATTEN_SIM
- BUY_REAL, SELL_REAL, FLATTEN_REAL

Confidence Ladder:
- 50% - 84%: Trigger _SIM actions
- 85%+: Trigger _REAL actions
"""

import json
import logging
import socket
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Execution port for raw socket commands.
EXECUTION_PORT = 5555
EXECUTION_HOST = str(getattr(config, "EXECUTION_HOST", "127.0.0.1") or "127.0.0.1").strip()


class ExecutionSocketClient:
    """Raw socket client for sending execution commands to port 5555."""

    def __init__(
        self,
        host: str = EXECUTION_HOST,
        port: int = EXECUTION_PORT,
        reconnect_attempts: int = 15,
        reconnect_delay: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.reconnect_attempts = max(1, int(reconnect_attempts or 15))
        self.reconnect_delay = max(0.5, float(reconnect_delay or 2.0))
        self.last_handshake_ok = False
        self.last_error = ""

    def handshake(self, timeout: float = 1.0) -> bool:
        """Send a lightweight startup ping to confirm the Ghost-Hand bridge is alive."""
        payload = {
            "type": "HANDSHAKE",
            "source": "main.py",
            "packet_id": f"handshake:{int(time.time() * 1000)}",
            "sent_at": time.time(),
        }
        ok, response = self._send_packet(payload, timeout=timeout, expect_success=False)
        status = str(response.get("status", "")).upper() if isinstance(response, dict) else ""
        ack = str(response.get("type", "")).upper() if isinstance(response, dict) else ""
        self.last_handshake_ok = ok and status in {"ACK", "SUCCESS"} and ack == "HANDSHAKE_ACK"
        if self.last_handshake_ok:
            logger.info("[SUCCESS] Ghost-Hand Socket Connection established on port %s!", self.port)
        return self.last_handshake_ok

    def reconnect(self, attempts: Optional[int] = None, timeout: float = 1.0) -> bool:
        """Actively retry the local socket until the execution server acknowledges.

        Retries every 2 seconds for up to 30 seconds (15 attempts) to tolerate
        slow server startup. Logs [RETRY] while waiting.
        """
        max_attempts = max(1, int(attempts or self.reconnect_attempts))
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "[EXEC_SOCKET] Handshake attempt %d/%d to %s:%s",
                attempt,
                max_attempts,
                self.host,
                self.port,
            )
            if self.handshake(timeout=timeout):
                return True
            if attempt < max_attempts:
                logger.info(
                    "[RETRY] Waiting for local execution server to wake up on port %s... "
                    "(attempt %d/%d, retrying in %.1fs)",
                    self.port,
                    attempt,
                    max_attempts,
                    self.reconnect_delay,
                )
                time.sleep(self.reconnect_delay)
        logger.error(
            "[EXEC_SOCKET] Ghost-Hand socket unavailable after %d attempts to %s:%s. Last error: %s",
            max_attempts,
            self.host,
            self.port,
            self.last_error or "no ACK",
        )
        return False

    def _send_packet(self, packet_data: dict, timeout: float, expect_success: bool = True) -> tuple[bool, dict]:
        packet = json.dumps(packet_data)
        logger.info("[EXEC_SOCKET] Sending to %s:%s -> %s", self.host, self.port, packet)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))
                sock.sendall(packet.encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
                response_raw = sock.recv(4096).decode("utf-8", errors="replace")
                logger.info("[EXEC_SOCKET] ACK from %s:%s -> %s", self.host, self.port, response_raw)
                try:
                    response = json.loads(response_raw) if response_raw else {}
                except json.JSONDecodeError:
                    response = {}
                status = str(response.get("status", "")).upper()
                ok = status == "SUCCESS" if expect_success else bool(response)
                self.last_error = "" if ok else str(response or response_raw or "empty response")
                return ok, response
        except ConnectionRefusedError as exc:
            self.last_error = f"connection refused: {exc}"
            logger.warning(
                "[EXEC_SOCKET] Connection refused to %s:%s. Ensure the execution server is running.",
                self.host,
                self.port,
            )
            return False, {}
        except socket.timeout as exc:
            self.last_error = f"timeout: {exc}"
            logger.error("[EXEC_SOCKET] Timeout connecting to %s:%s", self.host, self.port)
            return False, {}
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("[EXEC_SOCKET] Socket packet failed: %s", exc)
            return False, {}

    def send_command(self, action: str, timeout: float = 5.0,
                     entry_price: float = 0.0, stop_loss: float = 0.0,
                     take_profit: float = 0.0,
                     ticker: str = "",
                     quantity: float = 0.0,
                     target: Optional[dict] = None,
                     selectors: Optional[dict] = None,
                     source: str = "main.py") -> bool:
        """
        Send a clean JSON packet over raw socket.

        =====================================================================
        FIX 3: FEED REAL MARKET DATA INTO socket payload
        =====================================================================
        The packet now includes live market prices so the receiving execution
        server can pass them to escalator.trigger_probe() instead of (0,0,0).

        Args:
            action: Command to send (BUY_SIM, SELL_SIM, FLATTEN_SIM, BUY_REAL, etc.)
            timeout: Socket timeout in seconds.
            entry_price: Live market entry price (0.0 = not provided).
            stop_loss: Calculated stop-loss price (0.0 = not provided).
            take_profit: Calculated take-profit price (0.0 = not provided).

        Returns:
            True if command was sent successfully, False otherwise.
        """
        packet_data = {
            "action": action,
            "ticker": ticker,
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "target": target or {},
            "selectors": selectors or {},
            "source": source,
            "packet_id": f"{source}:{action}:{int(time.time() * 1000)}",
            "sent_at": time.time(),
        }
        ok, response = self._send_packet(packet_data, timeout=timeout, expect_success=True)
        if not ok:
            logger.warning("[EXEC_SOCKET] Command %s failed; attempting active reconnect.", action)
            if self.reconnect(timeout=min(timeout, 1.5)):
                ok, response = self._send_packet(packet_data, timeout=timeout, expect_success=True)
        logger.info(
            "[EXEC_SOCKET] Command %s delivery confirmed=%s packet_id=%s response=%s",
            action,
            ok,
            packet_data["packet_id"],
            response,
        )
        return ok

    def send_flatten(self, confidence: float) -> bool:
        """Send FLATTEN command based on confidence level."""
        action = self._get_flatten_action(confidence)
        return self.send_command(action)

    def send_trade_action(self, base_action: str, confidence: float) -> bool:
        """Send BUY/SELL command based on confidence level."""
        action = self._get_trade_action(base_action, confidence)
        return self.send_command(action)

    @staticmethod
    def _get_trade_action(base_action: str, confidence: float) -> str:
        """
        Map base action and confidence to SIM/REAL action.

        Confidence Ladder:
        - 50% - 84%: _SIM actions
        - 85%+: _REAL actions
        """
        suffix = "_REAL" if confidence >= 85.0 else "_SIM"
        return f"{base_action.upper()}{suffix}"

    @staticmethod
    def _get_flatten_action(confidence: float) -> str:
        """Map confidence to FLATTEN_SIM or FLATTEN_REAL."""
        if confidence >= 85.0:
            return "FLATTEN_REAL"
        return "FLATTEN_SIM"


def send_execution_command(action: str, host: str = EXECUTION_HOST, port: int = EXECUTION_PORT) -> bool:
    """Send a single execution command to the socket server."""
    client = ExecutionSocketClient(host, port)
    return client.send_command(action)


def send_trade_with_confidence(
    base_action: str,
    confidence: float,
    host: str = EXECUTION_HOST,
    port: int = EXECUTION_PORT,
) -> bool:
    """Send a trade action with confidence-based SIM/REAL mapping."""
    client = ExecutionSocketClient(host, port)
    return client.send_trade_action(base_action, confidence)
