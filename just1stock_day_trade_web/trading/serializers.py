from rest_framework import serializers
from .models import Signal, Order, Fill


class FillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fill
        fields = ['id', 'fill_price', 'fill_quantity', 'fill_time']


class OrderSerializer(serializers.ModelSerializer):
    fills = FillSerializer(many=True, read_only=True)
    filled_quantity = serializers.ReadOnlyField()
    signal_stock_id = serializers.CharField(source='signal.stock_id', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'signal', 'signal_stock_id', 'broker_order_id',
            'side', 'price', 'quantity', 'order_status',
            'broker_response', 'is_entry', 'filled_quantity',
            'fills', 'created_at',
        ]


class SignalListSerializer(serializers.ModelSerializer):
    """輕量版，用於列表"""
    pnl_pct = serializers.ReadOnlyField()
    avg_fill_price = serializers.ReadOnlyField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)

    class Meta:
        model = Signal
        fields = [
            'id', 'stock_id', 'stock_name', 'signal_time', 'direction',
            'direction_display', 'score', 'status', 'status_display',
            'signal_price', 'current_price', 'avg_fill_price',
            'stop_loss', 'take_profit', 'pnl_pct',
        ]


class SignalDetailSerializer(serializers.ModelSerializer):
    """完整版，含 orders/fills 與 lifecycle"""
    pnl_pct = serializers.ReadOnlyField()
    avg_fill_price = serializers.ReadOnlyField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    orders = OrderSerializer(many=True, read_only=True)

    class Meta:
        model = Signal
        fields = [
            'id', 'stock_id', 'stock_name', 'signal_time', 'direction',
            'direction_display', 'score', 'reason', 'status', 'status_display',
            'signal_price', 'current_price', 'avg_fill_price',
            'stop_loss', 'take_profit', 'exit_rule',
            'pnl_pct', 'realized_pnl_pct', 'lifecycle_events',
            'orders', 'created_at', 'updated_at',
        ]
