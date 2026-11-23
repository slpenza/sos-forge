"""Allow `python -m sosforge ...` as an alternative to the `sos` script."""
import sys

from .cli import main

sys.exit(main())
