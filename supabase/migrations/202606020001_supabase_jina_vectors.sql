create schema if not exists extensions;
create extension if not exists vector with schema extensions;

create table if not exists public.falsa_info_chunks (
  id text primary key,
  chunk_text text not null,
  source text,
  embedding extensions.vector(1024) not null,
  embedding_model text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.driver_trip_embeddings (
  trip_id uuid primary key references public.driver_trips(id) on delete cascade,
  chunk_text text not null,
  embedding extensions.vector(1024) not null,
  embedding_model text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_falsa_info_chunks_embedding
  on public.falsa_info_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists idx_driver_trip_embeddings_embedding
  on public.driver_trip_embeddings using hnsw (embedding vector_cosine_ops);

drop trigger if exists set_falsa_info_chunks_updated_at on public.falsa_info_chunks;
create trigger set_falsa_info_chunks_updated_at
before update on public.falsa_info_chunks
for each row execute function public.set_updated_at();

drop trigger if exists set_driver_trip_embeddings_updated_at on public.driver_trip_embeddings;
create trigger set_driver_trip_embeddings_updated_at
before update on public.driver_trip_embeddings
for each row execute function public.set_updated_at();

create or replace function public.departure_bucket_clock_time(bucket text)
returns time
language sql
immutable
as $$
  select case bucket
    when 'morning' then time '06:00'
    when 'noon' then time '12:00'
    when 'night' then time '18:00'
    else null
  end;
$$;

create or replace function public.match_falsa_info(
  query_embedding extensions.vector(1024),
  match_threshold float default 0.0,
  match_count int default 5
)
returns table (
  id text,
  chunk_text text,
  source text,
  similarity float
)
language sql
stable
as $$
  select
    falsa_info_chunks.id,
    falsa_info_chunks.chunk_text,
    falsa_info_chunks.source,
    1 - (falsa_info_chunks.embedding <=> query_embedding) as similarity
  from public.falsa_info_chunks
  where 1 - (falsa_info_chunks.embedding <=> query_embedding) >= match_threshold
  order by falsa_info_chunks.embedding <=> query_embedding
  limit match_count;
$$;

create or replace function public.match_active_trips(
  query_embedding extensions.vector(1024),
  match_threshold float default 0.0,
  match_count int default 10,
  filter_departure text default null,
  filter_destination text default null,
  filter_departure_date date default null,
  filter_departure_time text default null,
  filter_requested_time time default null,
  filter_seats int default 1,
  filter_vehicle_type text default null
)
returns table (
  trip_id uuid,
  departure text,
  destination text,
  departure_date date,
  departure_time text,
  available_seats integer,
  total_seats integer,
  price numeric,
  status text,
  driver_name text,
  driver_phone_number text,
  car_type text,
  chunk_text text,
  similarity float,
  time_difference_minutes integer
)
language sql
stable
as $$
  with ranked as (
    select
      driver_trips.id as trip_id,
      driver_trips.departure,
      driver_trips.destination,
      driver_trips.departure_date,
      driver_trips.departure_time,
      driver_trips.available_seats,
      driver_trips.total_seats,
      driver_trips.price,
      driver_trips.status,
      customers.name as driver_name,
      customers."remoteJid" as driver_phone_number,
      driver_cars.car_type,
      driver_trip_embeddings.chunk_text,
      1 - (driver_trip_embeddings.embedding <=> query_embedding) as similarity,
      driver_trip_embeddings.embedding <=> query_embedding as vector_distance,
      case
        when filter_requested_time is null then null
        else abs(
          extract(
            epoch from (
              public.departure_bucket_clock_time(driver_trips.departure_time)
              - filter_requested_time
            )
          ) / 60
        )::integer
      end as time_difference_minutes
    from public.driver_trip_embeddings
    join public.driver_trips on driver_trips.id = driver_trip_embeddings.trip_id
    left join public.drivers on drivers.id = driver_trips.driver_id
    left join public.customers on customers.id = drivers.customer_id
    left join public.driver_cars on driver_cars.id = driver_trips.car_id
    where driver_trips.status = 'active'
      and driver_trips.available_seats >= coalesce(filter_seats, 1)
      and (
        filter_departure is null
        or driver_trips.departure ilike '%' || filter_departure || '%'
      )
      and (
        filter_destination is null
        or driver_trips.destination ilike '%' || filter_destination || '%'
      )
      and (
        filter_departure_date is null
        or driver_trips.departure_date = filter_departure_date
      )
      and (
        filter_departure_time is null
        or driver_trips.departure_time = filter_departure_time
      )
      and (
        filter_vehicle_type is null
        or driver_cars.car_type ilike '%' || filter_vehicle_type || '%'
      )
      and (
        driver_trips.departure_date > (now() at time zone 'Asia/Aden')::date
        or (
          driver_trips.departure_date = (now() at time zone 'Asia/Aden')::date
          and (
            (now() at time zone 'Asia/Aden')::time < time '12:00'
            or (
              (now() at time zone 'Asia/Aden')::time < time '18:00'
              and driver_trips.departure_time in ('noon', 'night')
            )
            or (
              (now() at time zone 'Asia/Aden')::time >= time '18:00'
              and driver_trips.departure_time = 'night'
            )
          )
        )
      )
  )
  select
    ranked.trip_id,
    ranked.departure,
    ranked.destination,
    ranked.departure_date,
    ranked.departure_time,
    ranked.available_seats,
    ranked.total_seats,
    ranked.price,
    ranked.status,
    ranked.driver_name,
    ranked.driver_phone_number,
    ranked.car_type,
    ranked.chunk_text,
    ranked.similarity,
    ranked.time_difference_minutes
  from ranked
  where ranked.similarity >= match_threshold
  order by
    ranked.time_difference_minutes nulls last,
    ranked.departure_date,
    ranked.vector_distance
  limit match_count;
$$;

alter table public.falsa_info_chunks enable row level security;
alter table public.driver_trip_embeddings enable row level security;

grant all on public.falsa_info_chunks to service_role;
grant all on public.driver_trip_embeddings to service_role;
grant usage on schema extensions to service_role;
grant all on all routines in schema public to service_role;
