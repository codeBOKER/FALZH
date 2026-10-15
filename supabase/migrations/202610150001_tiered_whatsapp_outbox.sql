alter table public.whatsapp_outbox
  add column if not exists discarded_at timestamptz,
  add column if not exists discard_reason text;

alter table public.whatsapp_outbox
  drop constraint if exists whatsapp_outbox_status_check;

alter table public.whatsapp_outbox
  add constraint whatsapp_outbox_status_check
  check (status in ('pending', 'sending', 'sent', 'failed', 'discarded'));

update public.whatsapp_outbox
set status = 'discarded',
    discarded_at = coalesce(discarded_at, now()),
    discard_reason = coalesce(discard_reason, 'Permanent failure from the previous retry policy')
where status = 'failed';
