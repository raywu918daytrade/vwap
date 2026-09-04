import json
import os
import urllib.request
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

# 伺服器端知道後端真實 URL；瀏覽器只看到 /proxy（不暴露 onrender.com，避免廣告攔截）
_BACKEND = os.environ.get("TRADING_BACKEND_URL", "http://localhost:8000").rstrip("/")
# _BACKEND = os.environ.get("TRADING_BACKEND_URL", "http://day-trade-back.just1stock.com").rstrip("/")


def dashboard(request):
    return render(request, "trading/dashboard.html", {"backend": "/proxy"})


def health(request):
    def ping():
        try:
            urllib.request.urlopen(f"{_BACKEND}/health", timeout=5)
        except Exception:
            pass

    import threading

    threading.Thread(target=ping, daemon=True).start()
    return JsonResponse({"status": "ok"})


def proxy_stream(request):
    """SSE 透傳：伺服器端連到 FastAPI /stream，把事件逐行推給瀏覽器。"""

    def _gen():
        try:
            req = urllib.request.Request(f"{_BACKEND}/stream")
            # FastAPI 端每 5 秒送一次 heartbeat，若這裡 30 秒內讀不到任何一行，
            # 代表上游連線已經悄悄卡死（不會拋例外、也不會關閉），readline() 會
            # 永遠 block，佔用一條 gunicorn thread 且瀏覽器 EventSource 誤以為連線正常
            # 而不會自動重連。加上 timeout 讓它主動斷線 → 觸發前端自動重連 + fetchAll()。
            with urllib.request.urlopen(req, timeout=30) as r:
                while True:
                    line = r.readline()
                    if not line:
                        break
                    yield line
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'msg': str(e)})}\n\n".encode()

    resp = StreamingHttpResponse(_gen(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


@csrf_exempt
def proxy_api(request, api_path):
    """通用 API 透傳（GET / POST）。"""
    qs = request.META.get("QUERY_STRING", "")
    url = f"{_BACKEND}/{api_path}" + (f"?{qs}" if qs else "")
    try:
        if request.method == "POST":
            req = urllib.request.Request(
                url,
                data=request.body,
                headers={"Content-Type": "application/json"},
            )
        else:
            req = urllib.request.Request(url)
        timeout = 180 if (
            "sr_vwap_cross" in api_path
            or "vwap_breakout" in api_path
            or "vwap_sr_replay" in api_path
            or "vwap_sr_catchup" in api_path
            or "vwap_macd_div" in api_path
            or "vwap_activity" in api_path
        ) else 15
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            ct = r.headers.get("Content-Type", "application/json")
        return HttpResponse(content, content_type=ct)
    except urllib.error.HTTPError as e:
        return HttpResponse(e.read(), content_type="application/json", status=e.code)
    except Exception as e:
        return HttpResponse(
            json.dumps({"error": str(e)}),
            content_type="application/json",
            status=502,
        )
