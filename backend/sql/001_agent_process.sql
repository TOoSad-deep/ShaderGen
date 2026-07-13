-- ShaderGen Agent 过程数据 schema。
--
-- 范围：
--   - 存储产品级 Agent 过程数据：运行记录、阶段事件、运行内诊断日志、产物元数据、模型调用摘要、评估摘要和失败摘要。
--   - 不在这里存储普通应用日志。请求日志、调试日志、堆栈和基础设施诊断信息继续使用 Python logging。
--   - 不在这些表里存储密钥、API key、模型供应商完整原始响应或 base64 图片字节；只存文件或对象引用及其元数据。
--   - 模型 reasoning_content 作为独立字段保存，仅用于受控调试和评估。
--
-- ponytail: 在需要跨环境有序迁移前，手写 SQL 已足够。

CREATE TABLE IF NOT EXISTS agent_runs (
    id uuid PRIMARY KEY,
    project_id uuid,
    status text NOT NULL,
    glsl_model_name text,
    vision_model_name text,
    input jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT agent_runs_status_check
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    CONSTRAINT agent_runs_finished_after_started_check
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT agent_runs_error_matches_status_check
        CHECK (
            (status = 'failed' AND error IS NOT NULL)
            OR (status <> 'failed' AND error IS NULL)
        )
);

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS project_id uuid;

COMMENT ON TABLE agent_runs IS
    '一次用户可见的 Agent 执行。它是持久化过程账本的根记录，不是应用日志表。';
COMMENT ON COLUMN agent_runs.id IS
    '应用生成的运行 id。API 响应、前端轮询和过程事件应使用同一个 id。';
COMMENT ON COLUMN agent_runs.project_id IS
    'Shader 项目的连续性和 Memory 隔离 id；NULL 表示历史记录或非项目运行。';
COMMENT ON COLUMN agent_runs.status IS
    '当前运行生命周期状态：pending、running、succeeded 或 failed。';
COMMENT ON COLUMN agent_runs.glsl_model_name IS
    '本次运行用于生成 GLSL 的模型型号。NULL 表示尚未进入 GLSL 生成阶段，或该运行不需要该阶段。';
COMMENT ON COLUMN agent_runs.vision_model_name IS
    '本次运行用于视觉分析的模型型号。NULL 表示尚未进入视觉分析阶段，或该运行不需要该阶段。';
COMMENT ON COLUMN agent_runs.input IS
    '规范化后的请求快照，例如 idea、需求、参考图 id、约束和测试规划。不要存储 base64 原始图片或密钥。';
COMMENT ON COLUMN agent_runs.result IS
    '最终输出摘要，例如 GLSL 产物 id、评分摘要、评审摘要和被选中候选结果的元数据。';
COMMENT ON COLUMN agent_runs.error IS
    '失败运行的用户安全失败摘要。完整堆栈留在应用日志中，不写入这里。';
COMMENT ON COLUMN agent_runs.started_at IS
    '运行记录创建或执行开始的时间。';
COMMENT ON COLUMN agent_runs.finished_at IS
    '执行进入 succeeded 或 failed 的时间。NULL 表示运行仍在执行或等待执行。';
COMMENT ON CONSTRAINT agent_runs_status_check ON agent_runs IS
    '在产品需要更多状态前，显式限制运行生命周期状态。';
COMMENT ON CONSTRAINT agent_runs_finished_after_started_check ON agent_runs IS
    '防止出现结束时间早于开始时间的无效数据。';
COMMENT ON CONSTRAINT agent_runs_error_matches_status_check ON agent_runs IS
    '只允许失败运行保存错误摘要，避免成功记录含义混乱。';

CREATE TABLE IF NOT EXISTS agent_events (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq integer NOT NULL,
    stage text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    reasoning_content text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_events_seq_positive_check CHECK (seq > 0),
    CONSTRAINT agent_events_run_seq_unique UNIQUE (run_id, seq)
);

ALTER TABLE agent_events
    ADD COLUMN IF NOT EXISTS reasoning_content text;

COMMENT ON TABLE agent_events IS
    'Agent 运行的追加式业务事件。用于存储审计、回放分析和 UI 进度所需的阶段输入、输出和决策。';
COMMENT ON COLUMN agent_events.id IS
    '数据库本地事件 id。单次运行内的稳定排序使用 run_id 加 seq。';
COMMENT ON COLUMN agent_events.run_id IS
    '所属 Agent 运行。事件随运行记录删除，便于本地开发清理。';
