from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from .models import Expense

class ExpenseListView(ListView):
    model = Expense
    template_name = 'kakeibo/expense_list.html'
    context_object_name = 'expenses'
    ordering = ['-date'] 

class ExpenseCreateView(CreateView):
    model = Expense
    fields = ['date', 'item', 'amount']
    template_name = 'kakeibo/expense_form.html'
    success_url = reverse_lazy('expense_list')

class ExpenseUpdateView(UpdateView):
    model = Expense
    fields = ['date', 'item', 'amount']
    template_name = 'kakeibo/expense_form.html'   # ← 追加と同じフォームを使う
    success_url = reverse_lazy('expense_list')


class ExpenseDeleteView(DeleteView):
    model = Expense
    template_name = 'kakeibo/expense_confirm_delete.html'
    success_url = reverse_lazy('expense_list')