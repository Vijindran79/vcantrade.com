"""
VcanTrade AI - Cloud Market Scanner
Monitors 10 chosen counters using yfinance without a screen.
Detects technical signals (RSI Cross, Volume Spike, SMA Cross) and triggers Swarm Debate.

Architecture:
- Runs on Vast.ai server (headless)
- Uses yfinance for market data
- Calculates technical indicators (RSI, SMA, Volume)
- Triggers Swarm Consensus when signal detected
- Dispatches high-confidence signals (>0.70) to Local Executor
"""

import logging
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests

import config
from core.models import MarketDataPoint, SignalAction, ConfidenceLevel
from core.swarm_consensus import OllamaSwarmConsensus as SwarmConsensus

logger = logging.getLogger(__name__)


class TechnicalSignal:
    """Represents a detected technical signal."""
    
    def __init__(self, ticker: str, signal_type: str, strength: float, metadata: dict = None):
        self.ticker = ticker
        self.signal_type = signal_type  # "VOLUME_SPIKE", "RSI_OVERSOLD", "RSI_OVERBOUGHT", "SMA_CROSS"
        self.strength = strength  # 0.0-1.0
        self.metadata = metadata or {}
        self.timestamp = datetime.utcnow()


class CloudScanner:
    """
    Cloud-based market scanner that monitors 10 tickers using yfinance.
    Detects technical signals and triggers Swarm Debate when conditions met.
    """
    
    def __init__(self):
        self.tickers = config.CLOUD_TICKERS  # Dynamically updated from dashboard
        self.consensus = SwarmConsensus()
        self.recent_signals: Dict[str, datetime] = {}  # Prevent duplicate signals
        self.signal_cooldown = 300  # 5 minutes between signals for same ticker
        
    async def scan_all_tickers(self) -> List[TechnicalSignal]:
        """Scan all tickers and return detected signals."""
        signals = []
        
        for ticker in self.tickers:
            try:
                detected = await self._scan_single_ticker(ticker)
                signals.extend(detected)
            except Exception as e:
                logger.error(f"Failed to scan {ticker}: {e}")
                
        return signals
    
    async def _scan_single_ticker(self, ticker: str) -> List[TechnicalSignal]:
        """Scan a single ticker for technical signals."""
        signals = []
        
        # Fetch 1-minute data for last day
        df = await self._fetch_market_data(ticker)
        if df is None or df.empty:
            return signals
        
        # Calculate technical indicators
        df = self._calculate_indicators(df)
        
        # Detect signals
        volume_spike = self._detect_volume_spike(df, ticker)
        if volume_spike:
            signals.append(volume_spike)
        
        rsi_signal = self._detect_rsi_signal(df, ticker)
        if rsi_signal:
            signals.append(rsi_signal)
        
        sma_signal = self._detect_sma_cross(df, ticker)
        if sma_signal:
            signals.append(sma_signal)
        
        return signals
    
    async def _fetch_market_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """Fetch market data using yfinance."""
        try:
            symbol = yf.Ticker(ticker)
            # Add timeout protection - yfinance can hang on network issues
            import concurrent.futures
            
            def fetch_data():
                return symbol.history(period="1d", interval="1m")
            
            # Run in thread with timeout (15 seconds)
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(fetch_data)
                df = future.result(timeout=15)

            if df.empty:
                logger.warning(f"No data for {ticker}")
                return None

            return df
        except concurrent.futures.TimeoutError:
            logger.error(f"Timeout fetching data for {ticker} - network issue")
            return None
        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators (RSI, SMA, etc.)."""
        # RSI (14-period)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        # SMA (20 and 50 period)
        df['SMA_FAST'] = ta.sma(df['Close'], length=config.SMA_FAST)
        df['SMA_SLOW'] = ta.sma(df['Close'], length=config.SMA_SLOW)
        
        # Volume moving average
        df['VOL_MA'] = ta.sma(df['Volume'], length=20)
        
        return df
    
    def _detect_volume_spike(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect volume spike (>3x average)."""
        if len(df) < 2:
            return None
        
        last_vol = df['Volume'].iloc[-1]
        avg_vol = df['VOL_MA'].iloc[-1]
        
        if pd.isna(avg_vol) or avg_vol == 0:
            return None
        
        volume_ratio = last_vol / avg_vol
        
        if volume_ratio > config.VOLUME_SPIKE_MULTIPLIER:
            strength = min(1.0, volume_ratio / 10.0)  # Normalize to 0-1
            return TechnicalSignal(
                ticker=ticker,
                signal_type="VOLUME_SPIKE",
                strength=strength,
                metadata={
                    "volume_ratio": volume_ratio,
                    "last_volume": last_vol,
                    "avg_volume": avg_vol,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _detect_rsi_signal(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect RSI overbought/oversold conditions."""
        if len(df) < 2:
            return None
        
        current_rsi = df['RSI'].iloc[-1]
        prev_rsi = df['RSI'].iloc[-2]
        
        if pd.isna(current_rsi) or pd.isna(prev_rsi):
            return None
        
        # RSI crosses below oversold (bullish)
        if prev_rsi >= config.RSI_OVERSOLD and current_rsi < config.RSI_OVERSOLD:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERSOLD",
                strength=0.75,
                metadata={
                    "rsi": current_rsi,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        # RSI crosses above overbought (bearish)
        if prev_rsi <= config.RSI_OVERBOUGHT and current_rsi > config.RSI_OVERBOUGHT:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="RSI_OVERBOUGHT",
                strength=0.75,
                metadata={
                    "rsi": current_rsi,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _detect_sma_cross(self, df: pd.DataFrame, ticker: str) -> Optional[TechnicalSignal]:
        """Detect SMA fast/slow cross (Golden Cross / Death Cross)."""
        if len(df) < 2:
            return None
        
        current_fast = df['SMA_FAST'].iloc[-1]
        current_slow = df['SMA_SLOW'].iloc[-1]
        prev_fast = df['SMA_FAST'].iloc[-2]
        prev_slow = df['SMA_SLOW'].iloc[-2]
        
        if any(pd.isna([current_fast, current_slow, prev_fast, prev_slow])):
            return None
        
        # Golden Cross (bullish)
        if prev_fast <= prev_slow and current_fast > current_slow:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="SMA_CROSS_BULLISH",
                strength=0.80,
                metadata={
                    "sma_fast": current_fast,
                    "sma_slow": current_slow,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        # Death Cross (bearish)
        if prev_fast >= prev_slow and current_fast < current_slow:
            return TechnicalSignal(
                ticker=ticker,
                signal_type="SMA_CROSS_BEARISH",
                strength=0.80,
                metadata={
                    "sma_fast": current_fast,
                    "sma_slow": current_slow,
                    "price": df['Close'].iloc[-1]
                }
            )
        
        return None
    
    def _is_signal_cooldown(self, ticker: str, signal_type: str) -> bool:
        """Check if signal is within cooldown period."""
        key = f"{ticker}:{signal_type}"
        if key in self.recent_signals:
            elapsed = (datetime.utcnow() - self.recent_signals[key]).total_seconds()
            return elapsed < self.signal_cooldown
        return False
    
    def _record_signal(self, ticker: str, signal_type: str):
        """Record signal timestamp for cooldown tracking."""
        key = f"{ticker}:{signal_type}"
        self.recent_signals[key] = datetime.utcnow()
    
    async def process_signals(self, signals: List[TechnicalSignal]) -> Optional[dict]:
        """
        Process detected signals through Swarm Debate.
        Returns trade signal if confidence > threshold.
        FAST MODE: Single agent call with 15s timeout for local execution.
        """
        if not signals:
            return None

        for signal in signals:
            # Check cooldown
            if self._is_signal_cooldown(signal.ticker, signal.signal_type):
                logger.debug(f"Signal cooldown: {signal.ticker} - {signal.signal_type}")
                continue

            logger.info(
                f"🔥 Signal detected: {signal.signal_type} on {signal.ticker} "
                f"(strength: {signal.strength:.2f})"
            )

            # Build market data for Swarm
            market_data = self._build_market_data(signal)

            # Run Swarm Debate
            try:
                analysis, transcript = await self.consensus.run(market_data)

                # Calculate confidence score (0.0-1.0)
                confidence_score = self._calculate_confidence(analysis, transcript)

                logger.info(f"Swarm consensus: {confidence_score:.2f} confidence")

                # ALWAYS return signal - let user decide, not the AI
                self._record_signal(signal.ticker, signal.signal_type)

                # FILTER: Only dispatch BUY/SELL signals (skip HOLD)
                if analysis.action.value == "HOLD":
                    logger.info(f"⏸️ HOLD signal for {signal.ticker} - not dispatching (no trade)")
                    continue  # Skip to next signal

                # Build clean signal data - NO datetime objects!
                return {
                    "ticker": signal.ticker,
                    "action": analysis.action.value,
                    "confidence": confidence_score,
                    "entry_price": float(analysis.entry_price),
                    "stop_loss": float(analysis.stop_loss),
                    "take_profit": float(analysis.take_profit),
                    "reason": str(analysis.reason),
                    "signal_type": signal.signal_type,
                    "investment_amount": 1000.0,  # Default $1000 per trade
                    "transcript": {
                        "technical_sniper": {
                            "agent": transcript.technical_sniper.agent,
                            "action": transcript.technical_sniper.action,
                            "conviction": transcript.technical_sniper.conviction,
                        },
                        "macro_analyst": {
                            "agent": transcript.macro_analyst.agent,
                            "action": transcript.macro_analyst.action,
                            "conviction": transcript.macro_analyst.conviction,
                        },
                        "risk_manager": {
                            "agent": transcript.risk_manager.agent,
                            "verdict": transcript.risk_manager.verdict,
                            "conviction": transcript.risk_manager.conviction,
                        },
                        "ceo_verdict": transcript.ceo_verdict,
                    }
                }

            except Exception as e:
                logger.error(f"Swarm consensus failed: {e}")
                continue

        return None
    
    def _build_market_data(self, signal: TechnicalSignal) -> MarketDataPoint:
        """Build MarketDataPoint from technical signal."""
        price = signal.metadata.get("price", 0.0)
        volume = signal.metadata.get("last_volume", 0.0)
        
        return MarketDataPoint(
            asset=signal.ticker,
            price=price,
            volume=volume,
            price_change_1h=0.0,  # Would calculate from historical data
            price_change_24h=0.0,
            indicators={
                "RSI": signal.metadata.get("rsi", 50),
                "SIGNAL_TYPE": signal.signal_type,
                "SIGNAL_STRENGTH": signal.strength
            }
        )
    
    def _calculate_confidence(self, analysis, transcript) -> float:
        """
        Calculate numerical confidence score (0.0-1.0) from Swarm output.
        Maps ConfidenceLevel enum to numerical values.
        """
        confidence_map = {
            ConfidenceLevel.LOW: 0.40,
            ConfidenceLevel.MEDIUM: 0.60,
            ConfidenceLevel.HIGH: 0.80,
            ConfidenceLevel.VERY_HIGH: 0.95
        }
        
        base_confidence = confidence_map.get(analysis.confidence, 0.50)
        
        # Adjust based on agent alignment
        alignment_bonus = 0.0
        if transcript:
            agents_aligned = 0
            total_agents = 0
            
            # Check Technical Sniper
            if transcript.technical_sniper.action in ["BUY", "SELL"]:
                total_agents += 1
                if transcript.technical_sniper.action == analysis.action.value:
                    agents_aligned += 1
            
            # Check Macro Analyst
            if transcript.macro_analyst.action in ["BULLISH", "BEARISH"]:
                total_agents += 1
                macro_aligned = (
                    (transcript.macro_analyst.action == "BULLISH" and analysis.action == SignalAction.BUY) or
                    (transcript.macro_analyst.action == "BEARISH" and analysis.action == SignalAction.SELL)
                )
                if macro_aligned:
                    agents_aligned += 1
            
            # Check Risk Manager
            if transcript.risk_manager.verdict == "APPROVE":
                total_agents += 1
                agents_aligned += 1
            
            if total_agents > 0:
                alignment_ratio = agents_aligned / total_agents
                alignment_bonus = alignment_ratio * 0.15  # Max 15% bonus
        
        # Include signal strength
        signal_weight = 0.05  # Small weight for technical signal strength
        
        final_confidence = min(1.0, base_confidence + alignment_bonus + signal_weight)
        return round(final_confidence, 2)
    
    async def dispatch_to_local(self, signal_data: dict) -> bool:
        """
        Dispatch trade signal to local laptop executor via HTTP.
        Returns True if successfully received.
        """
        try:
            local_url = f"{config.CLOUD_SCANNER_URL}/api/signal"
            
            logger.info(f"📡 Attempting to dispatch signal to: {local_url}")
            logger.info(f"   Signal: {signal_data.get('action')} {signal_data.get('ticker')} (confidence: {signal_data.get('confidence', 0):.2f})")

            response = requests.post(
                local_url,
                json=signal_data,
                timeout=config.LOCAL_EXECUTION_TIMEOUT,
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                logger.info(f"✅ Signal dispatched successfully to local executor")
                return True
            else:
                logger.error(f"❌ Failed to dispatch signal: HTTP {response.status_code} - {response.text}")
                return False

        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Connection refused - Signal Listener not running at {config.CLOUD_SCANNER_URL}")
            logger.error(f"   Make sure the Signal Listener thread is started")
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"❌ Request timeout - Signal Listener didn't respond within {config.LOCAL_EXECUTION_TIMEOUT}s")
            return False
        except Exception as e:
            logger.error(f"❌ Signal dispatch failed: {type(e).__name__}: {e}")
            return False
    
    async def run_scanner(self):
        """Main scanner loop - continuous scanning."""
        logger.info(f" Cloud Scanner started - monitoring {len(self.tickers)} tickers")
        logger.info(f"Tickers: {', '.join(self.tickers)}")
        logger.info(f"Confidence threshold: {config.SWARM_CONFIDENCE_THRESHOLD}")
        
        while True:
            try:
                # Scan all tickers
                signals = await self.scan_all_tickers()
                
                # Process through Swarm
                if signals:
                    trade_signal = await self.process_signals(signals)
                    
                    if trade_signal:
                        # Dispatch to local executor
                        success = await self.dispatch_to_local(trade_signal)
                        
                        if success:
                            logger.info(f" Trade signal executed: {trade_signal}")
                        else:
                            logger.warning(f" Trade signal dispatch failed")
                
                # Wait before next scan
                await asyncio.sleep(config.SCAN_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user")
                break
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await asyncio.sleep(5)  # Wait before retry


def run_cloud_scanner():
    """Entry point for cloud scanner (runs on Vast.ai server)."""
    import sys
    
    print("=" * 60)
    print("VcanTrade AI - Cloud Market Scanner")
    print("=" * 60)
    print(f"Monitoring: {len(config.CLOUD_TICKERS)} tickers")
    for ticker in config.CLOUD_TICKERS:
        print(f"  - {ticker}")
    print(f"Scan interval: {config.SCAN_INTERVAL}s")
    print(f"Confidence threshold: {config.SWARM_CONFIDENCE_THRESHOLD}")
    print("=" * 60)
    
    scanner = CloudScanner()
    
    try:
        asyncio.run(scanner.run_scanner())
    except KeyboardInterrupt:
        print("\nShutting down scanner...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal scanner error: {e}")
        raise


if __name__ == "__main__":
    run_cloud_scanner()
