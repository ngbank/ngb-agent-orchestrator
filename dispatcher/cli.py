#!/usr/bin/env python3
"""
Agent Orchestrator CLI

Command-line interface for managing agentic workflows and Goose integration.
"""

import click
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """
    Agent Orchestrator CLI
    
    Manage agentic workflows, JIRA integration, and Goose recipes.
    """
    pass


@cli.command()
def status():
    """Check orchestrator status and configuration."""
    click.echo("🚀 Agent Orchestrator Status")
    click.echo("=" * 50)
    
    # Check environment configuration
    env_file = Path(".env")
    if env_file.exists():
        click.echo("✅ Environment file (.env) found")
    else:
        click.echo("⚠️  Environment file (.env) not found")
        click.echo("   Copy .env.example to .env and configure credentials")
    
    # Check required directories
    required_dirs = ["dispatcher", "recipes", "schemas", "state"]
    for dir_name in required_dirs:
        if Path(dir_name).exists():
            click.echo(f"✅ Directory '{dir_name}/' exists")
        else:
            click.echo(f"❌ Directory '{dir_name}/' missing")
    
    # Check JIRA configuration
    jira_url = os.getenv("JIRA_URL")
    if jira_url:
        click.echo(f"✅ JIRA URL configured: {jira_url}")
    else:
        click.echo("⚠️  JIRA_URL not configured")
    
    click.echo("=" * 50)
    click.echo("Orchestrator is ready! ✨")


@cli.command()
def init():
    """Initialize the orchestrator environment."""
    click.echo("🔧 Initializing Agent Orchestrator...")
    
    # Check if .env exists
    if not Path(".env").exists():
        if Path(".env.example").exists():
            click.echo("📋 Creating .env from .env.example...")
            import shutil
            shutil.copy(".env.example", ".env")
            click.echo("✅ .env file created")
            click.echo("⚠️  Please edit .env and add your credentials")
        else:
            click.echo("❌ .env.example not found")
            return
    else:
        click.echo("✅ .env file already exists")
    
    # Ensure state directory exists
    state_dir = Path("state")
    state_dir.mkdir(exist_ok=True)
    click.echo("✅ State directory ready")
    
    click.echo("\n🎉 Initialization complete!")
    click.echo("Next steps:")
    click.echo("  1. Edit .env with your credentials")
    click.echo("  2. Run 'python dispatcher/cli.py status' to verify setup")


@cli.command()
@click.option("--recipe", "-r", help="Name of the Goose recipe to run")
def goose(recipe):
    """Execute a Goose recipe."""
    if not recipe:
        click.echo("📚 Available Goose recipes:")
        recipes_dir = Path("recipes")
        if recipes_dir.exists():
            recipes = list(recipes_dir.glob("*.yaml")) + list(recipes_dir.glob("*.yml"))
            if recipes:
                for r in recipes:
                    click.echo(f"  - {r.stem}")
            else:
                click.echo("  No recipes found in recipes/ directory")
        else:
            click.echo("  recipes/ directory not found")
        return
    
    click.echo(f"🪿 Running Goose recipe: {recipe}")
    # TODO: Implement Goose execution logic
    click.echo("⚠️  Goose execution not yet implemented")


if __name__ == "__main__":
    cli()
