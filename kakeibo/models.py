from django.db import models
from django.conf import settings
from datetime import date

class Household(models.Model):
    name = models.CharField('世帯名', max_length=50)

    def __str__(self):
        return self.name
    
class Category(models.Model):
    name = models.CharField('費目名', max_length=30)
    household = models.ForeignKey('Household', on_delete=models.CASCADE, related_name='categories', null=True, blank=True)

    class Meta:
        verbose_name = '費目'
        verbose_name_plural = '費目'
        ordering = ['name']
        unique_together = (('household', 'name'),)

    def __str__(self) -> str:
        return self.name

class Expense(models.Model):
    date = models.DateField('日付')
    item = models.CharField('項目', max_length=100)
    amount = models.IntegerField('金額')
    # 追加：費目（最初は任意にしておくと移行が楽）
    category = models.ForeignKey(
        Category,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='expenses',
        verbose_name='費目'
    )

    class Meta:
        ordering = ['-date']

    def __str__(self) -> str:
        return f"{self.date} {self.item} {self.amount}円"


class Budget(models.Model):
    month   = models.DateField('対象月（1日固定）', db_index=True)
    category = models.ForeignKey(
        Category, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='budgets'
    )
    amount  = models.PositiveIntegerField('予算(円)')

    class Meta:
        ordering = ['-month', 'category__name']
        # unique_together でもOKだが将来互換のため UniqueConstraint に
        constraints = [
            models.UniqueConstraint(
                fields=['month', 'category'],
                name='uniq_budget_month_category'
            )
        ]
        indexes = [
            models.Index(fields=['month']),
            models.Index(fields=['category']),
        ]

    def save(self, *args, **kwargs):
        # 入力が「2025-11-15」でも 2025-11-01 に正規化（UI/CSV取込時の保険）
        if self.month:
            self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    def __str__(self):
        cat = self.category.name if self.category else '全体'
        return f'{self.month:%Y-%m} / {cat} : {self.amount:,}円'


class Income(models.Model):
    date   = models.DateField('日付')
    source = models.CharField('収入源', max_length=100)
    amount = models.PositiveIntegerField('金額(円)')
    note   = models.CharField('メモ', max_length=200, blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f'{self.date} {self.source} {self.amount}円'
    
class CategoryRule(models.Model):
    keyword  = models.CharField(max_length=50, unique=True)   # 例: "ローソン", "Amazon", "電気"
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.keyword} → {self.category.name}"

# 画像アップしてOCR → 行ごとに確認してからExpenseへ落とす用
class Receipt(models.Model):
    image = models.ImageField(upload_to='receipts/%Y/%m/%d')
    store = models.CharField(max_length=120, blank=True)
    date = models.DateField(default=date.today)
    total = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} {self.store or 'レシート'}"

class ReceiptLine(models.Model):
    receipt   = models.ForeignKey(Receipt, related_name='lines', on_delete=models.CASCADE)
    raw_text  = models.CharField(max_length=255)        # OCR生文字
    item      = models.CharField(max_length=255, blank=True)  # 整形後/編集後
    amount    = models.IntegerField(null=True, blank=True)
    category  = models.ForeignKey('Category', null=True, blank=True, on_delete=models.SET_NULL)
    include   = models.BooleanField(default=True)       # 登録対象にするか
    confidence= models.FloatField(null=True, blank=True)
    y_min     = models.IntegerField(null=True, blank=True)  # 画像上の位置(簡易)
    y_max     = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.item or self.raw_text