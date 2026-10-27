-- datawazi metadata in Supabase Postgres. Safe to run again: everything is "if not exists" or replaced.
--
-- The schema is not exposed through Supabase's public Data API, so browsers cannot query it.
-- The API server runs each request as the signed-in user (role "authenticated" with their JWT
-- claims), so the row-level security below decides what that user can see and change, even if
-- the application code missed a check. {schema} is filled in by db.py (validated name).

create schema if not exists {schema};
revoke all on schema {schema} from public, anon;
grant usage on schema {schema} to authenticated;

create table if not exists {schema}.workspaces (
  id text primary key,
  name text not null check (length(name) between 1 and 60),
  created_at timestamptz not null default now()
);
-- Workspace settings: require_mfa, allowed email domains, export and deletion dates.
alter table {schema}.workspaces add column if not exists settings jsonb not null default '{}'::jsonb;

create table if not exists {schema}.members (
  workspace_id text not null references {schema}.workspaces (id) on delete cascade,
  user_id text not null,
  email text not null,
  role text not null check (role in ('viewer', 'creator', 'admin')),
  joined_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index if not exists members_by_user on {schema}.members (user_id);

-- Invite links: only a hash of the token is kept, so a database leak cannot be used to join.
create table if not exists {schema}.invites (
  id text primary key,
  workspace_id text not null references {schema}.workspaces (id) on delete cascade,
  email text not null,
  role text not null check (role in ('viewer', 'creator', 'admin')),
  token_hash text not null unique,
  invited_by text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  unique (workspace_id, email)
);

-- Datasets and dashboards: their JSON records (sources, steps, model, pages). Data files live elsewhere.
create table if not exists {schema}.records (
  kind text not null check (kind in ('dataset', 'dashboard')),
  id text not null,
  workspace_id text not null references {schema}.workspaces (id) on delete cascade,
  record jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (kind, id)
);
create index if not exists records_by_workspace on {schema}.records (workspace_id, kind);
-- Workspace preferences (custom AI instructions) are records too.
alter table {schema}.records drop constraint if exists records_kind_check;
alter table {schema}.records add constraint records_kind_check check (kind in ('dataset', 'dashboard', 'preferences'));

-- The signed-in user's role in a workspace, or null. Security definer so policies can use it
-- without the members policy calling itself.
create or replace function {schema}.my_role(ws text) returns text
language sql stable security definer set search_path = '' as $$
  select m.role from {schema}.members m where m.workspace_id = ws and m.user_id = (select auth.uid())::text
$$;

alter table {schema}.workspaces enable row level security;
alter table {schema}.members enable row level security;
alter table {schema}.invites enable row level security;
alter table {schema}.records enable row level security;

drop policy if exists workspaces_read on {schema}.workspaces;
create policy workspaces_read on {schema}.workspaces for select to authenticated
  using ({schema}.my_role(id) is not null);
drop policy if exists workspaces_rename on {schema}.workspaces;
create policy workspaces_rename on {schema}.workspaces for update to authenticated
  using ({schema}.my_role(id) = 'admin') with check ({schema}.my_role(id) = 'admin');

drop policy if exists members_read on {schema}.members;
create policy members_read on {schema}.members for select to authenticated
  using ({schema}.my_role(workspace_id) is not null);
drop policy if exists members_change on {schema}.members;
create policy members_change on {schema}.members for update to authenticated
  using ({schema}.my_role(workspace_id) = 'admin') with check ({schema}.my_role(workspace_id) = 'admin');
drop policy if exists members_remove on {schema}.members;
-- Admins remove members; anyone can remove themselves (leaving, or deleting their account).
-- The app keeps every workspace's last admin.
create policy members_remove on {schema}.members for delete to authenticated
  using ({schema}.my_role(workspace_id) = 'admin' or user_id = (select auth.uid())::text);

drop policy if exists invites_admin on {schema}.invites;
create policy invites_admin on {schema}.invites for all to authenticated
  using ({schema}.my_role(workspace_id) = 'admin') with check ({schema}.my_role(workspace_id) = 'admin');

drop policy if exists records_read on {schema}.records;
create policy records_read on {schema}.records for select to authenticated
  using ({schema}.my_role(workspace_id) is not null);
drop policy if exists records_write on {schema}.records;
create policy records_write on {schema}.records for insert to authenticated
  with check ({schema}.my_role(workspace_id) in ('creator', 'admin'));
drop policy if exists records_update on {schema}.records;
create policy records_update on {schema}.records for update to authenticated
  using ({schema}.my_role(workspace_id) in ('creator', 'admin'))
  with check ({schema}.my_role(workspace_id) in ('creator', 'admin'));
drop policy if exists records_delete on {schema}.records;
create policy records_delete on {schema}.records for delete to authenticated
  using ({schema}.my_role(workspace_id) in ('creator', 'admin'));

-- AI usage: one row per AI call (tokens, model, feature, person), for credits and cost. Written by the server
-- as the service; workspace admins can read their workspace's rows. Credits are worked out when read.
create table if not exists {schema}.usage_events (
  id bigserial primary key,
  workspace_id text not null references {schema}.workspaces (id) on delete cascade,
  user_id text,
  email text,
  at timestamptz not null default now(),
  feature text not null,
  model text,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  cached_tokens integer not null default 0,
  ms integer not null default 0
);
create index if not exists usage_by_workspace on {schema}.usage_events (workspace_id, at);
alter table {schema}.usage_events enable row level security;
drop policy if exists usage_read on {schema}.usage_events;
create policy usage_read on {schema}.usage_events for select to authenticated
  using ({schema}.my_role(workspace_id) = 'admin');

revoke all on all tables in schema {schema} from public, anon;
grant select, insert, update, delete on all tables in schema {schema} to authenticated;

-- A new user's first workspace: they become its admin. The email comes from the verified token.
create or replace function {schema}.create_workspace(p_id text, p_name text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  insert into {schema}.workspaces (id, name) values (p_id, p_name);
  insert into {schema}.members (workspace_id, user_id, email, role)
  values (p_id, auth.uid()::text, lower(coalesce(auth.jwt() ->> 'email', '')), 'admin');
end $$;

-- What an invite link is for, shown before joining. Anyone signed in who holds the token may ask.
create or replace function {schema}.invite_info(p_hash text)
returns table (workspace text, email text, role text, invited_by text, expires_at timestamptz)
language sql stable security definer set search_path = '' as $$
  select w.name, i.email, i.role, i.invited_by, i.expires_at
  from {schema}.invites i join {schema}.workspaces w on w.id = i.workspace_id
  where i.token_hash = p_hash
$$;

-- Joining with an invite: only the invited email address, only before it expires, only once.
create or replace function {schema}.accept_invite(p_hash text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  inv {schema}.invites;
  me text := lower(coalesce(auth.jwt() ->> 'email', ''));
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  select * into inv from {schema}.invites where token_hash = p_hash for update;
  if not found then raise exception 'invite not found' using errcode = 'DW404'; end if;
  if inv.expires_at < now() then raise exception 'invite expired' using errcode = 'DW410'; end if;
  if inv.email <> me then raise exception 'invite is for %', inv.email using errcode = 'DW403'; end if;
  insert into {schema}.members (workspace_id, user_id, email, role)
  values (inv.workspace_id, auth.uid()::text, me, inv.role)
  on conflict (workspace_id, user_id) do nothing;
  delete from {schema}.invites where id = inv.id;
  return inv.workspace_id;
end $$;

revoke all on function {schema}.my_role(text), {schema}.create_workspace(text, text),
  {schema}.invite_info(text), {schema}.accept_invite(text) from public, anon;
grant execute on function {schema}.my_role(text), {schema}.create_workspace(text, text),
  {schema}.invite_info(text), {schema}.accept_invite(text) to authenticated;
