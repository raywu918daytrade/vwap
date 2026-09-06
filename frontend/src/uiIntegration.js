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

function unifyD1Toolbar() {
  const found = findD1Toolbar();
  if (!found) return;

  const { d1Details, d1Row, topRow, topDetails } = found;
  if (topRow.dataset.d1Unified === "1") return;

  const topMain = topRow.firstElementChild;
  const d1Main = d1Row.firstElementChild;
  const topMenu = topDetails.querySelector(".dropdown-content");
  const d1Menu = d1Details.querySelector(".dropdown-content");
  if (!topMain || !d1Main || !topMenu || !d1Menu) return;

  topRow.dataset.d1Unified = "1";

  d1Main.classList.add("d1-inline-filters");
  topMain.appendChild(d1Main);

  const sectionLabel = document.createElement("div");
  sectionLabel.className = "d1-menu-section-label";
  sectionLabel.textContent = "D1型態";
  topMenu.appendChild(sectionLabel);

  d1Menu.classList.add("d1-menu-inline");
  topMenu.appendChild(d1Menu);

  d1Row.classList.add("d1-toolbar-hidden-row");
}

let scheduled = false;
function scheduleUnify() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    unifyD1Toolbar();
  });
}

export function installUiIntegration() {
  if (typeof document === "undefined") return;
  scheduleUnify();
  const observer = new MutationObserver(scheduleUnify);
  observer.observe(document.documentElement, { childList: true, subtree: true });
}
