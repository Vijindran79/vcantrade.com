"""
VcaniTrade AI - Swarm Consensus Judge

The 'Judge' that looks at signals from the Hunter (Vibe agent), the Scout
(Liquidity agent), and the Vision brain to give a final verdict.

This module re-exports OllamaSwarmConsensus from core.brain_swarm so that
main.py, verify_production.py, and the Co-Pilot chatbot can import it cleanly.
The underlying class already performs the full 3-step debate:
  1. Vibe Agent   -> Market mood and regime detection (The Hunter)
  2. Liquidity Agent -> Zone confirmation and order-flow bias (The Scout)
  3. Vision Agent -> Chart pattern validation (The Vision brain)
  4. The Closer   -> Final verdict with entry, SL, and TP

All execution is 100% local via Ollama + qwen2.5:latest.
"""

from core.brain_swarm import OllamaSwarmConsensus

# Semantic alias: SwarmJudge is the final arbiter that aggregates
# Hunter (Vibe), Scout (Liquidity), and Vision into one verdict.
SwarmJudge = OllamaSwarmConsensus

__all__ = ["OllamaSwarmConsensus", "SwarmJudge"]
