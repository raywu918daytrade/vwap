function findD1Toolbar() {
  const rows = [...document.querySelectorAll("details")];
  const d1Details = rows.find((details) => details.querySelector('summary[title="D1型態設定"]'));
  if (!d1Details) return null;

  const d1Row = d1Details.parentElement;
  const topRow = d1Row?.previousElementSibling;
  const topDetails = topRow?.querySelector("details");
  if (!d1Row || !topRow || !topDetails) return null;

  return { d1Details, d1Row, topRow, topDetails };
}

function integrateD1Settings() {
  const found = findD1Toolbar();
  if (!found) return;

  const { d1Details, d1Row, topRow, topDetails } = found;
  if (topRow.dataset.d1Integrated === "1") return;

  const topMenu = topDetails.querySelector(".dropdown-content");
  const d1Menu = d1Details.querySelector(".dropdown-content");
  if (!topMenu || !d1Menu) return;

  topRow.dataset.d1Integrated = "1";

  const sectionLabel = document.createElement("div");
  sectionLabel.className = "d1-menu-section-label";
  sectionLabel.textContent = "D1型態";
  topMenu.appendChild(sectionLabel);

  d1Menu.classList.add("d1-menu-inline");
  topMenu.appendChild(d1Menu);

  // 型態過濾已移到表格欄位標題旁的 checkbox，舊功能過濾列不再顯示。
  d1Row.classList.add("d1-toolbar-hidden-row");
}

function patternFilterButtons() {
  const found = findD1Toolbar();
  if (!found) return [];

  return [...found.d1Row.querySelectorAll("button")].filter((button) => {
    const title = button.getAttribute("title") || "";
    return title.includes("過濾") && button.textContent.trim();
  });
}

function normalizeText(value) {
  return (value || "").replace(/\s+/g, "").trim();
}

function findPatternButtonForHeader(headerText, buttons) {
  const normalizedHeader = normalizeText(headerText);
  return buttons.find((button) => {
    const buttonText = normalizeText(button.textContent);
    const title = normalizeText(button.getAttribute("title"));
    return buttonText === normalizedHeader || title.includes(normalizedHeader);
  });
}

function syncPatternHeaderFilters() {
  const buttons = patternFilterButtons();
  if (!buttons.length) return;

  const headers = [...document.querySelectorAll("thead th")];

  for (const header of headers) {
    if (header.querySelector(".pattern-header-filter")) continue;

    const originalText = header.textContent.trim();
    if (!originalText) continue;

    const button = findPatternButtonForHeader(originalText, buttons);
    if (!button) continue;

    const name = button.textContent.trim() || originalText;
    header.textContent = "";

    const nameSpan = document.createElement("span");
    nameSpan.className = "pattern-header-label";
    nameSpan.textContent = originalText;
    header.appendChild(nameSpan);

    const label = document.createElement("label");
    label.className = "pattern-header-filter";
    label.title = `過濾${name}`;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "checkbox checkbox-xs pattern-header-checkbox";
    checkbox.setAttribute("aria-label", `過濾${name}`);
    checkbox.checked = (button.getAttribute("title") || "").startsWith("取消");

    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", (event) => {
      event.stopPropagation();
      const currentButtons = patternFilterButtons();
      const currentButton = findPatternButtonForHeader(originalText, currentButtons);
      currentButton?.click();
    });

    label.addEventListener("click", (event) => event.stopPropagation());
    label.appendChild(checkbox);
    header.appendChild(label);
  }

  for (const header of headers) {
    const checkbox = header.querySelector('.pattern-header-checkbox');
    const headerText = header.querySelector('.pattern-header-label')?.textContent.trim();
    if (!checkbox || !headerText) continue;

    const button = findPatternButtonForHeader(headerText, buttons);
    if (!button) continue;
    checkbox.checked = (button.getAttribute("title") || "").startsWith("取消");
  }
}

let scheduled = false;
function scheduleIntegration() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    integrateD1Settings();
    syncPatternHeaderFilters();
  });
}

export function installUiIntegration() {
  if (typeof document === "undefined") return;
  scheduleIntegration();
  const observer = new MutationObserver(scheduleIntegration);
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["title"] });
}
