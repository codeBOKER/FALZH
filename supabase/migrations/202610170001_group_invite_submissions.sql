create table if not exists public.group_invite_submissions (
  id uuid primary key default gen_random_uuid(),
  invite_link text not null,
  description text,
  submitted_by_customer_id uuid not null
    references public.customers(id) on delete cascade,
  source_message_id uuid
    references public.messages(id) on delete set null,
  status text not null default 'pending_review'
    check (status in ('pending_review', 'accepted', 'rejected')),
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_group_invite_submissions_review_queue
  on public.group_invite_submissions (status, created_at);

create index if not exists idx_group_invite_submissions_customer
  on public.group_invite_submissions (submitted_by_customer_id, created_at desc);

alter table public.group_invite_submissions enable row level security;

revoke all on table public.group_invite_submissions from public, anon, authenticated;
grant all on table public.group_invite_submissions to service_role;
