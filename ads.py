"""광고 코드 (구글 애드센스, 카카오 애드핏, 쿠팡 파트너스).

값은 모두 환경변수(정적 사이트: GitHub 저장소 Variables, 앱: Streamlit Secrets)로 받는다.
값이 없으면 해당 광고는 아예 그리지 않는다.

  ADSENSE_CLIENT     ca-pub-1234567890123456         (애드센스 게시자 ID, 자동 광고)
  ADFIT_UNIT_PC      DAN-xxxxxxxx  (728x90)           (애드핏 PC 광고단위)
  ADFIT_UNIT_MO      DAN-xxxxxxxx  (320x100)          (애드핏 모바일 광고단위)
  COUPANG_ID         123456                           (쿠팡 파트너스 다이나믹 배너 id)
  COUPANG_TRACKING   AF1234567                        (쿠팡 파트너스 trackingCode)
  COUPANG_TEMPLATE   carousel                         (선택, 기본 carousel)
  GA_ID              G-XXXXXXXXXX                     (구글 애널리틱스 4 측정 ID, 방문 통계)
"""
import json
import os
import re

COUPANG_NOTICE = "이 사이트는 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."


def conf(name: str, getter=None) -> str:
    v = getter(name) if getter else None
    return (v or os.environ.get(name, "") or "").strip()


def ga_head(mid: str) -> str:
    """구글 애널리틱스 4 (방문자 수·많이 본 페이지·유입 경로 집계용)."""
    if not re.fullmatch(r"G-[A-Z0-9]{4,20}", mid):
        return ""
    return (f'<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{mid}');</script>")


def adsense_head(client: str) -> str:
    if not re.fullmatch(r"ca-pub-\d{10,20}", client):
        return ""
    return (f'<meta name="google-adsense-account" content="{client}">\n'
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={client}" '
            'crossorigin="anonymous"></script>')


def ads_txt(client: str) -> str:
    if not re.fullmatch(r"ca-pub-\d{10,20}", client):
        return ""
    return f"google.com, {client.replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n"


def adfit(unit_pc: str, unit_mo: str) -> str:
    """PC 728x90 / 모바일 320x100. 화면 폭에 따라 하나만 보인다."""
    parts = []
    for unit, w, h, cls in [(unit_pc, 728, 90, "ad-pc"), (unit_mo, 320, 100, "ad-mo")]:
        if re.fullmatch(r"DAN-[A-Za-z0-9]+", unit or ""):
            parts.append(f'<div class="{cls}"><ins class="kakao_ad_area" style="display:none;" data-ad-unit="{unit}" '
                         f'data-ad-width="{w}" data-ad-height="{h}"></ins></div>')
    if not parts:
        return ""
    return ('<div class="ad">' + "".join(parts) +
            '<script async src="https://t1.daumcdn.net/kas/static/ba.min.js"></script></div>')


def coupang_script(cid: str, tracking: str, template: str = "carousel", width=680, height=140) -> str:
    if not (cid.isdigit() and re.fullmatch(r"AF\d+", tracking or "")):
        return ""
    opt = json.dumps({"id": int(cid), "template": template or "carousel", "trackingCode": tracking,
                      "width": str(width), "height": str(height), "tsource": ""})
    return f'<script src="https://ads-partners.coupang.com/g.js"></script><script>new PartnersCoupang.G({opt});</script>'


def coupang_block(cid: str, tracking: str, template: str = "carousel") -> str:
    """정적 사이트용: 배너 + 대가성 문구."""
    s = coupang_script(cid, tracking, template)
    if not s:
        return ""
    return f'<div class="ad ad-cp"><p class="ad-notice">{COUPANG_NOTICE}</p>{s}</div>'
