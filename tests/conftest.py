"""Put the addon dir on sys.path so modules import flat (no Anki package)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
