"""开发脚本：创建新的微服务骨架。

用法（在仓库根目录执行）：
    python scripts/create_service.py <service_name>

说明：
- 仅在 services/ 下生成目录与占位文件，不包含任何业务实现；
- 分层约定与 services/property_verification 保持一致；
- 若目标服务已存在则直接退出，不覆盖任何文件。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = ROOT / "services"

PACKAGES = [
    "app",
    "app/api",
    "app/api/v1",
    "app/api/v1/endpoints",
    "app/application",
    "app/application/commands",
    "app/application/queries",
    "app/application/services",
    "app/domain",
    "app/domain/entities",
    "app/domain/value_objects",
    "app/domain/repositories",
    "app/domain/providers",
    "app/infrastructure",
    "app/infrastructure/config",
    "app/infrastructure/database",
    "app/schemas",
    "app/security",
    "tests",
    "tests/unit",
    "tests/integration",
]

PLAIN_DIRS = ["tests/fixtures", "storage/uploads", "storage/screenshots", "storage/temp"]

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def main() -> int:
    if len(sys.argv) != 2 or not NAME_PATTERN.match(sys.argv[1]):
        print("用法: python scripts/create_service.py <service_name>（小写字母/数字/下划线）")
        return 1
    name = sys.argv[1]
    target = SERVICES_DIR / name
    if target.exists():
        print(f"服务已存在，未做任何修改: {target}")
        return 1

    for pkg in PACKAGES:
        pkg_dir = target / pkg
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text(
            '"""TODO: 补充模块说明。"""\n', encoding="utf-8"
        )

    (target / "app" / "main.py").write_text(
        '"""' + name + ' 服务入口（占位）。"""\n'
        "\n"
        "\n"
        "def create_app():\n"
        '    """应用工厂（占位）。"""\n'
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )

    for extra in PLAIN_DIRS:
        (target / extra).mkdir(parents=True, exist_ok=True)
    for keep in ["storage/uploads/.gitkeep", "storage/screenshots/.gitkeep", "storage/temp/.gitkeep"]:
        (target / keep).write_text("", encoding="utf-8")

    (target / "README.md").write_text(
        "# " + name + "\n\n独立微服务（骨架，未实现任何业务功能）。\n",
        encoding="utf-8",
    )
    print(f"已创建服务骨架: {target}")
    print("下一步：请在 docs/service-registry.md 中登记该服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
