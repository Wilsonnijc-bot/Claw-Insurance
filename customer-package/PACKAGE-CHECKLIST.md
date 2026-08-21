# 客户安装包交付清单（内部使用）

这个目录是客户分发骨架，不是源码副本。客户运行时只从 Docker Hub 拉取已经发布的前端和后端镜像。

## 每次交付前

1. 确认 `CLAW_VERSION` 对应的前端、后端多架构镜像已经发布。
2. 把整个 `customer-package/` 复制到仓库外的临时交付目录。
3. 将 `.env.example` 复制为 `.env`。
4. 将三个 `*.example.json` 复制为对应的 `config.json`、`supabase.json`、`google.json`。
5. 在 `config.json` 和 `supabase.json` 中填写客户专属虚拟 Key、HTTPS 服务地址和统一模型别名。
6. 将客户操作系统/芯片对应的已签名 CDP Helper 放到 `cdp-helper/`。
7. 将自行编写的用户手册放进 `docs/`。
8. 在干净电脑上执行安装脚本，验证登录、WhatsApp、AI 草稿、产品查询和录音。
9. 删除测试产生的 WhatsApp 登录状态、客户历史和运行数据，再制作 ZIP。
10. 为 ZIP 生成 SHA-256 校验值，通过安全渠道交付客户虚拟 Key。

## CDP Helper 目标文件名

- Windows：`cdp-helper/nanobot-cdp-helper.exe`
- macOS/Linux：`cdp-helper/nanobot-cdp-helper`

PyInstaller 不能跨平台构建。Windows、macOS Intel、macOS Apple Silicon、Linux amd64/arm64 需要分别构建对应二进制。

## 禁止放入客户安装包

- 源代码和 `.git/`
- LiteLLM master key
- Moonshot/Kimi 上游 Key
- Supabase service-role
- Google service account JSON
- PostgreSQL/SSH 密码
- 其他客户的虚拟 Key、记忆、WhatsApp 登录状态或运行数据

## 客户收到的内容

- `compose.yml`
- `.env`
- `config.json`、`supabase.json`、`google.json`
- 对应平台的 CDP Helper
- 安装、启动、停止和日志脚本
- `docs/` 中由维护者放入的用户手册

