import sys
from pathlib import Path

# Make the module under test importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
