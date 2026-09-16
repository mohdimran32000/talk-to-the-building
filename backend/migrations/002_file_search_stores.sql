-- File Search Stores: one per user, stores Gemini store resource name
create table if not exists file_search_stores (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,
  store_resource_name text not null,
  display_name text,
  created_at timestamptz default now() not null,

  constraint unique_user_store unique (user_id)
);

alter table file_search_stores enable row level security;

create policy "Users can manage their own stores"
  on file_search_stores for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Uploaded Files: tracks each file uploaded to a store
create table if not exists uploaded_files (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade not null,
  store_id uuid references file_search_stores(id) on delete cascade not null,
  file_name text not null,
  file_size bigint not null,
  mime_type text not null,
  status text not null default 'processing',
  gemini_file_name text,
  created_at timestamptz default now() not null
);

alter table uploaded_files enable row level security;

create policy "Users can manage their own files"
  on uploaded_files for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
