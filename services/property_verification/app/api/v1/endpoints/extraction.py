"""证件信息提取端点（说明）。

当前「提取业务件号 / 证件编码」作为验证流程的内部步骤执行，不单独对外暴露。
未来如需独立提取能力，可在本端点扩展 POST /extraction/jobs。

实现见 app/infrastructure/verification_runner.py（复用 scripts/house_verify.py）。
"""
from __future__ import annotations
