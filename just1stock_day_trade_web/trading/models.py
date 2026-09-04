from django.db import models

SIGNAL_STATUS = [
    ('SIGNAL_ONLY',    '只出訊號'),
    ('RISK_CHECKED',   '通過風控'),
    ('ORDER_SENT',     '已送單'),
    ('ORDER_ACCEPTED', '委託成功'),
    ('PARTIAL_FILLED', '部分成交'),
    ('FILLED',         '完全成交'),
    ('HOLDING',        '持倉中'),
    ('EXIT_SIGNAL',    '出場訊號'),
    ('EXIT_SENT',      '出場委託'),
    ('CLOSED',         '已平倉'),
    ('REJECTED',       '下單失敗'),
    ('CANCELLED',      '取消'),
    ('ERROR',          '異常'),
]

DIRECTION = [
    ('BUY',  '買進'),
    ('SELL', '賣出'),
]

ORDER_STATUS = [
    ('PENDING',   '待送出'),
    ('SENT',      '已送出'),
    ('ACCEPTED',  '委託成功'),
    ('PARTIAL',   '部分成交'),
    ('FILLED',    '完全成交'),
    ('CANCELLED', '已取消'),
    ('REJECTED',  '已拒絕'),
    ('ERROR',     '異常'),
]


class Signal(models.Model):
    stock_id         = models.CharField(max_length=10, verbose_name='股票代號')
    stock_name       = models.CharField(max_length=50, blank=True, verbose_name='股票名稱')
    signal_time      = models.DateTimeField(verbose_name='訊號時間')
    direction        = models.CharField(max_length=4, choices=DIRECTION, verbose_name='方向')
    score            = models.IntegerField(verbose_name='模型分數')
    reason           = models.JSONField(default=list, verbose_name='訊號原因')
    status           = models.CharField(max_length=20, choices=SIGNAL_STATUS, default='SIGNAL_ONLY', verbose_name='狀態')
    signal_price     = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='訊號價')
    current_price    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='現價')
    stop_loss        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='停損')
    take_profit      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='停利')
    exit_rule        = models.CharField(max_length=200, blank=True, verbose_name='出場規則')
    realized_pnl_pct = models.FloatField(null=True, blank=True, verbose_name='實現損益%')
    lifecycle_events = models.JSONField(default=list, verbose_name='生命週期事件')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-signal_time']
        verbose_name = '交易訊號'
        verbose_name_plural = '交易訊號'

    def __str__(self):
        return f"{self.signal_time.strftime('%H:%M')} {self.stock_id} {self.get_direction_display()} [{self.get_status_display()}]"

    @property
    def avg_fill_price(self):
        fills = Fill.objects.filter(order__signal=self)
        total_qty = sum(f.fill_quantity for f in fills)
        if total_qty == 0:
            return None
        total_val = sum(float(f.fill_price) * f.fill_quantity for f in fills)
        return round(total_val / total_qty, 2)

    @property
    def pnl_pct(self):
        if self.status == 'CLOSED':
            return self.realized_pnl_pct
        afp = self.avg_fill_price
        if afp and self.current_price:
            if self.direction == 'BUY':
                return round((float(self.current_price) - afp) / afp * 100, 2)
            else:
                return round((afp - float(self.current_price)) / afp * 100, 2)
        return None


class Order(models.Model):
    signal          = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name='orders', verbose_name='訊號')
    broker_order_id = models.CharField(max_length=100, blank=True, verbose_name='券商委託編號')
    side            = models.CharField(max_length=4, choices=DIRECTION, verbose_name='方向')
    price           = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='委託價')
    quantity        = models.IntegerField(verbose_name='委託量')
    order_status    = models.CharField(max_length=20, choices=ORDER_STATUS, default='PENDING', verbose_name='狀態')
    broker_response = models.TextField(blank=True, verbose_name='券商回傳')
    is_entry        = models.BooleanField(default=True, verbose_name='進場單')
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '委託單'
        verbose_name_plural = '委託單'

    def __str__(self):
        return f"{self.signal.stock_id} {self.get_side_display()} {self.price}×{self.quantity}"

    @property
    def filled_quantity(self):
        return sum(f.fill_quantity for f in self.fills.all())


class Fill(models.Model):
    order         = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='fills', verbose_name='委託單')
    fill_price    = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='成交價')
    fill_quantity = models.IntegerField(verbose_name='成交量')
    fill_time     = models.DateTimeField(verbose_name='成交時間')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fill_time']
        verbose_name = '成交紀錄'
        verbose_name_plural = '成交紀錄'

    def __str__(self):
        return f"{self.fill_time.strftime('%H:%M:%S')} {self.fill_price}×{self.fill_quantity}"
