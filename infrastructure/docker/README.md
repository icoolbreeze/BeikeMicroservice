# infrastructure/docker

容器与镜像相关说明。

- 各服务的 `Dockerfile` 位于服务自身目录内（如 `services/property_verification/Dockerfile`）；
- 本目录用于沉淀通用镜像规范（基础镜像、非 root 运行、多阶段构建等，待补充）；
- 当前不包含可直接用于生产环境的配置。
