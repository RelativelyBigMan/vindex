/** Home: the hero's sign-in button. */
import { startSignIn } from "../app.js";

document.querySelector("[data-home-signin]")?.addEventListener("click", (event) => startSignIn(event.currentTarget));
