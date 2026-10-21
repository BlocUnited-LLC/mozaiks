import GreetingsPage from "./pages/custom/GreetingsPage.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("GreetingsPage", GreetingsPage);
}
