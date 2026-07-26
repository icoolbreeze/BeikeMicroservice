# 常用开发命令入口（占位）
# 注意：当前仓库仅包含结构骨架，以下命令多为预留。
# 注意：本文件中命令行前为 Tab 缩进。

.PHONY: help new-service serve-pv

help: ## 显示可用命令
	@grep -E "^[a-zA-Z_-]+:.*?## " $(MAKEFILE_LIST) | awk "BEGIN {FS = \":.*?## \"}; {printf \"  make %-16s %s\\n\", $$1, $$2}"

new-service: ## 创建新微服务骨架，用法：make new-service NAME=my_service
	python scripts/create_service.py $(NAME)

serve-pv: ## 启动 property_verification 服务（房源信息验证入口）
	cd services/property_verification && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
