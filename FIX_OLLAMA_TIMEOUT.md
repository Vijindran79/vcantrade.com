# 🔥 URGENT: Fix Ollama Connection Timeout

## 📊 Current Status:
- ❌ Port 11434: **TIMEOUT** (firewall blocking)
- ✅ Config updated to use port 11434
- ❌ Ollama not accessible from your laptop

---

## 🛠️ FIX: Run These Commands on Vast.ai Server

### Step 1: SSH into Your Vast.ai Server
From your laptop's PowerShell:
```powershell
ssh root@91.150.160.38
```

### Step 2: Check if Ollama is Running
```bash
ollama list
```

**If it says "Error: could not connect":**
```bash
# Kill any existing Ollama processes
pkill -f ollama

# Start Ollama bound to ALL interfaces (not just localhost)
ollama serve &
```

### Step 3: Verify Ollama is Listening on Port 11434
```bash
netstat -tulpn | grep 11434
```

**Expected output:**
```
tcp6  0  0 :::11434  :::*  LISTEN  12345/ollama
```

**If you see `127.0.0.1:11434` instead of `:::11434`:**
Ollama is only listening on localhost. We need to fix this.

### Step 4: Force Ollama to Listen on All Interfaces
```bash
# Stop current Ollama
pkill -f ollama

# Set environment variable to bind to all interfaces
export OLLAMA_HOST=0.0.0.0:11434

# Restart Ollama
ollama serve &

# Wait 10 seconds
sleep 10

# Verify it's listening on the correct address
netstat -tulpn | grep 11434
```

### Step 5: Test from Vast.ai Server
```bash
curl http://localhost:11434/api/tags
```
This should return a JSON list of models.

### Step 6: Test from Your LAPTOP
Run the diagnostic script:
```powershell
cd C:\Users\vijin\vcantrade.com-2
python test_ollama_connection.py
```

---

## 🔥 Alternative: SSH Port Forwarding (If Firewall Blocked)

If Vast.ai's firewall is blocking port 11434, you can tunnel through SSH:

### On Your Laptop (PowerShell):
```powershell
# Create SSH tunnel for Ollama
ssh -L 11434:localhost:11434 root@91.150.160.38 -N
```

Then update `config.py` to use localhost:
```python
OLLAMA_BASE_URL = "http://localhost:11434"
```

This is **more secure** anyway (no public exposure)!

---

## ✅ Success Criteria:
1. `test_ollama_connection.py` shows port 11434 as **OPEN**
2. `curl http://91.150.160.38:11434/api/tags` returns models from your laptop
3. `main.py` no longer shows "Connection timed out" errors

---

## 🎯 After Fix:
Once connection works:
- Swarm agents will get responses from `openhermes`
- Confidence will jump from **0.45 → 0.80+**
- Trades will execute when confidence > **0.70**
