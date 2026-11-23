-- Documentos do CRDD (ASD, CRLV-e, comprovante de comunicação, ATPV-e) guardados pela API.
-- O PDF fica em bytea já comprimido e cifrado (AES-256-GCM com a ENCRYPTION_KEY da API):
-- a API acessa o Supabase com a chave anon, então o conteúdo nunca é gravado em claro.
-- Rode uma vez no SQL Editor do Supabase.

create table if not exists public.crdd_documentos (
    id bigint generated always as identity primary key,
    tipo text not null check (tipo in ('asd', 'crlve', 'comunicacao', 'atpve')),
    referencia text not null,
    placa text,
    descricao text,
    pdf bytea not null,
    tamanho_original integer not null,
    tamanho integer not null,
    created_at timestamptz not null default now(),
    unique (tipo, referencia)
);

create index if not exists crdd_documentos_placa_idx on public.crdd_documentos (placa);

-- PDFs are already compressed (JPEG/deflate) and encrypted, so TOAST compression would only cost CPU
alter table public.crdd_documentos alter column pdf set storage external;

alter table public.crdd_documentos enable row level security;

create policy "api le documentos" on public.crdd_documentos for select to anon using (true);
create policy "api grava documentos" on public.crdd_documentos for insert to anon with check (true);
create policy "api atualiza documentos" on public.crdd_documentos for update to anon using (true) with check (true);
