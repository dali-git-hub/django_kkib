# kakeibo/utils.py
import unicodedata
from typing import Optional, Tuple
from django.conf import settings
from django.db.models.functions import Length
from .models import Category

try:
    from .models import CategoryRule
except Exception:
    CategoryRule = None  # type: ignore

FALLBACK_KEYWORDS = {
    "食費": ["昼ご飯", "夕飯", "弁当", "外食", "レストラン", "マクド", "ローソン", "セブン", "スーパー"],
    "住宅": ["家賃", "ローン", "管理費"],
    "水道光熱": ["電気代", "ガス代", "水道代", "電気", "ガス", "水道"],
    "通信": ["スマホ", "携帯", "通信", "wifi", "インターネット"],
    "交通": ["バス", "電車", "地下鉄", "切符", "高速", "ガソリン", "駐車場", "レンタカー", "タクシー"],
    "日用品": ["ドラッグ", "洗剤", "ティッシュ", "トイレットペーパー"],
    "交際費": ["飲み会", "プレゼント", "会食"],
    "医療": ["病院", "薬", "処方", "診察"],
    "教育・教養": ["本", "書籍", "kindle", "受講", "授業料"],
}

def _norm(s: str) -> str:
    if not s:
        return ""
    return unicodedata.normalize("NFKC", s).lower()

def _get_category_by_name(name: str) -> Optional[Category]:
    if not name:
        return None
    return Category.objects.filter(name=name).first()

def _cloud_classify(text: str) -> Optional[Tuple[str, float]]:
    """
    クラウドに投げて (label, score) を返す想定。
    ここではダミー実装。実際のAPI呼び出しは後で差し込む。
    戻り値例: ("食費", 0.83)  /  見つからなければ None
    """
    cfg = getattr(settings, "CATEGORY_AI", {"enabled": False})
    if not cfg.get("enabled"):
        return None

    # --- ここに実際のクラウド呼び出しを入れる（例） ---
    # OpenAI/Bedrock/Vertex/Comprehend などでテキスト分類 or LLMプロンプト
    # resp_label, resp_score = call_provider(text)  # 0.0 - 1.0
    # ラベルをアプリのカテゴリ名に写像
    # label_map = cfg.get("label_map", {})
    # mapped = label_map.get(resp_label, resp_label)
    # return mapped, float(resp_score)
    # -----------------------------------------------

    return None  # まだ未実装なので None

def guess_category(item: str, memo: str = "", user_choice: Optional[Category] = None) -> Optional[Category]:
    """
    優先順位:
      1) user_choice があればそれを尊重
      2) DBルール（最長一致）
      3) 内蔵辞書
      4) クラウド（有効時＆score>=threshold）
    """
    if user_choice:
        return user_choice

    text = _norm((item or "") + " " + (memo or ""))

    # 2) DBルール：長いキーワード優先
    if CategoryRule is not None:
        rules = (CategoryRule.objects
                 .select_related("category")
                 .annotate(klen=Length("keyword"))
                 .order_by("-klen", "keyword"))
        for r in rules:
            if _norm(r.keyword) in text:
                return r.category

    # 3) 内蔵辞書
    for cat_name, words in FALLBACK_KEYWORDS.items():
        for w in words:
            if _norm(w) in text:
                found = _get_category_by_name(cat_name)
                if found:
                    return found

    # 4) クラウド
    label_score = _cloud_classify(text)
    if label_score:
        label, score = label_score
        threshold = float(getattr(settings, "CATEGORY_AI", {}).get("threshold", 0.65))
        if score >= threshold:
            return _get_category_by_name(label)

    return None
