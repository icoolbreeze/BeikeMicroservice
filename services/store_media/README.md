# store_media

门店多媒体广告发布、房源轮播与角色管理微服务。服务可独立部署，默认监听 `8010`。

## 功能

- 管理员、区域经理、店长上传和维护所属范围内的图片或视频，上传时默认立即发布；
- 图片可设置 1–3600 秒停留时长，视频播放完毕后自动切换；
- 标题、图片时长、顺序、发布状态和待删除项通过一个事务统一保存；
- 展示端不叠加任何文字，每 30 秒同步一次清单，图片底边显示低亮度进度条；
- 展示端预加载并复用媒体节点，已发布文件使用不可变资源缓存，避免每轮切换重复加载；
- 系统管理员、区域经理、店长、店员四级 RBAC，权限按区域和门店隔离；
- SQLite 元数据、密码 PBKDF2 哈希、随机不透明会话令牌、本地媒体持久化；
- 管理页 `/`，展示页 `/display.html?store_id=<门店标识>`，OpenAPI `/docs`。

## 本地启动

```powershell
cd services/store_media
$env:SM_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:SM_BOOTSTRAP_ADMIN_PASSWORD="请设置至少8位的随机密码"
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8010
```

首次成功启动会在空数据库中创建系统管理员。随后应移除引导账号环境变量；已有用户时不会重复创建。
生产环境建议把 `SM_STORAGE_DIR` 指向持久卷。SQLite 适合单实例初期部署；多实例部署前应按架构文档迁移至 PostgreSQL 和对象存储。

## 权限边界

| 角色 | 管理范围 |
| --- | --- |
| 系统管理员 | 全部区域、门店、账号和内容 |
| 区域经理 | 所属区域内门店、店长、店员和内容 |
| 店长 | 所属门店店员和内容 |
| 店员 | 登录并查看所属门店，无发布权限 |

## 测试

```powershell
python -m pytest
```

## 服务器发布

生产 systemd 单元默认监听 `8080`，部署目录、持久化目录和验证命令见 [`deploy/README.md`](deploy/README.md)。
Docker Compose 同样默认将宿主机 `8080` 映射到容器 `8010`，可通过 `SM_PUBLIC_PORT` 修改宿主端口。
