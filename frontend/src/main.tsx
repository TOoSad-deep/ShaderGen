import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { NodeLabPage } from "./pages/NodeLabPage";

const page = window.location.pathname.replace(/\/+$/, "") === "/lab"
  ? <NodeLabPage />
  : <App />;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {page}
  </StrictMode>,
);
