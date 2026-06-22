-- Migration: driver records now reference customers as primary identity
-- 1) Add customer_id foreign key to drivers
-- 2) Populate customer_id from existing customer records when possible
-- 3) Enforce uniqueness and non-nullability
-- 4) Drop redundant driver identity columns

ALTER TABLE public.drivers
ADD COLUMN customer_id uuid;

ALTER TABLE public.customers
RENAME COLUMN phone_number TO "remoteJid";

UPDATE public.drivers
SET customer_id = public.customers.id
FROM public.customers
WHERE public.drivers.phone_number = public.customers."remoteJid";

ALTER TABLE public.drivers
ADD CONSTRAINT drivers_customer_id_key UNIQUE (customer_id);

ALTER TABLE public.drivers
ALTER COLUMN customer_id SET NOT NULL;

ALTER TABLE public.drivers
ADD CONSTRAINT drivers_customer_id_fkey
    FOREIGN KEY (customer_id)
    REFERENCES public.customers(id)
    ON DELETE CASCADE;

ALTER TABLE public.drivers
DROP COLUMN name,
DROP COLUMN phone_number;
