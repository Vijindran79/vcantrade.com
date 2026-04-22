#!/usr/bin/env python3
"""
Test Pipeline for Swarm Consensus Architecture
Migrated to Groq API (llama3-70b-8192)
"""

import asyncio
import logging
import time
import os
from typing import List, Dict

# Fix Windows console encoding - must be BEFORE any rich imports
if os.name == "nt":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Import from core modules
from core.brain_swarm import OllamaSwarmConsensus
import config

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
console = Console()


def get_sample_agents_config() -> List[Dict]:
    """Return sample configuration for specialist agents"""
    return [
        {
            "name": "Risk Manager",
            "system_prompt": """You are a Risk Management specialist. 
Analyze potential risks, downsides, and failure modes of any proposal or situation.
Be critical and cautious. Identify what could go wrong.""",
        },
        {
            "name": "Opportunity Analyst",
            "system_prompt": """You are an Opportunity Analysis specialist.
Identify potential benefits, upsides, and growth opportunities in any situation.
Be optimistic but realistic. Focus on what could go right.""",
        },
        {
            "name": "Technical Expert",
            "system_prompt": """You are a Technical Implementation specialist.
Assess feasibility, technical requirements, and implementation complexity.
Focus on practical execution details and resource needs.""",
        },
        {
            "name": "Market Strategist",
            "system_prompt": """You are a Market Strategy specialist.
Analyze market conditions, competitive landscape, and timing.
Consider user adoption, market fit, and strategic positioning.""",
        },
    ]


async def run_test_pipeline():
    """Execute the full swarm consensus test pipeline"""

    # Display migration info
    console.print(
        Panel.fit(
            "[bold blue][SUCCESS] Swarm Consensus Test Pipeline[/bold blue]\n"
            f"[green]LLM Provider:[/green] Groq API\n"
            f"[green]Model:[/green] {config.LLM_MODEL}\n"
            f"[green]Temperature:[/green] {config.LLM_TEMPERATURE}\n"
            f"[green]Max Tokens:[/green] {config.LLM_MAX_TOKENS}\n"
            "[yellow]Note: Using ultra-fast Groq inference with llama-3.3-70b-versatile[/yellow]",
            title="System Configuration",
        )
    )

    # Initialize Swarm Consensus (no arguments needed - uses config directly)
    console.print("\n[bold]Initializing Swarm Consensus...[/bold]")
    try:
        swarm = OllamaSwarmConsensus()
        console.print(
            "[green][OK] Swarm initialized successfully with Groq client[/green]"
        )
    except Exception as e:
        console.print(f"[red][FAIL] Failed to initialize Swarm: {e}[/red]")
        logger.error(f"Initialization error: {e}")
        return

    # Test query
    test_query = """
    Should our company invest $500K in developing an AI-powered customer service chatbot 
    to replace 50% of our human support team within the next 6 months?
    
    Context:
    - Current support team: 20 people
    - Monthly support costs: $80K
    - Customer satisfaction score: 87%
    - Competitors are starting to adopt AI chatbots
    - Our technical team has limited ML experience
    """

    console.print(Panel(test_query.strip(), title="Test Query", border_style="cyan"))

    # Get agent configurations
    agents_config = get_sample_agents_config()
    console.print(f"\n[bold]Deploying {len(agents_config)} specialist agents...[/bold]")

    # Display agent table
    table = Table(title="Active Agents")
    table.add_column("Agent Name", style="cyan")
    table.add_column("Role", style="magenta")

    role_map = {
        "Risk Manager": "Risk Assessment",
        "Opportunity Analyst": "Benefit Analysis",
        "Technical Expert": "Feasibility Study",
        "Market Strategist": "Market Analysis",
    }

    for agent in agents_config:
        table.add_row(agent["name"], role_map.get(agent["name"], "Specialist"))

    console.print(table)

    # Execute swarm with timing
    console.print("\n[bold yellow]Starting parallel swarm execution...[/bold yellow]")
    start_time = time.time()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Agents analyzing...", total=None)

            # Run the swarm asynchronously
            result = await swarm.execute_swarm(agents_config, test_query)

            progress.update(task, completed=True)

    except Exception as e:
        console.print(f"\n[red][FAIL] Swarm execution failed: {e}[/red]")
        logger.error(f"Execution error: {e}", exc_info=True)
        return

    elapsed_time = time.time() - start_time
    console.print(f"\n[green][OK] Swarm completed in {elapsed_time:.2f} seconds[/green]")

    # Display results
    console.print("\n" + "=" * 60)
    console.print("[bold magenta][CHART] SWARM CONSENSUS RESULTS[/bold magenta]")
    console.print("=" * 60)

    # Show individual agent responses
    console.print("\n[bold]Individual Agent Analyses:[/bold]")
    for agent_resp in result.get("agent_responses", []):
        confidence_emoji = {
            "LOW": "[RED]",
            "MEDIUM": "[YELLOW]",
            "HIGH": "[GREEN]",
            "VERY_HIGH": "[GREEN_SQ]",
        }.get(agent_resp["confidence"], "[WHITE]")

        panel_content = (
            f"[bold]Confidence:[/bold] {confidence_emoji} {agent_resp['confidence']}\n\n"
            f"{agent_resp['brief']}\n\n"
            f"[italic]Reasoning: {agent_resp['reasoning']}[/italic]"
        )

        console.print(
            Panel(panel_content, title=f"[ROBOT] {agent_resp['name']}", border_style="blue")
        )

    # Show CEO synthesis
    if result.get("status") == "success":
        console.print("\n[bold green][TARGET] CEO Final Decision:[/bold green]")

        final_confidence_emoji = {
            "LOW": "[RED]",
            "MEDIUM": "[YELLOW]",
            "HIGH": "[GREEN]",
            "VERY_HIGH": "[GREEN_SQ]",
        }.get(result["final_confidence"], "[WHITE]")

        decision_panel = Panel(
            f"[bold]Decision:[/bold] {result['final_decision']}\n\n"
            f"[bold]Confidence:[/bold] {final_confidence_emoji} {result['final_confidence']}\n\n"
            f"[bold]Synthesis Reasoning:[/bold]\n{result['synthesis_reasoning']}",
            title="[EMOJI] CEO Synthesis",
            border_style="green",
        )
        console.print(decision_panel)

        if result.get("recommended_actions"):
            console.print("\n[bold]Recommended Actions:[/bold]")
            for i, action in enumerate(result["recommended_actions"], 1):
                console.print(f"  {i}. {action}")

        # Summary stats
        console.print("\n" + "=" * 60)
        console.print("[bold]Performance Metrics:[/bold]")
        console.print(f"  [BULLET] Total Agents: {result.get('agent_count', 0)}")
        console.print(f"  [BULLET] Execution Time: {elapsed_time:.2f}s")
        console.print(f"  [BULLET] Model: {config.LLM_MODEL}")
        console.print(f"  [BULLET] Status: [green]SUCCESS[/green]")
        console.print("=" * 60)

    else:
        console.print(
            f"\n[red][FAIL] CEO Synthesis Failed:[/red] {result.get('synthesis_reasoning', 'Unknown error')}"
        )


def main():
    """Main entry point"""
    console.print("\n[bold cyan]Starting Swarm Consensus Pipeline Test[/bold cyan]\n")

    try:
        # Properly await the async coroutine using asyncio.run()
        asyncio.run(run_test_pipeline())
        console.print("\n[green][OK] Test completed successfully![/green]\n")
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted by user[/yellow]\n")
    except Exception as e:
        console.print(f"\n[red][FAIL] Test failed with error: {e}[/red]\n")
        logger.error("Test pipeline failed", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
