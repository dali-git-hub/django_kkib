# kakeibo/urls.py
from django.urls import path
from . import views

app_name = "kakeibo"

urlpatterns = [
    # トップページ＝家計簿一覧（← reverse('kakeibo:ledger') がここを指す）
    path("", views.ExpenseListView.as_view(), name="ledger"),

    # ダッシュボード（必要なら）
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),

    # 支出
    path("expenses/", views.ExpenseListView.as_view(), name="expense_list"),
    path("expense/add/", views.ExpenseCreateView.as_view(), name="expense_add"),
    path("expense/<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="expense_edit"),
    path("expense/<int:pk>/delete/", views.ExpenseDeleteView.as_view(), name="expense_delete"),

    # 収入
    path("incomes/", views.IncomeListView.as_view(), name="income_list"),
    path("incomes/add/", views.IncomeCreateView.as_view(), name="income_add"),
    path("incomes/<int:pk>/edit/", views.IncomeUpdateView.as_view(), name="income_edit"),
    path("incomes/<int:pk>/delete/", views.IncomeDeleteView.as_view(), name="income_delete"),

    # 予算
    path("budgets/", views.BudgetListView.as_view(), name="budget_list"),
    path("budgets/add/", views.BudgetCreateView.as_view(), name="budget_add"),
    path("budgets/<int:pk>/edit/", views.BudgetUpdateView.as_view(), name="budget_edit"),
    path("budgets/<int:pk>/delete/", views.BudgetDeleteView.as_view(), name="budget_delete"),

    # 集計・分析
    path("summary/", views.ExpenseMonthlySummaryView.as_view(), name="expense_summary"),
    path("analytics/", views.AnalyticsPageView.as_view(), name="analytics"),
    path("api/analytics/", views.AnalyticsApiView.as_view(), name="analytics_api"),

    # OCR フロー
    path("receipt/upload/", views.ReceiptUploadView.as_view(), name="receipt_upload"),
    path("receipt/review/", views.ReceiptReviewView.as_view(), name="receipt_review"),

    # API
    path("api/guess-category/", views.guess_category_api, name="guess_category_api"),

    #まとめ削除機能
    path('expenses/bulk-delete/', views.ExpenseBulkDeleteView.as_view(), name='expense_bulk_delete'),
]