from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict, Optional, Union, IO

import numpy as np
import cv2

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from google.cloud import vision
from google.oauth2 import service_account

from .utils import guess_category


# ====== ノイズ除外用パターン ======
TEL_RE    = re.compile(r'(TEL|電話|CALL)[:：]?\s*\d{2,4}[-‐–―ー]?\d{2,4}[-‐–―ー]?\d{3,4}')
POINT_RE  = re.compile(r'(ﾎﾟｲﾝﾄ|ポイント|T-?POINT|楽天ポイント|dポイント|P[ ：]?\d+)')
IGNORE_HARD = (
    'お買上', '領収', 'ありがとうございました', '軽減税率', '小計対象',
    'レジ', '担当', '合計点数', '小計', '合計', '税込', '税抜', '内税', '外税',
    '割引', '値引', 'クーポン', '会員', 'No.', '№'
)
MONEY_RE = re.compile(r'([0-9]{1,3}(?:[,，][0-9]{3})+|[0-9]+)\s*(円)?$')
MEMBER_RE = re.compile(r'(会員|会員番号|ﾒﾝﾊﾞｰ|ID)[:：]?\s*[A-Z0-9\-]{6,}')
CARD_RE   = re.compile(r'(VISA|MASTER|JCB|AMEX|WAON|nanaco|Suica|PASMO|PayPay|楽天Edy)')

# ====== Vision クライアント（settingsのJSONを最優先で利用） ======
def _gcv_client() -> vision.ImageAnnotatorClient:
    cred_file: Path = getattr(settings, 'VISION_CREDENTIALS_FILE', None)
    if isinstance(cred_file, (str, Path)):
        p = Path(cred_file)
        if p.exists():
            creds = service_account.Credentials.from_service_account_file(str(p))
            return vision.ImageAnnotatorClient(credentials=creds)
    # 環境変数 GOOGLE_APPLICATION_CREDENTIALS が設定されている場合は自動で使われる
    return vision.ImageAnnotatorClient()


# ====== 画像前処理（OpenCV） ======
def _to_bytes(image: Union[bytes, bytearray, memoryview, str, Path, UploadedFile, IO[bytes]]) -> bytes:
    # すでに bytes 系
    if isinstance(image, (bytes, bytearray, memoryview)):
        return bytes(image)
    # Django の UploadedFile
    if isinstance(image, UploadedFile):
        try:
            image.seek(0)
        except Exception:
            pass
        return image.read()
    # file-like (read() を持つもの)
    if hasattr(image, "read") and callable(image.read):
        pos = None
        try:
            pos = image.tell()
        except Exception:
            pass
        data = image.read()
        try:
            if pos is not None:
                image.seek(pos)
        except Exception:
            pass
        return data
    # パス
    p = Path(image)
    return p.read_bytes()

def _preprocess(image) -> bytes:
    """画像 → ほどよく前処理したPNG bytes（OCR精度向上用）"""
    raw = _to_bytes(image)

    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return raw  # もし開けなければそのまま

    # グレースケール化＆軽いノイズ除去→二値化
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    thr  = cv2.adaptiveThreshold(gray, 255,
                                 cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 7)

    # 2000px程度に長辺リサイズ（過剰解像の抑制）
    h, w = thr.shape[:2]
    max_side = max(h, w)
    if max_side > 2000:
        scale = 2000 / max_side
        thr = cv2.resize(thr, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".png", thr)
    return bytes(buf) if ok else raw

# ====== メイン：行抽出 ======
def extract_lines(image: Union[bytes, str, Path]) -> List[Dict]:
    """
    入力画像から「1行=1明細」を推定して返す。
    戻り値: [{raw_text, item, amount, confidence, y_min, y_max}, ...]
    """
    content = _preprocess(image)
    client = _gcv_client()

    img = vision.Image(content=content)
    resp = client.document_text_detection(
        image=img, image_context=vision.ImageContext(language_hints=['ja', 'en'])
    )
    if resp.error.message:
        raise RuntimeError(resp.error.message)

    out: List[Dict] = []
    if not resp.text_annotations:
        return out

    # token単位のバウンディングから y で行グループ化
    tokens = []
    for a in resp.text_annotations[1:]:
        y = min(v.y for v in a.bounding_poly.vertices)
        tokens.append((y, a.description))
    tokens.sort(key=lambda t: t[0])

    groups: List[list] = []
    buf: List[tuple] = []
    cur_y: Optional[float] = None
    for y, tok in tokens:
        if cur_y is None or abs(y - cur_y) <= 15:
            buf.append((y, tok))
            cur_y = y if cur_y is None else (0.8 * cur_y + 0.2 * y)
        else:
            groups.append(buf)
            buf = [(y, tok)]
            cur_y = y
    if buf:
        groups.append(buf)

    for g in groups:
        text = " ".join(tok for _, tok in g)
        text = text.replace('¥', '').replace(',', ' ').strip()
        raw = text

        # 明確なノイズはここで捨てる
        if TEL_RE.search(text) or POINT_RE.search(text):
            continue
        if any(k in text for k in IGNORE_HARD):
            continue
        # 任意で会員/カード系も弾きたい場合は追加
        if MEMBER_RE.search(text) or CARD_RE.search(text) or re.search(r'[＊*#]{4,}', text):
            continue

        m = MONEY_RE.search(text)
        if not m:
            continue
        amount = int(m.group(1).replace(',', '').replace('，', ''))
        name = MONEY_RE.sub('', text).strip(" :：-–—~〜\t")
        if not name:
            continue

        y_min = min(y for y, _ in g)
        y_max = max(y for y, _ in g)

        out.append({
            "raw_text": raw,
            "item": name,
            "amount": amount,
            "confidence": 0.9,  # 必要なら word.confidence の平均に変更可
            "y_min": y_min,
            "y_max": y_max,
        })
    return out

def parse_receipt(image_file) -> List[Dict]:
    """UploadedFile など -> [{'item', 'amount', 'category'}...] を返す"""
    rows = extract_lines(image_file)  # 新実装を呼ぶ
    results: List[Dict] = []
    for r in rows:
        cat = guess_category(item=r["item"])
        results.append({"item": r["item"], "amount": r["amount"], "category": cat})
    return results

def upload_receipt(request):
    if request.method == "POST":
        f = request.FILES["image"]                # InMemoryUploadedFile/TemporaryUploadedFile
        rows = extract_lines(f)                   # そのまま渡せばOK（bytes/UploadedFile両対応）

