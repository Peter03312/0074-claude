"""确保测试能以仓库根为导入起点，并支持 `from tests.naive import ...`。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
