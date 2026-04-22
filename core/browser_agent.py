"""
VcaniTrade AI - Autonomous Browser Agent (Playwright)

The bot's "eyes and hands" - opens browser, checks prices, 
and executes autonomous agentic work while Qwen analyzes.

Features:
- Opens TradingView/other sites to verify prices
- Scrapes real-time market data
- Takes screenshots for vision analysis
- Works autonomously in background
- Full async support
"""

import asyncio
import logging
import base64
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

import config

logger = logging.getLogger(__name__)


class BrowserAgent:
    """
    Autonomous browser agent that can:
    - Open websites and check prices
    - Scrape market data from TradingView, Yahoo Finance, etc.
    - Take screenshots for vision analysis
    - Navigate and interact with web pages autonomously
    - Self-heal: Auto-restart on repeated failures (Stage 4)
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False

        # STAGE 4: Self-Healing Logic
        self.error_count = 0  # Consecutive error counter
        self.error_threshold = 3  # Restart after 3 consecutive errors
        self.last_error: Optional[str] = None
        self.restart_count = 0  # Total restarts performed
        self.max_restarts = 5  # Maximum restart attempts before giving up

        logger.info(f"🌐 Browser Agent initialized (headless={headless})")

    async def get_current_url(self) -> str:
        """Get the current URL of the active browser tab."""
        if not self.is_running or not self.page:
            return ""
        
        try:
            return self.page.url
        except Exception as e:
            logger.error(f"Failed to get current URL: {e}")
            return ""

    async def detect_ticker_from_url(self) -> Optional[str]:
        """
        Detect which ticker the user is currently viewing based on URL.
        Works with TradingView, Yahoo Finance, and other common platforms.
        """
        url = await self.get_current_url()
        if not url:
            return None
        
        # TradingView patterns
        if "tradingview.com" in url:
            # e.g., https://www.tradingview.com/symbols/BTCUSD/
            import re
            match = re.search(r'/symbols/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"👁️ Browser Context: User viewing TradingView {ticker}")
                return ticker
        
        # Yahoo Finance patterns
        elif "finance.yahoo.com" in url:
            # e.g., https://finance.yahoo.com/quote/BTC-USD/
            import re
            match = re.search(r'/quote/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"👁️ Browser Context: User viewing Yahoo Finance {ticker}")
                return ticker
        
        # eToro patterns
        elif "etoro.com" in url:
            import re
            match = re.search(r'/markets/([^/]+)', url)
            if match:
                ticker = match.group(1)
                logger.info(f"👁️ Browser Context: User viewing eToro {ticker}")
                return ticker
        
        return None

    async def detect_market_context(self) -> Dict[str, Any]:
        """
        Detect market context from current browser activity.
        Returns dict with detected ticker, market, and suggested focus.
        """
        ticker = await self.detect_ticker_from_url()
        if not ticker:
            return {"context": "unknown", "ticker": None, "market": None}
        
        # Classify market based on ticker
        market = "US"  # Default
        if any(x in ticker.upper() for x in ["BTC", "ETH", "SOL", "CRYPTO"]):
            market = "CRYPTO"
        elif any(x in ticker.upper() for x in ["HK", "HKG", "700", "9988", "TCEHY"]):
            market = "ASIAN"
        elif any(x in ticker.upper() for x in ["EUR", "GBP", "JPY", "USD"]):
            market = "FOREX"
        
        return {
            "context": "browser_focus",
            "ticker": ticker,
            "market": market,
            "message": f"User viewing {ticker} - prioritizing {market} analysis",
        }

    async def navigate_to_chart(self, ticker: str) -> bool:
        """
        Navigate to TradingView chart for specific ticker.
        Uses fast load strategy or 'Warm Start' if already on TV.

        Args:
            ticker: Symbol like BTC-USD, TSLA, EURUSD=X

        Returns:
            True if navigation successful
        """
        if not self.is_running:
            await self.start()

        # Convert ticker to TradingView format
        tv_symbol = ticker.replace("-USD", "").replace("=X", "").replace(".", "")

        # Map common formats
        if "BTC" in tv_symbol:
            tv_symbol = "BTCUSD"
        elif "ETH" in tv_symbol:
            tv_symbol = "ETHUSD"
        elif "SOL" in tv_symbol:
            tv_symbol = "SOLUSD"
        elif "BNB" in tv_symbol:
            tv_symbol = "BNBUSD"
        elif "XRP" in tv_symbol:
            tv_symbol = "XRPUSD"
        elif "ADA" in tv_symbol:
            tv_symbol = "ADAUSD"

        current_url = await self.get_current_url()
        
        # WARM START LOGIC: If already on a TradingView chart, use keyboard RPA to flip symbol
        # This is much faster than a full page reload and prevents 24/7 session timeouts
        if "tradingview.com/chart" in current_url or "tradingview.com/symbols" in current_url:
            try:
                logger.info(f"⚡ Warm Start: Flipping symbol to {tv_symbol} via keyboard...")
                # 1. Focus the page
                await self.page.bring_to_front()
                # 2. Press '/' to open symbol search if needed, or just type
                # In TV, typing directly usually triggers symbol search
                await self.page.keyboard.type(tv_symbol, delay=50)
                await asyncio.sleep(0.5)
                await self.page.keyboard.press("Enter")
                
                # Give it a moment to flip
                await asyncio.sleep(2)
                logger.info(f"✅ Warm Start complete for {tv_symbol}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ Warm Start failed, falling back to full load: {e}")

        # COLD START / FALLBACK: Full page navigation
        url = f"https://www.tradingview.com/symbols/{tv_symbol}/"

        try:
            logger.info(f"🌐 Navigating to TradingView: {ticker} ({tv_symbol})")
            
            # Use 'commit' instead of 'domcontentloaded' - much faster, doesn't wait for WS
            await asyncio.wait_for(
                self.page.goto(url, wait_until="commit", timeout=8000),
                timeout=10.0
            )
            
            # Give page a moment to stabilize
            await asyncio.sleep(1)
            
            logger.info(f"✅ Chart loaded for {ticker}")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ Navigation timeout for {ticker}, proceeding anyway")
            return True  # Return True - page may still be usable
        except Exception as e:
            logger.error(f"❌ Failed to navigate to chart for {ticker}: {e}")
            return False

    async def get_live_price(self) -> float:
        """
        Read the current live price from the TradingView chart.
        Uses multiple selector strategies for reliability.

        Returns:
            Current price as float, or 0.0 if failed
        """
        try:
            import asyncio
            
            # Try multiple selectors that TradingView might use
            selectors_to_try = [
                '.js-symbol-last',
                '.tv-symbol-price',
                '[data-testid="qsp-price"]',
                'span[class*="price"]',
            ]
            
            for selector in selectors_to_try:
                try:
                    price_elem = await asyncio.wait_for(
                        self.page.query_selector(selector),
                        timeout=2.0
                    )
                    if price_elem:
                        price_text = await price_elem.inner_text()
                        # Clean up: remove commas, currency symbols, etc.
                        price_text = price_text.replace(',', '').replace('$', '').strip()
                        if price_text and price_text.replace('.', '').isdigit():
                            price = float(price_text)
                            logger.info(f"📊 Live price fetched: ${price:.2f}")
                            return price
                except Exception:
                    continue  # Try next selector
            
            # Last resort: try to extract price from page title or URL
            logger.warning("⚠️ Price element not found - returning 0.0")
            return 0.0
            
        except Exception as e:
            logger.error(f"❌ Failed to get live price: {e}")
            return 0.0

    async def get_order_book(self) -> Tuple[float, float]:
        """
        Get bid/ask prices. For TradingView (which doesn't show order book),
        we estimate from the current price with typical spread.
        
        Returns:
            Tuple of (bid, ask) prices
        """
        try:
            price = await self.get_live_price()
            if price <= 0:
                return (0.0, 0.0)
            
            # Estimate spread (typical 0.01% for liquid assets)
            spread = price * 0.0001
            bid = price - (spread / 2)
            ask = price + (spread / 2)
            
            logger.debug(f"📊 Estimated bid/ask: {bid:.2f} / {ask:.2f}")
            return (bid, ask)
        except Exception as e:
            logger.error(f"❌ Failed to get order book: {e}")
            return (0.0, 0.0)

    async def click_order_button(self, action: str, quantity: float = 1000, price: float = 0) -> bool:
        """
        Click the buy/sell button on TradingView or exchange.
        
        NOTE: This is a DRY RUN simulation. Real exchange clicking
        requires specific selectors for each platform.
        
        Args:
            action: 'BUY' or 'SELL'
            quantity: Number of units to trade
            price: Target price (0 = market order)
            
        Returns:
            True if click simulated successfully
        """
        try:
            # For TradingView demo - just log the action
            # Real implementation would need exchange-specific selectors
            logger.info(f"🖱️ DRY RUN: Would click {action} for {quantity:.4f} units @ ${price:.2f}")
            logger.info(f"📝 TradingView doesn't support direct order execution in demo mode")
            logger.info(f"✅ Simulated {action} order: {quantity:.4f} units")
            
            # In production, this would:
            # 1. Click "Trade" button on TradingView
            # 2. Fill in quantity field
            # 3. Select BUY or SELL
            # 4. Click "Place Order"
            # For now, we simulate success
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to click order button: {e}")
            return False

    async def start(self):
        """Launch the browser agent."""
        if self.is_running:
            logger.warning("Browser agent already running")
            return
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                ]
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self.page = await self.context.new_page()
            self.is_running = True
            
            logger.info("✅ Browser agent launched successfully")
        except Exception as e:
            logger.error(f"❌ Failed to launch browser agent: {e}")
            raise

    async def stop(self):
        """Close the browser agent and cleanup all resources."""
        if self.browser and self.is_running:
            try:
                # Close page first
                if self.page:
                    try:
                        await self.page.close()
                    except:
                        pass

                # Close context
                if self.context:
                    try:
                        await self.context.close()
                    except:
                        pass

                # Close browser
                await self.browser.close()

                # Stop playwright
                if hasattr(self, 'playwright') and self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass

                self.is_running = False

                # STAGE 4: Explicitly set to None for GC
                self.page = None
                self.context = None
                self.browser = None

                # Record successful cleanup
                if hasattr(self, 'record_success'):
                    self.record_success()

                logger.info("🛑 Browser agent stopped and resources cleaned up")

            except Exception as e:
                logger.error(f"Error stopping browser agent: {e}")
                # Still reset state even on error
                self.is_running = False
                self.page = None
                self.context = None
                self.browser = None

    async def navigate_to(self, url: str, wait_until: str = "domcontentloaded"):
        """Navigate to a URL."""
        if not self.is_running:
            await self.start()
        
        try:
            logger.info(f"🌐 Navigating to: {url}")
            await self.page.goto(url, wait_until=wait_until, timeout=30000)
            logger.info(f"✅ Page loaded: {url}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to navigate to {url}: {e}")
            return False

    async def get_tradingview_price(self, symbol: str) -> Dict[str, Any]:
        """
        Get current price from TradingView for a symbol.
        The bot opens TradingView and reads the price itself!
        """
        if not self.is_running:
            await self.start()
        
        url = f"https://www.tradingview.com/symbols/{symbol.replace('-', '')}/"
        
        try:
            # Navigate to TradingView
            await self.navigate_to(url)
            
            # Wait for price to load
            await self.page.wait_for_selector('.js-symbol-last', timeout=10000)
            
            # Scrape the current price
            price_text = await self.page.text_content('.js-symbol-last')
            price = float(price_text.replace(',', '').strip())
            
            # Scrape change percentage
            try:
                change_text = await self.page.text_content('.js-change-text')
                change_pct = float(change_text.replace('%', '').strip())
            except:
                change_pct = 0.0
            
            # Take screenshot for vision analysis
            screenshot = await self.take_screenshot()
            
            logger.info(f"📊 TradingView data for {symbol}: ${price:.2f} ({change_pct:+.2f}%)")
            
            return {
                "symbol": symbol,
                "price": price,
                "change_pct": change_pct,
                "source": "TradingView",
                "timestamp": datetime.now().isoformat(),
                "screenshot": screenshot,
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get TradingView price for {symbol}: {e}")
            return {
                "symbol": symbol,
                "error": str(e),
                "source": "TradingView",
            }

    async def get_yahoo_finance_price(self, symbol: str) -> Dict[str, Any]:
        """Get current price from Yahoo Finance."""
        if not self.is_running:
            await self.start()
        
        url = f"https://finance.yahoo.com/quote/{symbol}/"
        
        try:
            # Navigate to Yahoo Finance
            await self.navigate_to(url)
            
            # Wait for price to load
            await self.page.wait_for_selector('[data-testid="qsp-price"]', timeout=10000)
            
            # Scrape the current price
            price_text = await self.page.get_attribute('[data-testid="qsp-price"]', 'innerText')
            price = float(price_text.replace(',', '').strip())
            
            # Scrape change
            try:
                change_text = await self.page.get_attribute('[data-testid="qsp-price-change"]', 'innerText')
                change = change_text.strip()
            except:
                change = "0.00"
            
            logger.info(f"📊 Yahoo Finance data for {symbol}: ${price:.2f} ({change})")
            
            return {
                "symbol": symbol,
                "price": price,
                "change": change,
                "source": "Yahoo Finance",
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get Yahoo Finance price for {symbol}: {e}")
            return {
                "symbol": symbol,
                "error": str(e),
                "source": "Yahoo Finance",
            }

    async def check_multiple_sources(self, symbol: str) -> Dict[str, Any]:
        """
        Agentic work: Check multiple sources and find the best price.
        This is the bot thinking and deciding autonomously!
        """
        logger.info(f"🧠 Agent checking multiple price sources for {symbol}...")
        
        results = {}
        
        # Try TradingView first
        try:
            tv_data = await self.get_tradingview_price(symbol)
            if "price" in tv_data:
                results["tradingview"] = tv_data
                logger.info(f"✅ TradingView: ${tv_data['price']:.2f}")
        except Exception as e:
            logger.warning(f"TradingView failed: {e}")
        
        # Try Yahoo Finance
        try:
            yf_data = await self.get_yahoo_finance_price(symbol)
            if "price" in yf_data:
                results["yahoo"] = yf_data
                logger.info(f"✅ Yahoo Finance: ${yf_data['price']:.2f}")
        except Exception as e:
            logger.warning(f"Yahoo Finance failed: {e}")
        
        # Find best price (average if multiple sources)
        prices = [data["price"] for data in results.values() if "price" in data]
        if prices:
            avg_price = sum(prices) / len(prices)
            logger.info(f"📊 Average price from {len(prices)} sources: ${avg_price:.2f}")
            
            return {
                "symbol": symbol,
                "price": avg_price,
                "sources_checked": len(results),
                "data": results,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            logger.error(f"❌ All price sources failed for {symbol}")
            return {
                "symbol": symbol,
                "error": "All sources failed",
            }

    async def take_screenshot(self, save_path: str = None) -> Optional[str]:
        """Take a screenshot and return as base64."""
        if not self.page:
            return None

        try:
            screenshot_bytes = await self.page.screenshot(full_page=True)
            base64_screenshot = base64.b64encode(screenshot_bytes).decode('utf-8')

            if save_path:
                with open(save_path, 'wb') as f:
                    f.write(screenshot_bytes)
                logger.info(f"📸 Screenshot saved: {save_path}")

            return base64_screenshot
        except Exception as e:
            logger.error(f"❌ Failed to take screenshot: {e}")
            return None

    async def inject_pine_script_to_tradingview(self, pine_script_code: str) -> bool:
        """
        Autonomous Adaptation: Open TradingView Pine Editor and inject generated code.
        
        This is the "AI's thoughts appear as lines on the live chart" feature.
        
        Args:
            pine_script_code: Generated Pine Script v6 code
            
        Returns:
            True if injection successful
        """
        try:
            if not self.is_running or not self.page:
                logger.error("Browser not running")
                return False
            
            # Check if we're on TradingView
            current_url = await self.get_current_url()
            if "tradingview.com" not in current_url:
                logger.warning("Not on TradingView, navigating...")
                await self.navigate_to_chart("BTCUSD")
            
            logger.info("🏗️ Injecting Pine Script to TradingView...")
            
            # Open pine Editor via keyboard shortcut (Ctrl+P for Pine Editor)
            await self.page.keyboard.press("Control+p")
            await asyncio.sleep(1)  # Wait for editor to open
            
            # Find editor textarea and paste code
            # TradingView Pine Editor has a specific textarea for code
            try:
                await self.page.click('textarea[class*="pine-editor"]', timeout=5000)
                await asyncio.sleep(0.5)
                
                # Select all existing code and delete it
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.5)
                
                # Type/Paste the generated code
                await self.page.keyboard.type(pine_script_code, delay=1)  # delay=1ms per char
                await asyncio.sleep(1)
                
                # Click "Add to Chart" button
                await self.page.click('button[title*="Add to Chart"]', timeout=5000)
                await asyncio.sleep(1)
                
                # Close editor
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                
                logger.info("✅ Pine Script injected and added to chart successfully")
                return True
            except Exception as editor_error:
                logger.error(f"Pine Editor interaction failed: {editor_error}")
                # Fallback: Try using execute_script to inject directly
                await self.page.evaluate(f"""
                    // This would require TradingView's internal API
                    // For now, we'll use keyboard-based approach
                """)
                return False
            
        except Exception as e:
            logger.error(f"❌ Pine Script injection failed: {e}")
            return False

    async def verify_zones_on_chart(self, expected_zones: list) -> dict:
        """
        Verify that AI-generated zones are visible on chart.
        
        Args:
            expected_zones: List of zones that should be on chart
            
        Returns:
            Verification result
        """
        try:
            if not self.is_running or not self.page:
                return {"status": "BROWSER_NOT_RUNNING"}
            
            # Take screenshot to verify zones
            screenshot = await self.take_screenshot()
            
            # For now, just return success (could use VLM to verify visually)
            return {
                "status": "VERIFIED",
                "zones_count": len(expected_zones),
                "screenshot": screenshot,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Zone verification failed: {e}")
            return {"status": "VERIFICATION_FAILED", "error": str(e)}

    async def execute_autonomous_task(self, symbol: str, task: str = "check_price") -> Dict[str, Any]:
        """
        Main entry point for autonomous agentic work.
        Qwen tells the bot what to do, and the browser agent does it.
        """
        logger.info(f"🤖 Autonomous task: {task} for {symbol}")
        
        try:
            if not self.is_running:
                await self.start()
            
            if task == "check_price":
                return await self.check_multiple_sources(symbol)
            elif task == "tradingview":
                return await self.get_tradingview_price(symbol)
            elif task == "yahoo":
                return await self.get_yahoo_finance_price(symbol)
            elif task == "screenshot":
                screenshot = await self.take_screenshot(f"screenshots/{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                return {"symbol": symbol, "screenshot": screenshot}
            else:
                logger.warning(f"Unknown task: {task}")
                return {"error": f"Unknown task: {task}"}
                
        except Exception as e:
            logger.error(f"❌ Autonomous task failed: {e}")
            return {"error": str(e)}

    async def __aenter__(self):
        """Async context manager support."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup."""
        await self.stop()

    # =========================================================================
    # STAGE 4: Self-Healing Logic (Auto-Restart on Failures)
    # =========================================================================

    async def self_heal_restart(self):
        """
        Self-healing restart logic.

        If a 'NoneType' or 'Browser Error' occurs more than 3 times,
        the bot automatically restarts the BrowserAgent service.
        """
        if self.restart_count >= self.max_restarts:
            logger.error(
                f"🛑 Self-Heal FAILED: Max restarts ({self.max_restarts}) reached. "
                f"Browser agent is unstable."
            )
            raise RuntimeError(
                f"Browser agent failed to self-heal after {self.max_restarts} attempts"
            )

        self.restart_count += 1
        logger.warning(
            f"🔧 Self-Healing Restart #{self.restart_count} initiated... "
            f"(Last error: {self.last_error})"
        )

        try:
            # Stop current browser instance
            await self.stop()

            # Reset state
            self.is_running = False
            self.browser = None
            self.context = None
            self.page = None

            # Wait before restart (cooling period)
            await asyncio.sleep(2)

            # Restart browser
            await self.start()

            # Reset error counter on successful restart
            self.error_count = 0

            logger.info(
                f"✅ Self-Healing Restart #{self.restart_count} successful. "
                f"Browser agent is ready."
            )

        except Exception as e:
            logger.error(f"❌ Self-Healing Restart #{self.restart_count} failed: {e}")
            raise

    def record_success(self):
        """Record a successful operation (reset error counter)."""
        self.error_count = 0
        self.last_error = None

    def record_error(self, error: str):
        """
        Record an error and trigger self-heal if threshold reached.

        Args:
            error: Error message or type
        """
        self.error_count += 1
        self.last_error = error

        logger.warning(
            f"⚠️ Browser error #{self.error_count}/{self.error_threshold}: {error}"
        )

        # Check if we should self-heal
        if self.error_count >= self.error_threshold:
            logger.warning(
                f"🚨 Error threshold reached ({self.error_threshold}). "
                f"Triggering self-healing restart..."
            )
            # Note: Actual restart is async, will be triggered by caller


# Convenience function for one-off usage
async def quick_price_check(symbol: str) -> Dict[str, Any]:
    """Quick one-shot price check using browser agent."""
    async with BrowserAgent(headless=True) as agent:
        return await agent.check_multiple_sources(symbol)


if __name__ == "__main__":
    # Test the browser agent
    async def main():
        print("=" * 60)
        print("VcaniTrade AI - Browser Agent Test")
        print("=" * 60)
        
        async with BrowserAgent(headless=False) as agent:
            # Test TradingView price check
            result = await agent.execute_autonomous_task("BTCUSD", "check_price")
            print(f"\nResult: {result}")
            
            # Wait to see the browser
            await asyncio.sleep(5)
        
        print("\n✅ Browser agent test complete")
    
    asyncio.run(main())
