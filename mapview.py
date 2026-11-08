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


# ───────────────────────── 시군구 색지도 (외부 지도 서버 없이 그림) ─────────────────────────
def _rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    xs, ys = [], []
    for poly in polys:
        ring = poly[0]  # 외곽선만
        xs += [p[0] for p in ring] + [None]
        ys += [p[1] for p in ring] + [None]
    return xs, ys


def choropleth(geo: dict, values: dict, hover: dict, *, scale, vmin, vmax, title="",
               height=640):
    """geo: GeoJSON, values: {코드: 값}, hover: {코드: 문자열}. plotly Figure 반환."""
    import plotly.colors as pc
    import plotly.graph_objects as go

    fig = go.Figure()
    for f in geo["features"]:
        code = f["id"]
        v = values.get(code)
        if v is None or v != v:
            color = "#e9ecef"
        else:
            t = 0.0 if vmax == vmin else min(max((v - vmin) / (vmax - vmin), 0), 1)
            color = pc.sample_colorscale(scale, [t])[0]
        xs, ys = _rings(f["geometry"])
        fig.add_trace(go.Scatter(x=xs, y=ys, fill="toself", fillcolor=color, mode="lines",
                                 line=dict(color="white", width=0.6), hoverinfo="text",
                                 text=hover.get(code, f["properties"].get("name", code)),
                                 hoveron="fills", showlegend=False))
    # 색 막대
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", showlegend=False, hoverinfo="skip",
                             marker=dict(colorscale=scale, cmin=vmin, cmax=vmax, color=[vmin],
                                         showscale=True, colorbar=dict(title=title, thickness=12))))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1.24)  # 위도 36° 부근 왜곡 보정
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white",
                      hoverlabel=dict(bgcolor="white"), dragmode="pan")
    return fig
