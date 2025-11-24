# kakeibo/urls.py
from django.urls import path
from . import views
from .views import ExpenseCreateView, ReceiptUploadView, ReceiptReviewView

app_name = 'kakeibo'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),  # ← ひとまず一覧をダッシュボード名で
    path('ledger/', views.ExpenseListView.as_view(), name='expense_list'),
    path('add/', views.ExpenseCreateView.as_view(), name='expense_add'),
    path('<int:pk>/edit/', views.ExpenseUpdateView.as_view(), name='expense_edit'),
    path('<int:pk>/delete/', views.ExpenseDeleteView.as_view(), name='expense_delete'),

    path('summary/', views.ExpenseMonthlySummaryView.as_view(), name='expense_summary'),
    path('analytics/', views.AnalyticsPageView.as_view(), name='analytics'),
    path('api/analytics/', views.AnalyticsApiView.as_view(), name='analytics_api'),

    path('incomes/', views.IncomeListView.as_view(), name='income_list'),
    path('incomes/add/', views.IncomeCreateView.as_view(), name='income_add'),
    path('incomes/<int:pk>/edit/', views.IncomeUpdateView.as_view(), name='income_edit'),
    path('incomes/<int:pk>/delete/', views.IncomeDeleteView.as_view(), name='income_delete'),

    path('budgets/', views.BudgetListView.as_view(), name='budget_list'),
    path('budgets/add/', views.BudgetCreateView.as_view(), name='budget_add'),
    path('budgets/<int:pk>/edit/', views.BudgetUpdateView.as_view(), name='budget_edit'),
    path('budgets/<int:pk>/delete/', views.BudgetDeleteView.as_view(), name='budget_delete'),

    path("api/guess-category/", views.guess_category_api, name="guess_category_api"),

    path("receipt/upload/", ReceiptUploadView.as_view(), name="receipt_upload"),
    path("receipt/review/", ReceiptReviewView.as_view(), name="receipt_review"),
]
