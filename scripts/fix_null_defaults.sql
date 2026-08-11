-- 修复数据库恢复后缺少默认值的常见问题
-- 执行: PGPASSWORD=xxx psql -h 127.0.0.1 -U hermes -d hermes -f fix_null_defaults.sql

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

-- 3. mode/类型列：补 NULL → 空字符串 → 加默认值
UPDATE "conversations" SET "channel_mode" = '' WHERE "channel_mode" IS NULL;
ALTER TABLE "conversations" ALTER COLUMN "channel_mode" SET DEFAULT '';
UPDATE "conversations" SET "session_mode" = '' WHERE "session_mode" IS NULL;
ALTER TABLE "conversations" ALTER COLUMN "session_mode" SET DEFAULT '';

-- 4. 所有其他表的 timestamp 列（通用扫描）
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name IN ('created_at', 'updated_at', 'ts')
          AND data_type LIKE '%timestamp%'
          AND is_nullable = 'YES'
          AND (column_default IS NULL OR column_default = '')
    LOOP
        EXECUTE format('UPDATE "%I" SET "%I" = NOW() WHERE "%I" IS NULL;', rec.table_name, rec.column_name, rec.column_name);
        EXECUTE format('ALTER TABLE "%I" ALTER COLUMN "%I" SET DEFAULT NOW();', rec.table_name, rec.column_name);
    END LOOP;
END $$;
