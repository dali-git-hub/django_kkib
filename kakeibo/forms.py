# kakeibo/forms.py
from datetime import date
from django import forms
from django.forms import formset_factory
from django.forms.widgets import ClearableFileInput

from .models import Expense, Budget, Category, Income
from .utils import guess_category

# ===== 収入 =====
class IncomeForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = ['date', 'source', 'amount', 'note']
        widgets = {
            'date':   forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'source': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'note':   forms.TextInput(attrs={'class': 'form-control'}),
        }

# ===== 一覧フィルタ =====
class ExpenseFilterForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date   = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    q          = forms.CharField(required=False, label='キーワード')
    category   = forms.ModelChoiceField(
        queryset=Category.objects.all().order_by('name'),
        required=False,
        empty_label='（すべての費目）',
        label='費目'
    )
# ===== 支出フォーム（単票） =====
class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'item', 'amount', 'category']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'autofocus': True}),
            'item': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例： 昼ご飯'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1, 'inputmode': 'numeric'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {'date':'日付','item':'項目','amount':'金額','category':'費目'}
        help_texts = {'amount':'1円以上の整数'}

    def clean_amount(self):
        v = self.cleaned_data['amount']
        if v is None or v < 1:
            raise forms.ValidationError('金額は1以上の整数を入力してください。')
        return v

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 未選択なら自動判別
        self.fields['category'].required = False
        if hasattr(self.fields['category'], 'empty_label'):
            self.fields['category'].empty_label = '（自動判別）'

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('category'):
            guessed = guess_category(cleaned.get('item') or '', cleaned.get('note',''))
            if guessed:
                cleaned['category'] = guessed
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.category:
            guessed = guess_category(obj.item or '', getattr(obj, 'note', ''))
            if guessed:
                obj.category = guessed
        if commit:
            obj.save()
        return obj

# ===== 予算 =====
class MonthInput(forms.TextInput):
    input_type = "month"

class BudgetForm(forms.ModelForm):
    month = forms.DateField(
        label='対象月',
        widget=MonthInput(),
        input_formats=['%Y-%m', '%Y-%m-%d'],
    )
    category = forms.ModelChoiceField(
        label='費目（空なら全体）',
        queryset=Category.objects.all(),
        required=False,
        empty_label='— 全体 —',
    )
    class Meta:
        model = Budget
        fields = ('month', 'category', 'amount')

    def clean_month(self):
        m = self.cleaned_data['month']
        return m.replace(day=1)

    def clean(self):
        cleaned = super().clean()
        m = cleaned.get('month'); c = cleaned.get('category')
        if m is not None:
            exists = Budget.objects.exclude(pk=self.instance.pk)\
                                   .filter(month=m, category=c).exists()
            if exists:
                raise forms.ValidationError('この「対象月×費目」の予算は既に登録済みです。')
        return cleaned

# ===== レシート取込（1行=1明細） =====
class ReceiptLineForm(forms.Form):
    item = forms.CharField(
        label="項目",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "品名など"})
    )
    amount = forms.CharField(   # ← 文字列で受け取り、','等を除去してから整数化
        label="金額",
        widget=forms.NumberInput(attrs={"class": "form-control", "inputmode":"numeric"})
    )
    category = forms.ModelChoiceField(
        label="費目",
        queryset=Category.objects.all().order_by("name"),
        required=False,
        empty_label="(自動判別/未選択)",
        widget=forms.Select(attrs={"class": "form-select"})
    )
    raw_text = forms.CharField(required=False, widget=forms.HiddenInput())

    # カンマ等の混入を除去して整数化
    def clean_amount(self):
        s = str(self.cleaned_data.get("amount", "")).replace(",", "").replace("，","")
        s = "".join(ch for ch in s if ch.isdigit())
        return int(s) if s else 0

# 行削除を有効化
ReceiptLineFormSet = formset_factory(ReceiptLineForm, extra=0, can_delete=True)

# アップロードフォーム
class ReceiptUploadForm(forms.Form):
    date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"})
    )
    image = forms.ImageField(
        widget=ClearableFileInput(attrs={
            "class": "form-control",
            "accept": "image/*",
            "capture": "environment",
        })
    )
    auto_guess_category = forms.BooleanField(
        label="費目を自動判別する",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"})
    )
    def clean_image(self):
        img = self.cleaned_data.get("image")
        if not img:
            raise forms.ValidationError("画像ファイルを指定してください。")
        return img
    