COMMENT ON COLUMN agent_events.seq IS
    '单次运行内的单调递增事件序号，从 1 开始，由应用分配。';
COMMENT ON COLUMN agent_events.stage IS
    '流水线阶段名称，例如 routing、agent、intent、dsl、render、eval、search、review 或 store。';
COMMENT ON COLUMN agent_events.event_type IS
    '业务事件名称，例如 started、completed、failed、model_call、artifact_created、score_recorded 或 review_recorded。';
COMMENT ON COLUMN agent_events.payload IS
    '事件专用 JSON 载荷。保持摘要化且安全：id、hash、指标、Prompt 版本、token 数、延迟和小型结构化输出。';
COMMENT ON COLUMN agent_events.reasoning_content IS
    '模型返回的 reasoning_content 思维链。仅用于受控调试和评估，不进入用户响应；可能包含大段文本，生产环境开启前需确认数据保留策略。';
COMMENT ON COLUMN agent_events.created_at IS
    '事件写入时间。';
COMMENT ON CONSTRAINT agent_events_seq_positive_check ON agent_events IS
    '要求便于阅读的事件序号从 1 开始。';
COMMENT ON CONSTRAINT agent_events_run_seq_unique ON agent_events IS
    '防止单次运行内出现重复事件序号，并支持按 seq 加载稳定事件时间线。';

CREATE INDEX IF NOT EXISTS agent_runs_status_started_at_idx
    ON agent_runs (status, started_at DESC);

CREATE INDEX IF NOT EXISTS agent_runs_project_started_at_idx
    ON agent_runs (project_id, started_at DESC);

COMMENT ON INDEX agent_runs_status_started_at_idx IS
    '支持后端或管理界面按状态查看最近运行记录。';

CREATE TABLE IF NOT EXISTS agent_logs (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_seq integer,
    level text NOT NULL,
    source text NOT NULL,
    message text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_logs_level_check
        CHECK (level IN ('debug', 'info', 'warning', 'error', 'critical')),
    CONSTRAINT agent_logs_run_event_fk
        FOREIGN KEY (run_id, event_seq) REFERENCES agent_events(run_id, seq)
);

COMMENT ON TABLE agent_logs IS
    '一次 Agent 运行内可查询的诊断日志。只保存与业务运行相关且安全的日志摘要，不替代 Python logging，也不接普通 debug logging。';
COMMENT ON COLUMN agent_logs.id IS
    '数据库本地日志 id。';
COMMENT ON COLUMN agent_logs.run_id IS
    '所属 Agent 运行。日志随运行记录删除，便于本地开发清理。';
COMMENT ON COLUMN agent_logs.event_seq IS
    '可选的关联事件序号。用于把日志挂到同一次运行内的某个阶段事件。';
COMMENT ON COLUMN agent_logs.level IS
    '日志级别：debug、info、warning、error 或 critical。debug 只允许运行内关键调试摘要，不接普通 debug logging。';
COMMENT ON COLUMN agent_logs.source IS
    '日志来源，例如 backend.shader、agent.model 或 shaderforge.eval。';
COMMENT ON COLUMN agent_logs.message IS
    '人可读日志摘要。不要写入完整堆栈、密钥、原始供应商响应或大段模型输出。';
COMMENT ON COLUMN agent_logs.context IS
    '安全的结构化上下文，例如 request_id、model、latency_ms、token 数、artifact_id 或错误分类。';
COMMENT ON COLUMN agent_logs.created_at IS
    '日志写入时间。';
COMMENT ON CONSTRAINT agent_logs_level_check ON agent_logs IS
    '限制日志级别，避免同义状态值扩散。';
COMMENT ON CONSTRAINT agent_logs_run_event_fk ON agent_logs IS
    '确保 event_seq 只能指向同一个 run_id 下的事件，避免日志挂错运行。';

CREATE INDEX IF NOT EXISTS agent_logs_run_created_at_idx
    ON agent_logs (run_id, created_at);

COMMENT ON INDEX agent_logs_run_created_at_idx IS
    '支持按时间顺序查看一次运行内的诊断日志。';

CREATE INDEX IF NOT EXISTS agent_logs_level_created_at_idx
    ON agent_logs (level, created_at DESC);

COMMENT ON INDEX agent_logs_level_created_at_idx IS
    '支持按日志级别查看最近的 warning、error 或 critical 记录。';
