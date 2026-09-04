import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        # 1. Signal 先建
        migrations.CreateModel(
            name='Signal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stock_id', models.CharField(max_length=10, verbose_name='股票代號')),
                ('stock_name', models.CharField(blank=True, max_length=50, verbose_name='股票名稱')),
                ('signal_time', models.DateTimeField(verbose_name='訊號時間')),
                ('direction', models.CharField(choices=[('BUY', '買進'), ('SELL', '賣出')], max_length=4, verbose_name='方向')),
                ('score', models.IntegerField(verbose_name='模型分數')),
                ('reason', models.JSONField(default=list, verbose_name='訊號原因')),
                ('status', models.CharField(
                    choices=[
                        ('SIGNAL_ONLY', '只出訊號'), ('RISK_CHECKED', '通過風控'),
                        ('ORDER_SENT', '已送單'), ('ORDER_ACCEPTED', '委託成功'),
                        ('PARTIAL_FILLED', '部分成交'), ('FILLED', '完全成交'),
                        ('HOLDING', '持倉中'), ('EXIT_SIGNAL', '出場訊號'),
                        ('EXIT_SENT', '出場委託'), ('CLOSED', '已平倉'),
                        ('REJECTED', '下單失敗'), ('CANCELLED', '取消'), ('ERROR', '異常'),
                    ],
                    default='SIGNAL_ONLY', max_length=20, verbose_name='狀態'
                )),
                ('signal_price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='訊號價')),
                ('current_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='現價')),
                ('stop_loss', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='停損')),
                ('take_profit', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='停利')),
                ('exit_rule', models.CharField(blank=True, max_length=200, verbose_name='出場規則')),
                ('realized_pnl_pct', models.FloatField(blank=True, null=True, verbose_name='實現損益%')),
                ('lifecycle_events', models.JSONField(default=list, verbose_name='生命週期事件')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': '交易訊號', 'verbose_name_plural': '交易訊號', 'ordering': ['-signal_time']},
        ),
        # 2. Order 建立時直接含 signal FK
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('signal', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='orders', to='trading.signal', verbose_name='訊號'
                )),
                ('broker_order_id', models.CharField(blank=True, max_length=100, verbose_name='券商委託編號')),
                ('side', models.CharField(choices=[('BUY', '買進'), ('SELL', '賣出')], max_length=4, verbose_name='方向')),
                ('price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='委託價')),
                ('quantity', models.IntegerField(verbose_name='委託量')),
                ('order_status', models.CharField(
                    choices=[
                        ('PENDING', '待送出'), ('SENT', '已送出'), ('ACCEPTED', '委託成功'),
                        ('PARTIAL', '部分成交'), ('FILLED', '完全成交'),
                        ('CANCELLED', '已取消'), ('REJECTED', '已拒絕'), ('ERROR', '異常'),
                    ],
                    default='PENDING', max_length=20, verbose_name='狀態'
                )),
                ('broker_response', models.TextField(blank=True, verbose_name='券商回傳')),
                ('is_entry', models.BooleanField(default=True, verbose_name='進場單')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': '委託單', 'verbose_name_plural': '委託單', 'ordering': ['-created_at']},
        ),
        # 3. Fill 最後建
        migrations.CreateModel(
            name='Fill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='fills', to='trading.order', verbose_name='委託單'
                )),
                ('fill_price', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='成交價')),
                ('fill_quantity', models.IntegerField(verbose_name='成交量')),
                ('fill_time', models.DateTimeField(verbose_name='成交時間')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name': '成交紀錄', 'verbose_name_plural': '成交紀錄', 'ordering': ['fill_time']},
        ),
    ]
