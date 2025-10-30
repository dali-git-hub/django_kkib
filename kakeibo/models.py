from django.db import models

class Expense(models.Model):
    date = models.DateField()
    item = models.CharField(max_length=100)
    amount = models.IntegerField()

    def __str__(self):
        return f"{self.date} {self.item} {self.amount}円"

# Create your models here.
