@echo off
python -c "import socket, json; s=socket.socket(); s.connect(('127.0.0.1', 5555)); s.send(json.dumps({'action': 'FLATTEN'}).encode()); print('FLATTEN sent'); s.close()"
pause
