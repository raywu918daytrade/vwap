from pathlib import Path

p = Path('frontend/src/App.jsx')
s = p.read_text()
old = '  const [selectedChartDate, setSelectedChartDate] = useState(() => vwapDate);'
new = '  const [selectedChartDate, setSelectedChartDate] = useState(null);'
assert old in s
p.write_text(s.replace(old, new, 1))
