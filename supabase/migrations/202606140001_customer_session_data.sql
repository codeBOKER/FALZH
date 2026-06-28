alter table public.customers
  add column if not exists session_data jsonb not null default '{}'::jsonb;
