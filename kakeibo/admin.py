from django.contrib import admin
from .models import Expense, Category, Budget, Household, Income, CategoryRule

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('id', 'date', 'item', 'amount', 'category')
    list_filter = ('category', 'date')
    search_fields = ('item',)
    date_hierarchy = 'date'

@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)

@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('month', 'category', 'amount')
    list_filter  = ('month', 'category')
    search_fields = ('category__name',)
    date_hierarchy = 'month'

@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ('date', 'source', 'amount')
    search_fields = ('source',)
    list_filter = ('date',)

@admin.register(CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = ("keyword", "category")
    search_fields = ("keyword", "category__name")


