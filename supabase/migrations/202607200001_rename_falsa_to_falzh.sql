-- Rename falsa_info_chunks -> falzh_info_chunks
-- Rename match_falsa_info -> match_falzh_info

-- Drop old trigger first (depends on old table name)
drop trigger if exists set_falsa_info_chunks_updated_at on public.falsa_info_chunks;

-- Rename table
alter table if exists public.falsa_info_chunks rename to falzh_info_chunks;

-- Rename index
drop index if exists public.idx_falsa_info_chunks_embedding;
create index if not exists idx_falzh_info_chunks_embedding
  on public.falzh_info_chunks using hnsw (embedding vector_cosine_ops);

-- Recreate trigger on new table name
create trigger set_falzh_info_chunks_updated_at
before update on public.falzh_info_chunks
for each row execute function public.set_updated_at();

-- Drop old function
drop function if exists public.match_falsa_info(vector, float, int);

-- Create new function with renamed table reference
create or replace function public.match_falzh_info(
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
    falzh_info_chunks.id,
    falzh_info_chunks.chunk_text,
    falzh_info_chunks.source,
    1 - (falzh_info_chunks.embedding <=> query_embedding) as similarity
  from public.falzh_info_chunks
  where 1 - (falzh_info_chunks.embedding <=> query_embedding) >= match_threshold
  order by falzh_info_chunks.embedding <=> query_embedding
  limit match_count;
$$;

-- Update RLS and grants
alter table public.falzh_info_chunks enable row level security;
grant all on public.falzh_info_chunks to service_role;
