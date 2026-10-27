create index if not exists idx_whatsapp_outbox_recipient_pending_queue
  on public.whatsapp_outbox (recipient, created_at, id)
  where status = 'pending';

create index if not exists idx_whatsapp_outbox_recipient_sending
  on public.whatsapp_outbox (recipient)
  where status = 'sending';

-- Fast check for "does this recipient have any message waiting to go out?".
create index if not exists idx_whatsapp_outbox_recipient_active
  on public.whatsapp_outbox (recipient)
  where status in ('pending', 'sending');

create index if not exists idx_messages_outbox_id
  on public.messages ((metadata->>'outbox_id'))
  where metadata->>'outbox_id' is not null;

-- Atomically claim one queued message for delivery. Serializes claims per
-- recipient across application processes with an advisory lock, and refuses
-- to claim while another message for the same recipient is in flight.
create or replace function public.claim_whatsapp_outbox_message(
  p_message_id uuid,
  p_ignore_retry_time boolean default false
)
returns setof public.whatsapp_outbox
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_recipient text;
begin
  select queued.recipient into v_recipient
  from public.whatsapp_outbox as queued
  where queued.id = p_message_id;

  if not found then
    return;
  end if;

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(v_recipient, 0)
  );

  if exists (
    select 1
    from public.whatsapp_outbox as queued
    where queued.recipient = v_recipient
      and queued.status = 'sending'
  ) then
    return;
  end if;

  return query
  update public.whatsapp_outbox as queued
  set status = 'sending',
      attempts = queued.attempts + 1,
      last_attempt_at = pg_catalog.now(),
      locked_at = pg_catalog.now(),
      last_error = null
  where queued.id = p_message_id
    and queued.status = 'pending'
    and (
      p_ignore_retry_time
      or queued.next_attempt_at <= pg_catalog.now()
    )
  returning queued.*;
end;
$$;

create or replace function public.list_pending_whatsapp_queue_heads()
returns table(
  recipient text,
  created_at timestamptz,
  next_attempt_at timestamptz
)
language sql
stable
security invoker
set search_path = ''
as $$
  with queue_heads as (
    select distinct on (queued.recipient)
      queued.recipient,
      queued.created_at,
      queued.next_attempt_at
    from public.whatsapp_outbox as queued
    where queued.status = 'pending'
    order by queued.recipient, queued.created_at, queued.id
  )
  select
    queue_heads.recipient,
    queue_heads.created_at,
    queue_heads.next_attempt_at
  from queue_heads
  order by queue_heads.created_at, queue_heads.recipient;
$$;

-- Batched pending-selection counts for trip cards (one round trip instead
-- of one exact count query per card).
create or replace function public.count_pending_trip_selections(
  p_trip_ids uuid[]
)
returns table(trip_id uuid, selection_count bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  select selections.trip_id, count(*)::bigint as selection_count
  from public.trip_selections as selections
  where selections.trip_id = any (p_trip_ids)
    and selections.status = 'pending'
  group by selections.trip_id;
$$;

revoke all on function public.claim_whatsapp_outbox_message(uuid, boolean)
  from public, anon, authenticated;
grant execute on function public.claim_whatsapp_outbox_message(uuid, boolean)
  to service_role;

revoke all on function public.list_pending_whatsapp_queue_heads()
  from public, anon, authenticated;
grant execute on function public.list_pending_whatsapp_queue_heads()
  to service_role;

revoke all on function public.count_pending_trip_selections(uuid[])
  from public, anon, authenticated;
grant execute on function public.count_pending_trip_selections(uuid[])
  to service_role;
