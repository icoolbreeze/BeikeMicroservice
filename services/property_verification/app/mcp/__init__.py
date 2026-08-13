"""MCP stdio transport: HTTP client (client.py), rate limiting (rate_limit.py),
tool inputs (schemas.py) and the server entry point (server.py).

pv-mcp 是已部署 property_verification 服务的薄客户端：核验流水线
（图片校验、模型提取、蓉e办查询、截图产物）全部在服务端执行，
本进程只负责上传、轮询与产物下载。
"""
