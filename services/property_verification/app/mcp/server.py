"""MCP stdio server exposing the deployed property_verification service.

Entry point for the ``pv-mcp`` console script. The pv-mcp process is a thin
HTTP client: submit a local certificate photo to the deployed service, poll
the async job status, and download result screenshots into a local temp
directory so the agent can read them.

Privacy contract (sensitive PII): the certificate photo is a sensitive
personal document. Tools are rate-limited per local user; the submitted
photo is deleted server-side once verification finishes, and agents SHOULD
delete their local copy after the workflow completes.
"""

from __future__ import annotations

import getpass
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.infrastructure.config.settings import Settings, load_settings
from app.mcp.client import PVClient, PVClientError
from app.mcp.rate_limit import RateLimiter
from app.mcp.schemas import (JobInput, VerifyDownloadInput,
                             VerifyShareLinkInput, VerifySubmitInput)

logger = logging.getLogger(__name__)

RATE_LIMITED_CODE = "RATE_LIMITED"

_INSTRUCTIONS = (
    "查档核验（房源信息验证）：上传不动产权证照片，由服务端提取业务件号与证件编码"
    "并在蓉e办完成核验，产出查询结果截图。"
    "工作流：pv_verify_submit 提交图片 → 用 pv_verify_status 轮询"
    "（建议间隔 5-10 秒）直到 status 为 succeeded 或 failed → "
    "succeeded 后用 pv_verify_download 下载截图查看。"
    "隐私要求：证件图片属敏感个人信息，job_id 即访问凭据请勿外泄；"
    "核验完成后应删除本地证件副本。"
)


def _caller_subject() -> str:
    # stdio transport has no authenticated caller identity; the local
    # OS user is the closest subject for quota attribution.
    return getpass.getuser() or "unknown"


def build_pv_mcp_server(client: PVClient, settings: Settings) -> MCPServer:
    """Create the MCP server wired to the deployed verification service."""
    submit_limiter = RateLimiter(settings.mcp_rate_limit_per_min)
    poll_limiter = RateLimiter(settings.mcp_poll_rate_limit_per_min)
    download_dir = settings.mcp_download_dir or None

    def _require_submit_quota(subject: str) -> None:
        if not submit_limiter.allow(subject):
            raise ToolError(
                f"{RATE_LIMITED_CODE}: submit quota of "
                f"{settings.mcp_rate_limit_per_min}/min exceeded"
            )

    def _require_poll_quota(subject: str) -> None:
        if not poll_limiter.allow(subject):
            raise ToolError(
                f"{RATE_LIMITED_CODE}: poll quota of "
                f"{settings.mcp_poll_rate_limit_per_min}/min exceeded"
            )

    def _client_error(exc: PVClientError) -> ToolError:
        return ToolError(str(exc))

    server = MCPServer(
        name="property-verification",
        instructions=_INSTRUCTIONS,
    )

    @server.tool(
        name="pv_verify_submit",
        description=(
            "上传本地产权证照片发起查档核验，返回 job_id（异步任务）。"
            "图片要求：JPG/PNG、≤10MB、清晰无遮挡。"
            "提交后应立即用 pv_verify_status 轮询任务进度。"
            "隐私：证件图片属敏感个人信息，核验完成后请删除本地证件副本。"
        ),
    )
    def pv_verify_submit(input: VerifySubmitInput) -> dict:
        _require_submit_quota(_caller_subject())
        try:
            return client.submit(input.image_path)
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_status",
        description=(
            "查询核验任务状态快照。status 为 succeeded 时产物可用；"
            "failed 时返回 error 原因；pending/running 时继续轮询"
            "（建议间隔 5-10 秒）。"
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_status(input: JobInput) -> dict:
        _require_poll_quota(_caller_subject())
        try:
            return client.get_status(input.job_id)
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_artifacts",
        description="列出任务产物清单（截图规格与文件信息）。",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_artifacts(input: JobInput) -> dict:
        _require_poll_quota(_caller_subject())
        try:
            return client.get_artifacts(input.job_id)
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_result",
        description=(
            "获取核验任务的官方查询原始数据：服务端在蓉e办查询时从页面表格解析的"
            "完整内容——headers(表头，按列顺序) / row(数据行，与表头一一对应) / "
            "fields(逐列字段映射，如 业务件号、产权证号、区域、街道、栋号、"
            "套内面积、房屋用途、是否抵押、是否查封 等)。"
            "响应同时包含 artifacts 产物清单，其中每个产物的 url 为"
            "**房产证查档截图**的绝对下载链接（panel=查档结果区域截图，"
            "full=整页查档截图，zip=全部产物打包）。"
            "本工具只返回原始数据，不做任何总结；结论（如 是否查封=是/否）由调用方"
            "根据 fields 自行归纳。status 为 succeeded 后调用；result 为 null "
            "表示尚未生成。"
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_result(input: JobInput) -> dict:
        _require_poll_quota(_caller_subject())
        try:
            return client.get_result(input.job_id)
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_share_link",
        description=(
            "生成产物截图的短期签名下载链接（绝对 URL + 过期时间）。"
            "用于把截图链接转发给最终用户（如微信/网页渠道）；签名过期后链接即失效。"
            "MCP 自身读取图片请用 pv_verify_download，不要用分享链接。"
            "注意：链接指向明文 HTTP 公网地址，请勿二次转发给无关第三方。"
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_share_link(input: VerifyShareLinkInput) -> dict:
        _require_poll_quota(_caller_subject())
        try:
            return client.get_share_link(input.job_id, input.spec, input.ttl)
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_download",
        description=(
            "下载任务产物到本地临时目录，返回绝对路径供读取查看。"
            "spec 取值：panel(结果区域截图)、full(整页截图)、zip(全部产物打包)。"
            "仅当任务 status 为 succeeded 时可用。"
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_download(input: VerifyDownloadInput) -> dict:
        _require_poll_quota(_caller_subject())
        try:
            path = client.download(input.job_id, input.spec, download_dir)
            return {"path": path}
        except PVClientError as exc:
            raise _client_error(exc) from exc

    @server.tool(
        name="pv_verify_stats",
        description="服务累计受理次数（观察用）。",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def pv_verify_stats() -> dict:
        _require_poll_quota(_caller_subject())
        try:
            return client.stats()
        except PVClientError as exc:
            raise _client_error(exc) from exc

    return server


def main() -> None:
    """Console entry point for ``pv-mcp``."""
    settings = load_settings()
    client = PVClient(settings.pv_base_url)
    server = build_pv_mcp_server(client, settings)
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
