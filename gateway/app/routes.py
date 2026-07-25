"""网关路由注册表（占位）。

未来维护路径前缀到上游服务的映射；当前为空，不做任何转发。
"""

# TODO: 示例结构 {"property-verification": "/api/v1/property-verification"}
ROUTE_TABLE: dict[str, str] = {}
