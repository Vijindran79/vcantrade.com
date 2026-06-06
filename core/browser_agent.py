"""
VcaniTrade AI - Autonomous Browser Agent (Playwright)

The bot's "eyes and hands" - opens browser, checks prices, 
and executes autonomous agentic work while Qwen analyzes.

Features:
- Opens WealthCharts/other sites to verify prices
- Scrapes real-time market data
- Takes screenshots for vision analysis
- Works autonomously in background
- Full async support
"""

import asyncio
import logging
import base64
import threading
import time
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

import config

logger = logging.getLogger(__name__)


class BrowserAgent:
    """
    Autonomous browser agent with persistent event loop and background thread.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False
        self.lock = threading.RLock()
        self.pause_cdp_listener = False
        self.target_locked = False
        self.target_locked_ticker: Optional[str] = None

        # Threading/Asyncio state
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._stop_event = threading.Event()

        # STAGE 4: Self-Healing Logic
        self.error_count = 0
        self.error_threshold = 3
        self.last_error: Optional[str] = None
        self.restart_count = 0
        self.max_restarts = 5
        self._dialog_handler_page: Optional[Page] = None
        self._navigating: bool = False
        self._last_cache_clear_date: Optional[str] = None
        self._failed_symbols: Dict[str, float] = {}
        self._on_page_switched = None

        logger.info(f"[GLOBE] Browser Agent initialized (headless={headless})")

    def run_in_loop(self, coro):
        """Run a coroutine in the agent's persistent background loop and return the result."""
        if not self.loop or not self.loop.is_running():
            logger.error("[GLOBE] Cannot run coroutine: Loop is not active")
            return None
        
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"[GLOBE] Coroutine execution failed: {e}")
            return None

    def start_background(self):
        """Start the browser agent in a persistent background thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("[GLOBE] Background thread already running")
            return True

        self._stop_event.clear()
        self.thread = threading.Thread(target=self._thread_entry, daemon=True, name="BrowserAgentLoop")
        self.thread.start()
        
        # Wait for loop to be ready
        if not self._loop_ready.wait(timeout=10):
            logger.error("[GLOBE] Background loop failed to initialize in time")
            return False
        
        # Now connect to the browser
        logger.info("[GLOBE] Background loop ready - connecting to browser...")
        success = self.run_in_loop(self.start())
        return success

    def _thread_entry(self):
        """Entry point for the background thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        
        try:
            # Keep loop alive until stop event is set
            self.loop.run_until_complete(self._loop_heartbeat())
        except Exception as e:
            logger.error(f"[GLOBE] Background loop crash: {e}")
        finally:
            self.is_running = False
            if self.loop and self.loop.is_running():
                self.loop.close()
            logger.info("[GLOBE] Background loop closed")

    async def _loop_heartbeat(self):
        """Simple periodic task to keep the loop active."""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

    def stop_background(self):
        """Stop the background thread and close the browser."""
        self._stop_event.set()
        if self.loop:
            self.run_in_loop(self.close())
        if self.thread:
            self.thread.join(timeout=5)

    async def close(self):
        """Close browser and clean up resources."""
        try:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
            self.is_running = False
            logger.info("[GLOBE] Browser Agent closed")
        except Exception as e:
            logger.error(f"[GLOBE] Close error: {e}")

    @staticmethod
    def _is_passive_observer_mode() -> bool:
        surface = str(getattr(config, "ACTIVE_EXECUTION_SURFACE", "") or "").upper().strip()
        return surface != "TRADINGVIEW"

    def _observer_log(self, symbol: str = "") -> None:
        label = str(symbol or "active tab").strip()
        logger.info("[OBSERVER] Monitoring active tab natively for symbol: %s", label)

    async def get_current_url(self) -> str:
        if not self.is_running or not self.page:
            return ""
        try:
            return self.page.url
        except Exception as e:
            logger.error(f"Failed to get current URL: {e}")
            return ""

    async def start(self):
        """Connect to existing Chrome via CDP."""
        if self.is_running: return True

        MAX_CDP_RETRIES = 3
        CDP_RETRY_DELAY = 2

        for attempt in range(1, MAX_CDP_RETRIES + 1):
            try:
                # Use simple CDP browser (bypasses Playwright timeout issues)
                from core.simple_cdp_browser import SimpleCDPBrowser
                
                cdp_url = getattr(config, "BROWSER_CDP_URL", "http://127.0.0.1:9222")
                logger.info("[CDP] Connecting via simple CDP to %s", cdp_url)
                
                self.simple_browser = SimpleCDPBrowser(cdp_url)
                if await self.simple_browser.connect():
                    self.is_running = True
                    logger.info("[OK] Connected to Chrome tab: %s", self.simple_browser.tab_url[:80])
                    
                    # DON'T call connect_over_cdp() - it times out!
                    # Instead, start Playwright but DON'T connect to browser
                    # We'll use simple_browser for all CDP communication
                    self.playwright = await async_playwright().start()
                    
                    # Create a minimal dummy browser for compatibility
                    # (some code may check if self.browser exists)
                    self.browser = None
                    self.context = None
                    
                    # Store the tab URL so RPA executor can use it
                    self.page = None  # We don't have a Playwright page
                    self._tab_url = self.simple_browser.tab_url
                    
                    logger.info("[CDP] Simple CDP connection successful - skipping Playwright CDP")
                    logger.info("[CDP] All CDP communication will use simple_cdp_browser")
                    
                    return True
                else:
                    raise RuntimeError("Simple CDP connection failed")

            except Exception as e:
                err_text = str(e).lower()
                if ("econnrefused" in err_text or "10061" in err_text) and attempt < MAX_CDP_RETRIES:
                    await asyncio.sleep(CDP_RETRY_DELAY)
                    continue
                logger.error("[CDP] Failed to connect: %s", e)
                if attempt >= MAX_CDP_RETRIES: return False
        return False
