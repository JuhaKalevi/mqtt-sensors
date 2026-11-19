"""Chia helpers shared by the chia_* collectors."""
from pathlib import Path

def chia_root():
    import pwd
    return Path(pwd.getpwuid(1000).pw_dir) / ".chia" / "mainnet"
