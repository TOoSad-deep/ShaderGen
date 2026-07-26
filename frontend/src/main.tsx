import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { NodeLabPage } from "./pages/NodeLabPage";

const RootPage = window.location.pathname === "/lab" ? NodeLabPage : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RootPage />
  </StrictMode>,
);
