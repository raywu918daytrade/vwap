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

  // 型態過濾改由表格欄位標題旁的 checkbox 控制，因此整排舊按鈕隱藏。
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

function syncPatternHeaderFilters() {
  const buttons = patternFilterButtons();
  if (!buttons.length) return;

  const headers = [...document.querySelectorAll("thead th")];

  for (const button of buttons) {
    const name = button.textContent.trim();
    const header = headers.find((th) => {
      const label = th.querySelector(".pattern-header-label")?.textContent.trim();
      if (label) return label === name;
      return th.textContent.trim() === name;
    });
    if (!header) continue;

    let label = header.querySelector(".pattern-header-filter");
    let checkbox = label?.querySelector('input[type="checkbox"]');

    if (!label || !checkbox) {
      const originalText = header.textContent.trim();
      header.textContent = "";

      const nameSpan = document.createElement("span");
      nameSpan.className = "pattern-header-label";
      nameSpan.textContent = originalText;
      header.appendChild(nameSpan);

      label = document.createElement("label");
      label.className = "pattern-header-filter";
      label.title = `過濾${name}`;

      checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "checkbox checkbox-xs pattern-header-checkbox";
      checkbox.setAttribute("aria-label", `過濾${name}`);

      checkbox.addEventListener("click", (event) => event.stopPropagation());
      checkbox.addEventListener("change", (event) => {
        event.stopPropagation();
        const currentButton = patternFilterButtons().find((item) => item.textContent.trim() === name);
        currentButton?.click();
      });

      label.addEventListener("click", (event) => event.stopPropagation());
      label.appendChild(checkbox);
      header.appendChild(label);
    }

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
