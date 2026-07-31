# 服务器连接说明

本文档用于说明项目部署服务器的连接入口和日常维护方式。仓库只记录连接元数据；私钥、密码、令牌和生产环境变量不得提交到版本控制。

## 门店媒体服务器

| 项目 | 值 |
| --- | --- |
| SSH 别名 | `beike-server` |
| 地址 | `119.29.187.184` |
| 默认用户 | `ubuntu` |
| SSH 配置 | `C:\Users\shipi\.ssh\config` |
| 私钥位置 | `C:\Users\shipi\.ssh\beike.pem` |
| 初始管理员凭据 | `C:\Users\shipi\.ssh\beike-store-media.env`（仅本机当前用户可读） |
| 应用端口 | `8080` |
| systemd 服务 | `store-media.service` |
| 当前代码链接 | `/opt/store-media/current` |
| 持久化目录 | `/var/lib/store-media` |
| 首次发布时间 | `2026-07-29` |

## 首次配置

连接前需由项目管理员通过安全渠道提供私钥，并保存到上表所列的本机路径。不要通过 Git、聊天消息或普通邮件传递私钥内容。

在 `C:\Users\shipi\.ssh\config` 中配置以下条目：

```sshconfig
Host beike-server
    HostName 119.29.187.184
    User ubuntu
    IdentityFile C:/Users/shipi/.ssh/beike.pem
    IdentitiesOnly yes
```

若在其他电脑上工作，请将 `IdentityFile` 改为该电脑上的实际私钥路径；不要修改仓库以适配个人路径。首次连接时，应通过项目管理员或云平台控制台核对服务器主机密钥指纹后再确认。

## 连接与验证

后续助手和部署脚本统一使用稳定的 SSH 别名：

```bash
ssh beike-server
```

登录后可验证服务状态：

```bash
sudo systemctl status store-media
curl --fail http://127.0.0.1:8080/api/v1/health
```

若服务器账户或地址发生变化，只更新本机 SSH 配置中的 `User` 或 `HostName`，仓库内的部署命令继续使用 `beike-server`。

## 常用维护命令

```bash
ssh beike-server
sudo systemctl status store-media
sudo journalctl -u store-media --since=-10min --no-pager
curl --fail http://127.0.0.1:8080/api/v1/health
```

生产目录和首次发布步骤见 [`services/store_media/deploy/README.md`](../services/store_media/deploy/README.md)。

## 常见问题

- `Permission denied (publickey)`：确认私钥路径正确、当前用户有权读取私钥，并检查 SSH 配置中的 `User` 和 `IdentityFile`。
- `Connection timed out`：确认当前网络可访问服务器，并检查云防火墙或安全组是否允许来源 IP 连接 SSH 端口。
- 服务不可用：先运行 `systemctl status`，再查看 `journalctl` 日志；分享排障信息前应删除密码、令牌、Cookie 和用户数据。

## 安全要求

- 不要把服务器私钥复制回仓库，也不要在命令输出、日志、截图或文档中展示私钥内容。
- 不要在 SSH 命令中通过参数传递密码；应用凭据保存在服务器的 `/etc/store-media/store-media.env`。
- 本机初始管理员凭据文件仅供首次部署使用。首个管理员创建后，应按部署文档移除引导凭据并重启服务。
- 私钥或凭据一旦疑似泄露，应立即停止使用并由管理员轮换，而不是只删除本地文件或 Git 记录。
