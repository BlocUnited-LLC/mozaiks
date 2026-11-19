import SupportProfileTab, { SupportAdminPanel } from "./components/SupportProfileTab.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("SupportProfileTab", SupportProfileTab);
  registerComponent("SupportAdminPanel", SupportAdminPanel);
}
