-- Asistente de Manger — tablas en Supabase
-- cuentas: el avance de cada cliente (lo editas tu; el asistente solo lee)
-- alertas: cada aviso que el asistente te manda (queda registro aunque falle WhatsApp)

create table if not exists public.cuentas (
  id                  bigint generated always as identity primary key,
  empresa             text not null,
  contacto_nombre     text,
  -- Solo digitos, con codigo de pais y sin "+": 51987654321
  telefono            text not null unique check (telefono ~ '^[0-9]{8,15}$'),
  email               text,
  plan                text,
  estado              text not null default 'activo'
                      check (estado in ('onboarding', 'activo', 'en_riesgo', 'pausado', 'baja')),
  avance              text,           -- lo que el asistente le cuenta al cliente
  proximo_paso        text,
  fecha_proximo_paso  date,
  notas_internas      text,           -- SOLO para ti: el asistente nunca las lee
  creado_en           timestamptz not null default now(),
  actualizado_en      timestamptz not null default now()
);

create table if not exists public.alertas (
  id          bigint generated always as identity primary key,
  creada_en   timestamptz not null default now(),
  telefono    text not null,
  empresa     text,
  categoria   text not null check (categoria in ('precio', 'queja', 'riesgo_baja', 'upsell', 'otro')),
  urgencia    text not null default 'normal' check (urgencia in ('alta', 'normal')),
  resumen     text not null,
  entregada   boolean not null default false,
  resuelta    boolean not null default false
);

create index if not exists alertas_pendientes_idx on public.alertas (creada_en) where not entregada;

-- actualizado_en se mantiene solo
create or replace function public.tocar_actualizado_en()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.actualizado_en = now();
  return new;
end;
$$;

drop trigger if exists cuentas_actualizado_en on public.cuentas;
create trigger cuentas_actualizado_en
  before update on public.cuentas
  for each row execute function public.tocar_actualizado_en();

-- RLS activado y SIN politicas: la anon key no puede leer ni escribir nada.
-- El servidor del asistente usa la service role key, que salta RLS.
alter table public.cuentas enable row level security;
alter table public.alertas enable row level security;
