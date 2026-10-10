--
-- PostgreSQL database dump
--

\restrict mmwJKwqjeA3JVtIaidbJLdgw9PKc68S0JUhcgqBdfGktidQ8SzpvG9rGb4KqLiH

-- Dumped from database version 17.6
-- Dumped by pg_dump version 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: departure_bucket_clock_time(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.departure_bucket_clock_time(bucket text) RETURNS time without time zone
    LANGUAGE sql IMMUTABLE
    AS $$
  select case bucket
    when 'morning' then time '06:00'
    when 'noon' then time '12:00'
    when 'night' then time '18:00'
    else null
  end;
$$;


--
-- Name: match_active_trips(extensions.vector, double precision, integer, text, text, text, date, text, time without time zone, integer, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.match_active_trips(query_embedding extensions.vector, match_threshold double precision DEFAULT 0.0, match_count integer DEFAULT 10, filter_departure text DEFAULT NULL::text, filter_destination text DEFAULT NULL::text, filter_driver_name text DEFAULT NULL::text, filter_departure_date date DEFAULT NULL::date, filter_departure_time text DEFAULT NULL::text, filter_requested_time time without time zone DEFAULT NULL::time without time zone, filter_seats integer DEFAULT 1, filter_vehicle_type text DEFAULT NULL::text) RETURNS TABLE(trip_id uuid, departure text, destination text, departure_date date, departure_time text, available_seats integer, total_seats integer, price numeric, status text, driver_name text, driver_phone_number text, car_type text, chunk_text text, similarity double precision, time_difference_minutes integer, registered boolean)
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
      AND (driver_trips.available_seats IS NULL OR driver_trips.available_seats >= COALESCE(filter_seats, 1))
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


--
-- Name: match_falzh_info(extensions.vector, double precision, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.match_falzh_info(query_embedding extensions.vector, match_threshold double precision DEFAULT 0.0, match_count integer DEFAULT 5) RETURNS TABLE(id text, chunk_text text, source text, similarity double precision)
    LANGUAGE sql STABLE
    AS $$
  select
    falzh_info_chunks.id,
    falzh_info_chunks.chunk_text,
    falzh_info_chunks.source,
    1 - (falzh_info_chunks.embedding <=> query_embedding) as similarity
  from public.falzh_info_chunks
  where 1 - (falzh_info_chunks.embedding <=> query_embedding) >= match_threshold
  order by falzh_info_chunks.embedding <=> query_embedding
  limit match_count;
$$;


--
-- Name: rls_auto_enable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rls_auto_enable() RETURNS event_trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  new.updated_at = now();
  return new;
end;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: customers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text,
    "remoteJid" text,
    preferred_language text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    user_mode text,
    phone_number text,
    session_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    registered boolean DEFAULT false NOT NULL,
    CONSTRAINT customers_preferred_language_check CHECK ((preferred_language = ANY (ARRAY['ar'::text, 'en'::text]))),
    CONSTRAINT customers_user_mode_check CHECK ((user_mode = ANY (ARRAY['driver'::text, 'passenger'::text])))
);


--
-- Name: driver_cars; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_cars (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    driver_id uuid NOT NULL,
    car_type text NOT NULL,
    plate_number text,
    seat_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT driver_cars_seat_count_check CHECK ((seat_count > 0))
);


--
-- Name: driver_trip_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_trip_embeddings (
    trip_id uuid NOT NULL,
    chunk_text text NOT NULL,
    embedding extensions.vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: driver_trips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_trips (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    driver_id uuid NOT NULL,
    car_id uuid,
    departure text NOT NULL,
    destination text NOT NULL,
    available_seats integer,
    total_seats integer,
    price numeric(12,2) NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    departure_date date NOT NULL,
    departure_time text NOT NULL,
    driver_message text,
    use_driver_message boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_trips_available_not_over_total CHECK (((available_seats IS NULL) OR (total_seats IS NULL) OR (available_seats <= total_seats))),
    CONSTRAINT driver_trips_available_seats_check CHECK ((available_seats >= 0)),
    CONSTRAINT driver_trips_departure_time_check CHECK ((departure_time = ANY (ARRAY['morning'::text, 'noon'::text, 'night'::text]))),
    CONSTRAINT driver_trips_price_check CHECK ((price >= (0)::numeric)),
    CONSTRAINT driver_trips_status_check CHECK ((status = ANY (ARRAY['active'::text, 'cancelled'::text, 'completed'::text]))),
    CONSTRAINT driver_trips_total_seats_check CHECK ((total_seats > 0))
);


--
-- Name: driver_wallet; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_wallet (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    driver_id uuid NOT NULL,
    balance numeric(12,2) DEFAULT 0 NOT NULL,
    last_updated timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: drivers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drivers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    rating numeric(3,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    customer_id uuid NOT NULL,
    CONSTRAINT drivers_status_check CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text, 'suspended'::text])))
);


