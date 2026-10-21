"""Enable ``python -m taskscheduler``."""

import sys

from .cli import main

sys.exit(main())
