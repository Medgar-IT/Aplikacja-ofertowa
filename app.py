import base64
import io
import os
import re
import uuid
from datetime import date, datetime
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
UNIT_OPTIONS = ["szt", "kg", "mb", "kpl"]
DOC_TYPE_OPTIONS = ["Oferta", "Zamówienie"]
PAYMENT_TERMS_OPTIONS = ["7 dni", "14 dni", "21 dni", "30 dni", "Przedpłata"]
VAT_RATE = 0.23

BASE_DIR = Path(__file__).resolve().parent

FONT_MONTSERRAT_REGULAR = BASE_DIR / "Montserrat-Regular.ttf"
FONT_MONTSERRAT_BOLD = BASE_DIR / "Montserrat-Bold.ttf"
FONT_OPENSANS_REGULAR = BASE_DIR / "OpenSans-Regular.ttf"
FONT_OPENSANS_BOLD = BASE_DIR / "OpenSans-Bold.ttf"

FONT_URLS = {
    FONT_MONTSERRAT_REGULAR: [
        "https://cdn.jsdelivr.net/gh/JulietaUla/Montserrat@master/fonts/ttf/Montserrat-Regular.ttf",
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Regular.ttf",
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf",
    ],
    FONT_MONTSERRAT_BOLD: [
        "https://cdn.jsdelivr.net/gh/JulietaUla/Montserrat@master/fonts/ttf/Montserrat-Bold.ttf",
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf",
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
    ],
    FONT_OPENSANS_REGULAR: [
        "https://cdn.jsdelivr.net/npm/open-sans-all@0.1.3/fonts/OpenSans-Regular.ttf",
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf",
    ],
    FONT_OPENSANS_BOLD: [
        "https://cdn.jsdelivr.net/npm/open-sans-all@0.1.3/fonts/OpenSans-Bold.ttf",
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
    ],
}

