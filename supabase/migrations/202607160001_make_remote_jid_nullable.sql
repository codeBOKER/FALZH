-- Make remoteJid nullable so unregistered drivers from groups
-- can be created without a WhatsApp JID (they only have phone_number)
ALTER TABLE public.customers ALTER COLUMN "remoteJid" DROP NOT NULL;

-- Update unique constraint to allow NULL values (PostgreSQL allows multiple NULLs by default)
-- No additional action needed since UNIQUE already permits multiple NULLs
