from .models import Expense, Budget, Category, Income
from django import forms
from datetime import date
from .utils import guess_category
from django.forms import formset_factory

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

class ExpenseFilterForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date   = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    q          = forms.CharField(required=False, label='キーワード')

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'item', 'amount', 'category']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'autofocus': True}),
            'item': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例： 昼ご飯'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1, 'inputmode': 'numeric'}),
            # Select にも Bootstrap の見た目を
            'category': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'date': '日付',
            'item': '項目',
            'amount': '金額',
            'category': '費目',
        }
        help_texts = {'amount': '1円以上の整数'}

    # 既存の金額チェックはそのまま
    def clean_amount(self):
        v = self.cleaned_data['amount']
        if v is None or v < 1:
            raise forms.ValidationError('金額は1以上の整数を入力してください。')
        return v

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 未選択なら自動判別できるよう、フォーム上は必須を外す
        if 'category' in self.fields:
            self.fields['category'].required = False
            # プルダウンの先頭に「（自動判別）」を表示
            if hasattr(self.fields['category'], 'empty_label'):
                self.fields['category'].empty_label = '（自動判別）'

    def clean(self):
        """
        カテゴリ未選択なら item(+memoがあれば)から自動推定して埋める。
        推定できなければ未設定のまま（※モデルが必須ならエラーにする）。
        """
        cleaned = super().clean()
        cat = cleaned.get('category')
        if not cat:
            item = cleaned.get('item') or ''
            # Expense に memo フィールドがある場合だけ利用（無ければ空文字）
            memo = cleaned.get('memo') if 'memo' in cleaned else ''
            guessed = guess_category(item, memo)
            if guessed:
                cleaned['category'] = guessed
            # モデル側で category が必須(null=False)なら、次の1行を有効化
            # else:
            #     self.add_error('category', '費目を選択するか、推定できるキーワードを項目に含めてください。')
        return cleaned

    def save(self, commit=True):
        """
        念のため保存直前でも補完（ビューや他経路から使われても安全）。
        """
        obj = super().save(commit=False)
        if not obj.category:
            memo = getattr(obj, 'memo', '')
            guessed = guess_category(obj.item or '', memo)
            if guessed:
                obj.category = guessed
        if commit:
            obj.save()
        return obj

#class ExpenseForm(forms.ModelForm):
    #class Meta:
        model = Expense
        fields = ['date', 'item', 'amount','category']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'autofocus': True}),
            'item': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例：　昼ご飯'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1, 'inputmode': 'numeric'}),
        }
        labels = {
            'date': '日付',
            'item': '項目',
            'amount': '金額',
            'category': '費目',
        }
        help_texts = {'amount': '1円以上の整数'}

        # 追加のサーバー側チェック
    #def clean_amount(self):
        v = self.cleaned_data['amount']
        if v is None or v < 1:
            raise forms.ValidationError('金額は1以上の整数を入力してください。')
        return v

class MonthInput(forms.TextInput):
    input_type = "month"   # ブラウザの月ピッカーを使う（Chrome/Safari等）

class BudgetForm(forms.ModelForm):
    # "YYYY-MM" でも "YYYY-MM-DD" でも受け付け、1日に正規化します
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
        """unique_together(month, category) をフォーム側で事前チェック"""
        cleaned = super().clean()
        m = cleaned.get('month')
        c = cleaned.get('category')
        if m is not None:
            exists = Budget.objects.exclude(pk=self.instance.pk)\
                                   .filter(month=m, category=c).exists()
            if exists:
                raise forms.ValidationError('この「対象月×費目」の予算は既に登録済みです。')
        return cleaned

# ここから追加（ReceiptLineForm を先に定義）
class ReceiptLineForm(forms.Form):
    item = forms.CharField(
        label="項目",
        widget=forms.TextInput(attrs={"class": "form-control"})
    )
    amount = forms.IntegerField(
        label="金額",
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control"})
    )
    category = forms.ModelChoiceField(
        label="費目",
        queryset=Category.objects.all().order_by("name"),
        required=False,
        empty_label="(自動判別/未選択)",
        widget=forms.Select(attrs={"class": "form-select"})
    )
    # 行の元テキストなどを隠しで持たせたい場合はこれも
    raw_text = forms.CharField(required=False, widget=forms.HiddenInput())
# ここまで追加

# そして“この後”に FormSet を作る
ReceiptLineFormSet = formset_factory(ReceiptLineForm, extra=0, can_delete=False)

# 末尾のどこでもOK（既存の ReceiptLineForm/ReceiptLineFormSet はそのまま）
from django.forms.widgets import ClearableFileInput

class ReceiptUploadForm(forms.Form):
    date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"})
    )
    image = forms.ImageField(
        widget=ClearableFileInput(attrs={
            "class": "form-control",
            "accept": "image/*",          # モバイルならギャラリー/カメラ選択
            "capture": "environment",     # 背面カメラ優先（対応端末のみ）
        })
    )
    