# 1. Konfiguracja strony (musi być pierwszym wywołaniem Streamlit w skrypcie)
st.set_page_config(
    page_title="System Ofertowy - Proles Concept",
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


# 4. Inicjalizacja stanu sesji dla Kreatora Dokumentu oraz funkcje pomocnicze
def create_empty_offer_item(default_product_id: Optional[str] = None) -> Dict[str, Any]:
    """Tworzy nowy, domyślny wiersz pozycji w kreatorze dokumentu."""
    return {
        "row_id": str(uuid.uuid4()),
        "product_id": default_product_id,
        "quantity": 1,
        "unit": "szt",
        "unit_price": 0.0,
        "custom_notes": "",
    }


def init_creator_state() -> None:
    """Inicjuje zmienne w st.session_state odpowiedzialne za aktualnie budowany dokument."""
    if "document_type" not in st.session_state:
        st.session_state.document_type = "Oferta"

    if "selected_client_id" not in st.session_state:
        st.session_state.selected_client_id = None

    if "realization_date" not in st.session_state:
        st.session_state.realization_date = date.today()

    if "payment_terms" not in st.session_state:
        st.session_state.payment_terms = "7 dni"

    if "delivery_address" not in st.session_state:
        st.session_state.delivery_address = ""

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


def generate_offer_number(document_type: str = "Oferta") -> str:
    """Generuje unikalny numer dokumentu (np. OFE/2026/11/A1B2 lub ZAM/2026/11/A1B2)."""
    now = datetime.now()
    prefix = "ZAM" if document_type == "Zamówienie" else "OFE"
    short_uid = uuid.uuid4().hex[:4].upper()
    return f"{prefix}/{now.strftime('%Y/%m')}/{short_uid}"


def format_product_label_with_code(product: Dict[str, Any]) -> str:
    """Zwraca nazwę produktu wraz z jego kodem (do wyświetlenia w Kreatorze i na listach)."""
    code = (product.get("product_code") or "").strip()
    name = product.get("name", "Produkt")
    return f"[{code}] {name}" if code else name


def format_date_pl(val: Any) -> str:
    """Formatuje datę (obiekt date/datetime lub string YYYY-MM-DD) do postaci DD.MM.YYYY."""
    if not val:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%d.%m.%Y")
    try:
        return pd.to_datetime(str(val)).strftime("%d.%m.%Y")
    except Exception:
        return str(val)


# 5. Konwersja obrazów z URL do Base64, obsługa czcionek (Montserrat + Open Sans) i generator PDF
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


@st.cache_resource
def ensure_modern_fonts() -> bool:
    """
    Sprawdza przy starcie aplikacji, czy w katalogu głównym istnieją pliki czcionek:
    Montserrat-Regular.ttf, Montserrat-Bold.ttf, OpenSans-Regular.ttf, OpenSans-Bold.ttf.
    Jeśli nie, pobiera je z publicznych, zaufanych źródeł CDN/GitHub i zapisuje lokalnie.
    """
    for font_path, urls in FONT_URLS.items():
        if font_path.exists() and font_path.stat().st_size > 15_000:
            continue
        for url in urls:
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                resp.raise_for_status()
                if len(resp.content) > 15_000:
                    font_path.write_bytes(resp.content)
                    break
            except Exception:
                continue
    return True


def pdf_link_callback(uri: str, rel: str) -> str:
    """
    Funkcja pomocnicza dla xhtml2pdf rozwiązująca ścieżki do lokalnych plików .ttf
    oraz przepuszczająca obrazy zakodowane jako data:image/...;base64.
    """
    if uri.startswith("data:"):
        return uri

    font_map = {
        "Montserrat-Regular.ttf": FONT_MONTSERRAT_REGULAR,
        "Montserrat-Bold.ttf": FONT_MONTSERRAT_BOLD,
        "OpenSans-Regular.ttf": FONT_OPENSANS_REGULAR,
        "OpenSans-Bold.ttf": FONT_OPENSANS_BOLD,
    }
    for fname, fpath in font_map.items():
        if uri == fname or uri.endswith(f"/{fname}"):
            return str(fpath)

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
    document_type: str = "Oferta",
    delivery_address: Optional[str] = None,
    realization_date: Optional[Any] = None,
    payment_terms: Optional[str] = None,
) -> bytes:
    """
    Generuje nowoczesny dokument PDF (Oferta lub Zamówienie) z użyciem czcionek Montserrat i Open Sans.
    Na PDF celowo pomijany jest kod produktu (widoczna jest wyłącznie nazwa i opis).
    """
    ensure_modern_fonts()

    is_order = document_type == "Zamówienie"
    doc_title_prefix = "ZAMÓWIENIE" if is_order else "OFERTA"

    logo_base64 = url_to_base64(LOGO_URL)
    final_offer_number = offer_number or generate_offer_number(document_type)
    final_offer_date = offer_date or datetime.now().strftime("%d.%m.%Y")
    final_realization_date = format_date_pl(realization_date)
    final_delivery_address = (delivery_address or "").strip()
    final_payment_terms = (payment_terms or "7 dni").strip()

    vat_amount = round(total_net * VAT_RATE, 2)
    total_gross = round(total_net + vat_amount, 2)

    html_template = """
    <!DOCTYPE html>
    <html lang="pl">
    <head>
        <meta charset="utf-8">
        <style>
            @font-face {
                font-family: 'Open Sans';
                src: url('OpenSans-Regular.ttf');
                font-weight: normal;
                font-style: normal;
            }
            @font-face {
                font-family: 'Open Sans';
                src: url('OpenSans-Bold.ttf');
                font-weight: bold;
                font-style: normal;
            }
            @font-face {
                font-family: 'Montserrat';
                src: url('Montserrat-Regular.ttf');
                font-weight: normal;
                font-style: normal;
            }
            @font-face {
                font-family: 'Montserrat';
                src: url('Montserrat-Bold.ttf');
                font-weight: bold;
                font-style: normal;
            }
            @page {
                size: A4 portrait;
                margin: 15mm;
            }
            body {
                background-color: #FFFFFF;
                font-family: 'Open Sans', sans-serif;
                font-size: 9.5pt;
                color: #333333;
                line-height: 1.4;
            }
            table.top-header-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 10px;
            }
            table.top-header-table td {
                border: none;
                padding: 0;
                vertical-align: middle;
            }
            .logo-fallback {
                font-family: 'Montserrat', sans-serif;
                font-size: 20pt;
                font-weight: bold;
                letter-spacing: 1px;
                color: #333333;
            }
            .doc-title-block {
                text-align: right;
            }
            .doc-title-main {
                font-family: 'Montserrat', sans-serif;
                font-size: 14pt;
                font-weight: bold;
                color: #333333;
                margin-bottom: 3px;
            }
            .doc-title-accent {
                color: #54B6A6;
            }
            .doc-date {
                font-family: 'Open Sans', sans-serif;
                font-size: 9.5pt;
                color: #333333;
            }
            .brand-divider {
                height: 2px;
                background-color: #54B6A6;
                margin-top: 6px;
                margin-bottom: 16px;
            }
            table.parties-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 16px;
            }
            table.parties-table td {
                width: 50%;
                border: 1px solid #E6E6E6;
                background-color: #F9F9F9;
                padding: 10px 12px;
                vertical-align: top;
                font-size: 9.5pt;
                color: #333333;
            }
            .party-heading {
                font-family: 'Montserrat', sans-serif;
                font-size: 9.5pt;
                font-weight: bold;
                color: #54B6A6;
                margin-bottom: 5px;
                letter-spacing: 0.5px;
            }
            .party-name {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                font-size: 10pt;
                color: #333333;
                margin-bottom: 2px;
            }
            .party-line {
                margin: 2px 0;
                color: #333333;
            }
            table.offer-table {
                width: 100%;
                border-collapse: collapse;
                border: 1px solid #DCDCDC;
            }
            table.offer-table th {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                font-size: 9.5pt;
                color: #333333;
                background-color: #F9F9F9;
                border: 1px solid #DCDCDC;
                border-bottom: 2px solid #54B6A6;
                padding: 8px;
                text-align: center;
                vertical-align: middle;
            }
            table.offer-table td {
                border: 1px solid #DCDCDC;
                padding: 8px;
                vertical-align: middle;
                color: #333333;
            }
            .col-product {
                width: 30%;
                text-align: center;
                vertical-align: top;
            }
            .product-name {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                font-size: 10pt;
                color: #333333;
            }
            .col-desc {
                width: 35%;
                text-align: left;
                vertical-align: middle;
            }
            .custom-notes {
                margin-top: 6px;
                font-size: 9pt;
                color: #333333;
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
                font-weight: bold;
            }
            .summary-offer-row td {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                font-size: 11pt;
                color: #333333;
                padding: 10px 8px;
                text-align: right;
                vertical-align: middle;
                background-color: #F9F9F9;
                border-top: 2px solid #54B6A6;
            }
            table.order-details-box {
                width: 100%;
                border-collapse: collapse;
                margin-top: 14px;
            }
            table.order-details-box td {
                background-color: #F9F9F9;
                border: 1px solid #E6E6E6;
                border-left: 3px solid #54B6A6;
                padding: 9px 12px;
                font-size: 9.5pt;
                color: #333333;
            }
            .order-details-heading {
                font-family: 'Montserrat', sans-serif;
                font-size: 9.5pt;
                font-weight: bold;
                color: #54B6A6;
                margin-bottom: 4px;
                letter-spacing: 0.4px;
            }
            .order-label {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                color: #333333;
            }
            table.order-summary-wrapper {
                width: 100%;
                border-collapse: collapse;
                margin-top: 12px;
            }
            table.order-summary-box {
                width: 100%;
                border-collapse: collapse;
            }
            table.order-summary-box td {
                background-color: #F9F9F9;
                border: 1px solid #E6E6E6;
                padding: 6px 10px;
                font-size: 9.5pt;
                color: #333333;
            }
            table.order-summary-box tr.gross-row td {
                font-family: 'Montserrat', sans-serif;
                font-weight: bold;
                font-size: 11pt;
                color: #333333;
                border-top: 2px solid #54B6A6;
                padding: 9px 10px;
            }
            .gross-accent {
                color: #54B6A6;
            }
        </style>
    </head>
    <body>
        <!-- Górny pasek: Logo po lewej, Tytuł dokumentu (OFERTA / ZAMÓWIENIE) i Data po prawej -->
        <table class="top-header-table">
            <tr>
                <td style="width: 50%; text-align: left;">
                    {% if logo_base64 %}
                        <img src="{{ logo_base64 }}" style="width: 210px; height: auto;" alt="Proles Concept" />
                    {% else %}
                        <div class="logo-fallback">proles CONCEPT</div>
                    {% endif %}
                </td>
                <td class="doc-title-block" style="width: 50%;">
                    <div class="doc-title-main">
                        {{ doc_title_prefix }} <span class="doc-title-accent">NR {{ offer_number }}</span>
                    </div>
                    <div class="doc-date">Data wystawienia: {{ offer_date }}</div>
                </td>
            </tr>
        </table>

        <!-- Subtelna linia oddzielająca w kolorze miętowej zieleni PROLES (#54B6A6) -->
        <div class="brand-divider"></div>

        <!-- Dwie kolumny: SPRZEDAWCA (lewa) oraz KLIENT (prawa) -->
        <table class="parties-table">
            <tr>
                <td>
                    <div class="party-heading">SPRZEDAWCA</div>
                    <div class="party-name">Proles Concept Robert Czajczyk</div>
                    <div class="party-line">ul. Kolejowa 3, 32-086 Dziekanowice</div>
                    <div class="party-line">ING Bank Śląski</div>
                    <div class="party-line">Numer konta: PL28 1050 1445 1000 0090 9422 5811</div>
                </td>
                <td>
                    <div class="party-heading">KLIENT</div>
                    <div class="party-name">{{ client.name }}</div>
                    {% if client.nip %}
                        <div class="party-line">NIP: {{ client.nip }}</div>
                    {% endif %}
                    {% if client.address %}
                        <div class="party-line">{{ client.address }}</div>
                    {% endif %}
                </td>
            </tr>
        </table>

        <!-- Tabela z pozycjami (bez kodu produktu) -->
        <table class="offer-table">
            <thead>
                <tr>
                    <th style="width: 30%;">Produkt</th>
                    <th style="width: 35%;">Opis</th>
                    <th style="width: 12%;">Ilość</th>
                    <th style="width: 11%;">Cena netto</th>
                    <th style="width: 12%;">Wartość netto</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <!-- Kolumna 1 (30%): Numer i nazwa produktu (Montserrat Bold) + wyśrodkowane zdjęcie pod spodem -->
                    <td class="col-product">
                        <span class="product-name">{{ loop.index }}. {{ item.name }}</span>
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

                    <!-- Kolumna 3 (12%): Ilość z wybraną jednostką miary (szt / kg / mb / kpl) -->
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

                {% if not is_order %}
                <!-- Podsumowanie dla OFERTY: tylko SUMA NETTO -->
                <tr class="summary-offer-row">
                    <td colspan="5">
                        SUMA NETTO: {{ total_net_str }} zł
                    </td>
                </tr>
                {% endif %}
            </tbody>
        </table>

        {% if is_order %}
        <!-- Sekcja pod tabelą dla ZAMÓWIENIA: Szczegóły realizacji (Adres dostawy, Data realizacji, Termin płatności) -->
        <table class="order-details-box">
            <tr>
                <td>
                    <div class="order-details-heading">SZCZEGÓŁY REALIZACJI</div>
                    <div>
                        <span class="order-label">Adres dostawy:</span>
                        {{ delivery_address if delivery_address else (client.address if client.address else '—') }}
                    </div>
                    <div style="margin-top: 3px;">
                        <span class="order-label">Data realizacji:</span>
                        {{ realization_date if realization_date else '—' }}
                    </div>
                    <div style="margin-top: 3px;">
                        <span class="order-label">Termin płatności:</span>
                        {{ payment_terms }}
                    </div>
                </td>
            </tr>
        </table>

        <!-- Nowoczesny blok podsumowania kosztów wyrównany do prawej dla ZAMÓWIENIA -->
        <table class="order-summary-wrapper">
            <tr>
                <td style="width: 52%; border: none;"></td>
                <td style="width: 48%; border: none; padding: 0;">
                    <table class="order-summary-box">
                        <tr>
                            <td style="text-align: left;">Wartość Netto:</td>
                            <td style="text-align: right; font-weight: bold;">{{ total_net_str }} zł</td>
                        </tr>
                        <tr>
                            <td style="text-align: left;">Podatek VAT (23%):</td>
                            <td style="text-align: right; font-weight: bold;">{{ vat_amount_str }} zł</td>
                        </tr>
                        <tr class="gross-row">
                            <td style="text-align: left;">DO ZAPŁATY (Brutto):</td>
                            <td style="text-align: right;" class="gross-accent">{{ total_gross_str }} zł</td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
        {% endif %}
    </body>
    </html>
    """

    # Przygotowanie danych pozycji (pomijamy product_code, przekazujemy wyłącznie czystą nazwę i opis)
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
        doc_title_prefix=doc_title_prefix,
        is_order=is_order,
        offer_number=final_offer_number,
        offer_date=final_offer_date,
        delivery_address=final_delivery_address,
        realization_date=final_realization_date,
        payment_terms=final_payment_terms,
        client={
            "name": client_data.get("name", ""),
            "nip": client_data.get("nip") or "",
            "address": client_data.get("address") or "",
        },
        items=prepared_items,
        total_net_str=format_pl_currency(total_net),
        vat_amount_str=format_pl_currency(vat_amount),
        total_gross_str=format_pl_currency(total_gross),
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


