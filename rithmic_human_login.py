"""
Rithmic Trader Pro Human-Like Auto Login Script
Target: Windows Desktop (Lion Hand)
Requirements: pip install pywinauto
"""

import subprocess
import time
import random
from pywinauto import Application

# ==================== CONFIGURATION ====================
RTRADER_PATH = r"C:\Program Files\Rithmic\R | Trader Pro\RTraderPro.exe"
USERNAME = "APEX-314327"
PASSWORD = "A#@gYtd@s#0N"
SYSTEM = "APEX"
GATEWAY = "Chicago Area"
PLUGIN_CHECKBOX = "Allow Plugins"
LOGIN_TIMEOUT = 30
# ======================================================

def human_sleep(min_s=0.3, max_s=1.2):
    time.sleep(random.uniform(min_s, max_s))

def launch_rtrader():
    print("[*] Launching Rithmic Trader Pro...")
    try:
        subprocess.Popen(RTRADER_PATH)
        human_sleep(3, 5)
        print("[+] Rithmic process started")
    except Exception as e:
        print(f"[!] Launch failed: {e}")
        exit(1)

def connect_app():
    print("[*] Connecting to Rithmic process...")
    try:
        app = Application(backend="win32").connect(path="RTraderPro.exe", timeout=LOGIN_TIMEOUT)
        print("[+] Connected to Rithmic")
        return app
    except Exception as e:
        print(f"[!] Connection error: {e}")
        exit(1)

def perform_login(app):
    print("[*] Waiting for login window...")
    try:
        dlg = app.window(title_re=r".*R \| Trader Pro.*Login.*")
        dlg.wait("exists ready", timeout=LOGIN_TIMEOUT)
        human_sleep()

        # Uncomment for first-run control inspection:
        # dlg.print_control_identifiers()

        # Username (1st Edit control)
        print("[*] Entering username...")
        user_field = dlg.child_window(class_name="Edit", found_index=0)
        user_field.click()
        human_sleep()
        user_field.type_keys(USERNAME, with_spaces=True, pause=0.08)
        human_sleep()

        # Password (2nd Edit control)
        print("[*] Entering password...")
        pass_field = dlg.child_window(class_name="Edit", found_index=1)
        pass_field.click()
        human_sleep()
        pass_field.type_keys(PASSWORD, with_spaces=True, pause=0.08)
        human_sleep()

        # System (1st ComboBox)
        print(f"[*] Selecting system: {SYSTEM}")
        system_combo = dlg.child_window(class_name="ComboBox", found_index=0)
        system_combo.select(SYSTEM)
        human_sleep()

        # Gateway (2nd ComboBox)
        print(f"[*] Selecting gateway: {GATEWAY}")
        gw_combo = dlg.child_window(class_name="ComboBox", found_index=1)
        gw_combo.select(GATEWAY)
        human_sleep()

        # Login button
        print("[*] Clicking Login...")
        login_btn = dlg.child_window(title="Login", class_name="Button")
        login_btn.click()
        print("[+] Login submitted")

        # Wait for auth completion
        time.sleep(15)
        return True
    except Exception as e:
        print(f"[!] Login failed: {e}")
        return False

def enable_plugins(app):
    print("[*] Enabling plugin mode...")
    try:
        main_win = app.window(title_re=r".*R \| Trader Pro.*")
        main_win.wait("exists ready", timeout=LOGIN_TIMEOUT)
        human_sleep(1, 2)

        # Navigate to API settings
        print("[*] Opening API settings...")
        main_win.menu_select("File->Settings->API")
        human_sleep(1, 2)

        api_dlg = app.window(title_re=r".*API Settings.*")
        api_dlg.wait("exists ready", timeout=15)

        # Check Allow Plugins
        plugin_cb = api_dlg.child_window(title=PLUGIN_CHECKBOX, class_name="Button")
        if plugin_cb.get_check_state() != 1:
            plugin_cb.click()
            print("[+] Allow Plugins enabled")
        else:
            print("[+] Plugins already enabled")

        api_dlg.close()
        human_sleep()
        return True
    except Exception as e:
        print(f"[!] Plugin setup failed: {e}")
        return False

if __name__ == "__main__":
    print("=== RITHMIC HUMAN AUTO-LOGIN ===")
    launch_rtrader()
    app = connect_app()
    if perform_login(app) and enable_plugins(app):
        print("=== DEPLOYMENT COMPLETE: RITHMIC LOGGED IN, PLUGINS ENABLED ===")
    else:
        print("=== DEPLOYMENT FAILED ===")
        exit(1)
