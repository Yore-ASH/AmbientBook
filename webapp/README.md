# TSCP 剧情工坊（网页版）

把原来的桌面工具链搬到浏览器里：在线编写剧本、上传音乐、**现场录制歌词时间**、
打包下载 `.tscpkg`，并直接在线播放（带无背景歌词窗）。

后端是 **Flask**，直接复用仓库里那套已经测过的 Python 逻辑 —— 网站导出的
`.tscpkg` 和桌面工具产出的完全一致，两边可以互换。

## 安装与运行

```bash
python -m pip install ".[web]"          # 或者 pip install -r requirements-web.txt
python -m webapp                        # 默认 http://0.0.0.0:8888
python -m webapp --port 9000            # 换端口
```

**第一个注册的账号自动成为管理员**，之后注册的都是普通用户。

> **服务器上只装 `requirements-web.txt`**（Flask + gunicorn）。根目录
> `requirements.txt` 里的 PySide6 / pygame 是桌面工具的依赖，headless Ubuntu 上
> 装了只会拖一堆系统库。这一点有测试兜底：网页版在**完全屏蔽这两个库**的环境里
> 也能正常启动、注册和建包。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TSCP_SECRET_KEY` | `dev-secret-change-me` | **上线必须改**，会话签名用 |
| `TSCP_DATA_DIR` | `webapp/data` | 数据库与剧情包存放目录 |
| `TSCP_HOST` | `0.0.0.0` | 监听地址 |
| `TSCP_PORT` | `8888` | 监听端口 |
| `TSCP_MAX_UPLOAD_MB` | `512` | 单个请求的大小上限 |
| `TSCP_SECURE_COOKIES` | 关 | 走 HTTPS 时设为 `1` |
| `TSCP_ALLOW_REGISTRATION` | 开 | 设为 `0` 关闭公开注册（管理员仍可建号） |
| `TSCP_FIRST_USER_IS_ADMIN` | 开 | 设 `0` 则第一个账号也是普通用户 |
| `TSCP_PROXY_HOPS` | `1` | 前置反向代理层数；直接对外时设 `0` |

### 生产部署（Ubuntu）

```bash
sudo ./deploy/install-ubuntu.sh          # 一键：系统用户 + venv + systemd + 8888
```

完整的 Ubuntu 流程（手动步骤、nginx 反代、更新、备份、排错表）见
[deploy/README.md](../deploy/README.md)。手动跑 gunicorn 长这样：

```bash
export TSCP_SECRET_KEY='...'
export TSCP_DATA_DIR=/var/lib/tscp-web
gunicorn "webapp:create_app()" --bind 0.0.0.0:8888 \
    --workers 2 --threads 4 --timeout 180
```

`--timeout` 要放宽：给剧情包塞一个大 FLAC 时打包会比较久。SQLite 已开启 WAL 并设了
busy timeout，所以多个 worker 同时读写不会报 `database is locked`。

## 目录结构

```text
webapp/
  __init__.py      create_app 工厂：数据库、蓝图、错误处理
  config.py        配置（全部可用环境变量覆盖）
  db.py            SQLite：用户、剧情包、管理员守卫所需的计数
  storage.py       剧情包文件读写，桥接到 PlotManager.model / tscp_player
  auth.py          注册、登录、改密码、login_required / admin_required
  api.py           用户端 API：剧情包、剧本、音乐、歌词、下载、音频流
  admin.py         管理员 API：账号管理、全站剧情包
  pages.py         HTML 路由
  app.py           python -m webapp 的入口
  templates/       base / index / editor / player / admin / error
  static/          style.css + app.js / auth.js / index.js / editor.js / player.js / admin.js
