-- Run this once in the Supabase project's SQL editor.
-- Single-row table: id is pinned to 1, every scrape upserts that one row.

create table public.olt_snapshot (
  id         integer primary key,
  scraped_at timestamptz,
  ok         boolean,
  error      text,
  pages      jsonb,
  constraint olt_snapshot_valid_id check (id in (1, 2))
);

-- RLS on with zero policies = nobody can read or write this table through
-- the public API except the service_role key, which bypasses RLS entirely.
-- That's the whole privacy model: no policy ever needs to be written.
alter table public.olt_snapshot enable row level security;
