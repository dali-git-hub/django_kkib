from datetime import date

from django.http import JsonResponse
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, TemplateView, FormView
)
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth

from .models import Expense, Budget, Income, Category
from .forms import ExpenseForm, ExpenseFilterForm, BudgetForm, IncomeForm, ReceiptLineFormSet
 
import calendar as _cal
from calendar import monthrange

import uuid, json
from uuid import uuid4
from pathlib import Path
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .forms import ReceiptUploadForm, ReceiptLineFormSet

# views.py
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt  # CSRFトークンを使うなら不要。今回は使うのでexemptは付けません
from django.utils.decorators import method_decorator
from django.utils.timezone import localdate
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from .utils import guess_category

# ==== レシート取り込み ====
from .ocr_client import extract_lines, parse_receipt

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

@require_POST
def guess_category_api(request):
    item = request.POST.get('item', '')
    memo = request.POST.get('memo', '')
    cat_id = request.POST.get('category')  # 既に選択済みなら尊重（なくてもOK）

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
# 先頭の import 群はそのままでOK（Sum, Expense, Budget, Income など既に読み込み済み想定）

class DashboardView(TemplateView):
    template_name = 'kakeibo/dashboard.html'

    def get_context_data(self, **kwargs):
        from django.db.models import Sum
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

        # --- カレンダー用データ（支出＋収入の両方を表示できるようにする） ---
        first_wd, days_in_month = _cal.monthrange(cur.year, cur.month)  # 0=Mon(月)

        # 当月の日付ごとに 支出・収入 の合計マップを作成
        exp_map = dict(
            qs_exp.values('date').annotate(total=Sum('amount')).values_list('date', 'total')
        )
        inc_map = dict(
            qs_inc.values('date').annotate(total=Sum('amount')).values_list('date', 'total')
        )

        rows = []
        week = [None] * first_wd  # 1週目前の空白セル

        for day in range(1, days_in_month + 1):
            d = date(cur.year, cur.month, day)
            exp = int(exp_map.get(d, 0) or 0)   # その日の支出合計
            inc = int(inc_map.get(d, 0) or 0)   # その日の収入合計
            cell = {
                'date': d,
                'exp': exp,          # ← テンプレで緑(+収入)表示に使う
                'inc': inc,          # ← テンプレで赤(-支出)表示に使う
                'has': bool(exp or inc),  # その日に何かあれば枠に色を付けられる
            }
            week.append(cell)
            if len(week) == 7:
                rows.append(week)
                week = []

        # 最終週の埋め草
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
        ctx['today']         = date.today()  # テンプレでは {{ today|date:"Y-m" }} を使用

        # ページ内の月ナビで既存クエリを維持
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

        # 今月合計
        qs_month = Expense.objects.filter(date__gte=start, date__lt=end)
        total = qs_month.aggregate(total=Sum("amount"))["total"] or 0

        # カテゴリ内訳（今月）
        rows = (qs_month.values("category__name")
                        .annotate(total=Sum("amount"))
                        .order_by("-total"))
        by_category = [
            {"name": r["category__name"] or "未分類", "total": r["total"] or 0}
            for r in rows
        ]

        # 直近6か月（古い -> 新しい）
        last_6m = []
        for k in range(5, -1, -1):
            d = add_month(cur, -k)
            s, e = month_bounds(d.year, d.month)
            t = Expense.objects.filter(date__gte=s, date__lt=e)\
                               .aggregate(total=Sum("amount"))["total"] or 0
            last_6m.append({"month": s.strftime("%Y-%m"), "total": t})

        # 前月比 / 前年同月比
        ps, pe = month_bounds(add_month(cur, -1).year, add_month(cur, -1).month)
        prev_total = Expense.objects.filter(date__gte=ps, date__lt=pe)\
                                    .aggregate(total=Sum("amount"))["total"] or 0
        mom_pct = ((total - prev_total) / prev_total) if prev_total else None

        ys, ye = month_bounds(cur.year - 1, cur.month)
        yoy_total = Expense.objects.filter(date__gte=ys, date__lt=ye)\
                                   .aggregate(total=Sum("amount"))["total"] or 0
        yoy_pct = ((total - yoy_total) / yoy_total) if yoy_total else None

        # （必要になったら Budget から拾う）
        overall_budget = None

        # 簡易サジェスト
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

    def get_success_url(self):
        return reverse_lazy('kakeibo:income_list')

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
    ordering = ['-date']
    paginate_by = 10

    def get_queryset(self):
        qs = super().get_queryset()
        f = ExpenseFilterForm(self.request.GET or None)
        if f.is_valid():
            cd = f.cleaned_data
            if cd.get('start_date'):
                qs = qs.filter(date__gte=cd['start_date'])
            if cd.get('end_date'):
                qs = qs.filter(date__lte=cd['end_date'])
            if cd.get('q'):
                qs = qs.filter(item__icontains=cd['q'])
        self.filter_form = f

        # 「月」指定（start/end 指定がある場合はそれを優先）
        sd_specified = f.is_valid() and (
            f.cleaned_data.get('start_date') or f.cleaned_data.get('end_date')
        )
        cur = _parse_month_param(self.request)
        self.current_month = cur
        if not sd_specified:
            start = cur
            end   = add_month(cur, 1)
            qs = qs.filter(date__gte=start, date__lt=end)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # フィルタフォーム
        ctx['filter_form'] = getattr(self, 'filter_form', ExpenseFilterForm())

        # 月ナビ
        cur = getattr(self, 'current_month', _parse_month_param(self.request))
        ctx['current_month'] = cur
        ctx['prev_month']    = add_month(cur, -1)
        ctx['next_month']    = add_month(cur, 1)
        ctx['this_month']    = date.today().replace(day=1)

        # クエリ維持（page, month は除外）
        qs = self.request.GET.copy()
        qs.pop('page', None)
        qs.pop('month', None)
        ctx['base_qs'] = qs.urlencode()

        # ---- 今月サマリ（支出・収入・収支）----
        start = cur
        end   = add_month(cur, 1)

        ctx['this_month_expense'] = (
            Expense.objects
            .filter(date__gte=start, date__lt=end)
            .aggregate(s=Sum('amount'))['s'] or 0
        )

        ctx['this_month_income'] = (
            Income.objects
            .filter(date__gte=start, date__lt=end)
            .aggregate(s=Sum('amount'))['s'] or 0
        )

        ctx['this_month_net'] = ctx['this_month_income'] - ctx['this_month_expense']

        return ctx

