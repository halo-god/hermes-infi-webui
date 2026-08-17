-- 修复数据库恢复后缺少默认值的常见问题
-- 执行: PGPASSWORD=xxx psql -h 127.0.0.1 -U hermes -d hermes -f fix_null_defaults.sql
-- 整体包在一个事务里：中途失败不留半完成状态。
BEGIN;

-- 1. 时间戳列：补 NULL → now() → 加默认值+NOT NULL
UPDATE "users" SET "created_at" = NOW() WHERE "created_at" IS NULL;
UPDATE "users" SET "updated_at" = NOW() WHERE "updated_at" IS NULL;
ALTER TABLE "users" ALTER COLUMN "created_at" SET DEFAULT NOW();
ALTER TABLE "users" ALTER COLUMN "created_at" SET NOT NULL;
ALTER TABLE "users" ALTER COLUMN "updated_at" SET DEFAULT NOW();
ALTER TABLE "users" ALTER COLUMN "updated_at" SET NOT NULL;

-- 2. boolean 列：补 NULL → false → 加默认值
UPDATE "conversations" SET "pinned" = false WHERE "pinned" IS NULL;
ALTER TABLE "conversations" ALTER COLUMN "pinned" SET DEFAULT false;
UPDATE "conversations" SET "is_channel" = false WHERE "is_channel" IS NULL;
ALTER TABLE "conversations" ALTER COLUMN "is_channel" SET DEFAULT false;

-- 3. channel_mode：合法值是 'mention'/'always'（模型 default='mention'）。
--    空字符串会绕过代码里的 default 分支（=== 'always' 判断失败当 mention 用，
--    但语义被破坏），必须补真实枚举值。
UPDATE "conversations" SET "channel_mode" = 'mention' WHERE "channel_mode" IS NULL OR "channel_mode" = '';
ALTER TABLE "conversations" ALTER COLUMN "channel_mode" SET DEFAULT 'mention';

--    session_mode 模型本来就是 nullable（NULL = 未设置，走默认行为），
--    不做任何填充；空串反而是非法状态，修复历史空串为 NULL。
UPDATE "conversations" SET "session_mode" = NULL WHERE "session_mode" = '';

-- 4. 所有其他表的 timestamp 列（通用扫描）
--    只处理 created_at/updated_at——列名 'ts' 已排除：某些表的 ts 是
--    业务时间戳（如 token 使用时间），NULL 表示"未知"，盲补 NOW() 会污染数据。
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name IN ('created_at', 'updated_at')
          AND data_type LIKE '%timestamp%'
          AND is_nullable = 'YES'
          AND (column_default IS NULL OR column_default = '')
    LOOP
        EXECUTE format('UPDATE "%I" SET "%I" = NOW() WHERE "%I" IS NULL;', rec.table_name, rec.column_name, rec.column_name);
        EXECUTE format('ALTER TABLE "%I" ALTER COLUMN "%I" SET DEFAULT NOW();', rec.table_name, rec.column_name);
    END LOOP;
END $$;

COMMIT;
