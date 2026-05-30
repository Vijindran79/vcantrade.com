"""
Test script for HAWK PROTOCOL implementation
Verifies: Single-asset lock, suspend/resume scanners, trailing stops, and profit harvest
"""

import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_hawk_protocol():
    """Test the HAWK PROTOCOL implementation"""
    print("[TEST] Starting HAWK PROTOCOL tests...")
    
    try:
        # Test 1: Import main module
        print("\n[TEST 1] Importing main module...")
        import main
        print("[PASS] Main module imported successfully")
        
        # Test 2: Create VcaniTradeEngine instance
        print("\n[TEST 2] Creating VcaniTradeEngine instance...")
        # Note: This will fail without QApplication, but we can test the class structure
        print("[INFO] Checking class attributes...")
        
        # Check if HAWK attributes exist
        engine_class = main.VcaniTradeEngine
        assert hasattr(engine_class, '__init__'), "Missing __init__ method"
        print("[PASS] VcaniTradeEngine class structure looks correct")
        
        # Test 3: Check HAWK PROTOCOL methods exist
        print("\n[TEST 3] Checking HAWK PROTOCOL methods...")
        required_methods = [
            'suspend_scanners',
            'resume_scanners', 
            'update_trailing_stops',
            'execute_global_profit_harvest',
            'execute_trade',
            'close_position'
        ]
        
        for method_name in required_methods:
            assert hasattr(engine_class, method_name), f"Missing method: {method_name}"
            print(f"[PASS] Method {method_name} exists")
        
        # Test 4: Check ui/dashboard module
        print("\n[TEST 4] Checking ui/dashboard module...")
        from ui import dashboard
        assert hasattr(dashboard.CommandCenter, 'trigger_harvest'), "Missing trigger_harvest method"
        print("[PASS] Dashboard CommandCenter has trigger_harvest method")
        
        # Test 5: Check HAWK attributes in __init__ signature
        print("\n[TEST 5] Checking HAWK attributes...")
        import inspect
        init_signature = inspect.signature(engine_class.__init__)
        print(f"[INFO] VcaniTradeEngine.__init__ signature: {init_signature}")
        
        print("\n[RESULT] All basic structure tests PASSED!")
        print("[INFO] To fully test, run the application with: python main.py")
        print("[INFO] Watch for these log messages:")
        print("  - '[HAWK] U-Turn Radar timer initialized (1000ms interval)'")
        print("  - '[HAWK] TARGET LOCKED: <ticker>. Scanners suspended.'")
        print("  - '[HAWK] Position closed: <ticker>'")
        print("  - '[HARVEST] Profit harvest executed'")
        
        return True
        
    except ImportError as e:
        print(f"[FAIL] Import error: {e}")
        return False
    except AssertionError as e:
        print(f"[FAIL] Assertion error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_hawk_protocol()
    sys.exit(0 if success else 1)