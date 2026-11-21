/** Home: the hero's sign-in button. */
import { startSignIn } from "../app.js";

document.querySelectorAll("[data-home-signin]").forEach((button) => button.addEventListener("click", (event) => startSignIn(event.currentTarget)));
