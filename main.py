"""唯一入口。用法见 README.md。

设计意图：把 CLI 逻辑放在 pyapp/cli，这里只做 sys.path 与退出码转接，
使 `python main.py` 与 `python -m pyapp.cli` 行为完全一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyapp.cli.__main__ import main  # noqa: E402  （必须在 sys.path 调整之后）

if __name__ == "__main__":
    raise SystemExit(main())
