# kakeibo/views.py
from datetime import date
from uuid import uuid4
import calendar as _cal
import re

from django.http import JsonResponse
from django.urls import reverse, reverse_lazy, NoReverseMatch
from django.views import View
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, TemplateView, FormView
)
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.shortcuts import redirect
from django.contrib import messages
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .models import Expense, Budget, Income, Category
from .forms import (
    ExpenseForm, ExpenseFilterForm, BudgetForm, IncomeForm,
    ReceiptUploadForm, ReceiptLineFormSet
)
from .utils import guess_category
from .ocr_client import extract_lines


# ===== 共通ヘルパー =====
def _parse_month_param(request):
    """?month=YYYY-MM -> date(YYYY, MM, 1)。無ければ当月1日"""
    m = request.GET.get('month')
    if m:
        try:
            y, mm = map(int, m.split('-'))
            return date(y, mm, 1)
        except ValueError:
            pass
    return date.today().replace(day=1)


def month_bounds(y: int, m: int):
    """月初と翌月初（半開区間の終端）"""
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start, end


def add_month(d: date, k: int) -> date:
    """月を k だけずらした月初を返す"""
    y = d.year + (d.month + k - 1) // 12
    m = (d.month + k - 1) % 12 + 1
    return date(y, m, 1)


# ===== カテゴリ推定 API =====
@require_POST
def guess_category_api(request):
    item = request.POST.get('item', '')
    memo = request.POST.get('memo', '')
    cat_id = request.POST.get('category')  # 既に選択済みなら尊重

    user_choice = None
    if cat_id:
        try:
            user_choice = Category.objects.get(pk=cat_id)
        except Category.DoesNotExist:
            user_choice = None

    cat = guess_category(item=item, memo=memo, user_choice=user_choice)
    if cat is None:
        return JsonResponse({"ok": True, "suggested_id": None, "suggested_name": None})
    return JsonResponse({"ok": True, "suggested_id": cat.id, "suggested_name": cat.name})


# ===== ダッシュボード =====
class DashboardView(TemplateView):
    template_name = 'kakeibo/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 月の決定
        cur = _parse_month_param(self.request)
        start, end = month_bounds(cur.year, cur.month)
        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, +1)

        # 今月の集計（支出/収入/収支）
        qs_exp = Expense.objects.filter(date__gte=start, date__lt=end)
        qs_inc = Income.objects.filter(date__gte=start, date__lt=end)
        exp_total = qs_exp.aggregate(total=Sum('amount'))['total'] or 0
        inc_total = qs_inc.aggregate(total=Sum('amount'))['total'] or 0
        ctx['expense_total'] = exp_total
        ctx['income_total']  = inc_total
        ctx['net_total']     = inc_total - exp_total

        # 予算（全体）
        overall = Budget.objects.filter(month=start, category__isnull=True)\
                                .aggregate(total=Sum('amount'))['total']
        ctx['overall_budget'] = overall

        # 円グラフ：カテゴリ内訳（今月）
        by_cat = (qs_exp.values('category__name')
                        .annotate(total=Sum('amount'))
                        .order_by('-total'))
        ctx['pie_labels'] = [r['category__name'] or '未分類' for r in by_cat]
        ctx['pie_values'] = [r['total'] or 0 for r in by_cat]

        # 折れ線：直近6か月（支出/収入）
        labels, last6_exp, last6_inc = [], [], []
        for k in range(5, -1, -1):  # 古→新
            d = add_month(cur, -k)
            s, e = month_bounds(d.year, d.month)
            labels.append(f'{d.year}-{d.month:02d}')
            last6_exp.append(Expense.objects.filter(date__gte=s, date__lt=e)
                             .aggregate(total=Sum('amount'))['total'] or 0)
            last6_inc.append(Income.objects.filter(date__gte=s, date__lt=e)
                             .aggregate(total=Sum('amount'))['total'] or 0)
        ctx['last6_labels'] = labels
        ctx['last6_exp']    = last6_exp
        ctx['last6_inc']    = last6_inc

        # 予算進捗（カテゴリ）
        budgets = { (b.category_id or 0): b.amount
                    for b in Budget.objects.filter(month=start) }
        spent_by_cat = { (r['category_id'] or 0): (r['total'] or 0)
                         for r in qs_exp.values('category_id')
                                        .annotate(total=Sum('amount')) }
        rows = []
        for key, spent in spent_by_cat.items():
            name = (Expense.objects
                    .filter(category_id=(None if key==0 else key))
                    .values_list('category__name', flat=True).first()) or '未分類'
            rows.append({
                'name': name,
                'spent': spent,
                'budget': budgets.get(key),
                'remain': (budgets.get(key) - spent) if budgets.get(key) else None,
            })
        # 予算があるのに支出がまだのカテゴリも出す
        for key, amount in budgets.items():
            if key not in spent_by_cat:
                name = Budget.objects.filter(month=start, category_id=(None if key==0 else key))\
                                     .values_list('category__name', flat=True).first() or '全体'
                rows.append({'name': name, 'spent': 0, 'budget': amount, 'remain': amount})
        ctx['cat_progress'] = sorted(rows, key=lambda r: (r['budget'] is None, r['name']))

        # 今月の支出一覧（最新10件）
        ctx['recent_expenses'] = (qs_exp.select_related('category')
                                  .order_by('-date', '-id')[:10])

        # カレンダー用データ（支出＋収入）
        first_wd, days_in_month = _cal.monthrange(cur.year, cur.month)
        exp_map = dict(
            qs_exp.values('date').annotate(total=Sum('amount')).values_list('date', 'total')
        )
        inc_map = dict(
            qs_inc.values('date').annotate(total=Sum('amount')).values_list('date', 'total')
        )

        rows = []
        week = [None] * first_wd
        for day in range(1, days_in_month + 1):
            d = date(cur.year, cur.month, day)
            exp = int(exp_map.get(d, 0) or 0)
            inc = int(inc_map.get(d, 0) or 0)
            cell = {
                'date': d,
                'exp': exp,   # 支出合計（赤表示想定）
                'inc': inc,   # 収入合計（緑表示想定）
                'has': bool(exp or inc),
            }
            week.append(cell)
            if len(week) == 7:
                rows.append(week)
                week = []
        if week:
            while len(week) < 7:
                week.append(None)
            rows.append(week)

        ctx['cal_rows'] = rows
        return ctx


