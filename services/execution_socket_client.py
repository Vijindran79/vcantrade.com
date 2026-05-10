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

import config

logger = logging.getLogger(__name__)

# Execution port for raw socket commands.
EXECUTION_PORT = 5555
EXECUTION_HOST = str(getattr(config, "EXECUTION_HOST", "127.0.0.1") or "127.0.0.1").strip()


class ExecutionSocketClient:
    """Raw socket client for sending execution commands to port 5555."""

    def __init__(self, host: str = EXECUTION_HOST, port: int = EXECUTION_PORT):
        self.host = host
        self.port = port

    def send_command(self, action: str, timeout: float = 5.0) -> bool:
        """
        Send a clean JSON packet {"action": "COMMAND_NAME"} over raw socket.

        Args:
            action: Command to send (BUY_SIM, SELL_SIM, FLATTEN_SIM, BUY_REAL, etc.)
            timeout: Socket timeout in seconds.

        Returns:
            True if command was sent successfully, False otherwise.
        """
        packet = json.dumps({"action": action})
        logger.info("[EXEC_SOCKET] Sending to %s:%s -> %s", self.host, self.port, packet)

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))
                sock.sendall(packet.encode("utf-8"))
                logger.info("[EXEC_SOCKET] Successfully sent command: %s", action)
                return True
        except ConnectionRefusedError:
            logger.warning(
                "[EXEC_SOCKET] Connection refused to %s:%s. Ensure the execution server is running.",
                self.host,
                self.port,
            )
            return False
        except socket.timeout:
            logger.error("[EXEC_SOCKET] Timeout connecting to %s:%s", self.host, self.port)
            return False
        except Exception as exc:
            logger.error("[EXEC_SOCKET] Failed to send command %s: %s", action, exc)
            return False

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
