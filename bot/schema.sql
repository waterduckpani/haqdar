-- Haqdar intake schema. Run this in the Supabase SQL editor.

-- One row per worker (Telegram chat). Holds the live intake state and the profile
-- being built up across the recording + follow-up rounds.
create table if not exists sessions (
    chat_id         bigint primary key,
    state           text not null default 'idle',
    partial_profile jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- One row per finished profile (kept as a history; a worker can complete many).
create table if not exists profiles (
    id         bigserial primary key,
    chat_id    bigint not null,
    profile    jsonb not null,
    created_at timestamptz not null default now()
);

create index if not exists profiles_chat_id_idx on profiles (chat_id);