```

`webapp/data/` 是运行时目录（数据库、`plots/`、`cache/`），已在 `.gitignore` 中。

## 前端为什么没有构建步骤

全部是原生 ES 模块和 `fetch`，没有 npm、没有打包器：拷到服务器上就能跑。浏览器端
只需要一份 `.tscp` 解析器（`static/app.js` 里的 `parseScript`）和 LRC 解析器，
它们和 Python 版本保持同样的格式规则。

## 用户端能做什么

* 注册 / 登录 / 改密码 / 改显示名
* 新建剧情包、改名称与简介、删除
* 导入 `.tscp`（校验格式）或 `.tscps`（自动编译），在线编辑并保存，`Ctrl+S` 快捷保存
* 插入音乐：先选**纯音乐**还是**带歌词**；带歌词可以选现成的 `.lrc`，也可以选
  `lyrics.txt` 后**在浏览器里播放音乐、逐句按键录制时间**；颜色用一个取色器选
* 为每首音乐单独试听、改颜色、删除
* **下载** `.tscpkg`（和桌面工具产出的一致）
* **在线播放**：逐字节奏、`<s>` 等待、`<c>` 清屏、`<p>` 换音乐、歌词窗、
  **补充内容**（显示在剧情下方，清屏也不会抹掉）

歌词窗会按脚本自动选字体：汉字用宋体、英文用 Times New Roman 斜体、其他语言给出
各语言的候选字体；颜色取自剧情包里的 `COLOR`。

> 浏览器能对 FLAC 做随机跳转，所以网页版的歌词是跟着 `audio.currentTime` 走的 ——
> 跳转到任意位置歌词都还对得上。这一点比桌面版（pygame 无法 seek FLAC）更好。

### 关于补充内容

网页播放器认得 `A|颜色|秒数|base64文本`，行为和桌面播放器一致：出现在剧情下方、
不占剧情时间、按自己的时长消失、清屏会把它重新画出来而**不重置剩余时间**。

但**网页编辑器里的剧本框是纯文本**，所以补充内容在那里显示为原始的 `A|…` 一行，
不能像桌面工坊那样用对话框填。想结构化地编辑它，请用
[Studio](../Studio/README.md)（剧情工坊）。

## 管理员端能做什么

* 站点概览：用户数、管理员数、剧情包数、总占用空间
* 用户管理：新建（可指定管理员）、改角色、停用/启用、改显示名与邮箱、重置密码、删除
* 删除用户会连同他的剧情包一起清掉
* 全部剧情包：查看所有者与大小、下载、删除

两条安全约束写在 `admin.py` 里：**不能对自己执行降权/停用/删除**，也**不能动掉最后
一名管理员**，避免把自己锁在门外。

## API 速查

所有接口都在 `/api` 下，未登录返回 401，权限不足返回 403。

```text
POST   /api/auth/register        注册（第一个账号成为管理员）
POST   /api/auth/login           登录
POST   /api/auth/logout          退出
GET    /api/auth/me              当前用户 + 是否需要初始化
POST   /api/auth/password        改自己的密码
PATCH  /api/auth/profile         改自己的显示名与邮箱

GET    /api/plots                我的剧情包（管理员可加 ?all=1）
POST   /api/plots                新建
GET    /api/plots/<id>           详情（含剧本、音乐、角色）
PATCH  /api/plots/<id>           改名 / 改简介
DELETE /api/plots/<id>           删除
GET    /api/plots/<id>/download  下载 .tscpkg

GET    /api/plots/<id>/scripts/<文件名>    读取剧本原文
PUT    /api/plots/<id>/scripts/<文件名>    保存剧本
POST   /api/plots/<id>/scripts             上传导入（.tscp 或 .tscps）
DELETE /api/plots/<id>/scripts/<文件名>    删除剧本

POST   /api/plots/<id>/music               插入音乐（multipart）
PATCH  /api/plots/<id>/music/<简称>        改类型 / 歌词 / 颜色
DELETE /api/plots/<id>/music/<简称>        删除音乐
GET    /api/plots/<id>/audio/<简称>        音频流（支持 Range，用于播放与跳转）
GET    /api/plots/<id>/lyrics/<简称>        该曲目的 LRC 文本

GET    /api/admin/overview       站点统计
GET    /api/admin/users          用户列表
POST   /api/admin/users          新建用户
PATCH  /api/admin/users/<id>     改角色/状态/资料/密码
DELETE /api/admin/users/<id>     删除用户（连带剧情包）
GET    /api/admin/plots          全站剧情包
GET    /api/admin/plots/<id>     任意剧情包详情
DELETE /api/admin/plots/<id>     删除任意剧情包
```

## 测试

```powershell
python -m pytest tests/test_webapp.py
```

覆盖注册登录、权限隔离（别人的剧情包一律 404，避免被枚举）、剧本导入与保存、
带歌词音乐的三种写法、音频 Range 请求、下载包能被桌面加载器读回、管理员守卫。
