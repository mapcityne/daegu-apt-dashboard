"""단지 지도(folium) 생성."""
import branca.colormap as cm
import folium
import pandas as pd

DAEGU_CENTER = (35.87, 128.60)


def _int(v, suffix=""):
    return f"{int(v)}{suffix}" if pd.notna(v) else "-"


def tooltip_of(r) -> str:
    """지도 툴팁 = 단지 선택 상자 라벨 (클릭 연동에 사용)."""
    return f"{r.단지명} · {r.구군} {r.법정동}"


def build_map(on_map: pd.DataFrame) -> folium.Map:
    """on_map: 단지키·단지명·구군·법정동·lat·lon·거래수·중위평당가·최근 거래 정보."""
    lo, hi = on_map["중위평당가"].quantile([0.05, 0.95])
    if hi <= lo:
        hi = lo + 1
    cmap = cm.LinearColormap(["#2c7bb6", "#ffffbf", "#d7191c"], vmin=lo, vmax=hi,
                             caption="기간 중위 평당가 (만원/3.3㎡)")
    m = folium.Map(DAEGU_CENTER, zoom_start=11, tiles="OpenStreetMap")
    for r in on_map.itertuples(index=False):
        popup = (f"<b>{r.단지명}</b><br>{r.구군} {r.법정동} · {_int(r.건축년도, '년')}<br>"
                 f"최근 거래 {r.계약일자:%Y.%m.%d}<br>"
                 f"<b>{r.거래금액_억:,.2f}억</b> (전용 {r.전용면적:.1f}㎡, {_int(r.층, '층')})<br>"
                 f"기간 내 {r.거래수}건 · 중위 평당가 {r.중위평당가:,.0f}만원")
        folium.CircleMarker(
            [r.lat, r.lon], radius=4 + min(r.거래수, 30) ** 0.5 * 1.3,
            color=cmap(min(max(r.중위평당가, lo), hi)), fill=True, fill_opacity=0.8,
            weight=1, tooltip=tooltip_of(r), popup=folium.Popup(popup, max_width=260),
        ).add_to(m)
    cmap.add_to(m)
    if len(on_map) > 1:
        m.fit_bounds([[on_map.lat.min(), on_map.lon.min()], [on_map.lat.max(), on_map.lon.max()]])
    return m
