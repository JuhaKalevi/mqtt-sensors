import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).resolve().with_name("tray.py")), run_name="__main__")
