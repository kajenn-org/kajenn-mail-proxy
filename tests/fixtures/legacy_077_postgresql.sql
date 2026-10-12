-- Original 0.7.7 PostgreSQL DDL; schema only.

CREATE TABLE IF NOT EXISTS tenants (
    "id" TEXT PRIMARY KEY,
    "name" TEXT,
    "client_auth" TEXT,
    "client_base_url" TEXT,
    "client_sync_path" TEXT,
    "client_attachment_path" TEXT,
    "rate_limits" TEXT,
    "large_file_config" TEXT,
    "active" INTEGER DEFAULT 1,
    "suspended_batches" TEXT,
    "api_key_hash" TEXT,
    "api_key_expires_at" TIMESTAMP,
    "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    "id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "host" TEXT NOT NULL,
    "port" INTEGER NOT NULL,
    "user" TEXT,
    "password" TEXT,
    "ttl" INTEGER DEFAULT 300,
    "limit_per_minute" INTEGER,
    "limit_per_hour" INTEGER,
    "limit_per_day" INTEGER,
    "limit_behavior" TEXT,
    "use_tls" INTEGER,
    "batch_size" INTEGER,
    "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "is_pec_account" INTEGER DEFAULT 0,
    "imap_host" TEXT,
    "imap_port" INTEGER DEFAULT 993,
    "imap_user" TEXT,
    "imap_password" TEXT,
    "imap_folder" TEXT DEFAULT 'INBOX',
    "imap_last_uid" INTEGER,
    "imap_last_sync" TIMESTAMP,
    "imap_uidvalidity" INTEGER,
    FOREIGN KEY ("tenant_id") REFERENCES tenants("id")
,
    PRIMARY KEY ("tenant_id", "id")
);

CREATE TABLE IF NOT EXISTS messages (
    "pk" TEXT PRIMARY KEY,
    "id" TEXT NOT NULL,
    "tenant_id" TEXT NOT NULL,
    "account_id" TEXT,
    "priority" INTEGER NOT NULL DEFAULT 2,
    "payload" TEXT NOT NULL,
    "batch_code" TEXT,
    "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "deferred_ts" INTEGER,
    "smtp_ts" INTEGER,
    "is_pec" INTEGER DEFAULT 0
,
    UNIQUE ("tenant_id", "id")
);

CREATE TABLE IF NOT EXISTS message_events (
    "id" SERIAL PRIMARY KEY,
    "message_pk" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "event_ts" INTEGER NOT NULL,
    "description" TEXT,
    "metadata" TEXT,
    "reported_ts" INTEGER
);

CREATE TABLE IF NOT EXISTS send_log (
    "account_id" TEXT,
    "timestamp" INTEGER
);

CREATE TABLE IF NOT EXISTS command_log (
    "id" SERIAL PRIMARY KEY,
    "command_ts" INTEGER NOT NULL,
    "endpoint" TEXT NOT NULL,
    "tenant_id" TEXT,
    "payload" TEXT NOT NULL,
    "response_status" INTEGER,
    "response_body" TEXT
);

CREATE TABLE IF NOT EXISTS instance_config (
    "key" TEXT PRIMARY KEY,
    "value" TEXT NOT NULL,
    "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instance (
    "id" SERIAL PRIMARY KEY,
    "name" TEXT DEFAULT 'mail-proxy',
    "api_token" TEXT,
    "bounce_enabled" INTEGER DEFAULT 0,
    "bounce_imap_host" TEXT,
    "bounce_imap_port" INTEGER DEFAULT 993,
    "bounce_imap_user" TEXT,
    "bounce_imap_password" TEXT,
    "bounce_imap_folder" TEXT DEFAULT 'INBOX',
    "bounce_imap_ssl" INTEGER DEFAULT 1,
    "bounce_poll_interval" INTEGER DEFAULT 60,
    "bounce_return_path" TEXT,
    "bounce_last_uid" INTEGER,
    "bounce_last_sync" TIMESTAMP,
    "bounce_uidvalidity" INTEGER,
    "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