# ===== 分析（ページ & API） =====
class AnalyticsPageView(TemplateView):
    template_name = 'kakeibo/analytics.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cur = _parse_month_param(self.request)

        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, +1)
        ctx['today']         = date.today()

        qs = self.request.GET.copy()
        qs.pop('page', None)
        qs.pop('month', None)
        ctx['base_qs'] = qs.urlencode()
        return ctx


class AnalyticsApiView(View):
    """GET /api/analytics/?month=YYYY-MM -> ダッシュボード用 JSON"""
    def get(self, request):
        cur = _parse_month_param(request)
        start, end = month_bounds(cur.year, cur.month)

        qs_month = Expense.objects.filter(date__gte=start, date__lt=end)
        total = qs_month.aggregate(total=Sum("amount"))["total"] or 0

        rows = (qs_month.values("category__name")
                        .annotate(total=Sum("amount"))
                        .order_by("-total"))
        by_category = [
            {"name": r["category__name"] or "未分類", "total": r["total"] or 0}
            for r in rows
        ]

        last_6m = []
        for k in range(5, -1, -1):
            d = add_month(cur, -k)
            s, e = month_bounds(d.year, d.month)
            t = Expense.objects.filter(date__gte=s, date__lt=e)\
                               .aggregate(total=Sum("amount"))["total"] or 0
            last_6m.append({"month": s.strftime("%Y-%m"), "total": t})

        ps, pe = month_bounds(add_month(cur, -1).year, add_month(cur, -1).month)
        prev_total = Expense.objects.filter(date__gte=ps, date__lt=pe)\
                                    .aggregate(total=Sum("amount"))["total"] or 0
        mom_pct = ((total - prev_total) / prev_total) if prev_total else None

        ys, ye = month_bounds(cur.year - 1, cur.month)
        yoy_total = Expense.objects.filter(date__gte=ys, date__lt=ye)\
                                   .aggregate(total=Sum("amount"))["total"] or 0
        yoy_pct = ((total - yoy_total) / yoy_total) if yoy_total else None

        overall_budget = None

        suggestions = []
        if prev_total and (mom_pct or 0) > 0.20:
            suggestions.append("先月比で20%以上増。固定費（住宅/通信/保険）を点検しましょう。")
        if total and by_category:
            top = by_category[0]
            share = (top["total"] / total) if total else 0
            if share > 0.40:
                suggestions.append(
                    f"「{top['name']}」が今月の{int(share*100)}%を占めています。まとめ買い・クーポン活用を検討。"
                )
        if not suggestions:
            suggestions.append("支出は安定しています。来月は貯蓄・投資の自動積立比率を+1〜2%上げるのがおすすめ。")

        data = {
            "month": start.strftime("%Y-%m"),
            "total": total,
            "by_category": by_category,
            "last_6m": last_6m,
            "mom": {"total": prev_total, "pct": mom_pct},
            "yoy": {"total": yoy_total, "pct": yoy_pct},
            "budget": {"overall": overall_budget} if overall_budget is not None else None,
            "cloud_suggestions": suggestions,
        }
        return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


