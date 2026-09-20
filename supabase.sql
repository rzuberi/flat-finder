-- Likes table shared by the flat finder sites. Run once in the Supabase SQL editor.
create table public.likes (
  id bigint generated always as identity primary key,
  site text not null default 'london',
  listing_id text not null,
  person text not null check (person in ('Rehan', 'Clara', 'Aidan', 'Alex', 'Leah')),
  created_at timestamptz not null default now(),
  unique (site, listing_id, person)
);

alter table public.likes enable row level security;

-- Small private app: the anon key may read, add and remove likes.
create policy "anon read" on public.likes for select to anon using (true);
create policy "anon insert" on public.likes for insert to anon with check (true);
create policy "anon delete" on public.likes for delete to anon using (true);

-- If the table already exists with the two-name check, widen it:
-- alter table public.likes drop constraint likes_person_check;
-- alter table public.likes add constraint likes_person_check
--   check (person in ('Rehan', 'Clara', 'Aidan', 'Alex', 'Leah'));
