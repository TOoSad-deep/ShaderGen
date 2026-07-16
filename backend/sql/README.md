# Backend SQL

这里记录配置 `DATABASE_URL` 时，Backend 启动会按文件名顺序执行的业务过程账本 SQL schema。没有 `DATABASE_URL` 时不会执行本目录 SQL；LangGraph Checkpointer/Store 的框架表不由这些文件创建，首次初始化方式见 `backend/README.md`。

`backend.sql` 是显式 wheel 资源包，以避免“作为 `backend` 的数据目录却又被 Python 解释为隐式 namespace package”的歧义。`__init__.py` 不得执行 DDL 或导入数据库客户端；实际初始化仍由 `backend/app/database/session.py` 负责。

SQL 文件必须幂等且按文件名保持稳定顺序。新脚本必须匹配 `*.sql` package-data 规则，确保源码树与 wheel 一致。当前不引入迁移框架；等出现多环境版本迁移、回滚、数据迁移或部署顺序问题时，再引入正式迁移工具。
