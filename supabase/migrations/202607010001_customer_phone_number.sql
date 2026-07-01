alter table public.customers add column if not exists phone_number text;

create index if not exists idx_customers_phone_number
  on public.customers(phone_number);
