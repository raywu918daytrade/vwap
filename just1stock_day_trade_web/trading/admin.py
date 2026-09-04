from django.contrib import admin
from .models import Signal, Order, Fill


class FillInline(admin.TabularInline):
    model = Fill
    extra = 0


class OrderInline(admin.TabularInline):
    model = Order
    extra = 0
    show_change_link = True


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display  = ['signal_time', 'stock_id', 'stock_name', 'direction', 'score', 'status', 'signal_price', 'current_price', 'pnl_pct']
    list_filter   = ['status', 'direction', 'signal_time']
    search_fields = ['stock_id', 'stock_name']
    inlines       = [OrderInline]
    readonly_fields = ['pnl_pct', 'avg_fill_price', 'created_at', 'updated_at']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'signal', 'side', 'price', 'quantity', 'order_status', 'filled_quantity']
    list_filter   = ['order_status', 'side', 'is_entry']
    search_fields = ['signal__stock_id', 'broker_order_id']
    inlines       = [FillInline]
    readonly_fields = ['filled_quantity', 'created_at', 'updated_at']


@admin.register(Fill)
class FillAdmin(admin.ModelAdmin):
    list_display  = ['fill_time', 'order', 'fill_price', 'fill_quantity']
    list_filter   = ['fill_time']
