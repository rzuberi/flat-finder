-- Likes table for the flat finder. Run this once in the Supabase SQL editor.
create table public.likes (
  id bigint generated always as identity primary key,
  site text not null default 'london',
  listing_id text not null,
  person text not null check (person in ('Rehan', 'Clara')),
  created_at timestamptz not null default now(),
  unique (site, listing_id, person)
);

alter table public.likes enable row level security;

-- Two-person hobby app: the anon key may read, add and remove likes.
create policy "anon read" on public.likes for select to anon using (true);
create policy "anon insert" on public.likes for insert to anon with check (true);
create policy "anon delete" on public.likes for delete to anon using (true);
