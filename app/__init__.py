"""离散乐句 DAG 压缩求值与逐事件比对 API。"""

import sys

# DAG 最多 2000 个节点，合法的单孩子变换链深度可达 2000。
# 压栈/下钻/路径计算按节点深度递归，必须高于默认 1000。
# 取 10_000 给 Python 自身调用帧留出充足余量。
if sys.getrecursionlimit() < 10_000:
    sys.setrecursionlimit(10_000)