class ExpenseCreateView(CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'kakeibo/expense_form.html'

    def get_success_url(self):
        m = self.object.date.strftime('%Y-%m')
        return f"{reverse('kakeibo:expense_list')}?month={m}"
    
    def get_initial(self):
        initial = super().get_initial()
        d = self.request.GET.get('date')
        if d:
            initial['date'] = d  # フォームのフィールド名が date の想定
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
        ctx['current_month'] = _parse_month_param(self.request)  # ← 追加
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
        # 編集時はオブジェクトの月、なければクエリのmonth
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

class ReceiptUploadView(FormView):
    template_name = 'kakeibo/receipt_upload.html'
    form_class = ReceiptUploadForm

    def form_valid(self, form):
        d = form.cleaned_data["date"]
        img = form.cleaned_data["image"]
        rows = extract_lines(form.cleaned_data["image"])

        created = 0
        for r in rows:
            Expense.objects.create(
                date=d,
                item=r["item"],
                amount=r["amount"],
                category=r.get("category")
            )
            created += 1

        if created:
            messages.success(self.request, f"レシートから {created} 件を追加しました。")
        else:
            messages.warning(self.request, "明細が検出できませんでした。画像を変えて再試行してください。")

        return redirect(f"{reverse('kakeibo:expense_list')}?month={d.strftime('%Y-%m')}")

class ReceiptUploadView(FormView):
    form_class = ReceiptUploadForm
    template_name = "kakeibo/receipt_upload.html"
    success_url = reverse_lazy("kakeibo:receipt_review")

    def form_valid(self, form):
        d   = form.cleaned_data["date"]
        img = form.cleaned_data["image"]

        # 画像バイトを一度確保（保存にもOCRにも使う）
        img.seek(0)
        data = img.read()

        # ※プレビュー用に一時保存（開発用）
        path = default_storage.save(f"receipts/tmp/{uuid4().hex}.jpg", ContentFile(data))
        image_url = default_storage.url(path)

        # OCR → 初期表示用データ
        rows = parse_receipt(data)  # bytesでもOK（_to_bytesが対応）
        initial = []
        for r in rows:
            initial.append({
                "item": r["item"],
                "amount": r["amount"],
                "category": r["category"],  # ModelChoiceFieldはインスタンスでもIDでも可
            })

        # セッションに預けてレビューへ
        self.request.session["ocr_initial"] = initial
        self.request.session["ocr_date"] = d.isoformat()
        self.request.session["ocr_image_url"] = image_url
        return super().form_valid(form)


class ReceiptReviewView(FormView):
    template_name = "kakeibo/receipt_review.html"
    form_class = ReceiptLineFormSet
    success_url = reverse_lazy("kakeibo:ledger")  # 一覧に戻す等、任意

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
        d_str = self.request.session.get("ocr_date")
        d = date.fromisoformat(d_str) if d_str else date.today()
        created = 0
        for f in form:
            if not f.cleaned_data:
                continue
            Expense.objects.create(
                date=d,
                item=f.cleaned_data["item"],
                amount=f.cleaned_data["amount"],
                category=f.cleaned_data.get("category"),
            )
            created += 1

        # 後片付け
        for k in ("ocr_initial", "ocr_date"):
            self.request.session.pop(k, None)

        messages.success(self.request, f"{created}件を登録しました。")
        return super().form_valid(form)