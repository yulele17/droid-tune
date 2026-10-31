"""让 `python -m tune` 可直接执行（见 README 的用法一）。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
