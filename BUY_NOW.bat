@echo off
python -c "import socket, json; s=socket.socket(); s.connect(('127.0.0.1', 5555)); s.send(json.dumps({'action': 'BUY_REAL'}).encode()); print('BUY_REAL sent'); s.close()"
pause
