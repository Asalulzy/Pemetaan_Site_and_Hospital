"""
Peta Sebaran Lokasi Rumah Sakit — Streamlit App
-------------------------------------------------
Menampilkan lokasi tiap fasilitas di peta. Untuk melihat jarak, pilih satu
Titik Asal dan satu/beberapa Titik Tujuan di sidebar — rute akan digambar
mengikuti jalan asli (via OSRM), lengkap dengan jarak (km) & estimasi
waktu tempuh. Jika layanan rute sedang tidak bisa diakses, otomatis
fallback ke jarak garis lurus (haversine) tanpa menggambar rute.

Cara menjalankan:
    pip install streamlit pandas pydeck requests
    streamlit run app_peta_lokasi.py
"""

import io
import numpy as np
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

# ============================================================
# CONFIG
# ============================================================

st.set_page_config(page_title="Peta Lokasi Fasilitas", layout="wide")

# Data default (dipakai kalau user tidak upload CSV sendiri)
DEFAULT_CSV = """Hospital,Lat,Long
RSUP H. Adam Malik,3.5185898868582375,98.60896292154237
RSUP Dr. Kariadi,-6.9940840049903805,110.40831581614081
RSUP Dr. Sardjito,-7.768403684817705,110.37408573943851
Dr. Soetomo Hospital,-7.267993540099591,112.75802338045767
Site 551 (RSIJ Cempaka Putih),-6.170446960329155,106.8718032044871
Site 552 (RSPG Cisarua),-6.688126414153318,106.93955269579277
Site 553 (RSHS Bandung),-6.897928569568532,107.59862490319328
"""

POINT_RADIUS = 25000  # meter, ukuran titik seragam

# ============================================================
# DESIGN: COLOR TOKENS & CSS (mengikuti design system referensi)
# ============================================================

INK = "#16232E"
PAPER = "#F6F7F4"
SURFACE = "#FFFFFF"
LINE = "#DCE2DE"
TEAL = "#0B5E63"
SLATE = "#3A5A78"

CUSTOM_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

html, body, [class*="css"] {{
    font-family: 'IBM Plex Sans', sans-serif;
    color: {INK};
}}
.stApp {{ background-color: {PAPER}; }}

h1 {{
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 600;
    font-size: 2rem;
    letter-spacing: -0.01em;
    color: {INK};
    border-top: 4px solid {TEAL};
    padding-top: 0.6rem;
    margin-bottom: 0.15rem;
}}
h2, h3 {{ font-family: 'IBM Plex Sans', sans-serif; font-weight: 600; color: {INK}; }}

section[data-testid="stSidebar"] {{
    background-color: {SURFACE};
    border-right: 1px solid {LINE};
}}

hr {{ border-top: 1px solid {LINE}; }}

.stButton>button {{
    border-radius: 4px;
    font-weight: 500;
    border: 1px solid {LINE};
}}

.legend-row {{ display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin: 0.4rem 0 1rem 0; }}
.legend-chip {{
    display: flex; align-items: center; gap: 0.35rem;
    font-size: 0.78rem; color: {INK}CC;
    border: 1px solid {LINE}; padding: 0.2rem 0.55rem;
    background-color: {SURFACE};
    font-family: 'IBM Plex Mono', monospace;
}}

.kpi-card {{
    background-color: {SURFACE};
    border: 1px solid {LINE};
    border-left: 4px solid {TEAL};
    padding: 0.8rem 1rem;
    font-family: 'IBM Plex Sans', sans-serif;
}}
.kpi-label {{ font-size: 0.78rem; color: {INK}99; font-family: 'IBM Plex Mono', monospace; }}
.kpi-value {{ font-size: 1.4rem; font-weight: 600; color: {INK}; }}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ============================================================
# HAVERSINE DISTANCE
# ============================================================

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0  # radius bumi (km)
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c


# ============================================================
# ROUTING VIA OSRM (rute mengikuti jalan asli, bukan garis lurus)
# ============================================================

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"


@st.cache_data(show_spinner=False)
def get_road_route(lat1, lon1, lat2, lon2):
    """Ambil rute jalan asli dari OSRM. Return dict {path, distance_km, duration_min}
    atau None kalau gagal (mis. tidak ada internet / servis down)."""
    try:
        url = OSRM_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2)
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]  # list of [lon, lat]
        return {
            "path": coords,
            "distance_km": route["distance"] / 1000.0,
            "duration_min": route["duration"] / 60.0,
        }
    except Exception:
        return None


# ============================================================
# LOAD DATA
# ============================================================

