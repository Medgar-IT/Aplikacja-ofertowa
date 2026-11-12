-- ============================================================================
-- KOMPLETNY SKRYPT INICJALIZACYJNY BAZY DANYCH SUPABASE (Tabele + Polityki RLS + Storage)
-- Wklej i uruchom ten skrypt w zakładce "SQL Editor" (przycisk "Run") w panelu Supabase.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Tabela klientów (clients)
CREATE TABLE IF NOT EXISTS public.clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    nip VARCHAR(20),
    address TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Tabela produktów (products)
CREATE TABLE IF NOT EXISTS public.products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Tabela ofert (offers)
CREATE TABLE IF NOT EXISTS public.offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES public.clients(id) ON DELETE RESTRICT,
    total_net_value NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Tabela pozycji oferty (offer_items)
CREATE TABLE IF NOT EXISTS public.offer_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id UUID NOT NULL REFERENCES public.offers(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
    quantity NUMERIC(10, 2) NOT NULL DEFAULT 1,
    unit_price NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    total_price NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    custom_notes TEXT
);

-- Dodanie kolumny custom_notes, jeśli tabela offer_items istniała już wcześniej
ALTER TABLE public.offer_items ADD COLUMN IF NOT EXISTS custom_notes TEXT;

-- Indeksy dla kluczy obcych
CREATE INDEX IF NOT EXISTS idx_offers_client_id ON public.offers(client_id);
CREATE INDEX IF NOT EXISTS idx_offer_items_offer_id ON public.offer_items(offer_id);
CREATE INDEX IF NOT EXISTS idx_offer_items_product_id ON public.offer_items(product_id);

-- ============================================================================
-- UPRAWNIENIA I POLITYKI RLS (Row Level Security) DLA TABEL
-- ============================================================================
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;

-- Polityki dla tabeli clients
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Pełny dostęp do clients" ON public.clients;
CREATE POLICY "Pełny dostęp do clients"
ON public.clients FOR ALL
TO public
USING (true)
WITH CHECK (true);

-- Polityki dla tabeli products
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Pełny dostęp do products" ON public.products;
CREATE POLICY "Pełny dostęp do products"
ON public.products FOR ALL
TO public
USING (true)
WITH CHECK (true);

-- Polityki dla tabeli offers
ALTER TABLE public.offers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Pełny dostęp do offers" ON public.offers;
CREATE POLICY "Pełny dostęp do offers"
ON public.offers FOR ALL
TO public
USING (true)
WITH CHECK (true);

-- Polityki dla tabeli offer_items
ALTER TABLE public.offer_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Pełny dostęp do offer_items" ON public.offer_items;
CREATE POLICY "Pełny dostęp do offer_items"
ON public.offer_items FOR ALL
TO public
USING (true)
WITH CHECK (true);

-- ============================================================================
-- SUPABASE STORAGE: Automatyczne utworzenie publicznego bucketa "product-images"
-- ============================================================================
INSERT INTO storage.buckets (id, name, public)
VALUES ('product-images', 'product-images', true)
ON CONFLICT (id) DO UPDATE SET public = true;

DROP POLICY IF EXISTS "Publiczny dostęp do odczytu zdjęć produktów" ON storage.objects;
CREATE POLICY "Publiczny dostęp do odczytu zdjęć produktów"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'product-images');

DROP POLICY IF EXISTS "Dostęp do wgrywania zdjęć produktów" ON storage.objects;
CREATE POLICY "Dostęp do wgrywania zdjęć produktów"
ON storage.objects FOR INSERT
TO public
WITH CHECK (bucket_id = 'product-images');

DROP POLICY IF EXISTS "Dostęp do aktualizacji zdjęć produktów" ON storage.objects;
CREATE POLICY "Dostęp do aktualizacji zdjęć produktów"
ON storage.objects FOR UPDATE
TO public
USING (bucket_id = 'product-images')
WITH CHECK (bucket_id = 'product-images');

DROP POLICY IF EXISTS "Dostęp do usuwania zdjęć produktów" ON storage.objects;
CREATE POLICY "Dostęp do usuwania zdjęć produktów"
ON storage.objects FOR DELETE
TO public
USING (bucket_id = 'product-images');

-- ============================================================================
-- ODŚWIEŻENIE PAMIĘCI PODRĘCZNEJ SCHEMATU API
-- ============================================================================
NOTIFY pgrst, 'reload schema';
