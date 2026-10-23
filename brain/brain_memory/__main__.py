"""python -m brain_memory ...  ->  brain_memory.cli.main, through run_cli (a network drop -> one STOPPED line)."""
from quickicp import run_cli

from brain_memory.cli import main

run_cli(main)
