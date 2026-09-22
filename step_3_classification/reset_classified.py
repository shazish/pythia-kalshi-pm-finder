#!/usr/bin/env python3
"""Reset classified.json to empty array for re-classification."""

# Support running this script directly from any working directory.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
with open('/home/shaah/kalshi-tracker/cache/classified.json', 'w') as f:
    json.dump([], f)
print("Reset classified.json to []")
