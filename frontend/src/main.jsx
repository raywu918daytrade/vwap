import React from "react";
import ReactDOM from "react-dom/client";
import "./realtimeBundleCache.js";
import App from "./App.jsx";
import DiagnosticsPanel from "./DiagnosticsPanel.jsx";
import "./styles.css";
import "./compactPatternColumns.js";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
    <DiagnosticsPanel />
  </React.StrictMode>,
);
