"""The Lakebase POC is its own small project (infrastructure/lakebase_poc) whose modules import `lakebase_poc.*`."""
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[2] / "infrastructure" / "lakebase_poc"
if str(POC) not in sys.path:
    sys.path.insert(0, str(POC))
