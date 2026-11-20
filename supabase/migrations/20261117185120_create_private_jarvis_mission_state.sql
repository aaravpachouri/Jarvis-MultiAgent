create table if not exists public.jarvis_missions (
  id text primary key,
  owner_id text not null,
  status text not null check (status in ('queued','running','completed','failed','cancelled','interrupted')),
  title text not null default '',
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now(),
  summary jsonb not null default '{}'::jsonb,
  constraint jarvis_missions_summary_size check (pg_column_size(summary) <= 262144)
);
create index if not exists jarvis_missions_owner_created_idx
  on public.jarvis_missions (owner_id, created_at desc);
create index if not exists jarvis_missions_status_updated_idx
  on public.jarvis_missions (status, updated_at desc);
alter table public.jarvis_missions enable row level security;
revoke all on table public.jarvis_missions from anon, authenticated;
grant all on table public.jarvis_missions to service_role;
comment on table public.jarvis_missions is
  'Private server-side durable lifecycle records for JARVIS missions. Access only through authenticated JARVIS API server credentials.';
