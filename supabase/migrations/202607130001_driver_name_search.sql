-- Add driver name filtering to match_active_trips RPC

DROP FUNCTION IF EXISTS public.match_active_trips(
  extensions.vector(1024), float, int, text, text, date, text, time, int, text
);

CREATE OR REPLACE FUNCTION public.match_active_trips(
  query_embedding extensions.vector(1024),
  match_threshold float DEFAULT 0.0,
  match_count int DEFAULT 10,
  filter_departure text DEFAULT NULL,
  filter_destination text DEFAULT NULL,
  filter_driver_name text DEFAULT NULL,
  filter_departure_date date DEFAULT NULL,
  filter_departure_time text DEFAULT NULL,
  filter_requested_time time DEFAULT NULL,
  filter_seats int DEFAULT 1,
  filter_vehicle_type text DEFAULT NULL
)
RETURNS TABLE (
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
  time_difference_minutes integer,
  registered boolean
)
LANGUAGE sql STABLE
AS $$
  WITH ranked AS (
    SELECT
      driver_trips.id AS trip_id,
      driver_trips.departure,
      driver_trips.destination,
      driver_trips.departure_date,
      driver_trips.departure_time,
      driver_trips.available_seats,
      driver_trips.total_seats,
      driver_trips.price,
      driver_trips.status,
      customers.name AS driver_name,
      customers."remoteJid" AS driver_phone_number,
      driver_cars.car_type,
      driver_trip_embeddings.chunk_text,
      COALESCE(customers.registered, false) AS registered,
      1 - (driver_trip_embeddings.embedding <=> query_embedding) AS similarity,
      driver_trip_embeddings.embedding <=> query_embedding AS vector_distance,
      CASE
        WHEN filter_requested_time IS NULL THEN NULL
        ELSE abs(
          extract(epoch FROM (
            public.departure_bucket_clock_time(driver_trips.departure_time)
            - filter_requested_time
          )) / 60
        )::integer
      END AS time_difference_minutes
    FROM public.driver_trip_embeddings
    JOIN public.driver_trips ON driver_trips.id = driver_trip_embeddings.trip_id
    LEFT JOIN public.drivers ON drivers.id = driver_trips.driver_id
    LEFT JOIN public.customers ON customers.id = drivers.customer_id
    LEFT JOIN public.driver_cars ON driver_cars.id = driver_trips.car_id
    WHERE driver_trips.status = 'active'
      AND driver_trips.available_seats >= COALESCE(filter_seats, 1)
      AND (filter_departure IS NULL OR driver_trips.departure ILIKE '%' || filter_departure || '%')
      AND (filter_destination IS NULL OR driver_trips.destination ILIKE '%' || filter_destination || '%')
      AND (filter_driver_name IS NULL OR customers.name ILIKE '%' || filter_driver_name || '%')
      AND (filter_departure_date IS NULL OR driver_trips.departure_date = filter_departure_date)
      AND (filter_departure_time IS NULL OR driver_trips.departure_time = filter_departure_time)
      AND (filter_vehicle_type IS NULL OR driver_cars.car_type ILIKE '%' || filter_vehicle_type || '%')
      AND (
        driver_trips.departure_date > (NOW() AT TIME ZONE 'Asia/Aden')::date
        OR (
          driver_trips.departure_date = (NOW() AT TIME ZONE 'Asia/Aden')::date
          AND (
            (NOW() AT TIME ZONE 'Asia/Aden')::time < TIME '12:00'
            OR (
              (NOW() AT TIME ZONE 'Asia/Aden')::time < TIME '18:00'
              AND driver_trips.departure_time IN ('noon', 'night')
            )
            OR (
              (NOW() AT TIME ZONE 'Asia/Aden')::time >= TIME '18:00'
              AND driver_trips.departure_time = 'night'
            )
          )
        )
      )
  )
  SELECT
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
    ranked.time_difference_minutes,
    ranked.registered
  FROM ranked
  WHERE ranked.similarity >= match_threshold
  ORDER BY
    ranked.registered DESC,
    ranked.time_difference_minutes NULLS LAST,
    ranked.departure_date,
    ranked.vector_distance
  LIMIT match_count;
$$;
