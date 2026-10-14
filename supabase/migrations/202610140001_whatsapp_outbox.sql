create table if not exists public.whatsapp_outbox (
  id uuid primary key default gen_random_uuid(),
  recipient text not null,
  payload jsonb not null,
  status text not null default 'pending'
    check (status in ('pending', 'sending', 'sent', 'failed')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  last_attempt_at timestamptz,
  locked_at timestamptz,
  sent_at timestamptz,
  last_error text,
  provider_response jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_whatsapp_outbox_queue
  on public.whatsapp_outbox (created_at, id)
  where status in ('pending', 'sending');

alter table public.whatsapp_outbox enable row level security;

grant all on table public.whatsapp_outbox to service_role;
