from pathlib import Path

p = Path('frontend/src/App.jsx')
s = p.read_text()

old = '''  const [signalLoadedKey, setSignalLoadedKey] = useState("");\n\n  const stockIdRef = useRef(stockId);'''
new = '''  const [signalLoadedKey, setSignalLoadedKey] = useState("");\n  const [selectedChartDate, setSelectedChartDate] = useState(() => vwapDate);\n\n  const stockIdRef = useRef(stockId);'''
assert old in s
s = s.replace(old, new, 1)

old = '''  const chartLoadSeqRef = useRef(0);\n  const today = useMemo(() => taipeiTodayIso(), [clock]);'''
new = '''  const chartLoadSeqRef = useRef(0);\n  const idxCacheRef = useRef(new Map());\n  const today = useMemo(() => taipeiTodayIso(), [clock]);'''
assert old in s
s = s.replace(old, new, 1)

start = s.index('  const loadCharts = useCallback(async () => {')
end = s.index('\n\n  const selectStock = useCallback', start)
new_load = '''  const loadCharts = useCallback(async () => {\n    const sid = stockId.trim();\n    if (!sid || selectedChartDate !== activeChartDate) return;\n    const requestId = chartLoadSeqRef.current + 1;\n    chartLoadSeqRef.current = requestId;\n    const isCurrent = () => requestId === chartLoadSeqRef.current;\n    setLoadingCharts(true);\n    setDayError("");\n    setIntradayError("");\n    const forceLive = !activeChartDate || activeChartDate === today;\n    const hasPatternOverlay = dayChartPatternType !== "none";\n    const chartForceLive = hasPatternOverlay && activeChartDate ? false : forceLive;\n\n    const dayPromise = fetchJson(\n      patternDetailPath(sid, {\n        patternType: dayChartPatternType,\n        timeframe: "day",\n        date: activeChartDate,\n        limit: dayChartLimit,\n        forceLive: chartForceLive,\n      }),\n    )\n      .then((result) => {\n        if (isCurrent()) setDayData(result);\n      })\n      .catch((error) => {\n        if (!isCurrent()) return;\n        setDayData(null);\n        setDayError(error?.message || "日K載入失敗");\n      });\n\n    const intradayPromise = fetchJson(\n      patternDetailPath(sid, {\n        patternType: activeChartPatternType,\n        timeframe: activeChartTimeframe,\n        date: activeChartDate,\n        limit: activeChartLimit,\n        fullDay: rightChartVariant === "intraday",\n        forceLive: chartForceLive,\n      }),\n    )\n      .then((result) => {\n        if (isCurrent()) setIntradayData(result);\n      })\n      .catch((error) => {\n        if (!isCurrent()) return;\n        setIntradayData(null);\n        setIntradayError(error?.message || `${activeChartLabel}載入失敗`);\n      });\n\n    await Promise.allSettled([dayPromise, intradayPromise]);\n    if (isCurrent()) setLoadingCharts(false);\n  }, [\n    activeChartLabel,\n    activeChartLimit,\n    activeChartPatternType,\n    activeChartTimeframe,\n    activeChartDate,\n    dayChartLimit,\n    dayChartPatternType,\n    rightChartVariant,\n    selectedChartDate,\n    stockId,\n    today,\n  ]);\n\n  const loadDateIndex = useCallback(async () => {\n    if (rightChartVariant !== "intraday" || chartIndicatorMode !== "idx") {\n      setIdxData(null);\n      return;\n    }\n    const key = `${activeChartDate || "today"}:${activeChartTimeframe}`;\n    const cached = idxCacheRef.current.get(key);\n    if (cached) {\n      setIdxData(cached);\n      return;\n    }\n    try {\n      const result = await fetchJson(\n        patternDetailPath(DEFAULT_STOCK, {\n          timeframe: activeChartTimeframe,\n          date: activeChartDate,\n          limit: activeChartLimit,\n          fullDay: true,\n        }),\n      );\n      idxCacheRef.current.set(key, result);\n      setIdxData(result);\n    } catch {\n      setIdxData(null);\n    }\n  }, [activeChartDate, activeChartLimit, activeChartTimeframe, chartIndicatorMode, rightChartVariant]);'''
s = s[:start] + new_load + s[end:]

old = '''  const selectStock = useCallback((sid, key = "", kind = "manual", chartMeta = {}) => {\n    const next = String(sid);\n    setStockId(next);\n    setSelectedEventKey(key);\n    setChartContext({ kind, ...chartMeta });\n    if (kind === "vwap" || kind === "obs") setFocusedPanel(kind);\n  }, []);'''
new = '''  const selectStock = useCallback((sid, key = "", kind = "manual", chartMeta = {}) => {\n    const next = String(sid);\n    setStockId(next);\n    setSelectedEventKey(key);\n    setSelectedChartDate(vwapDate);\n    setChartContext({ kind, ...chartMeta });\n    if (kind === "vwap" || kind === "obs") setFocusedPanel(kind);\n  }, [vwapDate]);'''
assert old in s
s = s.replace(old, new, 1)

old = '''  useEffect(() => {\n    if (isVwapChart && signalLoadedKey !== signalLoadKey) return undefined;\n    const delayMs = isVwapChart ? 350 : 0;\n    const timer = window.setTimeout(() => {\n      loadCharts();\n    }, delayMs);\n    return () => window.clearTimeout(timer);\n  }, [isVwapChart, loadCharts, reloadSeq, signalLoadedKey, signalLoadKey]);'''
new = '''  useEffect(() => {\n    loadDateIndex();\n  }, [loadDateIndex]);\n\n  useEffect(() => {\n    chartLoadSeqRef.current += 1;\n    setSelectedEventKey("");\n    setDayData(null);\n    setIntradayData(null);\n    setDayError("");\n    setIntradayError("");\n    setLoadingCharts(false);\n  }, [vwapDate]);\n\n  useEffect(() => {\n    if (isVwapChart && signalLoadedKey !== signalLoadKey) return undefined;\n    if (selectedChartDate !== activeChartDate) return undefined;\n    loadCharts();\n    return undefined;\n  }, [activeChartDate, isVwapChart, loadCharts, reloadSeq, selectedChartDate, signalLoadedKey, signalLoadKey]);'''
assert old in s
s = s.replace(old, new, 1)

p.write_text(s)
