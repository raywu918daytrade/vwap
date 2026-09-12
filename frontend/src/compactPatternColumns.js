const PATTERN_GROUPS = [
  ["W底", "M頭"],
  ["頭肩底", "頭肩頂"],
  ["ABCD 上漲", "ABCD 下跌"],
  ["突破壓力回測", "跌破支撐反彈"],
  ["MACD柱底背離", "MACD柱頂背離"],
  ["杯柄型態"],
  ["三角收斂"],
];

const GROUP_BY_LABEL = new Map(
  PATTERN_GROUPS.flatMap((labels, groupIndex) =>
    labels.map((label, rowIndex) => [label, { groupIndex, rowIndex, paired: labels.length > 1 }]),
  ),
);

const TABLE_SELECTOR = "table.table-pin-rows";
const FIXED_COLUMN_COUNT = 6;
const MIN_PATTERN_COLUMN_WIDTH = 104;
const pendingTables = new Set();
let frame = 0;

function headerLabel(cell) {
  return cell?.querySelector("span")?.textContent?.trim() || cell?.textContent?.trim() || "";
}

function showFullStockText(cell) {
  cell.style.flexDirection = "column";
  cell.style.alignItems = "flex-start";
  cell.style.justifyContent = "center";
  cell.style.whiteSpace = "normal";
  cell.style.overflow = "visible";

  cell.querySelectorAll(".truncate").forEach((node) => {
    node.style.maxWidth = "100%";
    node.style.overflow = "visible";
    node.style.textOverflow = "clip";
    node.style.whiteSpace = "normal";
    node.style.wordBreak = "keep-all";
  });

  [...cell.children].forEach((node) => {
    node.style.maxWidth = "100%";
  });
}

function compactTable(table) {
  if (!table?.isConnected) return;
  const headRow = table.tHead?.rows?.[0];
  if (!headRow || headRow.cells.length <= FIXED_COLUMN_COUNT) return;

  const patternHeaders = [...headRow.cells].slice(FIXED_COLUMN_COUNT);
  const recognized = patternHeaders
    .map((cell) => ({ cell, meta: GROUP_BY_LABEL.get(headerLabel(cell)) }))
    .filter((item) => item.meta);
  if (!recognized.length) return;

  const visibleGroups = [];
  for (const item of recognized) {
    if (!visibleGroups.includes(item.meta.groupIndex)) visibleGroups.push(item.meta.groupIndex);
  }

  const groupColumn = new Map(visibleGroups.map((groupIndex, index) => [groupIndex, FIXED_COLUMN_COUNT + index + 1]));
  const templateColumns = [
    "minmax(150px,1.35fr)",
    "72px",
    "64px",
    "40px",
    "52px",
    "44px",
    ...visibleGroups.map(() => `minmax(${MIN_PATTERN_COLUMN_WIDTH}px,1fr)`),
  ].join(" ");

  const bodyRows = table.tBodies[0] ? [...table.tBodies[0].rows] : [];
  for (const row of [headRow, ...bodyRows]) {
    const cells = [...row.cells];
    if (cells.length <= FIXED_COLUMN_COUNT) continue;

    row.style.display = "grid";
    row.style.gridTemplateColumns = templateColumns;
    row.style.gridAutoRows = "minmax(20px,auto)";
    row.style.alignItems = "stretch";

    cells.slice(0, FIXED_COLUMN_COUNT).forEach((cell, index) => {
      cell.style.gridColumn = String(index + 1);
      cell.style.gridRow = "1 / span 2";
      cell.style.display = "flex";
      cell.style.alignItems = "center";

      if (index === 0 && row !== headRow) {
        showFullStockText(cell);
      } else {
        cell.style.flexDirection = "row";
        if (index === 2) cell.style.justifyContent = "flex-end";
        if (index >= 3) cell.style.justifyContent = "center";
      }
    });

    patternHeaders.forEach((header, patternIndex) => {
      const cell = cells[FIXED_COLUMN_COUNT + patternIndex];
      if (!cell) return;
      const meta = GROUP_BY_LABEL.get(headerLabel(header));
      if (!meta) return;
      const column = groupColumn.get(meta.groupIndex);
      if (!column) return;

      cell.style.gridColumn = String(column);
      cell.style.gridRow = meta.paired ? String(meta.rowIndex + 1) : "1 / span 2";
      cell.style.minWidth = "0";
      cell.style.display = "flex";
      cell.style.alignItems = "center";
      cell.style.justifyContent = "center";
      cell.style.borderTop = meta.paired && meta.rowIndex === 1
        ? "1px solid color-mix(in oklab, currentColor 14%, transparent)"
        : "";
    });
  }

  table.style.minWidth = "100%";
  table.style.width = "max-content";
}

function queueTable(table) {
  if (!table) return;
  pendingTables.add(table);
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const tables = [...pendingTables];
    pendingTables.clear();
    tables.forEach(compactTable);
  });
}

function queueTablesFromNode(node) {
  if (!(node instanceof Element)) return;
  if (node.matches(TABLE_SELECTOR)) queueTable(node);
  node.querySelectorAll?.(TABLE_SELECTOR).forEach(queueTable);
}

function start() {
  document.querySelectorAll(TABLE_SELECTOR).forEach(queueTable);

  // React, charts and drawer updates used to trigger a full-document table scan
  // for every child-list mutation. Only queue the signal table that actually
  // changed, so live ticks do not repeatedly re-layout unrelated DOM.
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      const table = mutation.target instanceof Element ? mutation.target.closest(TABLE_SELECTOR) : null;
      if (table) queueTable(table);
      mutation.addedNodes.forEach(queueTablesFromNode);
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}
