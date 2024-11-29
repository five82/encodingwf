#!/usr/bin/env python3

import sys
import os
from pathlib import Path

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from video_encoder.main import main

if __name__ == "__main__":
    sys.exit(main())
