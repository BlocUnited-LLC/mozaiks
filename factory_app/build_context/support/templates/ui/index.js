import Support from "./pages/custom/Support.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("Support", Support);
}
