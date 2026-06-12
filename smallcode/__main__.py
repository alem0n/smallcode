"""
``__main__.py`` — 使 ``python -m smallcode`` 可以启动 REPL 交互式会话。

这是 Python 的标准入口约定：当用户执行 ``python -m smallcode`` 时，
Python 会自动运行本文件。我们只需从包中导入 ``main()`` 并调用即可。
"""

from smallcode import main

main()
