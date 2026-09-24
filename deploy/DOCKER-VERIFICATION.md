# 本地 Docker 验证记录（2026-09-24）

环境：Apple Silicon Mac、Docker Desktop 4.92.0、Docker Engine 29.8.0、Compose 5.5.1。
本次应用镜像为 `linux/arm64`；没有发布镜像或改动线上服务器。

## 已执行

1. 启动已有 Homebrew PostgreSQL 集群，没有重新初始化。
2. 对本地两个开发库做只读备份。Fanmade 开发副本原 schema 较旧，在备份后运行当前代码的
   `go run ./cmd/server -migrate`，增加现有封面/活动时间迁移；SSO 没有待执行迁移。
3. SSO 的 34 项原生测试通过；Fanmade 的 Go 全套测试通过，数据库用临时 schema 并自动清理。
4. 构建 SSO 和 Fanmade 镜像，使用 SSO 仓库的 PostgreSQL 配置在 Docker Desktop 中恢复两套服务。
5. 第一次恢复逐表比较账号/业务数据指纹，并检查签名密钥和全部音频/TJA 哈希。
6. 在测试副本创建一个明确标记的账号、谱面、封面和成绩，验证 HTTP OIDC + PKCE、上传、WebP 转换、
   音频/TJA 下载、游戏登录、成绩写入/幂等、归属关系和退出。
7. 使用真实 Chrome 浏览器确认 SSO 登录页正常渲染、登录成功并显示账号概览，未发现页面脚本错误。
8. 停止测试应用写入，导出数据库、文件和私有配置，再恢复第一套服务。
9. 停止第一套环境，使用完全独立的新项目、网络和数据卷恢复备份，再执行数据校验及登录/下载/成绩验证。

## 恢复核对

| 项目 | 原生 → 第一次 Docker | Docker → 全新 Docker（含测试样本） |
| --- | ---: | ---: |
| SSO 账号 | 35 | 36 |
| Fanmade 用户 ID | 35 | 36 |
| 谱面 / 版本 | 29 / 29 | 30 / 30 |
| 成绩 | 3 | 4 |
| WebP 封面 | 0 | 1 |
| 音频 / TJA 文件合计 | 58 | 60 |

这些是本地开发快照，不是生产数据数量。恢复时上述各表的完整行指纹一致，文件逐字节哈希一致，
SSO 签名密钥一致。两个数据库角色均非超级用户，且没有对另一个数据库的 CONNECT 权限。
第二次恢复后，原测试账号仍可网页登录和游戏登录，原成绩/谱面归属不变，封面与文件下载哈希一致。

## 当前留存

- 运行中：`ourtaiko-lab-restored-sso`（SSO、PostgreSQL）和 `ourtaiko-lab-restored-fanmade`（API）。
- 第一次 `ourtaiko-lab-first-*` 容器已停止，卷保留用于对照。
- 原始可用备份：`.data/docker-lab/bundles/raw-v4`。
- 已验证的 Docker 备份：`.data/docker-lab/bundles/docker-backup`。
- 测试样本及其私有凭据留在独立演练环境/忽略目录；没有修改原开发账号密码或把测试样本写回原生库。
- 原生数据库仍保留。升级前的两个数据库 dump 单独保存在 `.data/docker-lab/pre-schema-upgrade/`。
  早期不完整的诊断备份已清理；恢复演练使用 `raw-v4` 或 `docker-backup`。

## 边界与发现

- 原生 FFmpeg 8.1.2 依赖 `libx265.216.dylib`，而 Homebrew 当前默认 x265 为 4.3（217）。
  本次使用仍保留的 `/opt/homebrew/Cellar/x265/4.2/lib`，仅通过测试进程的 `DYLD_LIBRARY_PATH` 指定。
  没有修改系统链接或重装软件；Docker 镜像自身的 FFmpeg/cwebp 已通过真实上传验证。
- 初始跨系统指纹差异来自时间戳输出时区/排序，校验已统一 UTC 并在 Python 中排序。
- 本演练只部署后端和账号页面，没有部署 Fanmade 前端、生产 HTTPS 代理或真实邮件发送。
- 本地脚本故意限定开发库和新测试目标。真正生产迁移仍需目标架构镜像、加密备份传输、停写协调、
  正式域名/证书/SMTP 配置，以及切换与回退步骤，见 [Docker 文档](DOCKER.md)。
