import base64
import io
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from jinja2 import Template
from PIL import Image
from supabase import Client, create_client
from xhtml2pdf import pisa

# ============================================================================
# STAŁE KONFIGURACYJNE
# ============================================================================
STORAGE_BUCKET = "product-images"
LOGO_URL = "https://jjkcdlsniwytkyregzrf.supabase.co/storage/v1/object/public/product-images/obraz_2026-11-06_182711852.png"
BRAND_COLOR = "#54B6A6"
UNIT_OPTIONS = ["szt", "kg", "m"]

BASE_DIR = Path(__file__).resolve().parent
FONT_REGULAR_PATH = BASE_DIR / "DejaVuSans.ttf"
FONT_BOLD_PATH = BASE_DIR / "DejaVuSans-Bold.ttf"

FONT_URLS = {
    FONT_REGULAR_PATH: [
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf",
        "https://raw.githubusercontent.com/py-pdf/fpdf2/master/test/fonts/DejaVuSans.ttf",
    ],
    FONT_BOLD_PATH: [
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
        "https://raw.githubusercontent.com/py-pdf/fpdf2/master/test/fonts/DejaVuSans-Bold.ttf",
    ],
}

# 1. Konfiguracja strony (musi być pierwszym wywołaniem Streamlit w skrypcie)
st.set_page_config(
    page_title="System Ofertowy",
    page_icon="📄",
    layout="wide",
)