# 7. Wspólna funkcja zapisu oferty / zamówienia do bazy
def save_offer_to_database(
    supabase: Client,
    current_client: Dict[str, Any],
    offer_items_state: List[Dict[str, Any]],
    products_by_id: Dict[str, Dict[str, Any]],
    total_net_value: float,
    document_type: str = "Oferta",
    delivery_address: Optional[str] = None,
    realization_date: Optional[date] = None,
    payment_terms: Optional[str] = None,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """
    Zapisuje dokument (Ofertę lub Zamówienie) w tabeli `offers` (wraz z `document_type`,
    `delivery_address`, `realization_date`, `payment_terms` oraz `offer_number`) i pozycje w `offer_items`.
    Zwraca krotkę: (offer_id, offer_number, items_pdf_payload).
    """
    offer_number = generate_offer_number(document_type)
    is_order = document_type == "Zamówienie"

    offer_payload: Dict[str, Any] = {
        "client_id": current_client["id"],
        "total_net_value": round(total_net_value, 2),
        "offer_number": offer_number,
        "document_type": document_type,
        "delivery_address": (delivery_address or "").strip() if is_order else None,
        "realization_date": realization_date.isoformat() if (is_order and realization_date) else None,
        "payment_terms": payment_terms if is_order else None,
    }

    offer_insert = supabase.table("offers").insert(offer_payload).execute()
    if not offer_insert.data:
        raise RuntimeError("Nie udało się uzyskać ID nowo utworzonego dokumentu.")

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


# 8. Widok zakładki "📄 Kreator Dokumentu" (tab1)
def render_creator_tab(supabase: Client) -> None:
    """Renderuje kreator Oferty / Zamówienia z dynamiczną listą pozycji, terminem płatności, VAT/Brutto i eksportem do PDF."""
    st.header("📄 Kreator Dokumentu")
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

    # 1. Wybór typu dokumentu na samej górze kreatora
    if st.session_state.document_type not in DOC_TYPE_OPTIONS:
        st.session_state.document_type = "Oferta"

    doc_type_idx = DOC_TYPE_OPTIONS.index(st.session_state.document_type)
    chosen_doc_type = st.selectbox(
        "Typ dokumentu:",
        options=DOC_TYPE_OPTIONS,
        index=doc_type_idx,
    )
    st.session_state.document_type = chosen_doc_type
    is_order = chosen_doc_type == "Zamówienie"

    st.divider()

    # 2. Dane Sprzedawcy i Klienta
    if st.session_state.selected_client_id not in clients_by_id:
        st.session_state.selected_client_id = client_ids[0]

    st.subheader("1. Strony dokumentu (SPRZEDAWCA i KLIENT)")
    selected_client_index = client_ids.index(st.session_state.selected_client_id)

    chosen_client_id = st.selectbox(
        "Wybierz klienta z bazy:",
        options=client_ids,
        index=selected_client_index,
        format_func=lambda cid: f"{clients_by_id[cid]['name']} (NIP: {clients_by_id[cid].get('nip') or 'brak'})",
    )
    st.session_state.selected_client_id = chosen_client_id
    current_client = clients_by_id[chosen_client_id]

    col_seller, col_buyer = st.columns(2)
    with col_seller:
        with st.container(border=True):
            st.markdown("##### **SPRZEDAWCA**")
            st.markdown("**Proles Concept Robert Czajczyk**")
            st.markdown("ul. Kolejowa 3, 32-086 Dziekanowice")
            st.markdown("ING Bank Śląski")
            st.markdown("Numer konta: `PL28 1050 1445 1000 0090 9422 5811`")

    with col_buyer:
        with st.container(border=True):
            st.markdown("##### **KLIENT**")
            st.markdown(f"**{current_client['name']}**")
            st.markdown(f"NIP: {current_client.get('nip') or '—'}")
            st.markdown(f"Adres: {current_client.get('address') or '—'}")

    # Dodatkowe pola formularza widoczne wyłącznie dla typu 'Zamówienie'
    if is_order:
        st.subheader("📦 Szczegóły realizacji zamówienia")
        ord_col1, ord_col2 = st.columns([1, 2])
        with ord_col1:
            realization_date_val = st.date_input(
                "Data realizacji",
                value=st.session_state.realization_date or date.today(),
            )
            st.session_state.realization_date = realization_date_val

            if st.session_state.payment_terms not in PAYMENT_TERMS_OPTIONS:
                st.session_state.payment_terms = "7 dni"
            pay_idx = PAYMENT_TERMS_OPTIONS.index(st.session_state.payment_terms)
            payment_terms_val = st.selectbox(
                "Termin płatności",
                options=PAYMENT_TERMS_OPTIONS,
                index=pay_idx,
            )
            st.session_state.payment_terms = payment_terms_val

        with ord_col2:
            delivery_address_val = st.text_area(
                "Adres dostawy",
                value=st.session_state.delivery_address,
                placeholder=current_client.get("address") or "Wpisz adres dostawy zamówienia...",
                height=132,
            )
            st.session_state.delivery_address = delivery_address_val

    st.divider()
    st.subheader("2. Pozycje w dokumencie")

    hdr1, hdr2, hdr_unit, hdr3, hdr4, hdr5 = st.columns([3.3, 1.0, 0.9, 1.4, 1.4, 0.6])
    hdr1.markdown("**Zdjęcie i Produkt (Kod + Nazwa)**")
    hdr2.markdown("**Ilość**")
    hdr_unit.markdown("**Jedn.**")
    hdr3.markdown("**Cena jedn. netto (zł)**")
    hdr4.markdown("**Suma netto (zł)**")
    hdr5.markdown("**Usuń**")

    total_net_value = 0.0
    row_to_remove: Optional[int] = None

    for idx, item in enumerate(st.session_state.offer_items):
        row_key = item.get("row_id") or f"row_{idx}"
        item["row_id"] = row_key

        if item.get("product_id") not in products_by_id:
            item["product_id"] = product_ids[0]

        if item.get("unit") not in UNIT_OPTIONS:
            item["unit"] = "szt"

        with st.container(border=True):
            col1, col2, col_unit, col3, col4, col5 = st.columns([3.3, 1.0, 0.9, 1.4, 1.4, 0.6])

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

            row_total = float(item["quantity"]) * float(item["unit_price"])
            total_net_value += row_total
            with col4:
                st.markdown(f"### `{format_pl_currency(row_total)} zł`")

            with col5:
                if st.button("🗑️", key=f"del_{row_key}", help="Usuń tę pozycję"):
                    row_to_remove = idx

    if row_to_remove is not None:
        if len(st.session_state.offer_items) > 1:
            st.session_state.offer_items.pop(row_to_remove)
        else:
            st.session_state.offer_items = [create_empty_offer_item(product_ids[0])]
        st.rerun()

    btn_col1, btn_col2, _ = st.columns([1.5, 1.5, 4])
    with btn_col1:
        if st.button("➕ Dodaj kolejną pozycję", use_container_width=True):
            st.session_state.offer_items.append(create_empty_offer_item(product_ids[0]))
            st.rerun()

    with btn_col2:
        if st.button("🧹 Wyczyść formularz", use_container_width=True):
            st.session_state.offer_items = [create_empty_offer_item(product_ids[0])]
            st.session_state.delivery_address = ""
            st.session_state.realization_date = date.today()
            st.session_state.payment_terms = "7 dni"
            st.session_state.pop("last_generated_pdf", None)
            st.rerun()

    st.divider()

    # Podsumowanie w prawym dolnym rogu (dla Zamówienia wyświetla też VAT 23% i RAZEM BRUTTO)
    vat_value = round(total_net_value * VAT_RATE, 2)
    gross_value = round(total_net_value + vat_value, 2)

    sum_left, sum_right = st.columns([2.2, 1.8])
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
        if is_order:
            st.markdown(
                f"""
                <div style="text-align: right; background-color: #F9F9F9; padding: 14px 18px; border-radius: 8px; border-right: 4px solid {BRAND_COLOR}; color: #333333;">
                    <div style="font-size: 1.05rem; margin-bottom: 4px;">RAZEM NETTO: <strong>{format_pl_currency(total_net_value)} zł</strong></div>
                    <div style="font-size: 1.05rem; margin-bottom: 6px;">VAT (23%): <strong>{format_pl_currency(vat_value)} zł</strong></div>
                    <div style="font-size: 1.45rem; font-weight: bold; color: {BRAND_COLOR};">RAZEM BRUTTO: {format_pl_currency(gross_value)} zł</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<h2 style='text-align: right;'>RAZEM NETTO: {format_pl_currency(total_net_value)} zł</h2>",
                unsafe_allow_html=True,
            )

    st.write("")

    # Dynamiczne etykiety przycisków zależne od wybranego typu dokumentu
    draft_btn_label = (
        "💾 Zapisz zamówienie jako wersję roboczą"
        if is_order
        else "💾 Zapisz ofertę jako wersję roboczą"
    )
    pdf_btn_label = (
        "📄 Generuj zamówienie (PDF)"
        if is_order
        else "📄 Generuj ofertę (PDF)"
    )

    save_col_draft, save_col_pdf = st.columns(2)

    with save_col_draft:
        if st.button(
            draft_btn_label,
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
                        document_type=chosen_doc_type,
                        delivery_address=st.session_state.delivery_address if is_order else None,
                        realization_date=st.session_state.realization_date if is_order else None,
                        payment_terms=st.session_state.payment_terms if is_order else None,
                    )
                    st.session_state.pop("last_generated_pdf", None)
                    st.success(f"{chosen_doc_type} zapisano jako wersję roboczą! (Numer: {offer_number})")
            except Exception as e:
                st.error(f"❌ Wystąpił błąd podczas zapisywania wersji roboczej: {e}")

    with save_col_pdf:
        if st.button(
            pdf_btn_label,
            type="primary",
            use_container_width=True,
        ):
            try:
                with st.spinner("Zapisywanie w bazie Supabase i generowanie pliku PDF..."):
                    _, offer_number, items_pdf_payload = save_offer_to_database(
                        supabase=supabase,
                        current_client=current_client,
                        offer_items_state=st.session_state.offer_items,
                        products_by_id=products_by_id,
                        total_net_value=total_net_value,
                        document_type=chosen_doc_type,
                        delivery_address=st.session_state.delivery_address if is_order else None,
                        realization_date=st.session_state.realization_date if is_order else None,
                        payment_terms=st.session_state.payment_terms if is_order else None,
                    )

                    today_str = datetime.now().strftime("%d.%m.%Y")
                    pdf_bytes = generate_pdf(
                        client_data=current_client,
                        items_data=items_pdf_payload,
                        total_net=total_net_value,
                        offer_number=offer_number,
                        offer_date=today_str,
                        document_type=chosen_doc_type,
                        delivery_address=st.session_state.delivery_address if is_order else None,
                        realization_date=st.session_state.realization_date if is_order else None,
                        payment_terms=st.session_state.payment_terms if is_order else None,
                    )
                    safe_client_name = sanitize_filename(current_client["name"])
                    safe_offer_no = offer_number.replace("/", "_")
                    file_prefix = "Zamowienie" if is_order else "Oferta"
                    pdf_filename = f"{file_prefix}_{safe_offer_no}_{safe_client_name}.pdf"

                    st.session_state["last_generated_pdf"] = {
                        "bytes": pdf_bytes,
                        "filename": pdf_filename,
                        "client_name": current_client["name"],
                        "offer_number": offer_number,
                    }

                    st.success(
                        f"✅ {chosen_doc_type} ({offer_number}) zapisano w historii, a plik PDF jest gotowy do pobrania poniżej!"
                    )
            except Exception as e:
                st.error(f"❌ Wystąpił błąd podczas zapisu lub generowania PDF: {e}")

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


# 11. Widok zakładek "🕰️ Historia Ofert" (tab4) oraz "📦 Historia Zamówień" (tab5)
def render_documents_history_tab(supabase: Client, target_doc_type: str) -> None:
    """
    Renderuje historię zapisanych dokumentów wybranego typu ('Oferta' lub 'Zamówienie')
    z możliwością podglądu pozycji, powielenia do kreatora, pobrania PDF oraz usunięcia z bazy.
    """
    is_order_tab = target_doc_type == "Zamówienie"
    key_prefix = "order" if is_order_tab else "offer"

    if is_order_tab:
        st.header("📦 Historia Zamówień")
    else:
        st.header("🕰️ Historia Ofert")

    try:
        response = (
            supabase.table("offers")
            .select("*, clients(*), offer_items(*, products(*))")
            .eq("document_type", target_doc_type)
            .order("created_at", desc=True)
            .execute()
        )
        offers_data = response.data or []
    except Exception as e:
        st.error(f"❌ Nie udało się pobrać historii ({target_doc_type}) z bazy danych: {e}")
        return

    if not offers_data:
        empty_msg = (
            "Brak zapisanych zamówień w historii. Utwórz pierwsze zamówienie w zakładce '📄 Kreator Dokumentu'."
            if is_order_tab
            else "Brak zapisanych ofert w historii. Utwórz pierwszą ofertę w zakładce '📄 Kreator Dokumentu'."
        )
        st.info(empty_msg)
        return

    count_label = "zamówień" if is_order_tab else "ofert"
    st.caption(f"Łączna liczba zapisanych {count_label}: **{len(offers_data)}**")

    for offer in offers_data:
        offer_id = offer["id"]
        created_dt = pd.to_datetime(offer["created_at"])
        created_str = created_dt.strftime("%Y-%m-%d %H:%M")
        offer_date_pdf = created_dt.strftime("%d.%m.%Y")
        default_prefix = "ZAM" if is_order_tab else "OFE"
        offer_number = offer.get("offer_number") or f"{default_prefix}/{created_dt.strftime('%Y/%m')}/{offer_id[:4].upper()}"
        client_info = offer.get("clients") or {}
        client_name = client_info.get("name", "Nieznany klient")
        total_net = float(offer.get("total_net_value") or 0.0)
        total_gross = round(total_net * (1 + VAT_RATE), 2)
        delivery_address = offer.get("delivery_address") or ""
        realization_date = offer.get("realization_date")
        payment_terms = offer.get("payment_terms") or "7 dni"
        offer_items_raw = offer.get("offer_items") or []

        badge_icon = "📦 ZAMÓWIENIE" if is_order_tab else "📄 OFERTA"
        amount_summary = (
            f"NETTO: {format_pl_currency(total_net)} zł (BRUTTO: {format_pl_currency(total_gross)} zł)"
            if is_order_tab
            else f"NETTO: {format_pl_currency(total_net)} zł"
        )
        expander_title = (
            f"{badge_icon} {offer_number}  |  📅 {created_str}  |  🏢 {client_name}  |  💰 {amount_summary}"
        )

        with st.expander(expander_title, expanded=False):
            st.markdown(
                f"**Typ dokumentu:** `{target_doc_type}` | "
                f"**Numer:** `{offer_number}` | "
                f"**KLIENT:** {client_name} | "
                f"**NIP:** {client_info.get('nip') or '—'} | "
                f"**Adres:** {client_info.get('address') or '—'}"
            )
            if is_order_tab:
                st.markdown(
                    f"**Data realizacji:** `{format_date_pl(realization_date) or '—'}` | "
                    f"**Termin płatności:** `{payment_terms}` | "
                    f"**Adres dostawy:** {delivery_address or client_info.get('address') or '—'}"
                )

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
                st.warning("Brak zapisanych pozycji dla tego dokumentu.")

            act_col1, act_col2, act_col3 = st.columns(3)

            # 1. Powiel ofertę / zamówienie (wczytuje wszystkie pola, w tym payment_terms i jednostki miary)
            duplicate_btn_label = "🔄 Powiel zamówienie" if is_order_tab else "🔄 Powiel ofertę"
            with act_col1:
                if st.button(
                    duplicate_btn_label,
                    key=f"load_{key_prefix}_{offer_id}",
                    use_container_width=True,
                ):
                    st.session_state.document_type = target_doc_type
                    st.session_state.selected_client_id = offer.get("client_id")
                    st.session_state.delivery_address = delivery_address
                    st.session_state.payment_terms = (
                        payment_terms if payment_terms in PAYMENT_TERMS_OPTIONS else "7 dni"
                    )
                    if realization_date:
                        try:
                            st.session_state.realization_date = pd.to_datetime(realization_date).date()
                        except Exception:
                            st.session_state.realization_date = date.today()
                    else:
                        st.session_state.realization_date = date.today()

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
                        f"✅ {target_doc_type} powielono do kreatora! Przejdź do zakładki '📄 Kreator Dokumentu', aby wprowadzić zmiany."
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
                        document_type=target_doc_type,
                        delivery_address=delivery_address,
                        realization_date=realization_date,
                        payment_terms=payment_terms,
                    )
                    safe_name = sanitize_filename(client_name)
                    safe_offer_no = offer_number.replace("/", "_")
                    file_prefix = "Zamowienie" if is_order_tab else "Oferta"
                    st.download_button(
                        label="📄 Pobierz PDF",
                        data=pdf_bytes,
                        file_name=f"{file_prefix}_{safe_offer_no}_{safe_name}.pdf",
                        mime="application/pdf",
                        key=f"dl_pdf_{key_prefix}_{offer_id}",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Błąd PDF: {e}")

            # 3. Usuń z historii
            with act_col3:
                if st.button(
                    "🗑️ Usuń z historii",
                    key=f"del_{key_prefix}_{offer_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        supabase.table("offers").delete().eq("id", offer_id).execute()
                        set_flash_message("success", f"🗑️ {target_doc_type} usunięto z historii.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Błąd podczas usuwania dokumentu: {e}")


# 12. Główna funkcja aplikacji
def main() -> None:
    # Wstrzyknięcie firmowej kolorystyki (#54B6A6) oraz pobranie czcionek Montserrat i Open Sans przy starcie
    apply_custom_theme()
    ensure_modern_fonts()

    # Blokada aplikacji przed nieautoryzowanym dostępem
    if not check_authentication():
        st.stop()

    # Inicjalizacja klienta Supabase oraz stanu kreatora
    supabase = init_connection()
    init_creator_state()

    st.title("📄 System Zarządzania Ofertami i Zamówieniami")

    # Wyświetlenie ewentualnych powiadomień po przeładowaniu (st.rerun)
    show_flash_message()

    # Główna nawigacja oparta na 5 zakładkach
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📄 Kreator Dokumentu",
        "📦 Baza Produktów",
        "🏢 Baza Klientów",
        "🕰️ Historia Ofert",
        "📦 Historia Zamówień",
    ])

    with tab1:
        render_creator_tab(supabase)

    with tab2:
        render_products_tab(supabase)

    with tab3:
        render_clients_tab(supabase)

    with tab4:
        render_documents_history_tab(supabase, target_doc_type="Oferta")

    with tab5:
        render_documents_history_tab(supabase, target_doc_type="Zamówienie")


if __name__ == "__main__":
    main()
