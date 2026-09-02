(function () {
  const button = document.querySelector(".side-menu-button");
  const menu = document.getElementById("sideMenu");
  const overlay = document.querySelector(".side-menu-overlay");
  const closeButton = menu ? menu.querySelector(".side-menu-close") : null;

  if (!button || !menu || !overlay) return;

  function openMenu() {
    button.setAttribute("aria-expanded", "true");
    menu.setAttribute("aria-hidden", "false");
    menu.classList.add("is-open");
    overlay.hidden = false;
    overlay.classList.add("is-open");
    document.body.classList.add("side-menu-open");
    if (closeButton) closeButton.focus();
  }

  function closeMenu() {
    const wasOpen = menu.classList.contains("is-open");
    button.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-hidden", "true");
    menu.classList.remove("is-open");
    overlay.classList.remove("is-open");
    overlay.hidden = true;
    document.body.classList.remove("side-menu-open");
    if (wasOpen) button.focus();
  }

  button.addEventListener("click", () => {
    if (menu.classList.contains("is-open")) closeMenu();
    else openMenu();
  });

  document.querySelectorAll("[data-side-menu-close]").forEach((element) => {
    element.addEventListener("click", closeMenu);
  });

  menu.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.classList.contains("is-open")) closeMenu();
  });
})();
