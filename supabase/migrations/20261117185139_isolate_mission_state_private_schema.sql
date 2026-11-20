create schema if not exists jarvis_private;
revoke all on schema jarvis_private from public, anon, authenticated;
grant usage on schema jarvis_private to service_role;
alter table public.jarvis_missions set schema jarvis_private;
grant all on table jarvis_private.jarvis_missions to service_role;
revoke all on table jarvis_private.jarvis_missions from public, anon, authenticated;
