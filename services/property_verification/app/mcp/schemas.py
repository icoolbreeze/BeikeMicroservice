"""pv-mcp 工具输入模型。

MCP 工具输入比 HTTP API 更严格：未知字段拒绝（``extra="forbid"``），
避免脏参数被静默忽略后打到上游。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VerifySubmitInput(BaseModel):
    """``pv_verify_submit`` 输入：本地产权证图片路径。"""

    image_path: str = Field(min_length=1, max_length=1024,
                            description="本地不动产权证照片的绝对路径（JPG/PNG，≤10MB）")
    model_config = ConfigDict(extra="forbid")


class JobInput(BaseModel):
    """按 job_id 查询任务。"""

    job_id: str = Field(min_length=1, max_length=64,
                        description="pv_verify_submit 返回的任务 ID")
    model_config = ConfigDict(extra="forbid")


class VerifyDownloadInput(BaseModel):
    """``pv_verify_download`` 输入：任务 + 产物规格。"""

    job_id: str = Field(min_length=1, max_length=64)
    spec: str = Field(min_length=1, max_length=16,
                      description="产物规格：panel(区域截图) / full(整页截图) / zip(全部打包)")
    model_config = ConfigDict(extra="forbid")


class VerifyShareLinkInput(BaseModel):
    """``pv_verify_share_link`` 输入：任务 + 产物规格 + 有效秒数。"""

    job_id: str = Field(min_length=1, max_length=64)
    spec: str = Field(min_length=1, max_length=16,
                      description="产物规格：panel / full / zip")
    ttl: int = Field(default=600, ge=10, le=604800,
                     description="链接有效期秒数（10 ~ 604800，默认 600）")
    model_config = ConfigDict(extra="forbid")
