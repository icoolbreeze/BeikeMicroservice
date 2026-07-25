# 服务注册表

平台内所有微服务的清单与状态。新增服务时必须更新本表。

| 服务 | 路径 | 状态 | 说明 |
| --- | --- | --- | --- |
| property_verification | `services/property_verification` | 结构已创建（未实现） | 房源信息验证：接收不动产权证图片、提取业务件号与证件编码、调用合法授权渠道验证、解析结果、生成查询截图 |

## 规划中的候选服务

| 服务 | 状态 | 说明 |
| --- | --- | --- |
| image_processing | 规划中 | 房源图片处理 |
| document_extraction | 规划中 | 合同或文档信息提取 |
| data_sync | 规划中 | 内部系统数据同步 |
| notification | 规划中 | 消息通知 |
| contract_tools | 规划中 | 合同辅助工具 |
| house_tools | 规划中 | 房源相关辅助工具 |

> 候选服务仅为规划示例，实际新增以业务需求为准；创建方式见
> [service-development-guide.md](service-development-guide.md)。
