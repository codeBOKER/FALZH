-- rename booking_leads table to trip_selections
alter table if exists public.booking_leads
  rename to trip_selections;

-- drop old check constraint
alter table public.trip_selections
  drop constraint if exists booking_leads_status_check;

-- migrate existing 'confirmed' rows to 'pending' (no confirmation flow anymore)
update public.trip_selections
  set status = 'pending'
  where status = 'confirmed';

-- add new constraint without 'confirmed'
alter table public.trip_selections
  add constraint trip_selections_status_check
    check (status in ('pending', 'cancelled'));

-- rename indexes
drop index if exists public.idx_booking_leads_customer;
create index idx_trip_selections_customer
  on public.trip_selections(customer_id, created_at desc);

drop index if exists public.idx_booking_leads_trip;
create index idx_trip_selections_trip
  on public.trip_selections(trip_id, created_at desc);

-- rename trigger
drop trigger if exists set_booking_leads_updated_at on public.trip_selections;
create trigger set_trip_selections_updated_at
  before update on public.trip_selections
  for each row execute function public.set_updated_at();

-- rename rls
alter table public.trip_selections enable row level security;
