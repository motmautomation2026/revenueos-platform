"""python -m lead_qual ...  ->  lead_qual.cli.main, through run_cli (a network drop -> one STOPPED line)."""
from quickicp import run_cli

from lead_qual.cli import main

run_cli(main)