def apply_custom_theme() -> None:
    """Wstrzykuje niestandardowy styl CSS zmieniający kolor głównych przycisków i akcentów na firmowy turkus (#54B6A6)."""
    st.markdown(
        f"""
        <style>
            .stButton > button[kind="primary"],
            .stDownloadButton > button[kind="primary"],
            .stFormSubmitButton > button[kind="primary"],
            div[data-testid="stFormSubmitButton"] > button {{
                background-color: {BRAND_COLOR} !important;
                border-color: {BRAND_COLOR} !important;
                color: #ffffff !important;
            }}
            .stButton > button[kind="primary"]:hover,
            .stDownloadButton > button[kind="primary"]:hover,
            .stFormSubmitButton > button[kind="primary"]:hover,
            div[data-testid="stFormSubmitButton"] > button:hover {{
                background-color: #439b8c !important;
                border-color: #439b8c !important;
                color: #ffffff !important;
            }}
            .stButton > button[kind="primary"]:focus,
            .stButton > button[kind="primary"]:active {{
                background-color: {BRAND_COLOR} !important;
                border-color: {BRAND_COLOR} !important;
                color: #ffffff !important;
                box-shadow: 0 0 0 0.2rem rgba(84, 182, 166, 0.35) !important;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# 2. Inicjalizacja połączenia z bazą danych Supabase
@st.cache_resource
def init_connection() -> Client:
    """Inicjalizuje i zwraca klienta Supabase z wykorzystaniem pamięci podręcznej."""
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# 3. Mechanizm autoryzacji (blokada aplikacji hasłem)
def check_authentication() -> bool:
    """
    Sprawdza, czy użytkownik jest zalogowany w bieżącej sesji.
    Jeśli nie, wyświetla formularz logowania i weryfikuje hasło z st.secrets.
    """
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.title("🔒 Logowanie do Systemu Ofertowego")
    st.markdown("Wprowadź hasło dostępu, aby przejść do aplikacji.")

    with st.form("login_form"):
        password = st.text_input("Hasło", type="password")
        submit = st.form_submit_button("Zaloguj się", type="primary")

        if submit:
            if password == st.secrets.get("GLOBAL_PASSWORD"):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("❌ Nieprawidłowe hasło. Spróbuj ponownie.")

    return False


# 4. Inicjalizacja stanu sesji dla Kreatora Oferty oraz funkcje pomocnicze
def create_empty_offer_item(default_product_id: Optional[str] = None) -> Dict[str, Any]:
    """Tworzy nowy, domyślny wiersz pozycji w kreatorze oferty."""
    return {
        "row_id": str(uuid.uuid4()),
        "product_id": default_product_id,
        "quantity": 1,
        "unit": "szt",
        "unit_price": 0.0,
        "custom_notes": "",
    }


def init_creator_state() -> None:
    """Inicjuje zmienne w st.session_state odpowiedzialne za aktualnie budowaną ofertę."""
    if "selected_client_id" not in st.session_state:
        st.session_state.selected_client_id = None

    if "offer_items" not in st.session_state or not st.session_state.offer_items:
        st.session_state.offer_items = [create_empty_offer_item()]


def set_flash_message(msg_type: str, text: str) -> None:
    """Zapisuje komunikat w sesji, aby wyświetlić go po przeładowaniu strony (st.rerun)."""
    st.session_state["flash_message"] = (msg_type, text)


def show_flash_message() -> None:
    """Wyświetla i czyści zapisany w sesji komunikat zwrotny."""
    if "flash_message" in st.session_state:
        msg_type, text = st.session_state.pop("flash_message")
        if msg_type == "success":
            st.success(text)
        elif msg_type == "error":
            st.error(text)
        elif msg_type == "warning":
            st.warning(text)


def format_pl_currency(value: float) -> str:
    """Formatuje liczbę zmiennoprzecinkową do polskiego zapisu walutowego z przecinkiem (np. 1 299,00)."""
    formatted = f"{float(value):,.2f}"
    return formatted.replace(",", " ").replace(".", ",")


def sanitize_filename(name: str) -> str:
    """Oczyszcza nazwę klienta na potrzeby bezpiecznej nazwy pliku PDF."""
    cleaned = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"[\s]+", "_", cleaned)
    return cleaned or "Klient"


def generate_offer_number() -> str:
    """Generuje unikalny numer oferty w formacie OFE/ROK/MIESIĄC/ID (np. OFE/2026/11/A1B2)."""
    now = datetime.now()
    short_uid = uuid.uuid4().hex[:4].upper()
    return f"OFE/{now.strftime('%Y/%m')}/{short_uid}"


def format_product_label_with_code(product: Dict[str, Any]) -> str:
    """Zwraca nazwę produktu wraz z jego kodem (do wyświetlenia w Kreatorze i na listach)."""
    code = (product.get("product_code") or "").strip()
    name = product.get("name", "Produkt")
    return f"[{code}] {name}" if code else name


# 5. Konwersja obrazów z URL do Base64, obsługa czcionek i generator PDF (Jinja2 + xhtml2pdf)
def url_to_base64(url: Optional[str]) -> str:
    """
    Pobiera obraz z podanego adresu URL i konwertuje go do formatu Data URI (Base64).
    W przypadku pustego adresu URL lub błędu pobierania zwraca pusty string "".
    """
    if not url or not str(url).strip():
        return ""

    clean_url = str(url).strip()
    try:
        response = requests.get(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        if not response.content:
            return ""

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            ext = os.path.splitext(clean_url.split("?")[0])[1].lower()
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }
            content_type = mime_map.get(ext, "image/png")

        encoded_string = base64.b64encode(response.content).decode("utf-8")
        return f"data:{content_type};base64,{encoded_string}"
    except Exception:
        return ""


def ensure_polish_fonts() -> None:
    """
    Sprawdza, czy w folderze projektu istnieje plik czcionki DejaVuSans.ttf (oraz pogrubionej).
    Jeśli nie, pobiera go automatycznie przez requests z publicznego repozytorium CDN/GitHub.
    """
    for font_path, urls in FONT_URLS.items():
        if font_path.exists() and font_path.stat().st_size > 10_000:
            continue
        for url in urls:
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                resp.raise_for_status()
                font_path.write_bytes(resp.content)
                break
            except Exception:
                continue


def pdf_link_callback(uri: str, rel: str) -> str:
    """
    Funkcja pomocnicza dla xhtml2pdf rozwiązująca ścieżki do lokalnych czcionek
    oraz przepuszczająca bez zmian obrazy zakodowane jako data:image/...;base64.
    """
    if uri.startswith("data:"):
        return uri

    if uri == "DejaVuSans.ttf" or uri.endswith("/DejaVuSans.ttf"):
        return str(FONT_REGULAR_PATH)
    if uri == "DejaVuSans-Bold.ttf" or uri.endswith("/DejaVuSans-Bold.ttf"):
        return str(FONT_BOLD_PATH if FONT_BOLD_PATH.exists() else FONT_REGULAR_PATH)

    local_path = BASE_DIR / uri
    if local_path.exists():
        return str(local_path)
    return uri


def generate_pdf(
    client_data: Dict[str, Any],
    items_data: List[Dict[str, Any]],
    total_net: float,
    offer_number: Optional[str] = None,
    offer_date: Optional[str] = None,
) -> bytes:
    """
    Generuje dokument PDF z ofertą na podstawie szablonu HTML (Jinja2) oraz biblioteki xhtml2pdf.
    Uwzględnia numer oferty, datę, dwukolumnowy układ Sprzedający/Kupujący oraz jednostki miary.
    Na PDF celowo pomijany jest kod produktu (widoczna jest wyłącznie nazwa i opis).
    """
    ensure_polish_fonts()

    logo_base64 = url_to_base64(LOGO_URL)
    final_offer_number = offer_number or generate_offer_number()
    final_offer_date = offer_date or datetime.now().strftime("%d.%m.%Y")

    html_template = """
    <!DOCTYPE html>
    <html lang="pl">
    <head>
        <meta charset="utf-8">
        <style>
            @font-face {
                font-family: 'DejaVuSans';
                src: url('DejaVuSans.ttf');
                font-weight: normal;
                font-style: normal;
            }
            @font-face {
                font-family: 'DejaVuSans';
                src: url('DejaVuSans-Bold.ttf');
                font-weight: bold;
                font-style: normal;
            }
            @page {
                size: A4 portrait;
                margin: 15mm;
            }
            body {
                font-family: 'DejaVuSans', sans-serif;
                font-size: 10pt;
                color: #000000;
                line-height: 1.35;
            }
            table.top-header-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 12px;
            }
            table.top-header-table td {
                border: none;
                padding: 0;
                vertical-align: top;
            }
            .logo-fallback {
                font-size: 22pt;
                font-weight: bold;
                letter-spacing: 1px;
                color: #1a1a1a;
            }
            .offer-meta {
                text-align: right;
                font-size: 10.5pt;
            }
            .offer-number-badge {
                font-size: 13pt;
                font-weight: bold;
                color: #000000;
                margin-bottom: 4px;
            }
            table.parties-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 18px;
            }
            table.parties-table td {
                width: 50%;
                border: 1px solid #d9d9d9;
                background-color: #fafafa;
                padding: 10px 12px;
                vertical-align: top;
                font-size: 10pt;
            }
            .party-heading {
                font-size: 9.5pt;
                font-weight: bold;
                color: #54B6A6;
                margin-bottom: 4px;
                letter-spacing: 0.5px;
            }
            .party-line {
                margin: 2px 0;
            }
            table.offer-table {
                width: 100%;
                border-collapse: collapse;
                border: 1px solid #000000;
            }
            table.offer-table th {
                border: 1px solid #000000;
                padding: 8px;
                background-color: #f2f2f2;
                font-weight: bold;
                text-align: center;
                vertical-align: middle;
            }
            table.offer-table td {
                border: 1px solid #000000;
                padding: 8px;
                vertical-align: middle;
            }
            .col-product {
                width: 30%;
                text-align: center;
                vertical-align: top;
            }
            .col-desc {
                width: 35%;
                text-align: left;
                vertical-align: middle;
            }
            .custom-notes {
                margin-top: 6px;
                font-size: 9.5pt;
            }
            .col-qty {
                width: 12%;
                text-align: center;
                vertical-align: middle;
                white-space: nowrap;
            }
            .col-price {
                width: 11%;
                text-align: center;
                vertical-align: middle;
                white-space: nowrap;
            }
            .col-total {
                width: 12%;
                text-align: center;
                vertical-align: middle;
                white-space: nowrap;
            }
            .summary-row td {
                font-weight: bold;
                font-size: 11pt;
                padding: 10px 8px;
                text-align: right;
                vertical-align: middle;
                background-color: #f2f2f2;
            }
        </style>
    </head>
    <body>
        <!-- Górny pasek: Logo po lewej, Numer oferty i Data po prawej -->
        <table class="top-header-table">
            <tr>
                <td style="width: 55%; text-align: left;">
                    {% if logo_base64 %}
                        <img src="{{ logo_base64 }}" style="width: 220px; height: auto;" alt="Logo" />
                    {% else %}
                        <div class="logo-fallback">proles CONCEPT</div>
                    {% endif %}
                </td>
                <td class="offer-meta" style="width: 45%;">
                    <div class="offer-number-badge">OFERTA: {{ offer_number }}</div>
                    <div>Data: {{ offer_date }}</div>
                </td>
            </tr>
        </table>

        <!-- Dwie kolumny: SPRZEDAJĄCY (lewa) oraz KUPUJĄCY (prawa) -->
        <table class="parties-table">
            <tr>
                <td>
                    <div class="party-heading">SPRZEDAJĄCY:</div>
                    <div class="party-line"><strong>Magdalena Domagalska</strong></div>
                    <div class="party-line">ul. Kolejowa 3, 32-086 Dziekanowice</div>
                    <div class="party-line">NIP: 944 106 45 83</div>
                </td>
                <td>
                    <div class="party-heading">KUPUJĄCY:</div>
                    <div class="party-line"><strong>{{ client.name }}</strong></div>
                    {% if client.nip %}
                        <div class="party-line">NIP: {{ client.nip }}</div>
                    {% endif %}
                    {% if client.address %}
                        <div class="party-line">{{ client.address }}</div>
                    {% endif %}
                </td>
            </tr>
        </table>

        <!-- Tabela z pozycjami oferty (bez kodu produktu) -->
        <table class="offer-table">
            <thead>
                <tr>
                    <th style="width: 30%; background-color: #f2f2f2; font-weight: bold; text-align: center;">Produkt</th>
                    <th style="width: 35%; background-color: #f2f2f2; font-weight: bold; text-align: center;">Opis</th>
                    <th style="width: 12%; background-color: #f2f2f2; font-weight: bold; text-align: center;">Ilość</th>
                    <th style="width: 11%; background-color: #f2f2f2; font-weight: bold; text-align: center;">Cena netto</th>
                    <th style="width: 12%; background-color: #f2f2f2; font-weight: bold; text-align: center;">Wartość netto</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <!-- Kolumna 1 (30%): Numer i sama nazwa produktu (bez kodu produktu!) + zdjęcie pod spodem -->
                    <td class="col-product">
                        <strong>{{ loop.index }}. {{ item.name }}</strong>
                        {% if item.image_base64 %}
                            <br><img src="{{ item.image_base64 }}" style="width: 130px; height: auto; margin-top: 8px;" />
                        {% endif %}
                    </td>

                    <!-- Kolumna 2 (35%): Pełny opis produktu z bazy + opcjonalny dodatkowy opis -->
                    <td class="col-desc">
                        <div>{{ item.description_html | safe }}</div>
                        {% if item.custom_notes %}
                            <div class="custom-notes">{{ item.custom_notes_html | safe }}</div>
                        {% endif %}
                    </td>

                    <!-- Kolumna 3 (12%): Ilość z wybraną jednostką miary (szt / kg / m) -->
                    <td class="col-qty">
                        {{ item.quantity_str }} {{ item.unit }}
                    </td>

                    <!-- Kolumna 4 (11%): Cena jednostkowa netto z przecinkiem -->
                    <td class="col-price">
                        {{ item.unit_price_str }}
                    </td>

                    <!-- Kolumna 5 (12%): Wartość pozycji netto z przecinkiem -->
                    <td class="col-total">
                        {{ item.total_price_str }}
                    </td>
                </tr>
                {% endfor %}

                <!-- Ostatni wyróżniony wiersz podsumowania -->
                <tr class="summary-row">
                    <td colspan="5">
                        RAZEM NETTO: {{ total_net_str }} zł
                    </td>
                </tr>
            </tbody>
        </table>
    </body>
    </html>
    """

    # Przygotowanie danych pozycji (pomijamy product_code, przekazujemy tylko czystą nazwę i opis)
    prepared_items = []
    for item in items_data:
        qty = int(round(float(item.get("quantity", 1))))
        unit = (item.get("unit") or "szt").strip()
        unit_price = float(item.get("unit_price", 0.0))
        row_total = float(item.get("total_price", qty * unit_price))
        raw_desc = item.get("description") or ""
        raw_notes = item.get("custom_notes") or ""
        image_url = item.get("image_url") or ""
        image_base64 = url_to_base64(image_url) if image_url else ""

        prepared_items.append(
            {
                "name": item.get("name", "Produkt"),
                "image_base64": image_base64,
                "description_html": "<br/>".join(raw_desc.splitlines()),
                "custom_notes": raw_notes.strip(),
                "custom_notes_html": "<br/>".join(raw_notes.splitlines()),
                "quantity_str": str(qty),
                "unit": unit,
                "unit_price_str": format_pl_currency(unit_price),
                "total_price_str": format_pl_currency(row_total),
            }
        )

    template = Template(html_template)
    rendered_html = template.render(
        logo_base64=logo_base64,
        offer_number=final_offer_number,
        offer_date=final_offer_date,
        client={
            "name": client_data.get("name", ""),
            "nip": client_data.get("nip") or "",
            "address": client_data.get("address") or "",
        },
        items=prepared_items,
        total_net_str=format_pl_currency(total_net),
    )

    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(
        src=io.BytesIO(rendered_html.encode("utf-8")),
        dest=pdf_buffer,
        encoding="utf-8",
        link_callback=pdf_link_callback,
    )

    if pisa_status.err:
        raise RuntimeError("Wystąpił błąd podczas renderowania dokumentu PDF przez xhtml2pdf.")

    return pdf_buffer.getvalue()


# 6. Kompresja i wysyłanie zdjęcia produktu do Supabase Storage
def compress_image(
    uploaded_file: Any,
    max_size: Tuple[int, int] = (500, 500),
    quality: int = 80,
) -> bytes:
    """
    Otwiera wgrany obraz, zastępuje ewentualną przezroczystość (RGBA/P) białym tłem,
    zmniejsza rozdzielczość z zachowaniem proporcji do max_size i kompresuje do formatu JPEG.
    Zwraca skompresowane bajty obrazu.
    """
    uploaded_file.seek(0)
    img = Image.open(uploaded_file)

    if img.mode in ("RGBA", "P", "LA"):
        rgba_img = img.convert("RGBA")
        background = Image.new("RGB", rgba_img.size, (255, 255, 255))
        alpha_mask = rgba_img.split()[3]
        background.paste(rgba_img, mask=alpha_mask)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail(max_size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def upload_product_image(supabase: Client, uploaded_file: Any) -> str:
    """
    Kompresuje wgrany plik graficzny do formatu JPEG (max 500x500px, jakość 80),
    wysyła go do publicznego bucketa w Supabase Storage pod unikalną nazwą .jpg
    i zwraca jego publiczny adres URL.
    """
    compressed_bytes = compress_image(uploaded_file)
    unique_filename = f"{uuid.uuid4()}.jpg"

    supabase.storage.from_(STORAGE_BUCKET).upload(
        path=unique_filename,
        file=compressed_bytes,
        file_options={"content-type": "image/jpeg"},
    )

    public_url = supabase.storage.from_(STORAGE_BUCKET).get_public_url(unique_filename)
    return public_url


# 7. Wspólna funkcja zapisu oferty do bazy (używana przy wersji roboczej i przy generowaniu PDF)
def save_offer_to_database(
    supabase: Client,
    current_client: Dict[str, Any],
    offer_items_state: List[Dict[str, Any]],
    products_by_id: Dict[str, Dict[str, Any]],
    total_net_value: float,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    Zapisuje ofertę w tabeli `offers` (wraz z wygenerowanym `offer_number`)
    oraz pozycje w tabeli `offer_items` (wraz z `unit` i `custom_notes`).
    Zwraca krotkę: (offer_id, offer_number, items_pdf_payload).
    """
    offer_number = generate_offer_number()

    offer_insert = (
        supabase.table("offers")
        .insert(
            {
                "client_id": current_client["id"],
                "total_net_value": round(total_net_value, 2),
                "offer_number": offer_number,
            }
        )
        .execute()
    )
    if not offer_insert.data:
        raise RuntimeError("Nie udało się uzyskać ID nowo utworzonej oferty.")

    offer_id = offer_insert.data[0]["id"]

    items_db_payload = []
    items_pdf_payload = []

    for item in offer_items_state:
        prod = products_by_id[item["product_id"]]
        qty = int(item["quantity"])
        unit = item.get("unit") if item.get("unit") in UNIT_OPTIONS else "szt"
        u_price = round(float(item["unit_price"]), 2)
        t_price = round(qty * u_price, 2)
        notes = (item.get("custom_notes") or "").strip()

        items_db_payload.append(
            {
                "offer_id": offer_id,
                "product_id": prod["id"],
                "quantity": qty,
                "unit": unit,
                "unit_price": u_price,
                "total_price": t_price,
                "custom_notes": notes if notes else None,
            }
        )

        # Do PDF przekazujemy wyłącznie czystą nazwę produktu (bez product_code!)
        items_pdf_payload.append(
            {
                "name": prod["name"],
                "description": prod.get("description") or "",
                "image_url": prod.get("image_url") or "",
                "quantity": qty,
                "unit": unit,
                "unit_price": u_price,
                "total_price": t_price,
                "custom_notes": notes,
            }
        )

    try:
        supabase.table("offer_items").insert(items_db_payload).execute()
    except Exception as db_err:
        if "custom_notes" in str(db_err):
            fallback_payload = [
                {k: v for k, v in row.items() if k != "custom_notes"}
                for row in items_db_payload
            ]
            supabase.table("offer_items").insert(fallback_payload).execute()
        else:
            raise db_err

    return offer_id, offer_number, items_pdf_payload