# ===== 収入 =====
class IncomeListView(ListView):
    model = Income
    template_name = 'kakeibo/income_list.html'
    context_object_name = 'incomes'
    paginate_by = 10
    ordering = ['-date']


class IncomeCreateView(CreateView):
    model = Income
    form_class = IncomeForm
    template_name = 'kakeibo/income_form.html'
    success_url = reverse_lazy('kakeibo:income_list')

    def get_initial(self):
        initial = super().get_initial()
        d = self.request.GET.get('date')
        if d:
            initial['date'] = d
        return initial


class IncomeUpdateView(UpdateView):
    model = Income
    form_class = IncomeForm
    template_name = 'kakeibo/income_form.html'
    success_url = reverse_lazy('kakeibo:income_list')


class IncomeDeleteView(DeleteView):
    model = Income
    template_name = 'kakeibo/income_confirm_delete.html'
    success_url = reverse_lazy('kakeibo:income_list')


# ===== 支出 =====
class ExpenseListView(ListView):
    model = Expense
    template_name = 'kakeibo/expense_list.html'
    context_object_name = 'expenses'
    ordering = ['-date', '-id']
    paginate_by = 10   # 既定は10件

    # ── 「すべて見る」用：?view=all ならページネーション無効
    def get_paginate_by(self, queryset):
        if self.request.GET.get('view') == 'all':
            return None  # ページネーション無し＝全件表示
        # ?per_page=50 のように指定も可能（1〜500で丸め）
        per = self.request.GET.get('per_page')
        if per:
            try:
                n = max(1, min(500, int(per)))
                return n
            except ValueError:
                pass
        return self.paginate_by
    
    def get_queryset(self):
        qs = super().get_queryset().select_related('category')

        # ---- フィルタフォーム適用 ----
        f = ExpenseFilterForm(self.request.GET or None)
        self.filter_form = f
        if f.is_valid():
            cd = f.cleaned_data
            if cd.get('start_date'):
                qs = qs.filter(date__gte=cd['start_date'])
            if cd.get('end_date'):
                qs = qs.filter(date__lte=cd['end_date'])
            if cd.get('q'):
                qs = qs.filter(item__icontains=cd['q'])
            if cd.get('category'):
                qs = qs.filter(category=cd['category'])

        # ---- 月フィルタ（view=all のときは外す）----
        view_type = (self.request.GET.get('view') or '').lower()
        cur = _parse_month_param(self.request)
        self.current_month = cur

        # start/end が指定されていない場合のみ「当月しばり」をかける
        has_explicit_range = f.is_valid() and (
            f.cleaned_data.get('start_date') or f.cleaned_data.get('end_date')
        )
        if view_type != 'all' and not has_explicit_range:
            qs = qs.filter(date__gte=cur, date__lt=add_month(cur, 1))

        # ---- 並び替え（任意）----
        sort = self.request.GET.get('sort')
        if sort in ('date', '-date', 'amount', '-amount', 'item', '-item', 'category', '-category'):
            if 'category' in sort:
                prefix = '-' if sort.startswith('-') else ''
                qs = qs.order_by(f'{prefix}category__name', f'{prefix}date', f'{prefix}id')
            else:
                qs = qs.order_by(sort, '-id')
        else:
            qs = qs.order_by('-date', '-id')

        return qs



    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cur = getattr(self, 'current_month', _parse_month_param(self.request))

        ctx['filter_form']   = getattr(self, 'filter_form', ExpenseFilterForm())
        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, 1)
        ctx['this_month']    = date.today().replace(day=1)

        # ページングリンク用クエリを保持（page は除去）
        qs = self.request.GET.copy()
        qs.pop('page', None)
        ctx['base_qs'] = qs.urlencode()

        # 画面側で「全件表示中」などを出せるように
        ctx['is_all'] = (self.request.GET.get('view') == 'all')
        return ctx


