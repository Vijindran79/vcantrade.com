# DEPLOYMENT CHECKLIST - Fix "WINDOW HIDDEN" Error

## Pre-Flight Check
1. [ ] R|Trader Pro is running on 192.168.0.39 (trading desktop)
2. [ ] `.env` file exists in `C:\Users\vijin\vcantrade.com\` with:
   ```
   EXECUTION_HOST=192.168.0.39
   OLLAMA_BASE_URL=http://192.168.0.66:11434
   RTRADER_WINDOW_HINTS=R|Trader Pro,R|Trader,RTrader Pro,RTrader
   ```
3. [ ] Dependencies installed on 192.168.0.39:
   ```
   pip install pyautogui pygetwindow numpy
   ```

## Deployment Steps (on 192.168.0.39 - Trading Desktop)

### Step 1: Calibrate Button Coordinates
```powershell
cd C:\Users\vijin\vcantrade.com
python coordinate_calibration.py
```
- Follow prompts to capture: BUY, SELL, FLATTEN buttons + account dropdown
- Save the `config_coordinates.json` file

### Step 2: Test Window Detection
```powershell
cd C:\Users\vijin\vcantrade.com
python -c "import execution_server; print(execution_server.find_rtrader_window())"
```
- Should print R|Trader Pro window object (not None)

### Step 3: Start Execution Server
```powershell
cd C:\Users\vijin\vcantrade.com
python execution_server.py
```
- Should print: "GHOST-HAND SERVER (Master Stealth) - TRADING DESKTOP"
- Should print: "Listening on 0.0.0.0:5555"

## Start Main Bot (on main machine)

### Step 4: Verify Configuration
```powershell
cd C:\Users\vijin\vcantrade.com
python -c "import config; print(f'EXECUTION_HOST: {config.EXECUTION_HOST}')"
```
- Should print: `EXECUTION_HOST: 192.168.0.39`

### Step 5: Start Main Bot
```powershell
cd C:\Users\vijin\vcantrade.com
python main.py
```
- Should print: "Server: 192.168.0.39:5555" (NOT 127.0.0.1:5555!)

## Debugging "WINDOW HIDDEN" Error

If you still get "WINDOW HIDDEN: R|Trader Pro window not found":

1. Run this on 192.168.0.39 to see all window titles:
```powershell
python -c "import pygetwindow as gw; windows = gw.getAllWindows(); titles = [w.title for w in windows if w.title.strip()]; print('\n'.join(titles[:50]))"
```

2. Check if R|Trader Pro window title matches hints:
   - Hints are: `R|Trader Pro`, `R|Trader`, `RTrader Pro`, `RTrader`
   - If your window title is different, update `RTRADER_WINDOW_HINTS` in `.env` file

3. Check if window is minimized:
   - The `find_rtrader_window()` function now restores minimized windows automatically

4. Check multi-monitor setup:
   - Make sure R|Trader Pro is on the primary monitor
   - Or update `PRIMARY_MONITOR_WIDTH` and `PRIMARY_MONITOR_HEIGHT` in `.env`

## Files Modified
- `services/execution_socket_client.py` (NEW - created)
- `execution_server.py` (improved window detection)
- `config.py` (added R|Trader hints + multi-monitor support)
- `coordinate_calibration.py` (added R|Trader support)
- `.env.example` (created)
- `.env` (created - may need to update with your values)

## Verify Fix
After deployment, the log should show:
```
[WINDOW] Found R|Trader window: 'R|Trader Pro - ...'
[WINDOW] Successfully activated R|Trader window: ...
[STRIKE] BUY_SIM execution complete.
```

NOT:
```
[WINDOW HIDDEN] R|Trader Pro window not found - Window not found
```
