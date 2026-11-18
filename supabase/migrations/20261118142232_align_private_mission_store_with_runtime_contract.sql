create schema if not exists jarvis_private;
drop table if exists jarvis_private.jarvis_missions;
create table jarvis_private.jarvis_missions (
  task_id text primary key,
  agent_id text not null,
  status text not null check (status in ('queued','running','succeeded','failed','partial','timed_out','cancelled','interrupted')),
  prompt text not null default '',
  accepted_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  result_json jsonb,
  error text,
  updated_at timestamptz not null default now(),
  constraint jarvis_mission_prompt_size check (char_length(prompt) <= 500),
  constraint jarvis_mission_result_size check (result_json is null or pg_column_size(result_json) <= 262144)
);
create index jarvis_missions_status_updated_idx
  on jarvis_private.jarvis_missions(status, updated_at desc);
revoke all on schema jarvis_private from public, anon, authenticated;
grant usage on schema jarvis_private to service_role;
revoke all on table jarvis_private.jarvis_missions from public, anon, authenticated;
grant all on table jarvis_private.jarvis_missions to service_role;
alter table jarvis_private.jarvis_missions enable row level security;
comment on table jarvis_private.jarvis_missions is
  'Private durable JARVIS dashboard mission records. Accessible only to the backend service role.';
