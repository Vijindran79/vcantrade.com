"""
Execution Socket Client - Side-by-Side Execution
Sends trade commands to the remote execution server (R|Trader Pro desktop)
"""
import socket
import json
import logging
import config

logger = logging.getLogger(__name__)

class ExecutionSocketClient:
    """Client to send trade commands to the remote execution server via socket."""
    
    def __init__(self, host=None, port=None):
        self.host = host or config.EXECUTION_HOST
        self.port = port or 5555
        self.timeout = 30  # 30 second timeout
    
    @staticmethod
    def _get_trade_action(action: str, confidence: float) -> str:
        """Map action and confidence to BUY_SIM/BUY_REAL/SELL_SIM/SELL_REAL/FLATTEN"""
        action_upper = action.upper().strip()
        
        # Handle FLATTEN separately
        if "FLATTEN" in action_upper:
            if confidence >= 85.0:
                return "FLATTEN_REAL"
            return "FLATTEN_SIM"
        
        # Handle BUY/SELL
        base_action = "BUY" if "BUY" in action_upper else "SELL"
        
        if confidence >= 85.0:
            return f"{base_action}_REAL"
        return f"{base_action}_SIM"
    
    def send_command(self, command: str) -> bool:
        """
        Send a command to the execution server.
        Returns True if successful, False otherwise.
        """
        try:
            logger.info(f"[SOCKET] Connecting to {self.host}:{self.port} for command: {command}")
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect((self.host, self.port))
                
                # Send command as JSON
                data = json.dumps({"action": command}).encode("utf-8")
                s.send(data)
                
                # Wait for response
                response_data = s.recv(4096).decode("utf-8")
                if response_data:
                    response = json.loads(response_data)
                    status = response.get("status", "ERROR")
                    if status == "SUCCESS":
                        logger.info(f"[SOCKET] Command '{command}' executed successfully on {self.host}:{self.port}")
                        return True
                    else:
                        logger.warning(f"[SOCKET] Command '{command}' failed: {response.get('message', 'Unknown error')}")
                        return False
                else:
                    logger.warning(f"[SOCKET] No response from {self.host}:{self.port} for command: {command}")
                    return False
                    
        except socket.timeout:
            logger.error(f"[SOCKET] Timeout connecting to {self.host}:{self.port}")
            return False
        except ConnectionRefusedError:
            logger.error(f"[SOCKET] Connection refused by {self.host}:{self.port}. Make sure execution_server.py is running!")
            return False
        except Exception as e:
            logger.error(f"[SOCKET] Error sending command to {self.host}:{self.port}: {e}")
            return False
