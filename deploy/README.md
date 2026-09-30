# Ubuntu 部署说明

目标：在 Ubuntu 服务器上跑起 TSCP 剧情工坊，**监听 8888**，开机自启。

网页版**不需要 PySide6 / pygame** —— 那两个是桌面工具的依赖，headless 服务器上不装
能省掉一大堆系统库。服务器只装 `requirements-web.txt`（Flask + gunicorn）。

---

## 方式一：上传部署包（服务器上没有 git，或不想装）

```bash
python deploy/make_release.py        # 在开发机上生成，约 0.1 MB
```

把得到的 `tscp-web-deploy.zip` 上传到服务器，然后：

```bash
unzip tscp-web-deploy.zip
cd tscp-web
sudo ./deploy/install-ubuntu.sh
```

包里只有服务器需要的部分：`webapp/`、`PlotManager/`、`tscp_player/`、`deploy/`、
依赖清单和许可证。桌面工具（它们要 PySide6，headless 上跑不起来）和 94 MB 的示例
剧情都没有打进去。压缩包内的 `DEPLOY.txt` 有同样的步骤说明。

**8080 已经被占用不影响** —— 这个服务默认用 8888。如果 8888 也被占了，安装脚本会
在启动前直接报出来并告诉你怎么换端口。

---

## 方式二：在服务器上 git clone

```bash
git clone https://github.com/Yore-ASH/AmbientBook.git
cd AmbientBook
sudo ./deploy/install-ubuntu.sh
```

脚本会：建 `tscp` 系统用户 → 把代码同步到 `/opt/tscp` → 建虚拟环境装依赖 →
准备 `/var/lib/tscp-web` 数据目录 → 生成 `/etc/tscp-web.env`（含随机密钥）→
安装并启动 `tscp-web.service`。

完成后访问 `http://<服务器地址>:8888`，**第一个注册的账号自动成为管理员**。

可用环境变量覆盖默认路径：

```bash
sudo APP_DIR=/srv/tscp DATA_DIR=/srv/tscp-data PORT=8888 ./deploy/install-ubuntu.sh
```

---

## 手动部署

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip

sudo useradd --system --create-home --shell /usr/sbin/nologin tscp
sudo mkdir -p /opt/tscp /var/lib/tscp-web
sudo cp -a . /opt/tscp/
sudo rm -rf /opt/tscp/.git /opt/tscp/.venv /opt/tscp/webapp/data

sudo python3 -m venv /opt/tscp/.venv
sudo /opt/tscp/.venv/bin/pip install -r /opt/tscp/requirements-web.txt

sudo cp deploy/tscp-web.env.example /etc/tscp-web.env
sudo python3 -c "import secrets; print('TSCP_SECRET_KEY=' + secrets.token_urlsafe(48))"
#   ↑ 把输出填进 /etc/tscp-web.env，覆盖 change-me
sudo chmod 600 /etc/tscp-web.env

sudo cp deploy/tscp-web.service /etc/systemd/system/
sudo chown -R tscp:tscp /opt/tscp /var/lib/tscp-web

sudo systemctl daemon-reload
sudo systemctl enable --now tscp-web
```

打开防火墙：

```bash
sudo ufw allow 8888/tcp
```

---

## 前面挂 nginx（推荐给公网站点）

gunicorn 只监听本机，nginx 对外仍是 8888，顺便负责 TLS、静态文件和上传大小。

1. 改 systemd 的监听地址：

   ```bash
   sudo sed -i 's|--bind 0.0.0.0:8888|--bind 127.0.0.1:8000|' /etc/systemd/system/tscp-web.service
   ```

2. 打开代理信任（否则重定向和 Cookie 会以为是 http）：

   ```bash
   echo 'TSCP_PROXY_HOPS=1' | sudo tee -a /etc/tscp-web.env
   ```

3. 装 nginx 站点：

   ```bash
   sudo apt install -y nginx
   sudo cp deploy/nginx-8888.conf /etc/nginx/sites-available/tscp-web
   sudo ln -sf /etc/nginx/sites-available/tscp-web /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. 重启后端：

   ```bash
   sudo systemctl daemon-reload && sudo systemctl restart tscp-web
   ```

> `nginx-8888.conf` 里的 `client_max_body_size 512M` 和 `proxy_buffering off` 都是
> 必须的：前者决定音频能不能传上去，后者决定进度条能不能拖动。

---

## 更新到新版本

```bash
cd AmbientBook && git pull
sudo ./deploy/install-ubuntu.sh     # 会保留 /etc/tscp-web.env 里的密钥
```

脚本对已存在的 `/etc/tscp-web.env` **只读不写**，所以密钥和配置不会丢。
端口检查在服务已在运行时会被跳过（那一刻占用端口的正是它自己）。

用部署包的话，重新生成上传、解压覆盖、再跑一次安装脚本即可。

> **改完代码必须重启服务。** 生产走 `gunicorn "webapp:create_app()"`，代码在启动时
> 就加载进 worker 了，`git pull` 不会自动生效。前端 JS/CSS 还有浏览器缓存，更新后
> 让用户强刷一次（Ctrl+F5）。

---

## 备份

需要备份的只有数据和配置：

```bash
sudo systemctl stop tscp-web
sudo tar czf tscp-backup-$(date +%F).tar.gz /var/lib/tscp-web /etc/tscp-web.env
sudo systemctl start tscp-web
```

`/var/lib/tscp-web/` 里有 `app.db`（用户与归属）、`plots/`（每个剧情包一个
`.tscpkg`）、`cache/`（音频解压缓存，**可以不备份**，会自动重建）。

---

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| 8888 连不上 | `sudo ufw allow 8888/tcp`；云厂商的安全组也要放行 |
| `端口 8888 已经被占用` | 安装脚本会直接报出来；换端口 `sudo PORT=9999 ./deploy/install-ubuntu.sh` |
| 服务起不来 | `journalctl -u tscp-web -n 50 --no-pager` |
| `Address already in use` | `sudo ss -ltnp \| grep 8888` 找出占用者，或换端口 |
| 上传音频报 413 | 同时调大 `/etc/tscp-web.env` 的 `TSCP_MAX_UPLOAD_MB` 和 nginx 的 `client_max_body_size` |
| 登录后立刻掉线 | 走 HTTPS 但没设 `TSCP_SECURE_COOKIES=1`，或挂了 nginx 却没设 `TSCP_PROXY_HOPS=1` |
| 502 Bad Gateway | gunicorn 没起来；看 `systemctl status tscp-web` |
| 打包大剧情包超时 | 把 systemd 里的 `--timeout 180` 再调大，nginx 的 `proxy_read_timeout` 也要跟上 |
| 静态文件 404（走了 nginx） | 确认 `alias /opt/tscp/webapp/static/` 路径和实际部署目录一致 |

---

## 为什么不直接用 Flask 开发服务器

`python -m webapp` 自带的服务器是单线程、没有并发保护、也不适合长期跑公网。它只
适合在服务器上快速验证一次：

```bash
sudo -u tscp /opt/tscp/.venv/bin/python -m webapp --host 127.0.0.1 --port 8888
```

确认能起来之后，交给 gunicorn + systemd 常驻。
