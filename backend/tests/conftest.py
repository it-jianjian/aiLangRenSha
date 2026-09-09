"""pytest 全局前置：把测试钉死在 SQLite，与开发者 .env（可能指向 MySQL）解耦。

pydantic-settings 优先级：环境变量 > .env 文件 > 代码默认值。此处在任何 app.* 模块
被导入之前设置 DATABASE_URL 环境变量，确保 get_settings()（lru_cache 首次调用）读到
SQLite 测试库——测试永不连接真实 MySQL，无论开发者 .env 如何配置。

conftest.py 由 pytest 在 tests/ 下测试模块之前加载，故 os.environ 赋值先于 app 导入生效。
"""

import os

# 必须在导入任何 app.* 之前设置（放模块顶层，先于测试模块收集）
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_werewolf.db"