--
-- Name: falzh_info_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.falzh_info_chunks (
    id text NOT NULL,
    chunk_text text NOT NULL,
    source text,
    embedding extensions.vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    customer_id uuid NOT NULL,
    sender_type text NOT NULL,
    message text NOT NULL,
    whatsapp_message_id text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT messages_sender_type_check CHECK ((sender_type = ANY (ARRAY['customer'::text, 'assistant'::text, 'driver'::text, 'system'::text])))
);


--
-- Name: trip_selections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trip_selections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    customer_id uuid NOT NULL,
    trip_id uuid NOT NULL,
    requested_seats integer NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    notes text,
    driver_notification_status text DEFAULT 'not_sent'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT booking_leads_driver_notification_status_check CHECK ((driver_notification_status = ANY (ARRAY['not_sent'::text, 'sent'::text, 'failed'::text]))),
    CONSTRAINT booking_leads_requested_seats_check CHECK ((requested_seats > 0)),
    CONSTRAINT trip_selections_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'cancelled'::text])))
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    remote_jid text NOT NULL,
    remote_jid_alt text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);


--
-- Name: whatsapp_auth_prod; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whatsapp_auth_prod (
    id text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);


--
-- Name: whatsapp_auth_test; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.whatsapp_auth_test (
    id text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: trip_selections booking_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trip_selections
    ADD CONSTRAINT booking_leads_pkey PRIMARY KEY (id);


--
-- Name: customers customers_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_phone_number_key UNIQUE ("remoteJid");


--
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- Name: driver_cars driver_cars_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_cars
    ADD CONSTRAINT driver_cars_pkey PRIMARY KEY (id);


--
-- Name: driver_cars driver_cars_plate_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_cars
    ADD CONSTRAINT driver_cars_plate_number_key UNIQUE (plate_number);


--
-- Name: driver_trip_embeddings driver_trip_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_trip_embeddings
    ADD CONSTRAINT driver_trip_embeddings_pkey PRIMARY KEY (trip_id);


--
-- Name: driver_trips driver_trips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_trips
    ADD CONSTRAINT driver_trips_pkey PRIMARY KEY (id);


--
-- Name: driver_wallet driver_wallet_driver_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_wallet
    ADD CONSTRAINT driver_wallet_driver_id_key UNIQUE (driver_id);


--
-- Name: driver_wallet driver_wallet_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_wallet
    ADD CONSTRAINT driver_wallet_pkey PRIMARY KEY (id);


--
-- Name: drivers drivers_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_customer_id_key UNIQUE (customer_id);


--
-- Name: drivers drivers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_pkey PRIMARY KEY (id);


--
-- Name: falzh_info_chunks falsa_info_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.falzh_info_chunks
    ADD CONSTRAINT falsa_info_chunks_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: messages messages_whatsapp_message_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_whatsapp_message_id_key UNIQUE (whatsapp_message_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (remote_jid);


--
-- Name: whatsapp_auth_prod whatsapp_auth_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_auth_prod
    ADD CONSTRAINT whatsapp_auth_pkey PRIMARY KEY (id);


--
-- Name: whatsapp_auth_test whatsapp_auth_test_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.whatsapp_auth_test
    ADD CONSTRAINT whatsapp_auth_test_pkey PRIMARY KEY (id);


--
-- Name: idx_customers_phone_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customers_phone_number ON public.customers USING btree (phone_number);


--
-- Name: idx_driver_trip_embeddings_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_driver_trip_embeddings_embedding ON public.driver_trip_embeddings USING hnsw (embedding extensions.vector_cosine_ops);


--
-- Name: idx_driver_trips_active_route; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_driver_trips_active_route ON public.driver_trips USING btree (status, departure, destination, departure_date, departure_time);


--
-- Name: idx_falzh_info_chunks_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_falzh_info_chunks_embedding ON public.falzh_info_chunks USING hnsw (embedding extensions.vector_cosine_ops);


--
-- Name: idx_messages_customer_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_customer_created_at ON public.messages USING btree (customer_id, created_at DESC);


--
-- Name: idx_trip_selections_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trip_selections_customer ON public.trip_selections USING btree (customer_id, created_at DESC);


--
-- Name: idx_trip_selections_trip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trip_selections_trip ON public.trip_selections USING btree (trip_id, created_at DESC);


--
-- Name: idx_whatsapp_auth_test_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_whatsapp_auth_test_updated_at ON public.whatsapp_auth_test USING btree (updated_at);


--
-- Name: customers set_customers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_customers_updated_at BEFORE UPDATE ON public.customers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: driver_cars set_driver_cars_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_driver_cars_updated_at BEFORE UPDATE ON public.driver_cars FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: driver_trip_embeddings set_driver_trip_embeddings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_driver_trip_embeddings_updated_at BEFORE UPDATE ON public.driver_trip_embeddings FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: driver_trips set_driver_trips_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_driver_trips_updated_at BEFORE UPDATE ON public.driver_trips FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: drivers set_drivers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_drivers_updated_at BEFORE UPDATE ON public.drivers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: falzh_info_chunks set_falzh_info_chunks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_falzh_info_chunks_updated_at BEFORE UPDATE ON public.falzh_info_chunks FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: trip_selections set_trip_selections_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_trip_selections_updated_at BEFORE UPDATE ON public.trip_selections FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: trip_selections booking_leads_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trip_selections
    ADD CONSTRAINT booking_leads_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: trip_selections booking_leads_trip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trip_selections
    ADD CONSTRAINT booking_leads_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES public.driver_trips(id) ON DELETE RESTRICT;


--
-- Name: driver_cars driver_cars_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_cars
    ADD CONSTRAINT driver_cars_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE CASCADE;


--
-- Name: driver_trip_embeddings driver_trip_embeddings_trip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_trip_embeddings
    ADD CONSTRAINT driver_trip_embeddings_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES public.driver_trips(id) ON DELETE CASCADE;


--
-- Name: driver_trips driver_trips_car_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_trips
    ADD CONSTRAINT driver_trips_car_id_fkey FOREIGN KEY (car_id) REFERENCES public.driver_cars(id) ON DELETE SET NULL;


--
-- Name: driver_trips driver_trips_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_trips
    ADD CONSTRAINT driver_trips_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE CASCADE;


--
-- Name: driver_wallet driver_wallet_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_wallet
    ADD CONSTRAINT driver_wallet_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE CASCADE;


--
-- Name: drivers drivers_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: messages messages_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;


--
-- Name: customers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_cars; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_cars ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_trip_embeddings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_trip_embeddings ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_trips; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_trips ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_wallet; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_wallet ENABLE ROW LEVEL SECURITY;

--
-- Name: drivers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.drivers ENABLE ROW LEVEL SECURITY;

--
-- Name: falzh_info_chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.falzh_info_chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

--
-- Name: trip_selections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.trip_selections ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: whatsapp_auth_prod; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whatsapp_auth_prod ENABLE ROW LEVEL SECURITY;

--
-- Name: whatsapp_auth_test; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.whatsapp_auth_test ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict mmwJKwqjeA3JVtIaidbJLdgw9PKc68S0JUhcgqBdfGktidQ8SzpvG9rGb4KqLiH

