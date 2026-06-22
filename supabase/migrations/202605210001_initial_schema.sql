create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(),
  name text,
  "remoteJid" text not null unique,
  preferred_language text check (preferred_language in ('ar', 'en')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.drivers (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null unique references public.customers(id) on delete cascade,
  status text not null default 'active' check (status in ('active', 'inactive', 'suspended')),
  rating numeric(3,2),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.driver_wallet (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null unique references public.drivers(id) on delete cascade,
  balance numeric(12,2) not null default 0,
  last_updated timestamptz not null default now()
);

create table if not exists public.driver_cars (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references public.drivers(id) on delete cascade,
  car_type text not null,
  plate_number text unique,
  seat_count integer check (seat_count > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.driver_trips (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references public.drivers(id) on delete cascade,
  car_id uuid references public.driver_cars(id) on delete set null,
  departure text not null,
  destination text not null,
  departure_date date not null,
  departure_time text not null check (departure_time in ('morning', 'noon', 'night')),
  available_seats integer not null check (available_seats >= 0),
  total_seats integer not null check (total_seats > 0),
  price numeric(12,2) not null check (price >= 0),
  status text not null default 'active' check (status in ('active', 'cancelled', 'completed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint driver_trips_available_not_over_total check (available_seats <= total_seats)
);

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references public.customers(id) on delete cascade,
  sender_type text not null check (sender_type in ('customer', 'assistant', 'driver', 'system')),
  message text not null,
  whatsapp_message_id text unique,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.booking_leads (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references public.customers(id) on delete cascade,
  trip_id uuid not null references public.driver_trips(id) on delete restrict,
  requested_seats integer not null check (requested_seats > 0),
  status text not null default 'pending' check (status in ('pending', 'confirmed', 'cancelled')),
  notes text,
  driver_notification_status text not null default 'not_sent'
    check (driver_notification_status in ('not_sent', 'sent', 'failed')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_messages_customer_created_at
  on public.messages(customer_id, created_at desc);
create index if not exists idx_driver_trips_active_route
  on public.driver_trips(status, departure, destination, departure_date, departure_time);
create index if not exists idx_booking_leads_customer
  on public.booking_leads(customer_id, created_at desc);
create index if not exists idx_booking_leads_trip
  on public.booking_leads(trip_id, created_at desc);

drop trigger if exists set_customers_updated_at on public.customers;
create trigger set_customers_updated_at
before update on public.customers
for each row execute function public.set_updated_at();

drop trigger if exists set_drivers_updated_at on public.drivers;
create trigger set_drivers_updated_at
before update on public.drivers
for each row execute function public.set_updated_at();

drop trigger if exists set_driver_cars_updated_at on public.driver_cars;
create trigger set_driver_cars_updated_at
before update on public.driver_cars
for each row execute function public.set_updated_at();

drop trigger if exists set_driver_trips_updated_at on public.driver_trips;
create trigger set_driver_trips_updated_at
before update on public.driver_trips
for each row execute function public.set_updated_at();

drop trigger if exists set_booking_leads_updated_at on public.booking_leads;
create trigger set_booking_leads_updated_at
before update on public.booking_leads
for each row execute function public.set_updated_at();

alter table public.customers enable row level security;
alter table public.messages enable row level security;
alter table public.drivers enable row level security;
alter table public.driver_wallet enable row level security;
alter table public.driver_cars enable row level security;
alter table public.driver_trips enable row level security;
alter table public.booking_leads enable row level security;

grant usage on schema public to service_role;
grant all on all tables in schema public to service_role;
grant all on all routines in schema public to service_role;
grant all on all sequences in schema public to service_role;