# 8. Widok zakładki "📄 Kreator Oferty" (tab1)
def render_creator_tab(supabase: Client) -> None:
    """Renderuje kreator oferty z dynamiczną listą pozycji, wyborem jednostek, zapisem wersji roboczej i eksportem do PDF."""
    st.header("📄 Kreator Oferty")
    init_creator_state()

    try:
        clients_resp = supabase.table("clients").select("*").order("name").execute()
        clients_data = clients_resp.data or []

        products_resp = supabase.table("products").select("*").order("name").execute()
        products_data = products_resp.data or []
    except Exception as e:
        st.error(f"❌ Błąd podczas pobierania danych z bazy: {e}")
        return

    if not clients_data:
        st.warning("⚠️ Brak klientów w bazie. Dodaj najpierw klienta w zakładce '🏢 Baza Klientów'.")
        return

    if not products_data:
        st.warning("⚠️ Brak produktów w bazie. Dodaj najpierw produkt w zakładce '📦 Baza Produktów'.")
        return

    clients_by_id = {c["id"]: c for c in clients_data}
    products_by_id = {p["id"]: p for p in products_data}
    client_ids = list(clients_by_id.keys())
    product_ids = list(products_by_id.keys())

    if st.session_state.selected_client_id not in clients_by_id:
        st.session_state.selected_client_id = client_ids[0]

    st.subheader("1. Dane Klienta")
    selected_client_index = client_ids.index(st.session_state.selected_client_id)

    chosen_client_id = st.selectbox(
        "Wybierz klienta z bazy:",
        options=client_ids,
        index=selected_client_index,
        format_func=lambda cid: f"{clients_by_id[cid]['name']} (NIP: {clients_by_id[cid].get('nip') or 'brak'})",
    )
    st.session_state.selected_client_id = chosen_client_id
    current_client = clients_by_id[chosen_client_id]

    with st.container(border=True):
        st.markdown(f"**Nazwa firmy:** {current_client['name']}")
        st.markdown(f"**NIP:** {current_client.get('nip') or '—'}")
        st.markdown(f"**Adres:** {current_client.get('address') or '—'}")

    st.divider()
    st.subheader("2. Pozycje w ofercie")

    # Nagłówki kolumn (dodana kolumna Jednostka obok Ilości)
    hdr1, hdr2, hdr_unit, hdr3, hdr4, hdr5 = st.columns([3.3, 1.0, 0.9, 1.4, 1.4, 0.6])
    hdr1.markdown("**Zdjęcie i Produkt (Kod + Nazwa)**")
    hdr2.markdown("**Ilość**")
    hdr_unit.markdown("**Jedn.**")
    hdr3.markdown("**Cena jedn. netto (zł)**")
    hdr4.markdown("**Suma netto (zł)**")
    hdr5.markdown("**Usuń**")

    total_net_value = 0.0
    row_to_remove: Optional[int] = None

    # Dynamiczna lista pozycji z st.session_state.offer_items
    for idx, item in enumerate(st.session_state.offer_items):
        row_key = item.get("row_id") or f"row_{idx}"
        item["row_id"] = row_key

        if item.get("product_id") not in products_by_id:
            item["product_id"] = product_ids[0]

        if item.get("unit") not in UNIT_OPTIONS:
            item["unit"] = "szt"

        with st.container(border=True):
            col1, col2, col_unit, col3, col4, col5 = st.columns([3.3, 1.0, 0.9, 1.4, 1.4, 0.6])

            # Kolumna 1: Wybór produktu (z kodem produktu), miniaturka, opis z bazy i dodatkowy opis
            with col1:
                prod_idx = product_ids.index(item["product_id"])
                selected_prod_id = st.selectbox(
                    f"Produkt (pozycja #{idx + 1})",
                    options=product_ids,
                    index=prod_idx,
                    format_func=lambda pid: format_product_label_with_code(products_by_id[pid]),
                    key=f"prod_select_{row_key}",
                )
                item["product_id"] = selected_prod_id
                prod_info = products_by_id[selected_prod_id]

                sub_img, sub_desc = st.columns([1, 2.5])
                with sub_img:
                    if prod_info.get("image_url"):
                        st.image(prod_info["image_url"], width=90)
                    else:
                        st.caption("Brak zdjęcia")
                with sub_desc:
                    if prod_info.get("product_code"):
                        st.caption(f"**Kod produktu:** `{prod_info['product_code']}`")
                    st.caption(prod_info.get("description") or "Brak domyślnego opisu w bazie.")

                custom_notes = st.text_input(
                    "Dodatkowy opis pozycji (opcjonalnie)",
                    value=item.get("custom_notes") or "",
                    placeholder="np. Znakowanie laserowe w cenie, termin realizacji 7 dni...",
                    key=f"notes_{row_key}",
                )
                item["custom_notes"] = custom_notes

            # Kolumna 2: Ilość
            with col2:
                qty = st.number_input(
                    "Ilość",
                    min_value=1,
                    step=1,
                    value=max(1, int(item.get("quantity") or 1)),
                    key=f"qty_{row_key}",
                    label_visibility="collapsed",
                )
                item["quantity"] = int(qty)

            # Kolumna 2b: Jednostka miary (szt / kg / m)
            with col_unit:
                unit_idx = UNIT_OPTIONS.index(item["unit"])
                selected_unit = st.selectbox(
                    "Jednostka",
                    options=UNIT_OPTIONS,
                    index=unit_idx,
                    key=f"unit_{row_key}",
                    label_visibility="collapsed",
                )
                item["unit"] = selected_unit

            # Kolumna 3: Cena jednostkowa netto
            with col3:
                unit_price = st.number_input(
                    "Cena jedn. netto",
                    min_value=0.0,
                    step=0.01,
                    value=float(item.get("unit_price") or 0.0),
                    format="%.2f",
                    key=f"price_{row_key}",
                    label_visibility="collapsed",
                )
                item["unit_price"] = float(unit_price)

            # Kolumna 4: Suma netto wyliczona automatycznie
            row_total = float(item["quantity"]) * float(item["unit_price"])
            total_net_value += row_total
            with col4:
                st.markdown(f"### `{format_pl_currency(row_total)} zł`")

            # Kolumna 5: Przycisk usunięcia wiersza
            with col5:
                if st.button("🗑️", key=f"del_{row_key}", help="Usuń tę pozycję"):
                    row_to_remove = idx

    if row_to_remove is not None:
        if len(st.session_state.offer_items) > 1:
            st.session_state.offer_items.pop(row_to_remove)
        else:
            st.session_state.offer_items = [create_empty_offer_item(product_ids[0])]
        st.rerun()

    # Przyciski zarządzania listą pozycji
    btn_col1, btn_col2, _ = st.columns([1.5, 1.5, 4])
    with btn_col1:
        if st.button("➕ Dodaj kolejną pozycję", use_container_width=True):
            st.session_state.offer_items.append(create_empty_offer_item(product_ids[0]))
            st.rerun()

    with btn_col2:
        if st.button("🧹 Wyczyść formularz", use_container_width=True):
            st.session_state.offer_items = [create_empty_offer_item(product_ids[0])]
            st.session_state.pop("last_generated_pdf", None)
            st.rerun()

    st.divider()

    # Podsumowanie pozycji (z widocznym kodem produktu w podsumowaniu kreatora) i łączna kwota netto
    sum_left, sum_right = st.columns([2.5, 1.5])
    with sum_left:
        with st.expander("📋 Podgląd podsumowania pozycji (z kodami produktów)", expanded=False):
            summary_rows = []
            for idx, it in enumerate(st.session_state.offer_items, start=1):
                p_obj = products_by_id[it["product_id"]]
                q_val = int(it["quantity"])
                u_val = float(it["unit_price"])
                summary_rows.append(
                    {
                        "Lp.": idx,
                        "Kod i Nazwa": format_product_label_with_code(p_obj),
                        "Ilość": f"{q_val} {it.get('unit', 'szt')}",
                        "Cena netto": f"{format_pl_currency(u_val)} zł",
                        "Wartość netto": f"{format_pl_currency(q_val * u_val)} zł",
                    }
                )
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    with sum_right:
        st.markdown(
            f"<h2 style='text-align: right;'>RAZEM NETTO: {format_pl_currency(total_net_value)} zł</h2>",
            unsafe_allow_html=True,
        )

    # Dwa przyciski zapisu: Wersja robocza oraz Zapis + Generowanie PDF
    save_col_draft, save_col_pdf = st.columns(2)

    with save_col_draft:
        if st.button(
            "💾 Zapisz ofertę jako wersję roboczą",
            use_container_width=True,
        ):
            try:
                with st.spinner("Zapisywanie wersji roboczej w bazie Supabase..."):
                    _, offer_number, _ = save_offer_to_database(
                        supabase=supabase,
                        current_client=current_client,
                        offer_items_state=st.session_state.offer_items,
                        products_by_id=products_by_id,
                        total_net_value=total_net_value,
                    )
                    st.session_state.pop("last_generated_pdf", None)
                    st.success(f"Oferta zapisana! (Numer w historii: {offer_number})")
            except Exception as e:
                st.error(f"❌ Wystąpił błąd podczas zapisywania wersji roboczej: {e}")

    with save_col_pdf:
        if st.button(
            "💾 Zapisz ofertę w historii i wygeneruj PDF",
            type="primary",
            use_container_width=True,
        ):
            try:
                with st.spinner("Zapisywanie oferty w bazie Supabase i generowanie pliku PDF..."):
                    _, offer_number, items_pdf_payload = save_offer_to_database(
                        supabase=supabase,
                        current_client=current_client,
                        offer_items_state=st.session_state.offer_items,
                        products_by_id=products_by_id,
                        total_net_value=total_net_value,
                    )

                    today_str = datetime.now().strftime("%d.%m.%Y")
                    pdf_bytes = generate_pdf(
                        client_data=current_client,
                        items_data=items_pdf_payload,
                        total_net=total_net_value,
                        offer_number=offer_number,
                        offer_date=today_str,
                    )
                    safe_client_name = sanitize_filename(current_client["name"])
                    safe_offer_no = offer_number.replace("/", "_")
                    pdf_filename = f"Oferta_{safe_offer_no}_{safe_client_name}.pdf"

                    st.session_state["last_generated_pdf"] = {
                        "bytes": pdf_bytes,
                        "filename": pdf_filename,
                        "client_name": current_client["name"],
                        "offer_number": offer_number,
                    }

                    st.success(
                        f"✅ Oferta ({offer_number}) została zapisana w historii, a plik PDF jest gotowy do pobrania poniżej!"
                    )
            except Exception as e:
                st.error(f"❌ Wystąpił błąd podczas zapisu oferty lub generowania PDF: {e}")

    # Wyświetlenie przycisku pobrania wygenerowanego PDF
    if "last_generated_pdf" in st.session_state:
        pdf_info = st.session_state["last_generated_pdf"]
        st.download_button(
            label=f"📥 Pobierz wygenerowany plik: {pdf_info['filename']}",
            data=pdf_info["bytes"],
            file_name=pdf_info["filename"],
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )


# 9. Widok zakładki "📦 Baza Produktów" (tab2)
def render_products_tab(supabase: Client) -> None:
    """Renderuje interfejs zarządzania produktami (dodawanie z kodem produktu, podgląd, edycja, usuwanie)."""
    st.header("📦 Baza Produktów")

    with st.expander("➕ Dodaj nowy produkt", expanded=False):
        with st.form("add_product_form", clear_on_submit=True):
            col_code, col_name = st.columns([1, 2])
            with col_code:
                prod_code = st.text_input("Kod produktu", placeholder="np. PRD-001")
            with col_name:
                prod_name = st.text_input("Nazwa produktu *", placeholder="np. Długopis metalowy z grawerem")

            prod_desc = st.text_area(
                "Opis produktu *",
                placeholder="Szczegółowy opis parametrów technicznych i zakresu dostawy...",
            )
            prod_image = st.file_uploader(
                "Zdjęcie produktu (opcjonalnie)",
                type=["png", "jpg", "jpeg", "webp"],
            )
            submit_product = st.form_submit_button("💾 Zapisz produkt", type="primary")

            if submit_product:
                if not prod_name.strip() or not prod_desc.strip():
                    st.warning("⚠️ Pola 'Nazwa produktu' oraz 'Opis produktu' są wymagane.")
                else:
                    try:
                        image_url = None
                        if prod_image is not None:
                            image_url = upload_product_image(supabase, prod_image)

                        supabase.table("products").insert(
                            {
                                "product_code": prod_code.strip() if prod_code and prod_code.strip() else None,
                                "name": prod_name.strip(),
                                "description": prod_desc.strip(),
                                "image_url": image_url,
                            }
                        ).execute()

                        set_flash_message(
                            "success",
                            f"✅ Produkt '{prod_name.strip()}' został pomyślnie zapisany w bazie.",
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(
                            f"❌ Błąd podczas zapisywania produktu lub wgrywania zdjęcia "
                            f"(upewnij się, że w Supabase Storage istnieje publiczny bucket `{STORAGE_BUCKET}`): {e}"
                        )

    try:
        response = (
            supabase.table("products")
            .select("id, product_code, name, description, image_url, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        products_data = response.data or []
    except Exception as e:
        st.error(f"❌ Nie udało się pobrać listy produktów z bazy danych: {e}")
        return

    st.subheader(f"Lista produktów ({len(products_data)})")

    if not products_data:
        st.info("Brak produktów w bazie. Rozwiń formularz powyżej, aby dodać pierwszy produkt.")
        return

    df_products = pd.DataFrame(products_data)
    if "product_code" not in df_products.columns:
        df_products["product_code"] = ""
    df_products["product_code"] = df_products["product_code"].fillna("")
    df_products["created_at"] = pd.to_datetime(df_products["created_at"]).dt.strftime("%Y-%m-%d %H:%M")

    st.dataframe(
        df_products[["image_url", "product_code", "name", "description", "created_at"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "image_url": st.column_config.ImageColumn(
                "Zdjęcie",
                help="Miniaturka zdjęcia produktu",
                width="small",
            ),
            "product_code": st.column_config.TextColumn("Kod produktu", width="small"),
            "name": st.column_config.TextColumn("Nazwa produktu", width="medium"),
            "description": st.column_config.TextColumn("Opis produktu", width="large"),
            "created_at": st.column_config.TextColumn("Data dodania", width="small"),
        },
    )

    st.divider()

    st.subheader("🛠️ Edytuj lub usuń produkt")
    product_options = {
        f"{format_product_label_with_code(p)} (ID: {p['id'][:8]}...)": p for p in products_data
    }
    selected_label = st.selectbox(
        "Wybierz produkt z listy:",
        options=list(product_options.keys()),
        key="select_product_manage",
    )
    selected_product = product_options[selected_label]

    col_edit, col_delete = st.columns([2, 1])

    with col_edit:
        with st.form("edit_product_form"):
            st.markdown("**Aktualizacja danych produktu**")
            e_col_code, e_col_name = st.columns([1, 2])
            with e_col_code:
                new_code = st.text_input(
                    "Kod produktu",
                    value=selected_product.get("product_code") or "",
                )
            with e_col_name:
                new_name = st.text_input("Nazwa produktu *", value=selected_product["name"])

            new_desc = st.text_area("Opis produktu *", value=selected_product["description"] or "")
            new_image = st.file_uploader(
                "Nowe zdjęcie produktu (pozostaw puste, aby zachować obecne)",
                type=["png", "jpg", "jpeg", "webp"],
                key=f"edit_img_{selected_product['id']}",
            )
            update_btn = st.form_submit_button("✏️ Zaktualizuj produkt", type="primary")

            if update_btn:
                if not new_name.strip() or not new_desc.strip():
                    st.warning("⚠️ Nazwa i opis produktu nie mogą być puste.")
                else:
                    try:
                        update_payload = {
                            "product_code": new_code.strip() if new_code and new_code.strip() else None,
                            "name": new_name.strip(),
                            "description": new_desc.strip(),
                        }
                        if new_image is not None:
                            update_payload["image_url"] = upload_product_image(supabase, new_image)

                        supabase.table("products").update(update_payload).eq(
                            "id", selected_product["id"]
                        ).execute()

                        set_flash_message("success", f"✅ Produkt '{new_name.strip()}' został zaktualizowany.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Błąd podczas aktualizacji produktu: {e}")

    with col_delete:
        st.markdown("**Usuwanie produktu**")
        if selected_product.get("image_url"):
            st.image(selected_product["image_url"], caption=selected_product["name"], width=150)
        st.warning("Usunięcie produktu jest nieodwracalne.")
        if st.button("🗑️ Usuń produkt", type="primary", key="delete_product_btn"):
            try:
                supabase.table("products").delete().eq("id", selected_product["id"]).execute()
                set_flash_message("success", f"🗑️ Produkt '{selected_product['name']}' został usunięty z bazy.")
                st.rerun()
            except Exception as e:
                err_msg = str(e)
                if "23503" in err_msg or "foreign key" in err_msg.lower():
                    st.error(
                        "❌ Nie można usunąć tego produktu, ponieważ jest już przypisany do jednej lub więcej ofert w historii."
                    )
                else:
                    st.error(f"❌ Błąd podczas usuwania produktu: {e}")


# 10. Widok zakładki "🏢 Baza Klientów" (tab3)
def render_clients_tab(supabase: Client) -> None:
    """Renderuje interfejs zarządzania klientami (dodawanie, podgląd, edycja, usuwanie)."""
    st.header("🏢 Baza Klientów")

    with st.expander("➕ Dodaj nowego klienta", expanded=False):
        with st.form("add_client_form", clear_on_submit=True):
            client_name = st.text_input("Nazwa firmy *", placeholder="np. Budimex Sp. z o.o.")
            client_nip = st.text_input("NIP", placeholder="np. 123-456-78-90")
            client_address = st.text_input(
                "Adres",
                placeholder="np. ul. Jesionowa 12, 55-032 Przykładowo",
            )
            submit_client = st.form_submit_button("💾 Zapisz klienta", type="primary")

            if submit_client:
                if not client_name.strip():
                    st.warning("⚠️ Pole 'Nazwa firmy' jest wymagane.")
                else:
                    try:
                        supabase.table("clients").insert(
                            {
                                "name": client_name.strip(),
                                "nip": client_nip.strip() if client_nip else None,
                                "address": client_address.strip() if client_address else None,
                            }
                        ).execute()

                        set_flash_message(
                            "success",
                            f"✅ Klient '{client_name.strip()}' został pomyślnie zapisany w bazie.",
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Błąd podczas zapisywania klienta: {e}")

    try:
        response = (
            supabase.table("clients")
            .select("id, name, nip, address, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        clients_data = response.data or []
    except Exception as e:
        st.error(f"❌ Nie udało się pobrać listy klientów z bazy danych: {e}")
        return

    st.subheader(f"Lista klientów ({len(clients_data)})")

    if not clients_data:
        st.info("Brak klientów w bazie. Rozwiń formularz powyżej, aby dodać pierwszego klienta.")
        return

    df_clients = pd.DataFrame(clients_data)
    df_clients["created_at"] = pd.to_datetime(df_clients["created_at"]).dt.strftime("%Y-%m-%d %H:%M")

    st.dataframe(
        df_clients[["name", "nip", "address", "created_at"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "name": st.column_config.TextColumn("Nazwa firmy", width="medium"),
            "nip": st.column_config.TextColumn("NIP", width="small"),
            "address": st.column_config.TextColumn("Adres", width="large"),
            "created_at": st.column_config.TextColumn("Data dodania", width="small"),
        },
    )

    st.divider()

    st.subheader("🛠️ Edytuj lub usuń klienta")
    client_options = {
        f"{c['name']} (NIP: {c.get('nip') or 'brak'})": c for c in clients_data
    }
    selected_client_label = st.selectbox(
        "Wybierz klienta z listy:",
        options=list(client_options.keys()),
        key="select_client_manage",
    )
    selected_client = client_options[selected_client_label]

    col_edit, col_delete = st.columns([2, 1])

    with col_edit:
        with st.form("edit_client_form"):
            st.markdown("**Aktualizacja danych klienta**")
            edit_name = st.text_input("Nazwa firmy *", value=selected_client["name"])
            edit_nip = st.text_input("NIP", value=selected_client.get("nip") or "")
            edit_address = st.text_input("Adres", value=selected_client.get("address") or "")
            update_client_btn = st.form_submit_button("✏️ Zaktualizuj dane klienta", type="primary")

            if update_client_btn:
                if not edit_name.strip():
                    st.warning("⚠️ Nazwa firmy nie może być pusta.")
                else:
                    try:
                        supabase.table("clients").update(
                            {
                                "name": edit_name.strip(),
                                "nip": edit_nip.strip() if edit_nip else None,
                                "address": edit_address.strip() if edit_address else None,
                            }
                        ).eq("id", selected_client["id"]).execute()

                        set_flash_message(
                            "success",
                            f"✅ Dane klienta '{edit_name.strip()}' zostały zaktualizowane.",
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Błąd podczas aktualizacji klienta: {e}")

    with col_delete:
        st.markdown("**Usuwanie klienta**")
        st.warning("Usunięcie klienta z bazy jest nieodwracalne.")
        if st.button("🗑️ Usuń klienta", type="primary", key="delete_client_btn"):
            try:
                supabase.table("clients").delete().eq("id", selected_client["id"]).execute()
                set_flash_message(
                    "success",
                    f"🗑️ Klient '{selected_client['name']}' został usunięty z bazy.",
                )
                st.rerun()
            except Exception as e:
                err_msg = str(e)
                if "23503" in err_msg or "foreign key" in err_msg.lower():
                    st.error(
                        "❌ Nie można usunąć tego klienta, ponieważ posiada on już przypisane oferty w bazie danych."
                    )
                else:
                    st.error(f"❌ Błąd podczas usuwania klienta: {e}")


# 11. Widok zakładki "🕰️ Historia Ofert" (tab4)
def render_history_tab(supabase: Client) -> None:
    """
    Renderuje historię zapisanych ofert z możliwością podglądu pozycji,
    powielenia oferty do kreatora, pobrania PDF oraz usunięcia oferty z bazy.
    """
    st.header("🕰️ Historia Ofert")

    try:
        response = (
            supabase.table("offers")
            .select("*, clients(*), offer_items(*, products(*))")
            .order("created_at", desc=True)
            .execute()
        )
        offers_data = response.data or []
    except Exception as e:
        st.error(f"❌ Nie udało się pobrać historii ofert z bazy danych: {e}")
        return

    if not offers_data:
        st.info("Brak zapisanych ofert w historii. Utwórz pierwszą ofertę w zakładce '📄 Kreator Oferty'.")
        return

    st.caption(f"Łączna liczba zapisanych ofert: **{len(offers_data)}**")

    for offer in offers_data:
        offer_id = offer["id"]
        created_dt = pd.to_datetime(offer["created_at"])
        created_str = created_dt.strftime("%Y-%m-%d %H:%M")
        offer_date_pdf = created_dt.strftime("%d.%m.%Y")
        offer_number = offer.get("offer_number") or f"OFE/{created_dt.strftime('%Y/%m')}/{offer_id[:4].upper()}"
        client_info = offer.get("clients") or {}
        client_name = client_info.get("name", "Nieznany klient")
        total_net = float(offer.get("total_net_value") or 0.0)
        offer_items_raw = offer.get("offer_items") or []

        expander_title = (
            f"📄 {offer_number}  |  📅 {created_str}  |  🏢 {client_name}  |  💰 RAZEM NETTO: {format_pl_currency(total_net)} zł"
        )

        with st.expander(expander_title, expanded=False):
            st.markdown(
                f"**Numer oferty:** `{offer_number}` | "
                f"**Klient:** {client_name} | "
                f"**NIP:** {client_info.get('nip') or '—'} | "
                f"**Adres:** {client_info.get('address') or '—'}"
            )

            # Przygotowanie listy pozycji do wyświetlenia w podglądzie oraz do PDF (bez kodu produktu w PDF)
            preview_rows = []
            pdf_items = []

            for idx, oi in enumerate(offer_items_raw, start=1):
                prod = oi.get("products") or {}
                qty = int(round(float(oi.get("quantity") or 1)))
                unit = oi.get("unit") if oi.get("unit") in UNIT_OPTIONS else "szt"
                u_price = float(oi.get("unit_price") or 0.0)
                t_price = float(oi.get("total_price") or (qty * u_price))
                notes = oi.get("custom_notes") or ""

                preview_rows.append(
                    {
                        "Lp.": idx,
                        "Kod produktu": prod.get("product_code") or "—",
                        "Produkt": prod.get("name", "Usunięty produkt"),
                        "Dodatkowy opis": notes,
                        "Ilość": f"{qty} {unit}",
                        "Cena netto": f"{format_pl_currency(u_price)} zł",
                        "Wartość netto": f"{format_pl_currency(t_price)} zł",
                    }
                )

                # Na PDF przekazujemy wyłącznie czystą nazwę produktu (bez product_code)
                pdf_items.append(
                    {
                        "name": prod.get("name", "Produkt"),
                        "description": prod.get("description") or "",
                        "image_url": prod.get("image_url") or "",
                        "quantity": qty,
                        "unit": unit,
                        "unit_price": u_price,
                        "total_price": t_price,
                        "custom_notes": notes,
                    }
                )

            if preview_rows:
                st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
            else:
                st.warning("Brak zapisanych pozycji dla tej oferty.")

            # Trzy przyciski akcji dla historycznej oferty
            act_col1, act_col2, act_col3 = st.columns(3)

            # 1. Powiel ofertę (wczytuje klienta, pozycje oraz jednostki miary do kreatora)
            with act_col1:
                if st.button(
                    "🔄 Powiel ofertę",
                    key=f"load_offer_{offer_id}",
                    use_container_width=True,
                ):
                    st.session_state.selected_client_id = offer.get("client_id")
                    loaded_items = []
                    for oi in offer_items_raw:
                        unit_val = oi.get("unit") if oi.get("unit") in UNIT_OPTIONS else "szt"
                        loaded_items.append(
                            {
                                "row_id": str(uuid.uuid4()),
                                "product_id": oi.get("product_id"),
                                "quantity": int(round(float(oi.get("quantity") or 1))),
                                "unit": unit_val,
                                "unit_price": float(oi.get("unit_price") or 0.0),
                                "custom_notes": oi.get("custom_notes") or "",
                            }
                        )
                    st.session_state.offer_items = (
                        loaded_items if loaded_items else [create_empty_offer_item()]
                    )
                    st.success(
                        "✅ Oferta została powielona do kreatora! Przejdź do zakładki '📄 Kreator Oferty', aby ją zmodyfikować."
                    )

            # 2. Pobierz PDF
            with act_col2:
                try:
                    pdf_bytes = generate_pdf(
                        client_data=client_info,
                        items_data=pdf_items,
                        total_net=total_net,
                        offer_number=offer_number,
                        offer_date=offer_date_pdf,
                    )
                    safe_name = sanitize_filename(client_name)
                    safe_offer_no = offer_number.replace("/", "_")
                    st.download_button(
                        label="📄 Pobierz PDF",
                        data=pdf_bytes,
                        file_name=f"Oferta_{safe_offer_no}_{safe_name}.pdf",
                        mime="application/pdf",
                        key=f"dl_pdf_{offer_id}",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Błąd PDF: {e}")

            # 3. Usuń z historii
            with act_col3:
                if st.button(
                    "🗑️ Usuń z historii",
                    key=f"del_offer_{offer_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        supabase.table("offers").delete().eq("id", offer_id).execute()
                        set_flash_message("success", "🗑️ Oferta została usunięta z historii.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Błąd podczas usuwania oferty: {e}")


# 12. Główna funkcja aplikacji
def main() -> None:
    # Wstrzyknięcie firmowej kolorystyki (#54B6A6)
    apply_custom_theme()

    # Blokada aplikacji przed nieautoryzowanym dostępem
    if not check_authentication():
        st.stop()

    # Inicjalizacja klienta Supabase oraz stanu kreatora
    supabase = init_connection()
    init_creator_state()

    st.title("📄 System Zarządzania Ofertami")

    # Wyświetlenie ewentualnych powiadomień po przeładowaniu (st.rerun)
    show_flash_message()

    # Główna nawigacja oparta na zakładkach
    tab1, tab2, tab3, tab4 = st.tabs([
        "📄 Kreator Oferty",
        "📦 Baza Produktów",
        "🏢 Baza Klientów",
        "🕰️ Historia Ofert",
    ])

    with tab1:
        render_creator_tab(supabase)

    with tab2:
        render_products_tab(supabase)

    with tab3:
        render_clients_tab(supabase)

    with tab4:
        render_history_tab(supabase)


if __name__ == "__main__":
    main()
