# daily-learn

每日在线学习脚本。Cron 运行时路径为 `/root/.hermes/scripts/daily-learn/daily_learn.sh`，源码由 `/mnt/d/HermesProject/scripts/daily-learn/` 管理并通过 deploy 发布。

## 环境变量

脚本会读取 `/root/.hermes/.env`，并要求：

- `KT_DB_URL`
- `LITELLM_MASTER_KEY`

不要在脚本中硬编码数据库连接串或密钥。