@st.cache_data
def load_data(path_or_buffer):
    df = pd.read_csv(path_or_buffer)
    df.columns = [c.strip() for c in df.columns]
    df["Lat"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Long"] = pd.to_numeric(df["Long"], errors="coerce")
    df = df.dropna(subset=["Lat", "Long"]).reset_index(drop=True)
    return df


st.sidebar.header("Data Source")
uploaded = st.sidebar.file_uploader("Upload CSV (opsional, kolom: Hospital, Lat, Long)", type=["csv"])

if uploaded is not None:
    df = load_data(uploaded)
    sumber = "manual upload"
else:
    df = load_data(io.StringIO(DEFAULT_CSV))
    sumber = "data bawaan"

st.sidebar.caption(f"Data dimuat dari: **{sumber}**")

fdf = df.reset_index(drop=True)

st.sidebar.header("Rute")
hospital_list = fdf["Hospital"].tolist()
origin = st.sidebar.selectbox(
    "Titik Asal", ["(tidak ada)"] + hospital_list, index=0
)

destinations = []
if origin != "(tidak ada)":
    dest_options = [h for h in hospital_list if h != origin]
    destinations = st.sidebar.multiselect(
        "Titik Tujuan (bisa lebih dari satu)", dest_options, default=[]
    )

# ============================================================
# HEADER
# ============================================================

st.title("Peta Sebaran Lokasi Fasilitas")

st.divider()

# ============================================================
# MAP
# ============================================================

map_df = fdf.copy()
# Warna: asal = teal gelap, tujuan terpilih = oranye, sisanya = abu netral
def point_color(name):
    if name == origin:
        return [11, 94, 99, 220]        # teal — titik asal
    if name in destinations:
        return [193, 100, 47, 220]      # oranye — titik tujuan
    return [90, 100, 108, 160]          # abu — lainnya

map_df["color"] = map_df["Hospital"].apply(point_color)
map_df["radius"] = map_df["Hospital"].apply(lambda h: POINT_RADIUS * 1.3 if h in ([origin] + destinations) else POINT_RADIUS)

view_state = pdk.ViewState(
    latitude=map_df["Lat"].mean(),
    longitude=map_df["Long"].mean(),
    zoom=4.3,
    pitch=0,
)

layers = []
route_results = []  # simpan hasil rute untuk tabel di bawah

if origin != "(tidak ada)" and destinations:
    o_row = fdf[fdf["Hospital"] == origin].iloc[0]
    path_records = []

    with st.spinner("Mengambil rute jalan..."):
        for dest in destinations:
            d_row = fdf[fdf["Hospital"] == dest].iloc[0]
            route = get_road_route(o_row["Lat"], o_row["Long"], d_row["Lat"], d_row["Long"])
            if route is not None:
                path_records.append({
                    "path": route["path"],
                    "from_name": origin,
                    "to_name": dest,
                })
                route_results.append({
                    "Tujuan": dest,
                    "Jarak (km)": round(route["distance_km"], 1),
                    "Estimasi Waktu (menit)": round(route["duration_min"], 0),
                    "Sumber": "Rute jalan (OSRM)",
                })
            else:
                # fallback: garis lurus haversine kalau rute tidak tersedia
                straight_km = haversine_km(o_row["Lat"], o_row["Long"], d_row["Lat"], d_row["Long"])
                route_results.append({
                    "Tujuan": dest,
                    "Jarak (km)": round(straight_km, 1),
                    "Estimasi Waktu (menit)": None,
                    "Sumber": "Garis lurus (layanan rute tidak tersedia)",
                })

    if path_records:
        path_df = pd.DataFrame(path_records)
        route_layer = pdk.Layer(
            "PathLayer",
            data=path_df,
            get_path="path",
            get_color=[11, 94, 99, 200],
            get_width=4,
            width_min_pixels=2,
            pickable=True,
        )
        layers.append(route_layer)

point_layer = pdk.Layer(
    "ScatterplotLayer",
    data=map_df,
    get_position="[Long, Lat]",
    get_radius="radius",
    get_fill_color="color",
    pickable=True,
    stroked=True,
    get_line_color=[22, 35, 46],
    get_line_width=1,
    auto_highlight=True,
)
layers.append(point_layer)

tooltip = {
    "html": f"""
    <div style="font-family:'IBM Plex Sans',Arial,sans-serif; min-width:180px; line-height:1.5;">
        <div style="font-size:14px; font-weight:600; margin-bottom:6px;
                    border-bottom:2px solid {TEAL}; padding-bottom:4px;">
            {{Hospital}}
        </div>
        <div style="font-size:12px; font-family:'IBM Plex Mono',monospace; color:#5b6b73;">
            Koordinat: {{Lat}}, {{Long}}
        </div>
    </div>
    """,
    "style": {
        "backgroundColor": SURFACE,
        "color": INK,
        "border": f"1px solid {LINE}",
        "borderRadius": "2px",
        "padding": "8px",
        "fontSize": "12px",
    },
}

st.pydeck_chart(
    pdk.Deck(layers=layers, initial_view_state=view_state, tooltip=tooltip, map_style="road"),
    use_container_width=True,
    height=650,
)

if origin != "(tidak ada)" and not destinations:
    st.caption("Pilih satu atau lebih **Titik Tujuan** di sidebar untuk menampilkan rute & jarak dari titik asal.")

# ============================================================
# TABEL JARAK DARI TITIK ASAL
# ============================================================

if route_results:
    st.divider()
    st.subheader(f"Jarak & Waktu Tempuh dari: {origin}")
    result_df = pd.DataFrame(route_results).sort_values("Jarak (km)").reset_index(drop=True)
    st.dataframe(result_df, use_container_width=True, hide_index=True)

st.divider()

# ============================================================
# DATA LENGKAP
# ============================================================

st.subheader("Data Lokasi")
st.dataframe(fdf, use_container_width=True, hide_index=True)

csv = fdf.to_csv(index=False).encode("utf-8")
st.download_button("Download data (CSV)", csv, "lokasi_fasilitas.csv", "text/csv")