class ExpenseCreateView(CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'kakeibo/expense_form.html'

    def get_initial(self):
        initial = super().get_initial()
        d = self.request.GET.get('date')
        if d:
            initial['date'] = d
        return initial

    def get_success_url(self):
        m = self.object.date.strftime('%Y-%m')
        return f"{reverse('kakeibo:expense_list')}?month={m}"


class ExpenseUpdateView(UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'kakeibo/expense_form.html'

    def get_success_url(self):
        m = self.object.date.strftime('%Y-%m')
        return f"{reverse('kakeibo:expense_list')}?month={m}"


class ExpenseDeleteView(DeleteView):
    model = Expense
    template_name = 'kakeibo/expense_confirm_delete.html'

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        self._redir_month = self.object.date.strftime('%Y-%m')
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        m = getattr(self, '_redir_month', date.today().strftime('%Y-%m'))
        return f"{reverse('kakeibo:expense_list')}?month={m}"


class ExpenseMonthlySummaryView(ListView):
    template_name = 'kakeibo/expense_summary.html'
    context_object_name = 'rows'
    paginate_by = 12

    def get_queryset(self):
        qs = Expense.objects.all()
        self.filter_form = ExpenseFilterForm(self.request.GET or None)
        if self.filter_form.is_valid():
            cd = self.filter_form.cleaned_data
            if cd.get('start_date'):
                qs = qs.filter(date__gte=cd['start_date'])
            if cd.get('end_date'):
                qs = qs.filter(date__lte=cd['end_date'])
            if cd.get('q'):
                qs = qs.filter(item__icontains=cd['q'])

        self.grand_total = qs.aggregate(total=Sum('amount'))['total'] or 0
        return (qs.annotate(month=TruncMonth('date'))
                 .values('month')
                 .order_by('-month')
                 .annotate(total=Sum('amount'), count=Count('id')))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filter_form'] = getattr(self, 'filter_form', ExpenseFilterForm())
        cur = _parse_month_param(self.request)
        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, +1)
        qs = self.request.GET.copy()
        qs.pop('page', None)
        qs.pop('month', None)
        ctx['base_qs'] = qs.urlencode()
        return ctx

@method_decorator(csrf_protect, name='dispatch')
class ExpenseBulkDeleteView(View):
    def post(self, request):
        ids = request.POST.getlist('ids')
        month = request.POST.get('month') or date.today().strftime('%Y-%m')
        if ids:
            deleted, _ = Expense.objects.filter(id__in=ids).delete()
            messages.success(request, f"{deleted} 件削除しました。")
        else:
            messages.info(request, "削除対象が選択されていません。")
        return redirect(f"{reverse('kakeibo:expense_list')}?month={month}&all=1")

# ===== 予算 =====
class BudgetListView(ListView):
    model = Budget
    template_name = 'kakeibo/budget_list.html'
    context_object_name = 'budgets'
    paginate_by = 20

    def get_queryset(self):
        self.current_month = _parse_month_param(self.request)
        return (Budget.objects.filter(month=self.current_month)
                              .select_related('category')
                              .order_by('category__name'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cur = getattr(self, 'current_month', _parse_month_param(self.request))
        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, +1)
        ctx['base_qs']       = ''
        return ctx


class BudgetCreateView(CreateView):
    model = Budget
    form_class = BudgetForm
    template_name = 'kakeibo/budget_form.html'

    def get_initial(self):
        return {'month': _parse_month_param(self.request)}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['current_month'] = _parse_month_param(self.request)
        return ctx

    def get_success_url(self):
        m = self.object.month.strftime('%Y-%m')
        return f"{reverse('kakeibo:budget_list')}?month={m}"


class BudgetUpdateView(UpdateView):
    model = Budget
    form_class = BudgetForm
    template_name = 'kakeibo/budget_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['current_month'] = getattr(self.object, 'month', _parse_month_param(self.request))
        return ctx

    def get_success_url(self):
        m = self.object.month.strftime('%Y-%m')
        return f"{reverse('kakeibo:budget_list')}?month={m}"


class BudgetDeleteView(DeleteView):
    model = Budget
    template_name = 'kakeibo/budget_confirm_delete.html'

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        self._redir_month = self.object.month.strftime('%Y-%m')
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        m = getattr(self, '_redir_month', date.today().strftime('%Y-%m'))
        return f"{reverse('kakeibo:budget_list')}?month={m}"

# === ノイズ行フィルタ ===
_SKIP_PATTERNS = [
    r'小計', r'合計', r'計[ 　]*小', r'消費税', r'内税', r'外税',
    r'領収', r'お会計', r'会計', r'担当', r'レジ', r'番号', r'No\.?',
    r'有効期限', r'TEL|電話', r'ﾎﾟｲﾝﾄ|ポイント|T-?POINT|dポイント|楽天ポイント',
    r'^\s*[A-Z]{1,2}\s*$',          # 単独の英字
    r'^\s*\d{6,}\s*$',              # 桁の多い連番（電話/カード末尾 等）
]
_SKIP_RE = [re.compile(p) for p in _SKIP_PATTERNS]

def _drop_noise(item: str, amount: int) -> bool:
    if not item or len(item.strip()) < 2:
        return True
    for rx in _SKIP_RE:
        if rx.search(item):
            return True
    # 電話番号などが金額に入ったケースを除外（閾値は適宜調整）
    if amount is not None and amount > 300000:
        return True
    return False


# ===== レシート取り込み：アップロード → レビュー =====
class ReceiptUploadView(FormView):
    template_name = "kakeibo/receipt_upload.html"
    form_class = ReceiptUploadForm

    def form_valid(self, form):
        from .ocr_client import extract_lines   # 循環回避のためローカル import
        d   = form.cleaned_data["date"]
        f   = form.cleaned_data["image"]
        f.seek(0)
        data = f.read()

        # プレビュー用に保存（開発）
        tmp_path = default_storage.save(f"receipts/tmp/{uuid4().hex}.jpg", ContentFile(data))
        self.request.session["ocr_image_url"] = default_storage.url(tmp_path)
        self.request.session["ocr_date"] = d.isoformat()

        # OCR
        raw_rows = extract_lines(data)  # [{'item','amount',...}]
        initial = []
        for r in raw_rows:
            item = (r.get("item") or "").strip()
            amt  = int(r.get("amount") or 0)
            if _drop_noise(item, amt):
                continue
            cat = guess_category(item=item)
            initial.append({
                "item": item,
                "amount": amt,
                "category": (cat.pk if cat else None),
                "raw_text": r.get("raw_text",""),
            })

        # セッションへ預けて確認画面へ
        self.request.session["ocr_initial"] = initial
        return redirect("kakeibo:receipt_review")


class ReceiptReviewView(FormView):
    template_name = "kakeibo/receipt_review.html"
    form_class = ReceiptLineFormSet

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["initial"] = self.request.session.get("ocr_initial", [])
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["image_url"] = self.request.session.get("ocr_image_url")
        ctx["date"] = self.request.session.get("ocr_date")
        return ctx

    def form_valid(self, form):
        # 1) 登録日（戻り先の月）を確定
        d_iso = self.request.session.get("ocr_date")
        try:
            d = date.fromisoformat(d_iso) if d_iso else date.today()
        except Exception:
            d = date.today()

        # 2) 行ごとに登録
        created = 0
        for f in form:
            if not getattr(f, "cleaned_data", None):
                continue
            item = (f.cleaned_data.get("item") or "").strip()
            amount = f.cleaned_data.get("amount")
            if not item or amount is None:
                continue
            Expense.objects.create(
                date=d,
                item=item,
                amount=amount,
                category=f.cleaned_data.get("category") or None,
            )
            created += 1

        # 3) セッションクリア & メッセージ
        for k in ("ocr_initial", "ocr_image_url", "ocr_date"):
            self.request.session.pop(k, None)
        messages.success(self.request, f"{created} 件登録しました。")

        # 4) その月の「全件表示」一覧へ戻る
        try:
            base = reverse("kakeibo:expense_list")
            return redirect(f"{base}?month={d.strftime('%Y-%m')}&view=all")
        except NoReverseMatch:
            return redirect("/")

# ===== forms.py =====
from datetime import date
from django import forms
from django.forms import formset_factory
from .models import Expense, Budget, Income, Category

# ===== 予算フォーム =====
class BudgetForm(forms.ModelForm):
    month = forms.DateField(
        label='対象月',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'month'})
    )


    class Meta:
        model = Budget
        fields = ['month', 'category', 'amount']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount':   forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1, 'inputmode': 'numeric'}),
        }
        labels = {'month':'対象月','category':'費目','amount':'予算金額'}
        help_texts = {'amount':'1円以上の整数'}
    def clean_amount(self):
        v = self.cleaned_data['amount']
        if v is None or v < 1:
            raise forms.ValidationError('予算金額は1以上の整数を入力してください。')
        return v
# ===== レシートアップロードフォーム 