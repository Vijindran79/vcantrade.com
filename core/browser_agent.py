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
from typing import Optional, Dict, Any
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
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False
        
        logger.info(f"🌐 Browser Agent initialized (headless={headless})")

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
        """Close the browser agent."""
        if self.browser and self.is_running:
            try:
                await self.browser.close()
                await self.playwright.stop()
                self.is_running = False
                logger.info("🛑 Browser agent stopped")
            except Exception as e:
                logger.error(f"Error stopping browser agent: {e}")

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
