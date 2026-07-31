# 生产部署

默认目标是 Ubuntu、`ubuntu` 运行用户和 systemd，公网/内网入口端口为 `8080`。

目录约定：

- 代码：`/opt/store-media/current`
- Python 环境：`/opt/store-media/venv`
- 持久化数据：`/var/lib/store-media`
- 私密环境变量：`/etc/store-media/store-media.env`

首次发布：

```bash
sudo install -d -o ubuntu -g ubuntu /opt/store-media/current /var/lib/store-media
sudo install -d -m 750 -o root -g ubuntu /etc/store-media
python3 -m venv /opt/store-media/venv
/opt/store-media/venv/bin/pip install /opt/store-media/current/services/store_media
sudo install -m 0644 \
  /opt/store-media/current/services/store_media/deploy/store-media.service \
  /etc/systemd/system/store-media.service
sudo systemctl daemon-reload
sudo systemctl enable --now store-media
```

`/etc/store-media/store-media.env` 首次启动时至少配置：

```dotenv
SM_BOOTSTRAP_ADMIN_USERNAME=
SM_BOOTSTRAP_ADMIN_PASSWORD=
SM_BOOTSTRAP_ADMIN_DISPLAY_NAME=系统管理员
```

首个管理员创建后应从环境文件移除引导用户名和密码，再执行 `sudo systemctl restart store-media`。

验证：

```bash
systemctl is-active store-media
curl --fail http://127.0.0.1:8080/api/v1/health
journalctl -u store-media --since "10 minutes ago" --no-pager
```
