create table if not exists jarvis_private.api_credentials (
  id smallint primary key check (id = 1),
  token_sha256 text not null check (token_sha256 ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz not null default now()
);
alter table jarvis_private.api_credentials enable row level security;
revoke all on table jarvis_private.api_credentials from public, anon, authenticated;
grant all on table jarvis_private.api_credentials to service_role;
revoke all on schema jarvis_private from public, anon, authenticated;
grant usage on schema jarvis_private to service_role;
grant all on table jarvis_private.jarvis_missions to service_role;
grant all on table jarvis_private.api_credentials to service_role;
alter role authenticator set pgrst.db_schemas = 'public, storage, graphql_public, jarvis_private';
notify pgrst, 'reload config';
