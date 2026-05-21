document.addEventListener("DOMContentLoaded", () => {
  /** Auto-dismiss for Django messages and client `showToast` (same as formset row undo). */
  const DEFAULT_TOAST_MS = 7000;

  const toastStack = document.querySelector(".toast-stack");
  const root = document.documentElement;
  const savedTheme = window.localStorage.getItem("theme");
  const themeToggle = document.querySelector("#theme-toggle");

  const setTheme = (theme) => {
    if (theme) {
      root.dataset.theme = theme;
      window.localStorage.setItem("theme", theme);
    } else {
      delete root.dataset.theme;
      window.localStorage.removeItem("theme");
    }
    if (themeToggle) {
      themeToggle.checked = root.dataset.theme === "dark";
    }
  };

  if (savedTheme) {
    setTheme(savedTheme);
  } else if (themeToggle) {
    themeToggle.checked = window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  themeToggle?.addEventListener("change", () => {
    setTheme(themeToggle.checked ? "dark" : "light");
  });

  document.querySelectorAll("[data-site-nav-menu]").forEach((menu) => {
    if (!(menu instanceof HTMLDetailsElement)) {
      return;
    }
    const panel = menu.querySelector(".site-nav-menu-panel");
    if (!(panel instanceof HTMLElement)) {
      return;
    }
    panel.querySelectorAll("a[href]").forEach((link) => {
      link.addEventListener("click", () => {
        menu.open = false;
      });
    });
    panel.querySelectorAll("button[type='submit']").forEach((button) => {
      button.addEventListener("click", () => {
        menu.open = false;
      });
    });
  });

  const dismissToast = (toast) => {
    const timeoutId = Number.parseInt(toast.dataset.timeoutId || "", 10);
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    toast.classList.add("is-hiding");
    window.setTimeout(() => toast.remove(), 260);
  };

  const scheduleToast = (toast, duration = DEFAULT_TOAST_MS) => {
    const priorId = Number.parseInt(toast.dataset.timeoutId || "", 10);
    if (priorId) {
      window.clearTimeout(priorId);
    }
    const close = toast.querySelector(".toast-close");
    close?.addEventListener("click", () => dismissToast(toast));
    const fromAttr = Number.parseInt(toast.dataset.toastMs || "", 10);
    const ms = Number.isFinite(fromAttr) && fromAttr > 0 ? fromAttr : duration;
    const timeoutId = window.setTimeout(() => dismissToast(toast), ms);
    toast.dataset.timeoutId = String(timeoutId);
  };

  const showToast = (message, tag = "success", options = {}) => {
    if (!toastStack) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `message ${tag}`;
    toast.dataset.toast = "";
    toast.innerHTML = `
      <span></span>
      <div class="toast-actions">
        ${
          options.actionLabel
            ? '<button class="toast-action" type="button"></button>'
            : ""
        }
        <button class="toast-close" type="button" aria-label="Dismiss message">×</button>
      </div>
    `;
    toast.querySelector("span").textContent = message;
    const action = toast.querySelector(".toast-action");
    if (action) {
      action.textContent = options.actionLabel;
      action.addEventListener("click", () => {
        options.onAction?.();
        dismissToast(toast);
      });
    }
    toastStack.append(toast);
    scheduleToast(toast, options.duration ?? DEFAULT_TOAST_MS);
  };

  const PENDING_TOAST_KEY = "recipe-site-pending-toast";

  const queueToastForNextPage = (message, tag = "success") => {
    if (!message) {
      return;
    }
    try {
      sessionStorage.setItem(PENDING_TOAST_KEY, JSON.stringify({ message, tag }));
    } catch {
      /* storage disabled */
    }
  };

  const showPendingToast = () => {
    try {
      const raw = sessionStorage.getItem(PENDING_TOAST_KEY);
      if (!raw) {
        return;
      }
      sessionStorage.removeItem(PENDING_TOAST_KEY);
      const data = JSON.parse(raw);
      if (data?.message) {
        showToast(data.message, data.tag || "success");
      }
    } catch {
      sessionStorage.removeItem(PENDING_TOAST_KEY);
    }
  };

  document.querySelectorAll("[data-toast]").forEach(scheduleToast);
  showPendingToast();

  const updateStars = (form) => {
    const checked = form.querySelector("input[type='radio']:checked");
    const checkedValue = checked ? Number.parseInt(checked.value, 10) : 0;
    form.querySelectorAll(".star-choice").forEach((choice) => {
      const input = choice.querySelector("input[type='radio']");
      const value = input ? Number.parseInt(input.value, 10) : 0;
      choice.classList.toggle("is-filled", value > 0 && value <= checkedValue);
    });
  };

  const clearStarFormUI = (form) => {
    form.querySelectorAll("input[type='radio']").forEach((radio) => {
      radio.checked = false;
    });
    updateStars(form);
    const active = document.activeElement;
    if (active instanceof HTMLElement && form.contains(active)) {
      active.blur();
    }
  };

  const syncStarFormsFromPayload = (payload) => {
    if (!payload?.ok) {
      return;
    }
    if (payload.rating != null) {
      return;
    }
    document.querySelectorAll(".star-rating-form").forEach((form) => {
      if (form instanceof HTMLFormElement) {
        clearStarFormUI(form);
      }
    });
  };

  const submitStarRating = async (form, { clear = false } = {}) => {
    const body = new FormData(form);
    if (clear) {
      body.set("clear", "1");
      for (const key of [...body.keys()]) {
        if (key === "value" || key.endsWith("-value")) {
          body.delete(key);
        }
      }
    }
    const response = await fetch(form.action.split("#")[0], {
      method: "POST",
      body,
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
    });
    const payload = await response.json();
    return { payload, response };
  };

  const starChoiceForEvent = (form, target) => {
    if (!(target instanceof Element)) {
      return null;
    }
    const choice = target.closest(".star-choice");
    if (!(choice instanceof HTMLElement) || choice.closest("form") !== form) {
      return null;
    }
    return choice;
  };

  const bindStarRatingForm = (form, { onSaved } = {}) => {
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    updateStars(form);

    const rememberPriorChecked = (event) => {
      const choice = starChoiceForEvent(form, event.target);
      const input = choice?.querySelector("input[type='radio']");
      if (!(input instanceof HTMLInputElement)) {
        return;
      }
      input.dataset.priorChecked = input.checked ? "1" : "0";
    };

    form.addEventListener("mousedown", rememberPriorChecked);
    form.addEventListener("pointerdown", rememberPriorChecked);

    form.addEventListener(
      "click",
      async (event) => {
        const choice = starChoiceForEvent(form, event.target);
        const input = choice?.querySelector("input[type='radio']");
        if (!(input instanceof HTMLInputElement) || input.dataset.priorChecked !== "1") {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        form.dataset.suppressRatingChange = "1";
        clearStarFormUI(form);
        try {
          const { payload, response } = await submitStarRating(form, { clear: true });
          if (payload.ok) {
            clearStarFormUI(form);
            syncStarFormsFromPayload(payload);
          }
          if (onSaved) {
            onSaved(payload, response);
          }
        } catch {
          showToast("Rating could not be removed. Please try again.", "error");
        } finally {
          delete form.dataset.suppressRatingChange;
          form.querySelectorAll("input[type='radio']").forEach((radio) => {
            delete radio.dataset.priorChecked;
          });
        }
      },
      true,
    );

    form.addEventListener("change", async (event) => {
      if (form.dataset.suppressRatingChange === "1") {
        return;
      }
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || target.type !== "radio" || target.form !== form) {
        return;
      }
      updateStars(form);
      try {
        const { payload, response } = await submitStarRating(form);
        if (onSaved) {
          onSaved(payload, response);
        }
      } catch {
        showToast("Rating could not be saved. Please try again.", "error");
      }
    });
  };

  (() => {
    const datalist = document.getElementById("recipe-tag-suggestions");
    if (!datalist) {
      return;
    }

    let panel = document.getElementById("recipe-tag-suggest-panel");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "recipe-tag-suggest-panel";
      panel.className = "tag-suggest-panel";
      panel.hidden = true;
      panel.setAttribute("role", "listbox");
      panel.setAttribute("aria-label", "Existing tag names");
      document.body.append(panel);
    }

    let activeInput = /** @type {HTMLInputElement | null} */ (null);
    let items = /** @type {string[]} */ ([]);
    let highlightIndex = -1;

    const readSuggestions = () =>
      Array.from(datalist.querySelectorAll("option"))
        .map((o) => o.value.trim())
        .filter(Boolean);

    const filterSuggestions = (query) => {
      const uniq = [...new Set(readSuggestions())];
      uniq.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
      const q = query.trim().toLowerCase();
      if (!q) {
        return uniq.slice(0, 25);
      }
      return uniq.filter((name) => name.toLowerCase().includes(q)).slice(0, 25);
    };

    const positionPanel = (input) => {
      const r = input.getBoundingClientRect();
      const gap = 4;
      const margin = 8;
      const minWidth = 160;
      const maxWidth = Math.min(320, window.innerWidth - margin * 2);
      const width = Math.min(Math.max(r.width, minWidth), maxWidth);
      let left = r.left;
      if (left + width > window.innerWidth - margin) {
        left = window.innerWidth - margin - width;
      }
      if (left < margin) {
        left = margin;
      }
      let top = r.bottom + gap;
      const panelHeight = panel.offsetHeight || 0;
      const maxTop = window.innerHeight - margin - panelHeight;
      if (panelHeight > 0 && top > maxTop) {
        top = Math.max(margin, r.top - gap - panelHeight);
      }
      panel.style.position = "fixed";
      panel.style.left = `${Math.round(left)}px`;
      panel.style.top = `${Math.round(top)}px`;
      panel.style.width = `${Math.round(width)}px`;
      panel.style.maxWidth = `${Math.round(maxWidth)}px`;
    };

    const updateHighlightClasses = () => {
      panel.querySelectorAll(".tag-suggest-item").forEach((el, i) => {
        el.classList.toggle("tag-suggest-item--active", i === highlightIndex);
        el.setAttribute("aria-selected", i === highlightIndex ? "true" : "false");
      });
    };

    const scrollActiveIntoView = () => {
      const el = panel.querySelector(`.tag-suggest-item:nth-child(${highlightIndex + 1})`);
      if (el instanceof HTMLElement) {
        el.scrollIntoView({ block: "nearest" });
      }
    };

    const closePanel = () => {
      panel.hidden = true;
      panel.innerHTML = "";
      items = [];
      highlightIndex = -1;
      activeInput = null;
    };

    /** Apply a picked suggestion; on quick-add tag form, submit immediately instead of only filling the field. */
    const applySuggestionPick = (input, name) => {
      input.value = name;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      closePanel();
      const quickForm = input.closest("form[data-recipe-quick-tag-form]");
      if (quickForm instanceof HTMLFormElement) {
        const submitter = quickForm.querySelector("button[type='submit']");
        if (submitter instanceof HTMLButtonElement) {
          submitter.click();
        } else {
          quickForm.requestSubmit();
        }
        return;
      }
      input.focus();
    };

    const renderPanel = () => {
      if (!activeInput) {
        return;
      }
      items = filterSuggestions(activeInput.value);
      panel.innerHTML = "";
      highlightIndex = items.length > 0 ? 0 : -1;
      items.forEach((name, index) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tag-suggest-item";
        btn.setAttribute("role", "option");
        btn.setAttribute("aria-selected", index === highlightIndex ? "true" : "false");
        btn.textContent = name;
        btn.addEventListener("click", () => {
          const input = activeInput;
          if (!input) {
            return;
          }
          applySuggestionPick(input, name);
        });
        panel.append(btn);
      });
      if (items.length === 0) {
        panel.hidden = true;
      } else {
        panel.hidden = false;
        updateHighlightClasses();
        positionPanel(activeInput);
        window.requestAnimationFrame(() => {
          if (activeInput && !panel.hidden) {
            positionPanel(activeInput);
          }
        });
      }
    };

    const openFor = (input) => {
      activeInput = input;
      renderPanel();
    };

    panel.addEventListener("mousedown", (e) => {
      e.preventDefault();
    });

    document.addEventListener("focusin", (e) => {
      const t = e.target;
      if (t instanceof HTMLInputElement && t.dataset.tagSuggest === "true") {
        openFor(t);
        return;
      }
      if (activeInput && t instanceof Node && !panel.contains(t) && t !== activeInput) {
        closePanel();
      }
    });

    document.addEventListener("input", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLInputElement) || t.dataset.tagSuggest !== "true") {
        return;
      }
      activeInput = t;
      renderPanel();
    });

    document.addEventListener("keydown", (e) => {
      if (!activeInput || panel.hidden || document.activeElement !== activeInput) {
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlightIndex = Math.min(highlightIndex + 1, items.length - 1);
        updateHighlightClasses();
        scrollActiveIntoView();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlightIndex = Math.max(highlightIndex - 1, 0);
        updateHighlightClasses();
        scrollActiveIntoView();
      } else if (e.key === "Enter") {
        if (highlightIndex >= 0 && items[highlightIndex]) {
          e.preventDefault();
          const input = activeInput;
          const picked = items[highlightIndex];
          applySuggestionPick(input, picked);
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        closePanel();
      }
    });

    document.addEventListener("click", (e) => {
      const t = e.target;
      if (!activeInput || panel.hidden) {
        return;
      }
      if (!(t instanceof Node)) {
        return;
      }
      if (!panel.contains(t) && t !== activeInput) {
        closePanel();
      }
    });

    window.addEventListener("resize", () => {
      if (activeInput && !panel.hidden) {
        positionPanel(activeInput);
      }
    });

    window.addEventListener(
      "scroll",
      () => {
        if (activeInput && !panel.hidden) {
          positionPanel(activeInput);
        }
      },
      true,
    );
  })();

  document.querySelectorAll("[data-print-recipe]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });

  (() => {
    const root = document.querySelector("[data-make-mode]");
    if (!root) {
      return;
    }

    const track = root.querySelector("[data-make-swipe-track]");
    const zone = root.querySelector("[data-make-swipe-zone]");
    const panels = [...root.querySelectorAll("[data-make-panel]")];
    const phaseEl = root.querySelector("[data-make-phase]");
    const dots = [...root.querySelectorAll("[data-make-dot]")];
    const swipeHint = root.querySelector("[data-make-swipe-hint]");
    const prevBtn = root.querySelector("[data-make-nav='prev']");
    const nextBtn = root.querySelector("[data-make-nav='next']");
    const mobileQuery = window.matchMedia("(max-width: 48rem)");
    const touchLikeQuery = window.matchMedia("(hover: none), (pointer: coarse)");
    const MAKE_CHECK_PRESS_CLEAR_MS = 220;
    let makeCheckPressClearId = 0;

    const clearMakeCheckTouchPress = () => {
      root.querySelectorAll(".make-check-item.is-touch-pressed").forEach((item) => {
        item.classList.remove("is-touch-pressed");
      });
    };

    const scheduleMakeCheckTouchPressClear = () => {
      window.clearTimeout(makeCheckPressClearId);
      makeCheckPressClearId = window.setTimeout(clearMakeCheckTouchPress, MAKE_CHECK_PRESS_CLEAR_MS);
    };

    if (touchLikeQuery.matches) {
      root.addEventListener(
        "pointerdown",
        (event) => {
          const item = event.target.closest("[data-make-item]");
          if (!(item instanceof HTMLButtonElement)) {
            return;
          }
          window.clearTimeout(makeCheckPressClearId);
          clearMakeCheckTouchPress();
          item.classList.add("is-touch-pressed");
        },
        { passive: true },
      );

      root.addEventListener(
        "pointerup",
        (event) => {
          if (event.target.closest("[data-make-item]")) {
            scheduleMakeCheckTouchPressClear();
          }
        },
        { passive: true },
      );

      root.addEventListener("pointercancel", scheduleMakeCheckTouchPressClear, { passive: true });

      document.addEventListener(
        "pointerdown",
        (event) => {
          if (event.target.closest("[data-make-item]")) {
            return;
          }
          window.clearTimeout(makeCheckPressClearId);
          clearMakeCheckTouchPress();
        },
        { passive: true },
      );
    }

    const panelKeys = ["ingredients", "steps"];
    const phaseLabels = ["Ingredients", "Instructions"];
    const urls = {
      ingredients: root.dataset.makeUrlIngredients || "",
      steps: root.dataset.makeUrlSteps || "",
    };
    const detailUrl = root.dataset.makeDetailUrl || "";
    const recordForm = document.getElementById("make-record-form");
    const exitOverlay = document.querySelector("[data-make-exit-overlay]");
    const exitDialog = exitOverlay?.querySelector("[data-make-exit-dialog]");
    const exitStepMade = exitOverlay?.querySelector('[data-make-exit-step="made"]');
    const exitStepReview = exitOverlay?.querySelector('[data-make-exit-step="review"]');
    const exitReviewLede = exitOverlay?.querySelector("[data-make-exit-review-lede]");
    const exitStayClose = exitOverlay?.querySelector("[data-make-exit-stay]");
    const exitBackBtn = exitOverlay?.querySelector("[data-make-exit-back]");
    const recipeSlug = root.dataset.makeRecipeSlug || "";
    const panelScrollTops = {
      ingredients: 0,
      steps: 0,
    };

    let exitTargetUrl = detailUrl;
    let exitDialogOpen = false;
    let pendingMadeRecord = false;

    const panelContentEl = (panel) => panel.querySelector("[data-make-scroll]") || panel;

    const makePathnames = new Set(
      [urls.ingredients, urls.steps]
        .map((href) => {
          try {
            return new URL(href, window.location.origin).pathname;
          } catch {
            return "";
          }
        })
        .filter(Boolean),
    );
    const isMakePath = (pathname) => makePathnames.has(pathname);

    const navigateAway = (url) => {
      window.location.assign(url || detailUrl);
    };

    const showExitStep = (step) => {
      if (!(exitStepMade instanceof HTMLElement) || !(exitStepReview instanceof HTMLElement)) {
        return;
      }
      const onMade = step === "made";
      exitStepMade.hidden = !onMade;
      exitStepReview.hidden = onMade;
      exitBackBtn?.toggleAttribute("hidden", onMade);
      if (exitDialog instanceof HTMLElement) {
        exitDialog.setAttribute(
          "aria-labelledby",
          onMade ? "make-exit-made-title" : "make-exit-review-title",
        );
        exitDialog.classList.toggle("is-review-step", !onMade);
      }
    };

    const syncExitReviewLede = () => {
      if (!(exitReviewLede instanceof HTMLElement)) {
        return;
      }
      const checked = exitRatingForm?.querySelector("input[type='radio']:checked");
      exitReviewLede.textContent = checked
        ? "Tap your score again to remove it, or skip for now."
        : "Tap a star to rate. Tap your score again to remove it.";
    };

    const recordMadeIfPending = async () => {
      if (!pendingMadeRecord) {
        return;
      }
      await recordMade();
      pendingMadeRecord = false;
    };

    const finalizeMadeAndLeave = async () => {
      await recordMadeIfPending();
      closeExitDialog();
      navigateAway(exitTargetUrl);
    };

    const skipReviewAndLeave = async () => {
      try {
        await finalizeMadeAndLeave();
      } catch {
        showToast("Could not save that you made this recipe. Try again.", "error");
      }
    };

    const closeExitDialog = () => {
      if (!(exitOverlay instanceof HTMLElement)) {
        return;
      }
      exitOverlay.hidden = true;
      exitDialogOpen = false;
      document.body.classList.remove("make-exit-open");
    };

    const openExitDialog = () => {
      if (!(exitOverlay instanceof HTMLElement) || !(exitDialog instanceof HTMLElement)) {
        navigateAway(exitTargetUrl);
        return;
      }
      pendingMadeRecord = false;
      showExitStep("made");
      exitOverlay.hidden = false;
      exitDialogOpen = true;
      document.body.classList.add("make-exit-open");
      exitDialog.focus();
    };

    const beginExit = (targetUrl) => {
      exitTargetUrl = targetUrl || detailUrl;
      openExitDialog();
    };

    const exitRatingForm = exitOverlay?.querySelector("[data-make-exit-rating]");

    const recordMade = async () => {
      if (!(recordForm instanceof HTMLFormElement)) {
        return { ok: false, has_rating: false };
      }
      const response = await fetch(recordForm.action, {
        method: "POST",
        body: new FormData(recordForm),
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        throw new Error("record failed");
      }
      return payload;
    };

    const syncExitTrap = () => {
      if (activeIndex !== 0 || history.state?.makeExitTrap) {
        return;
      }
      history.pushState(
        { makeExitTrap: 1, makePanel: panelKeys[activeIndex] },
        "",
        window.location.href,
      );
    };

    const listScrollOffset = () => {
      if (!zone) {
        return window.scrollY;
      }
      return Math.max(0, -zone.getBoundingClientRect().top);
    };

    const savePanelScroll = (panelIndex) => {
      panelScrollTops[panelKeys[panelIndex]] = listScrollOffset();
    };

    const restorePanelScroll = (panelIndex) => {
      if (!zone) {
        return;
      }
      const offset = panelScrollTops[panelKeys[panelIndex]] || 0;
      const zoneTop = zone.getBoundingClientRect().top + window.scrollY;
      const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      window.scrollTo(0, Math.min(zoneTop + offset, maxScroll));
    };

    const measurePanelHeight = (panel) => panelContentEl(panel).scrollHeight;

    const syncZoneHeight = ({ duringDrag = false } = {}) => {
      if (!zone || panels.length === 0) {
        return;
      }
      const height = duringDrag
        ? Math.max(...panels.map(measurePanelHeight))
        : measurePanelHeight(panels[activeIndex]);
      zone.style.height = `${Math.ceil(height)}px`;
    };

    const afterLayout = (callback) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(callback);
      });
    };

    let activeIndex = panelKeys.indexOf(root.dataset.makeInitialPanel || "ingredients");
    if (activeIndex < 0) {
      activeIndex = 0;
    }

    const syncSwipeHint = () => {
      if (!swipeHint || !mobileQuery.matches) {
        return;
      }
      swipeHint.textContent =
        activeIndex === 0
          ? "Swipe left for instructions."
          : "Swipe right for ingredients.";
    };

    const updateUi = ({ duringDrag = false, restoreScroll = false } = {}) => {
      if (track) {
        track.style.setProperty("--make-panel-index", String(activeIndex));
        track.dataset.activePanel = panelKeys[activeIndex];
        track.style.transform = "";
        track.classList.remove("is-dragging");
      }
      zone?.classList.remove("is-dragging");
      if (phaseEl) {
        phaseEl.textContent = `Step ${activeIndex + 1} of 2 · ${phaseLabels[activeIndex]}`;
      }
      dots.forEach((dot, index) => {
        dot.classList.toggle("is-active", index === activeIndex);
      });
      prevBtn?.toggleAttribute("disabled", activeIndex === 0);
      nextBtn?.toggleAttribute("disabled", activeIndex >= panels.length - 1);
      syncSwipeHint();
      afterLayout(() => {
        syncZoneHeight({ duringDrag });
        if (restoreScroll) {
          restorePanelScroll(activeIndex);
        }
      });
    };

    const setPanel = (index, { updateHistory = true, restoreScroll = true } = {}) => {
      const next = Math.max(0, Math.min(panels.length - 1, index));
      if (next === activeIndex) {
        return;
      }
      savePanelScroll(activeIndex);
      activeIndex = next;
      if (updateHistory) {
        const url = activeIndex === 0 ? urls.ingredients : urls.steps;
        if (url) {
          history.replaceState({ makePanel: panelKeys[activeIndex] }, "", url);
        }
      }
      updateUi({ restoreScroll });
    };

    root.addEventListener("click", (event) => {
      const exitTrigger = event.target.closest("[data-make-exit]");
      if (exitTrigger instanceof HTMLElement) {
        event.preventDefault();
        beginExit(exitTrigger instanceof HTMLAnchorElement ? exitTrigger.href : detailUrl);
        return;
      }
      const item = event.target.closest("[data-make-item]");
      if (item instanceof HTMLButtonElement) {
        const isDone = item.classList.toggle("is-done");
        item.setAttribute("aria-pressed", isDone ? "true" : "false");
        item.blur();
        if (touchLikeQuery.matches) {
          scheduleMakeCheckTouchPressClear();
        }
        afterLayout(() => syncZoneHeight());
        return;
      }
      const nav = event.target.closest("[data-make-nav]");
      if (nav instanceof HTMLButtonElement && !nav.disabled) {
        event.preventDefault();
        if (nav.dataset.makeNav === "prev") {
          setPanel(activeIndex - 1);
        } else if (nav.dataset.makeNav === "next") {
          setPanel(activeIndex + 1);
        }
      }
    });

    history.replaceState({ makePanel: panelKeys[activeIndex] }, "", window.location.href);
    updateUi();
    syncExitTrap();

    exitStayClose?.addEventListener("click", () => {
      pendingMadeRecord = false;
      closeExitDialog();
    });

    exitOverlay?.querySelector("[data-make-exit-not-made]")?.addEventListener("click", () => {
      pendingMadeRecord = false;
      closeExitDialog();
      navigateAway(exitTargetUrl);
    });

    exitOverlay?.querySelector("[data-make-exit-made]")?.addEventListener("click", () => {
      pendingMadeRecord = true;
      syncExitReviewLede();
      if (exitRatingForm instanceof HTMLFormElement) {
        updateStars(exitRatingForm);
      }
      showExitStep("review");
      if (exitDialog instanceof HTMLElement) {
        exitDialog.focus();
      }
      const firstStar = exitRatingForm?.querySelector("input[type='radio']");
      if (firstStar instanceof HTMLInputElement) {
        firstStar.focus();
      }
    });

    exitOverlay?.querySelector("[data-make-exit-skip-review]")?.addEventListener("click", () => {
      void skipReviewAndLeave();
    });

    exitBackBtn?.addEventListener("click", () => {
      pendingMadeRecord = false;
      showExitStep("made");
      if (exitDialog instanceof HTMLElement) {
        exitDialog.focus();
      }
    });

    bindStarRatingForm(exitRatingForm, {
      onSaved: (payload, response) => {
        if (response.ok && payload.ok) {
          queueToastForNextPage(payload.message, "success");
          void (async () => {
            try {
              await finalizeMadeAndLeave();
            } catch {
              showToast("Could not save that you made this recipe. Try again.", "error");
            }
          })();
          return;
        }
        showToast(payload.message, "error");
      },
    });

    document.addEventListener(
      "click",
      (event) => {
        if (exitDialogOpen) {
          return;
        }
        const link = event.target instanceof Element ? event.target.closest("a") : null;
        if (!(link instanceof HTMLAnchorElement) || root.contains(link)) {
          return;
        }
        if (link.target === "_blank" || link.hasAttribute("download")) {
          return;
        }
        const href = link.getAttribute("href");
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) {
          return;
        }
        let url;
        try {
          url = new URL(link.href, window.location.origin);
        } catch {
          return;
        }
        if (url.origin !== window.location.origin || isMakePath(url.pathname)) {
          return;
        }
        event.preventDefault();
        beginExit(`${url.pathname}${url.search}${url.hash}`);
      },
      true,
    );

    window.addEventListener("popstate", (event) => {
      if (event.state?.makeExitTrap === 1) {
        history.pushState(
          { makeExitTrap: 1, makePanel: panelKeys[activeIndex] },
          "",
          window.location.href,
        );
        beginExit(detailUrl);
        return;
      }
      const panel = event.state?.makePanel;
      if (panel && panelKeys.includes(panel)) {
        const nextIndex = panelKeys.indexOf(panel);
        if (nextIndex !== activeIndex) {
          savePanelScroll(activeIndex);
          activeIndex = nextIndex;
          updateUi({ restoreScroll: true });
          if (activeIndex === 0) {
            syncExitTrap();
          }
        }
      }
    });

    mobileQuery.addEventListener("change", syncSwipeHint);
    window.addEventListener("resize", () => {
      afterLayout(() => syncZoneHeight());
    });

    if (!zone || !track) {
      return;
    }

    let startX = 0;
    let startY = 0;
    let pointerX = 0;
    let axis = null;
    let dragging = false;
    let swipeTouchActive = false;

    const setDragOffset = (dx) => {
      const width = zone.getBoundingClientRect().width;
      const base = -activeIndex * width;
      track.style.transform = `translate3d(${base + dx}px, 0, 0)`;
    };

    const snapFromDrag = (dx) => {
      track.style.transform = "";
      track.classList.remove("is-dragging");
      const threshold = Math.min(80, zone.getBoundingClientRect().width * 0.18);
      if (dx <= -threshold && activeIndex < panels.length - 1) {
        setPanel(activeIndex + 1);
        return;
      }
      if (dx >= threshold && activeIndex > 0) {
        setPanel(activeIndex - 1);
        return;
      }
      updateUi({ duringDrag: false });
    };

    const swipeTouchAllowed = (event) => {
      if (!mobileQuery.matches || exitDialogOpen || event.touches.length !== 1) {
        return false;
      }
      const target = event.target;
      if (
        target instanceof Element &&
        exitOverlay instanceof HTMLElement &&
        !exitOverlay.hidden &&
        target.closest("[data-make-exit-overlay]")
      ) {
        return false;
      }
      return true;
    };

    const onTouchStart = (event) => {
      swipeTouchActive = false;
      if (!swipeTouchAllowed(event)) {
        return;
      }
      swipeTouchActive = true;
      startX = event.touches[0].clientX;
      startY = event.touches[0].clientY;
      pointerX = startX;
      axis = null;
      dragging = false;
    };

    const onTouchMove = (event) => {
      if (!swipeTouchActive || !mobileQuery.matches || event.touches.length !== 1) {
        return;
      }
      const x = event.touches[0].clientX;
      const y = event.touches[0].clientY;
      const dx = x - startX;
      const dy = y - startY;

      if (axis === null) {
        if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
          return;
        }
        axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      }
      if (axis === "y") {
        return;
      }

      event.preventDefault();
      if (!dragging) {
        dragging = true;
        track.classList.add("is-dragging");
        zone.classList.add("is-dragging");
        syncZoneHeight({ duringDrag: true });
      }
      let offset = dx;
      if (
        (activeIndex === 0 && offset > 0) ||
        (activeIndex === panels.length - 1 && offset < 0)
      ) {
        offset *= 0.35;
      }
      setDragOffset(offset);
      pointerX = x;
    };

    const onTouchEnd = () => {
      if (!swipeTouchActive) {
        return;
      }
      swipeTouchActive = false;
      if (!mobileQuery.matches || axis !== "x" || !dragging) {
        axis = null;
        dragging = false;
        return;
      }
      snapFromDrag(pointerX - startX);
      axis = null;
      dragging = false;
    };

    document.addEventListener("touchstart", onTouchStart, { passive: true });
    document.addEventListener("touchmove", onTouchMove, { passive: false });
    document.addEventListener("touchend", onTouchEnd);
    document.addEventListener("touchcancel", onTouchEnd);
  })();

  document.querySelectorAll(".recipe-quick-tag").forEach((details) => {
    if (!(details instanceof HTMLDetailsElement)) {
      return;
    }
    const panel = details.querySelector(".recipe-quick-tag-panel");
    const anchor = details.querySelector(".recipe-quick-tag-summary");
    let onDocClick = null;
    let onReposition = null;

    const clearQuickTagPanelPosition = () => {
      if (!(panel instanceof HTMLElement)) {
        return;
      }
      panel.classList.remove("is-positioned");
      panel.style.position = "";
      panel.style.left = "";
      panel.style.top = "";
      panel.style.width = "";
      panel.style.maxWidth = "";
      panel.style.right = "";
    };

    const positionQuickTagPanel = () => {
      if (!(panel instanceof HTMLElement) || !(anchor instanceof HTMLElement) || !details.open) {
        return;
      }
      const margin = 8;
      const gap = 4;
      const minWidth = 272;
      const maxWidth = Math.min(minWidth, window.innerWidth - margin * 2);
      const width = maxWidth;
      const anchorRect = anchor.getBoundingClientRect();
      let left = anchorRect.right - width;
      if (left < margin) {
        left = margin;
      }
      if (left + width > window.innerWidth - margin) {
        left = window.innerWidth - margin - width;
      }
      panel.classList.add("is-positioned");
      panel.style.position = "fixed";
      panel.style.left = `${Math.round(left)}px`;
      panel.style.top = `${Math.round(anchorRect.bottom + gap)}px`;
      panel.style.width = `${Math.round(width)}px`;
      panel.style.maxWidth = `${Math.round(maxWidth)}px`;
      panel.style.right = "auto";
    };

    const stopRepositionListeners = () => {
      if (onReposition) {
        window.removeEventListener("resize", onReposition);
        window.removeEventListener("scroll", onReposition, true);
        onReposition = null;
      }
    };

    details.addEventListener("toggle", () => {
      if (onDocClick) {
        document.removeEventListener("click", onDocClick);
        onDocClick = null;
      }
      stopRepositionListeners();
      if (!details.open) {
        clearQuickTagPanelPosition();
        return;
      }
      positionQuickTagPanel();
      onReposition = () => {
        positionQuickTagPanel();
      };
      window.addEventListener("resize", onReposition);
      window.addEventListener("scroll", onReposition, true);
      window.requestAnimationFrame(positionQuickTagPanel);
      onDocClick = (event) => {
        if (!(event.target instanceof Node) || !details.contains(event.target)) {
          const suggestPanel = document.getElementById("recipe-tag-suggest-panel");
          if (suggestPanel instanceof HTMLElement && suggestPanel.contains(event.target)) {
            return;
          }
          details.open = false;
        }
      };
      window.setTimeout(() => {
        document.addEventListener("click", onDocClick);
      }, 0);
    });
  });

  document.querySelectorAll("form[data-unsaved-warning]").forEach((form) => {
    let isSubmitting = false;
    let isDiscarding = false;

    const serializeForm = () => {
      const entries = [];
      const formData = new FormData(form);

      formData.forEach((value, key) => {
        if (value instanceof File) {
          if (!value.name && value.size === 0) {
            return;
          }
          entries.push([key, `file:${value.name}:${value.size}:${value.lastModified}`]);
          return;
        }
        entries.push([key, value]);
      });

      return JSON.stringify(entries);
    };

    const initialState = serializeForm();
    const hasUnsavedChanges = () => serializeForm() !== initialState;

    form.addEventListener("submit", () => {
      isSubmitting = true;
    });

    document.querySelectorAll("[data-discard-changes]").forEach((link) => {
      link.addEventListener("click", () => {
        isDiscarding = true;
      });
    });

    window.addEventListener("beforeunload", (event) => {
      if (isSubmitting || isDiscarding || !hasUnsavedChanges()) {
        return;
      }

      event.preventDefault();
      event.returnValue = "";
    });
  });

  const commentsList = document.querySelector("[data-comments-list]");
  const commentSortLinks = document.querySelectorAll("[data-comment-sort]");
  const sortComments = (order) => {
    if (!commentsList) {
      return;
    }

    const comments = Array.from(commentsList.querySelectorAll("[data-comment-created-at]"));
    comments.sort((first, second) => {
      const firstDate = Date.parse(first.dataset.commentCreatedAt || "");
      const secondDate = Date.parse(second.dataset.commentCreatedAt || "");
      const firstId = Number.parseInt(first.dataset.commentId || "0", 10);
      const secondId = Number.parseInt(second.dataset.commentId || "0", 10);
      const dateComparison = firstDate - secondDate;
      const idComparison = firstId - secondId;

      if (order === "newest") {
        return dateComparison === 0 ? -idComparison : -dateComparison;
      }
      return dateComparison === 0 ? idComparison : dateComparison;
    });
    comments.forEach((comment) => commentsList.append(comment));
  };

  commentSortLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const order = link.dataset.commentSort || "oldest";
      sortComments(order);
      commentSortLinks.forEach((sortLink) => {
        sortLink.classList.toggle("is-active", sortLink === link);
      });

      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set("comments", order);
      nextUrl.hash = "discussion";
      window.history.replaceState({}, "", nextUrl);
    });
  });

  const readAutogrowMinHeight = (textarea) => {
    const style = window.getComputedStyle(textarea);
    const lineHeight = Number.parseFloat(style.lineHeight) || 20;
    const padding =
      Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom);
    const border =
      Number.parseFloat(style.borderTopWidth) + Number.parseFloat(style.borderBottomWidth);
    const rows = Number.parseInt(textarea.getAttribute("rows") || "2", 10);
    return Math.ceil(rows * lineHeight + padding + border);
  };

  const resizeAutogrowTextarea = (textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }
    textarea.style.height = "auto";
    const minHeight = Number.parseInt(textarea.dataset.autogrowMinHeight || "", 10);
    const nextHeight = Math.max(
      textarea.scrollHeight,
      Number.isFinite(minHeight) ? minHeight : 0,
    );
    textarea.style.height = `${nextHeight}px`;
  };

  const initAutogrowTextareas = (root = document) => {
    root.querySelectorAll("textarea[data-autogrow]").forEach((textarea) => {
      if (!(textarea instanceof HTMLTextAreaElement)) {
        return;
      }
      if (!textarea.dataset.autogrowMinHeight) {
        textarea.dataset.autogrowMinHeight = String(readAutogrowMinHeight(textarea));
      }
      if (textarea.dataset.autogrowBound !== "true") {
        textarea.dataset.autogrowBound = "true";
        textarea.addEventListener("input", () => resizeAutogrowTextarea(textarea));
      }
      resizeAutogrowTextarea(textarea);
    });
  };

  initAutogrowTextareas();

  const updatePhotoPreview = (fileInput) => {
    if (!(fileInput instanceof HTMLInputElement)) {
      return;
    }
    const row = fileInput.closest("[data-photo-row]");
    if (!row) {
      return;
    }
    const image = row.querySelector("[data-photo-preview-image]");
    const placeholder = row.querySelector("[data-photo-placeholder]");
    const file = fileInput.files?.[0];
    if (!(image instanceof HTMLImageElement) || !file) {
      return;
    }
    if (image.dataset.objectUrl) {
      URL.revokeObjectURL(image.dataset.objectUrl);
    }
    const objectUrl = URL.createObjectURL(file);
    image.dataset.objectUrl = objectUrl;
    image.src = objectUrl;
    image.hidden = false;
    placeholder?.setAttribute("hidden", "");
  };

  const initPhotoEditors = (root = document) => {
    root.querySelectorAll("[data-photo-row] .photo-editor-file").forEach((input) => {
      if (!(input instanceof HTMLInputElement)) {
        return;
      }
      if (input.dataset.photoEditorBound !== "true") {
        input.dataset.photoEditorBound = "true";
        input.addEventListener("change", () => updatePhotoPreview(input));
      }
    });
  };

  initPhotoEditors();

  document.querySelectorAll("[data-formset]").forEach((formset) => {
    const rows = formset.querySelector("[data-formset-rows]");
    const template = formset.querySelector("[data-formset-template]");
    const totalInput = formset.querySelector("input[name$='-TOTAL_FORMS']");
    const addButton = formset.querySelector("[data-formset-add]");
    const removeLabel = formset.dataset.removeLabel || "Item";
    const isIngredientSortable = formset.dataset.sortable === "ingredients";
    const parentForm = formset.closest("form");
    let draggingRow = null;

    if (!rows || !template || !totalInput) {
      return;
    }

    const rowHasContent = (row) => {
      const idInput = row.querySelector("input[name$='-id']");
      if (idInput instanceof HTMLInputElement && idInput.value) {
        return true;
      }
      return Array.from(row.querySelectorAll("input, textarea, select")).some((field) => {
        if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
          return false;
        }
        if (field instanceof HTMLInputElement && ["hidden", "checkbox"].includes(field.type)) {
          return false;
        }
        if (field instanceof HTMLInputElement && field.type === "file") {
          return field.files && field.files.length > 0;
        }
        return field.value.trim() !== "";
      });
    };

    const syncOrder = () => {
      rows.querySelectorAll("[data-form-row]").forEach((row, index) => {
        const orderInput = row.querySelector("input[name$='-order']");
        if (!(orderInput instanceof HTMLInputElement)) {
          return;
        }
        if (!row.classList.contains("is-removed") && rowHasContent(row)) {
          orderInput.value = String(index + 1);
        } else if (!rowHasContent(row)) {
          orderInput.value = "";
        }
      });
    };

    const removalMessage = (row) => {
      if (removeLabel !== "Ingredient") {
        return `${removeLabel} removed.`;
      }
      const nameInput = row.querySelector("input[name$='-name']");
      const ingredientName =
        nameInput instanceof HTMLInputElement ? nameInput.value.trim() : "";
      return ingredientName ? `${ingredientName} removed.` : "Ingredient removed.";
    };

    const visibleRows = () =>
      Array.from(rows.querySelectorAll("[data-form-row]")).filter((row) => !row.classList.contains("is-removed"));

    const appendEmptyRow = () => {
      const index = Number.parseInt(totalInput.value, 10);
      const wrapper = document.createElement("div");
      wrapper.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index));
      const row = wrapper.firstElementChild;
      if (!row) {
        return null;
      }
      rows.append(row);
      totalInput.value = String(index + 1);
      syncOrder();
      initAutogrowTextareas(row);
      initPhotoEditors(row);
      return row;
    };

    const ensureTrailingEmptyRow = () => {
      const list = visibleRows();
      if (list.length === 0) {
        appendEmptyRow();
        return;
      }
      const last = list[list.length - 1];
      if (rowHasContent(last)) {
        appendEmptyRow();
      }
    };

    const maybeExpandLastRow = (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const row = target.closest("[data-form-row]");
      if (!row || row.classList.contains("is-removed")) {
        return;
      }
      const list = visibleRows();
      if (list.length === 0 || list[list.length - 1] !== row) {
        return;
      }
      if (!rowHasContent(row)) {
        return;
      }
      appendEmptyRow();
    };

    addButton?.addEventListener("click", () => {
      const row = appendEmptyRow();
      row?.querySelector("input, textarea, select")?.focus();
    });

    rows.addEventListener("input", maybeExpandLastRow);
    rows.addEventListener("change", maybeExpandLastRow);

    ensureTrailingEmptyRow();

    parentForm?.addEventListener("submit", syncOrder);

    formset.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.matches("[data-remove-row]")) {
        return;
      }
      const row = target.closest("[data-form-row]");
      if (!row) {
        return;
      }

      const deleteInput = row.querySelector("input[name$='-DELETE']");
      if (deleteInput instanceof HTMLInputElement) {
        if (deleteInput.type === "hidden") {
          deleteInput.value = "on";
        } else {
          deleteInput.checked = true;
        }
      }
      row.classList.add("is-removed");
      syncOrder();
      ensureTrailingEmptyRow();

      showToast(removalMessage(row), "success", {
        actionLabel: "Undo",
        duration: DEFAULT_TOAST_MS,
        onAction: () => {
          if (deleteInput instanceof HTMLInputElement) {
            if (deleteInput.type === "hidden") {
              deleteInput.value = "";
            } else {
              deleteInput.checked = false;
            }
          }
          row.classList.remove("is-removed");
          syncOrder();
          ensureTrailingEmptyRow();
        }
      });
    });

    if (isIngredientSortable) {
      let dragPointerId = null;
      let dragHandle = null;

      const finishIngredientDrag = () => {
        draggingRow?.classList.remove("dragging");
        draggingRow = null;
        dragPointerId = null;
        dragHandle = null;
        syncOrder();
      };

      const moveIngredientDrag = (event) => {
        if (!draggingRow) {
          return;
        }
        const hit =
          document.elementFromPoint(event.clientX, event.clientY) ??
          (event.target instanceof Element ? event.target : null);
        const overRow = hit instanceof Element ? hit.closest("[data-form-row]") : null;
        if (
          !overRow ||
          overRow === draggingRow ||
          overRow.classList.contains("is-removed") ||
          !rows.contains(overRow)
        ) {
          return;
        }
        const rect = overRow.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        rows.insertBefore(draggingRow, before ? overRow : overRow.nextSibling);
      };

      rows.addEventListener("pointerdown", (event) => {
        if (!(event.target instanceof HTMLElement)) {
          return;
        }
        const handle = event.target.closest("[data-drag-handle]");
        if (!handle) {
          return;
        }
        const row = handle.closest("[data-form-row]");
        if (!row || row.classList.contains("is-removed")) {
          return;
        }
        event.preventDefault();
        draggingRow = row;
        dragHandle = handle;
        dragPointerId = event.pointerId;
        row.classList.add("dragging");
        handle.setPointerCapture(event.pointerId);
      });

      rows.addEventListener("pointermove", (event) => {
        if (dragPointerId === null || event.pointerId !== dragPointerId) {
          return;
        }
        event.preventDefault();
        moveIngredientDrag(event);
      });

      rows.addEventListener("pointerup", (event) => {
        if (dragPointerId === null || event.pointerId !== dragPointerId) {
          return;
        }
        if (dragHandle instanceof HTMLElement) {
          try {
            dragHandle.releasePointerCapture(event.pointerId);
          } catch {
            /* pointer already released */
          }
        }
        finishIngredientDrag();
      });

      rows.addEventListener("pointercancel", (event) => {
        if (dragPointerId === null || event.pointerId !== dragPointerId) {
          return;
        }
        finishIngredientDrag();
      });
    }
  });

  const initRecipeCarousels = (root) => {
    root.querySelectorAll("[data-recipe-carousel]").forEach((carousel) => {
      const slides = Array.from(carousel.querySelectorAll("[data-carousel-slide]"));
      const prevButton = carousel.querySelector("[data-carousel-prev]");
      const nextButton = carousel.querySelector("[data-carousel-next]");
      let index = slides.findIndex((slide) => slide.classList.contains("is-active"));
      if (index < 0) {
        index = 0;
      }

      const show = (nextIndex) => {
        slides.forEach((slide, slideIndex) => {
          slide.classList.toggle("is-active", slideIndex === nextIndex);
        });
        index = nextIndex;
      };

      prevButton?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const nextIndex = (index - 1 + slides.length) % slides.length;
        show(nextIndex);
      });
      nextButton?.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const nextIndex = (index + 1) % slides.length;
        show(nextIndex);
      });
    });
  };

  const tagOverflowResizeObserver = Symbol("recipeTagOverflowResizeObserver");

  const initTagOverflow = (root = document) => {
    root.querySelectorAll("[data-tag-overflow]").forEach((overflowRoot) => {
      const main = overflowRoot.querySelector("[data-tag-overflow-main]");
      const row = overflowRoot.querySelector("[data-tag-overflow-row]");
      const details = overflowRoot.querySelector("[data-tag-overflow-details]");
      const panel = overflowRoot.querySelector("[data-tag-overflow-panel]");
      if (
        !(main instanceof HTMLElement) ||
        !(row instanceof HTMLElement) ||
        !(details instanceof HTMLDetailsElement) ||
        !(panel instanceof HTMLElement)
      ) {
        return;
      }

      const existing = overflowRoot[tagOverflowResizeObserver];
      if (existing instanceof ResizeObserver) {
        existing.disconnect();
      }

      const ensureOrder = () => {
        if (overflowRoot.dataset.overflowOrdered === "1") {
          return;
        }
        [...row.children, ...panel.children].forEach((child, index) => {
          child.dataset.overflowOrder = String(index);
        });
        overflowRoot.dataset.overflowOrdered = "1";
      };

      const collectOrdered = () =>
        [...row.children, ...panel.children].sort(
          (a, b) =>
            Number.parseInt(a.dataset.overflowOrder || "0", 10) -
            Number.parseInt(b.dataset.overflowOrder || "0", 10),
        );

      const consolidateToRow = () => {
        const ordered = collectOrdered();
        panel.replaceChildren();
        row.replaceChildren();
        ordered.forEach((el) => {
          row.append(el);
        });
      };

      const syncCountAndLabel = (count) => {
        const countEl = overflowRoot.querySelector("[data-tag-overflow-count]");
        if (countEl) {
          countEl.textContent = String(count);
        }
        const summary = details.querySelector("summary");
        if (summary instanceof HTMLElement) {
          if (count > 0) {
            summary.setAttribute("aria-label", `Show ${count} more tags`);
          } else {
            summary.removeAttribute("aria-label");
          }
        }
      };

      const layout = () => {
        ensureOrder();
        consolidateToRow();
        details.open = false;

        if (main.clientWidth < 40) {
          details.hidden = true;
          syncCountAndLabel(0);
          return;
        }

        if (row.scrollWidth <= row.clientWidth + 1) {
          details.hidden = true;
          syncCountAndLabel(0);
          return;
        }

        details.hidden = false;

        if (row.scrollWidth <= row.clientWidth + 1) {
          syncCountAndLabel(0);
          return;
        }

        let guard = 0;
        while (row.scrollWidth > row.clientWidth + 1 && row.children.length > 0 && guard < 240) {
          const widthBefore = row.scrollWidth;
          const last = row.lastElementChild;
          if (!last) {
            break;
          }
          panel.prepend(last);
          syncCountAndLabel(panel.childElementCount);
          if (row.scrollWidth >= widthBefore) {
            row.append(last);
            syncCountAndLabel(panel.childElementCount);
            break;
          }
          guard += 1;
        }

        if (panel.childElementCount === 0) {
          details.hidden = true;
          details.open = false;
          syncCountAndLabel(0);
        }
      };

      const ro = new ResizeObserver(() => {
        window.requestAnimationFrame(layout);
      });
      overflowRoot[tagOverflowResizeObserver] = ro;
      ro.observe(main);
      window.requestAnimationFrame(layout);

      if (
        overflowRoot.hasAttribute("data-tag-overflow-close-on-pick") &&
        overflowRoot.dataset.tagOverflowPickCloseBound !== "1"
      ) {
        overflowRoot.dataset.tagOverflowPickCloseBound = "1";
        panel.addEventListener("click", (event) => {
          if (!details.open) {
            return;
          }
          if (event.target.closest(".search-tag-chip")) {
            details.open = false;
          }
        });
      }
    });
  };

  initRecipeCarousels(document);
  initTagOverflow(document);

  const liveSearchForm = document.querySelector("[data-recipe-list-search]");
  const liveSearchDynamic = document.querySelector("[data-recipe-list-dynamic]");
  if (liveSearchForm instanceof HTMLFormElement && liveSearchDynamic) {
    const input = liveSearchForm.querySelector("input[name='q']");
    const sortInput = liveSearchForm.querySelector("[data-recipe-sort-input]");
    const sortCombobox = liveSearchForm.querySelector("[data-recipe-sort-combobox]");
    const sortTrigger = liveSearchForm.querySelector("[data-recipe-sort-trigger]");
    const sortListbox = liveSearchForm.querySelector("[data-recipe-sort-listbox]");
    const sortTriggerLabel = liveSearchForm.querySelector("[data-recipe-sort-trigger-label]");
    const sortDirInput = liveSearchForm.querySelector("[data-recipe-sort-dir]");
    const sortDirToggle = liveSearchForm.querySelector("[data-recipe-sort-dir-toggle]");
    const randomPick = document.querySelector("[data-recipe-random-pick]");
    const getTagHiddenRoot = () =>
      liveSearchForm.querySelector("[data-recipe-tag-hidden-inputs]");
    const getTagFiltersMount = () =>
      liveSearchForm.querySelector("[data-recipe-tag-filters-mount]");
    const getMadeByInput = () =>
      liveSearchForm.querySelector("[data-recipe-made-by-input]");
    const getMadeByCombobox = () =>
      liveSearchForm.querySelector("[data-recipe-made-by-combobox]");
    const getMadeByTrigger = () =>
      liveSearchForm.querySelector("[data-recipe-made-by-trigger]");
    const getMadeByListbox = () =>
      liveSearchForm.querySelector("[data-recipe-made-by-listbox]");
    const getMadeByTriggerLabel = () =>
      liveSearchForm.querySelector("[data-recipe-made-by-trigger-label]");
    const getListControlsMount = () =>
      liveSearchForm.querySelector("[data-recipe-list-controls-mount]");
    const sortTitle = "title";
    const defaultSortDir = {
      title: "asc",
      rating: "desc",
      cook_time: "asc",
      prep_time: "asc",
      ease: "asc",
      updated: "desc",
      made: "desc",
    };

    const sortLabels = {
      title: "Title",
      rating: "Rating",
      cook_time: "Cook time",
      prep_time: "Prep time",
      ease: "Ease",
      updated: "Updated",
      made: "Last made",
    };
    const validSortValues = new Set(Object.keys(defaultSortDir));

    const getSortValue = () => {
      if (!(sortInput instanceof HTMLInputElement)) {
        return sortTitle;
      }
      const value = sortInput.value.trim();
      return validSortValues.has(value) ? value : sortTitle;
    };

    const getDefaultDirForSort = (sortKey) => defaultSortDir[sortKey] || "asc";

    const getSortDirValue = () => {
      if (!(sortDirInput instanceof HTMLInputElement)) {
        return getDefaultDirForSort(getSortValue());
      }
      const raw = sortDirInput.value.trim().toLowerCase();
      if (raw === "asc" || raw === "desc") {
        return raw;
      }
      return getDefaultDirForSort(getSortValue());
    };

    const syncSortDirToggle = (dir) => {
      if (!(sortDirToggle instanceof HTMLButtonElement)) {
        return;
      }
      sortDirToggle.dataset.sortDir = dir;
      sortDirToggle.setAttribute(
        "aria-label",
        dir === "asc" ? "Ascending order. Click to reverse." : "Descending order. Click to reverse.",
      );
    };

    const easeTooltip = (liveSearchForm.getAttribute("data-recipe-ease-tooltip") || "").trim();

    const syncEaseHintCopy = () => {
      if (!easeTooltip) {
        return;
      }
      liveSearchForm.querySelectorAll("[data-recipe-ease-help-hint], [data-recipe-ease-summary]").forEach((el) => {
        if (el instanceof HTMLElement) {
          el.textContent = easeTooltip;
        }
      });
    };

    const closeEaseHelpHints = () => {
      if (!(sortListbox instanceof HTMLElement)) {
        return;
      }
      sortListbox.querySelectorAll("[data-recipe-ease-help-hint]").forEach((hint) => {
        if (hint instanceof HTMLElement) {
          hint.hidden = true;
        }
      });
      sortListbox.querySelectorAll("[data-recipe-ease-help]").forEach((btn) => {
        if (btn instanceof HTMLButtonElement) {
          btn.setAttribute("aria-expanded", "false");
        }
      });
    };

    const toggleEaseHelpHint = (btn) => {
      const option = btn.closest("[data-value]");
      const hint = option?.querySelector("[data-recipe-ease-help-hint]");
      if (!(hint instanceof HTMLElement)) {
        return;
      }
      const willOpen = hint.hidden;
      closeEaseHelpHints();
      if (willOpen) {
        hint.hidden = false;
        btn.setAttribute("aria-expanded", "true");
      }
    };

    const syncEaseTriggerTitle = () => {
      if (!(sortTrigger instanceof HTMLButtonElement)) {
        return;
      }
      sortTrigger.title = getSortValue() === "ease" && easeTooltip ? easeTooltip : "";
    };

    const syncEaseSummary = () => {
      const summary = liveSearchForm.querySelector("[data-recipe-ease-summary]");
      if (!(summary instanceof HTMLElement)) {
        return;
      }
      const show = getSortValue() === "ease" && easeTooltip;
      summary.hidden = !show;
      if (show && !summary.textContent.trim()) {
        summary.textContent = easeTooltip;
      }
    };

    const getSortOptions = () =>
      sortListbox ? [...sortListbox.querySelectorAll('[role="option"]')] : [];

    const syncTriggerLabel = () => {
      if (!(sortTriggerLabel instanceof HTMLElement)) {
        return;
      }
      const value = getSortValue();
      sortTriggerLabel.textContent = sortLabels[value] || sortLabels[sortTitle];
    };

    const syncListboxAriaSelected = () => {
      const value = getSortValue();
      getSortOptions().forEach((opt) => {
        const isSelected = opt.getAttribute("data-value") === value;
        opt.setAttribute("aria-selected", isSelected ? "true" : "false");
      });
    };

    const closeSortListbox = () => {
      if (!(sortListbox instanceof HTMLElement)) {
        return;
      }
      closeEaseHelpHints();
      sortListbox.hidden = true;
      sortCombobox?.classList.remove("is-open");
      sortTrigger?.setAttribute("aria-expanded", "false");
    };

    const getMadeByOptions = () => {
      const listbox = getMadeByListbox();
      return listbox ? [...listbox.querySelectorAll('[role="option"]')] : [];
    };

    const syncMadeByTriggerLabel = () => {
      const labelEl = getMadeByTriggerLabel();
      const madeByInput = getMadeByInput();
      if (!(labelEl instanceof HTMLElement) || !(madeByInput instanceof HTMLInputElement)) {
        return;
      }
      const value = madeByInput.value.trim();
      const selected = getMadeByOptions().find(
        (opt) => (opt.getAttribute("data-value") || "") === value,
      );
      labelEl.textContent = selected?.getAttribute("data-label") || "Anyone";
    };

    const syncMadeByListboxAriaSelected = () => {
      const madeByInput = getMadeByInput();
      const value = madeByInput instanceof HTMLInputElement ? madeByInput.value.trim() : "";
      getMadeByOptions().forEach((opt) => {
        const isSelected = (opt.getAttribute("data-value") || "") === value;
        opt.setAttribute("aria-selected", isSelected ? "true" : "false");
      });
    };

    const closeMadeByListbox = () => {
      const listbox = getMadeByListbox();
      const combobox = getMadeByCombobox();
      const trigger = getMadeByTrigger();
      if (!(listbox instanceof HTMLElement)) {
        return;
      }
      listbox.hidden = true;
      combobox?.classList.remove("is-open");
      trigger?.setAttribute("aria-expanded", "false");
    };

    const openMadeByListbox = () => {
      const listbox = getMadeByListbox();
      const combobox = getMadeByCombobox();
      const trigger = getMadeByTrigger();
      if (!(listbox instanceof HTMLElement)) {
        return;
      }
      closeSortListbox();
      listbox.hidden = false;
      combobox?.classList.add("is-open");
      trigger?.setAttribute("aria-expanded", "true");
    };

    const openSortListbox = () => {
      if (!(sortListbox instanceof HTMLElement)) {
        return;
      }
      closeMadeByListbox();
      sortListbox.hidden = false;
      sortCombobox?.classList.add("is-open");
      sortTrigger?.setAttribute("aria-expanded", "true");
    };

    if (input instanceof HTMLInputElement) {
      const listStateStorageKey = "recipe-list-state";

      const readListState = () => {
        try {
          const raw = sessionStorage.getItem(listStateStorageKey);
          if (!raw) {
            return null;
          }
          const data = JSON.parse(raw);
          if (!data || typeof data !== "object") {
            return null;
          }
          return data;
        } catch {
          return null;
        }
      };

      const writeListState = (state) => {
        try {
          sessionStorage.setItem(listStateStorageKey, JSON.stringify(state));
        } catch {
          /* storage full or disabled */
        }
      };

      const getMadeByValue = () => {
        const madeByInput = getMadeByInput();
        if (!(madeByInput instanceof HTMLInputElement)) {
          return "";
        }
        return madeByInput.value.trim();
      };

      const selectMadeByValue = (nextValue) => {
        const madeByInput = getMadeByInput();
        if (!(madeByInput instanceof HTMLInputElement)) {
          return;
        }
        const normalized = String(nextValue ?? "").trim();
        const allowed = new Set(
          getMadeByOptions().map((opt) => opt.getAttribute("data-value") || ""),
        );
        if (!allowed.has(normalized)) {
          return;
        }
        madeByInput.value = normalized;
        syncMadeByTriggerLabel();
        syncMadeByListboxAriaSelected();
        closeMadeByListbox();
        void runLiveSearch();
        getMadeByTrigger()?.focus();
      };

      const captureListStateFromForm = () => {
        const tags = [];
        getTagHiddenRoot()?.querySelectorAll("input[name='tag']").forEach((el) => {
          if (el instanceof HTMLInputElement && el.value) {
            tags.push(el.value);
          }
        });
        return {
          q: input.value.trim(),
          sort: getSortValue(),
          sort_dir: getSortDirValue(),
          tags,
          made_by: getMadeByValue(),
        };
      };

      const persistListState = () => {
        writeListState(captureListStateFromForm());
      };

      const listUrlFromState = (state) => {
        const params = new URLSearchParams();
        const q = (state.q || "").trim();
        if (q) {
          params.set("q", q);
        }
        const sort = validSortValues.has(state.sort) ? state.sort : sortTitle;
        const defaultDir = getDefaultDirForSort(sort);
        const dir =
          state.sort_dir === "asc" || state.sort_dir === "desc" ? state.sort_dir : defaultDir;
        const omitSortParams = sort === sortTitle && dir === getDefaultDirForSort(sortTitle);
        if (!omitSortParams) {
          params.set("sort", sort);
          if (dir !== defaultDir) {
            params.set("sort_dir", dir);
          }
        }
        const tags = Array.isArray(state.tags) ? state.tags : [];
        tags.forEach((slug) => {
          if (slug) {
            params.append("tag", slug);
          }
        });
        const madeBy = (state.made_by || "").trim();
        if (madeBy) {
          params.set("made_by", madeBy);
        }
        const search = params.toString();
        return `${window.location.pathname}${search ? `?${search}` : ""}`;
      };

      const restoreListStateIfNeeded = () => {
        const stored = readListState();
        if (!stored) {
          return false;
        }
        const current = new URL(window.location.href);
        const hasSort =
          current.searchParams.has("sort") || current.searchParams.has("sort_dir");
        const hasTags = current.searchParams.has("tag");
        const hasMadeBy = current.searchParams.has("made_by");
        const hasQ = current.searchParams.has("q");

        const urlSortRaw = current.searchParams.get("sort") || sortTitle;
        const urlSort = validSortValues.has(urlSortRaw) ? urlSortRaw : sortTitle;
        const urlDefaultDir = getDefaultDirForSort(urlSort);
        const urlDirRaw = current.searchParams.get("sort_dir");
        const urlSortDir =
          urlDirRaw === "asc" || urlDirRaw === "desc" ? urlDirRaw : urlDefaultDir;

        const storedSort = validSortValues.has(stored.sort) ? stored.sort : sortTitle;
        const storedDefaultDir = getDefaultDirForSort(storedSort);
        const storedSortDir =
          stored.sort_dir === "asc" || stored.sort_dir === "desc"
            ? stored.sort_dir
            : storedDefaultDir;

        const merged = {
          q: hasQ ? (current.searchParams.get("q") || "").trim() : (stored.q || "").trim(),
          sort: hasSort ? urlSort : storedSort,
          sort_dir: hasSort ? urlSortDir : storedSortDir,
          tags: hasTags
            ? current.searchParams.getAll("tag").filter(Boolean)
            : Array.isArray(stored.tags)
              ? stored.tags.filter(Boolean)
              : [],
          made_by: hasMadeBy
            ? (current.searchParams.get("made_by") || "").trim()
            : (stored.made_by || "").trim(),
        };

        const target = listUrlFromState(merged);
        const here = `${current.pathname}${current.search}`;
        if (target !== here) {
          window.location.replace(target);
          return true;
        }
        return false;
      };

      if (restoreListStateIfNeeded()) {
        return;
      }

      let debounceId = 0;
      let abortController = null;
      let requestSeq = 0;

      const syncRandomPickHref = () => {
        if (!(randomPick instanceof HTMLAnchorElement)) {
          return;
        }
        const base = randomPick.dataset.randomBase || "";
        if (!base) {
          return;
        }
        const q = input.value.trim();
        const params = new URLSearchParams();
        if (q) {
          params.set("q", q);
        }
        appendSelectedTagsToParams(params);
        appendMadeByToParams(params);
        randomPick.href = params.toString() ? `${base}?${params.toString()}` : base;
      };

      const appendSelectedTagsToParams = (params) => {
        getTagHiddenRoot()?.querySelectorAll("input[name='tag']").forEach((el) => {
          if (el instanceof HTMLInputElement && el.value) {
            params.append("tag", el.value);
          }
        });
      };

      const appendMadeByToParams = (params) => {
        const madeBy = getMadeByValue();
        if (madeBy) {
          params.set("made_by", madeBy);
        }
      };

      const appendSelectedTagsToUrl = (url) => {
        while (url.searchParams.has("tag")) {
          url.searchParams.delete("tag");
        }
        getTagHiddenRoot()?.querySelectorAll("input[name='tag']").forEach((el) => {
          if (el instanceof HTMLInputElement && el.value) {
            url.searchParams.append("tag", el.value);
          }
        });
      };

      const applySortToParams = (params) => {
        const sort = getSortValue();
        const dir = getSortDirValue();
        const defaultDir = getDefaultDirForSort(sort);
        const omitSortParams = sort === sortTitle && dir === getDefaultDirForSort(sortTitle);
        if (!omitSortParams) {
          params.set("sort", sort);
        }
        if (!omitSortParams && dir !== defaultDir) {
          params.set("sort_dir", dir);
        }
        appendSelectedTagsToParams(params);
        appendMadeByToParams(params);
      };

      const applySortToUrl = (url) => {
        const sort = getSortValue();
        const dir = getSortDirValue();
        const defaultDir = getDefaultDirForSort(sort);
        const omitSortParams = sort === sortTitle && dir === getDefaultDirForSort(sortTitle);
        if (omitSortParams) {
          url.searchParams.delete("sort");
          url.searchParams.delete("sort_dir");
        } else {
          url.searchParams.set("sort", sort);
          if (dir === defaultDir) {
            url.searchParams.delete("sort_dir");
          } else {
            url.searchParams.set("sort_dir", dir);
          }
        }
        appendSelectedTagsToUrl(url);
        const madeBy = getMadeByValue();
        if (madeBy) {
          url.searchParams.set("made_by", madeBy);
        } else {
          url.searchParams.delete("made_by");
        }
      };

      const syncListFiltersFromPartialHtml = (html) => {
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const extras = parsed.querySelector("[data-recipe-list-sync-extras]");
        if (!(extras instanceof HTMLElement)) {
          return;
        }
        const tagMount = getTagFiltersMount();
        const nextTags = extras.querySelector("[data-recipe-tag-filters]");
        if (tagMount instanceof HTMLElement && nextTags instanceof HTMLElement) {
          const curTags = tagMount.querySelector("[data-recipe-tag-filters]");
          if (curTags instanceof HTMLElement) {
            curTags.replaceWith(nextTags.cloneNode(true));
          } else {
            tagMount.append(nextTags.cloneNode(true));
          }
        }
        const listControlsMount = getListControlsMount();
        const nextMadeByGroup = extras.querySelector("[data-recipe-made-by-group]");
        if (listControlsMount instanceof HTMLElement) {
          const curMadeByGroup = listControlsMount.querySelector("[data-recipe-made-by-group]");
          if (nextMadeByGroup instanceof HTMLElement) {
            if (curMadeByGroup instanceof HTMLElement) {
              curMadeByGroup.replaceWith(nextMadeByGroup.cloneNode(true));
            } else {
              const sortGroup = listControlsMount.querySelector(".search-sort-group");
              if (sortGroup instanceof HTMLElement) {
                listControlsMount.insertBefore(nextMadeByGroup.cloneNode(true), sortGroup);
              } else {
                listControlsMount.append(nextMadeByGroup.cloneNode(true));
              }
            }
            syncMadeByTriggerLabel();
            syncMadeByListboxAriaSelected();
          } else if (curMadeByGroup instanceof HTMLElement) {
            curMadeByGroup.remove();
          }
        }
        initTagOverflow(liveSearchForm);
      };

      let appendAbortController = null;
      let appendRequestSeq = 0;
      let appendInFlight = false;
      let appendObserver = null;

      const teardownInfiniteScroll = () => {
        appendObserver?.disconnect();
        appendObserver = null;
      };

      const buildListFetchParams = () => {
        const params = new URLSearchParams();
        const q = input.value.trim();
        if (q) {
          params.set("q", q);
        }
        applySortToParams(params);
        return params;
      };

      const setLoadStatusVisible = (visible) => {
        const status = liveSearchDynamic.querySelector("[data-recipe-list-load-status]");
        if (!(status instanceof HTMLElement)) {
          return;
        }
        if (visible) {
          status.removeAttribute("hidden");
        } else {
          status.setAttribute("hidden", "");
        }
      };

      const mergeAppendFragment = (html) => {
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const chunk = parsed.querySelector("[data-recipe-list-append-chunk]");
        const grid = liveSearchDynamic.querySelector("[data-recipe-list-grid]");
        if (chunk instanceof HTMLElement && grid instanceof HTMLElement) {
          while (chunk.firstChild) {
            grid.append(chunk.firstChild);
          }
        }
        liveSearchDynamic
          .querySelectorAll("[data-recipe-list-sentinel], [data-recipe-list-load-status]")
          .forEach((el) => {
            el.remove();
          });
        const loadStatus = parsed.querySelector("[data-recipe-list-load-status]");
        const sentinel = parsed.querySelector("[data-recipe-list-sentinel]");
        if (loadStatus instanceof HTMLElement) {
          liveSearchDynamic.append(loadStatus);
        }
        if (sentinel instanceof HTMLElement) {
          liveSearchDynamic.append(sentinel);
        }
        initRecipeCarousels(liveSearchDynamic);
        initTagOverflow(liveSearchDynamic);
        return Boolean(sentinel);
      };

      const loadNextRecipePage = async () => {
        const sentinel = liveSearchDynamic.querySelector("[data-recipe-list-sentinel]");
        if (!(sentinel instanceof HTMLElement) || appendInFlight) {
          return;
        }
        const nextPage = sentinel.dataset.nextPage || "";
        if (!nextPage) {
          return;
        }
        appendInFlight = true;
        setLoadStatusVisible(true);
        appendAbortController?.abort();
        appendAbortController = new AbortController();
        const seq = ++appendRequestSeq;
        const params = buildListFetchParams();
        params.set("partial", "append");
        params.set("page", nextPage);
        try {
          const response = await fetch(`${window.location.pathname}?${params}`, {
            credentials: "same-origin",
            signal: appendAbortController.signal,
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const html = await response.text();
          if (seq !== appendRequestSeq) {
            return;
          }
          const hasMore = mergeAppendFragment(html);
          teardownInfiniteScroll();
          if (hasMore) {
            observeRecipeListSentinel();
          }
        } catch (error) {
          if (seq !== appendRequestSeq) {
            return;
          }
          if (error && typeof error === "object" && "name" in error && error.name === "AbortError") {
            return;
          }
          showToast("Could not load more recipes. Try again.", "error");
        } finally {
          if (seq === appendRequestSeq) {
            appendInFlight = false;
            setLoadStatusVisible(false);
          }
        }
      };

      const observeRecipeListSentinel = () => {
        teardownInfiniteScroll();
        const sentinel = liveSearchDynamic.querySelector("[data-recipe-list-sentinel]");
        if (!(sentinel instanceof HTMLElement)) {
          return;
        }
        appendObserver = new IntersectionObserver(
          (entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) {
              return;
            }
            void loadNextRecipePage();
          },
          { root: null, rootMargin: "240px 0px 0px", threshold: 0 },
        );
        appendObserver.observe(sentinel);
      };

      const runLiveSearch = async () => {
        const q = input.value.trim();
        const params = new URLSearchParams();
        if (q) {
          params.set("q", q);
        }
        applySortToParams(params);
        params.set("partial", "1");
        appendRequestSeq += 1;
        appendAbortController?.abort();
        teardownInfiniteScroll();
        abortController?.abort();
        abortController = new AbortController();
        const seq = ++requestSeq;
        liveSearchDynamic.setAttribute("aria-busy", "true");
        try {
          const response = await fetch(`${window.location.pathname}?${params}`, {
            credentials: "same-origin",
            signal: abortController.signal,
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const html = await response.text();
          if (seq !== requestSeq) {
            return;
          }
          liveSearchDynamic.innerHTML = html;
          syncListFiltersFromPartialHtml(html);
          initRecipeCarousels(liveSearchDynamic);
          initTagOverflow(liveSearchDynamic);
          observeRecipeListSentinel();

          const nextUrl = new URL(window.location.href);
          if (q) {
            nextUrl.searchParams.set("q", q);
          } else {
            nextUrl.searchParams.delete("q");
          }
          applySortToUrl(nextUrl);
          nextUrl.searchParams.delete("page");
          nextUrl.searchParams.delete("partial");
          const search = nextUrl.searchParams.toString();
          window.history.replaceState({}, "", `${nextUrl.pathname}${search ? `?${search}` : ""}`);
          persistListState();
          syncRandomPickHref();
        } catch (error) {
          if (seq !== requestSeq) {
            return;
          }
          if (error && typeof error === "object" && "name" in error && error.name === "AbortError") {
            return;
          }
          showToast("Search could not be updated. Try again.", "error");
        } finally {
          if (seq === requestSeq) {
            liveSearchDynamic.setAttribute("aria-busy", "false");
          }
        }
      };

      const selectSortValue = (nextValue) => {
        if (!(sortInput instanceof HTMLInputElement)) {
          return;
        }
        if (!validSortValues.has(nextValue)) {
          return;
        }
        sortInput.value = nextValue;
        syncTriggerLabel();
        syncListboxAriaSelected();
        closeSortListbox();
        const nextDefault = getDefaultDirForSort(nextValue);
        if (sortDirInput instanceof HTMLInputElement) {
          sortDirInput.value = nextDefault;
        }
        syncSortDirToggle(nextDefault);
        syncEaseTriggerTitle();
        syncEaseSummary();
        void runLiveSearch();
        sortTrigger?.focus();
      };

      syncEaseHintCopy();
      syncSortDirToggle(getSortDirValue());
      syncTriggerLabel();
      syncListboxAriaSelected();
      syncEaseTriggerTitle();
      syncEaseSummary();
      syncMadeByTriggerLabel();
      syncMadeByListboxAriaSelected();

      input.addEventListener("input", () => {
        syncRandomPickHref();
        window.clearTimeout(debounceId);
        debounceId = window.setTimeout(runLiveSearch, 280);
      });

      liveSearchForm.addEventListener("click", (event) => {
        const madeByTrigger =
          event.target instanceof Element ? event.target.closest("[data-recipe-made-by-trigger]") : null;
        if (madeByTrigger instanceof HTMLButtonElement) {
          event.stopPropagation();
          const madeByListbox = getMadeByListbox();
          if (madeByListbox instanceof HTMLElement) {
            if (madeByListbox.hidden) {
              openMadeByListbox();
            } else {
              closeMadeByListbox();
            }
          }
          return;
        }

        const madeByOption =
          event.target instanceof Element
            ? event.target.closest("[data-recipe-made-by-listbox] [data-value]")
            : null;
        if (madeByOption instanceof HTMLElement) {
          event.preventDefault();
          selectMadeByValue(madeByOption.getAttribute("data-value") || "");
          return;
        }

        const inFilters = event.target instanceof Element && event.target.closest("[data-recipe-tag-filters]");
        if (!inFilters) {
          return;
        }
        const btn = event.target.closest(".search-tag-chip");
        const tagHiddenRoot = getTagHiddenRoot();
        if (!(btn instanceof HTMLButtonElement) || !tagHiddenRoot) {
          return;
        }
        event.preventDefault();
        const slug = btn.dataset.tagSlug || "";
        if (!slug) {
          return;
        }
        const nextSelected = !btn.classList.contains("is-selected");
        btn.classList.toggle("is-selected", nextSelected);
        btn.setAttribute("aria-pressed", nextSelected ? "true" : "false");
        tagHiddenRoot.querySelectorAll("input[name='tag']").forEach((inp) => {
          if (inp instanceof HTMLInputElement && inp.value === slug) {
            inp.remove();
          }
        });
        if (nextSelected) {
          const hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = "tag";
          hidden.value = slug;
          tagHiddenRoot.append(hidden);
        }
        syncRandomPickHref();
        void runLiveSearch();
      });

      if (
        sortCombobox instanceof HTMLElement &&
        sortTrigger instanceof HTMLButtonElement &&
        sortListbox instanceof HTMLElement
      ) {
        sortTrigger.addEventListener("click", (event) => {
          event.stopPropagation();
          if (sortListbox.hidden) {
            openSortListbox();
          } else {
            closeSortListbox();
          }
        });

        sortTrigger.addEventListener("keydown", (event) => {
          if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            if (sortListbox.hidden) {
              openSortListbox();
            } else {
              closeSortListbox();
            }
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            const options = getSortOptions();
            if (sortListbox.hidden) {
              openSortListbox();
            }
            options[0]?.focus();
            return;
          }
          if (event.key === "Escape" && !sortListbox.hidden) {
            event.preventDefault();
            closeSortListbox();
            sortTrigger.focus();
          }
        });

        sortListbox.addEventListener("click", (event) => {
          const target = event.target;
          if (!(target instanceof Element)) {
            return;
          }
          const helpBtn = target.closest("[data-recipe-ease-help]");
          if (helpBtn instanceof HTMLButtonElement) {
            event.preventDefault();
            event.stopPropagation();
            toggleEaseHelpHint(helpBtn);
            return;
          }
          if (target.closest("[data-recipe-ease-help-hint]")) {
            event.preventDefault();
            event.stopPropagation();
            return;
          }
          const option = target.closest("[data-value]");
          if (!(option instanceof HTMLElement)) {
            return;
          }
          const nextValue = option.dataset.value || "";
          selectSortValue(nextValue);
        });

        sortListbox.addEventListener("keydown", (event) => {
          const options = getSortOptions();
          const active = document.activeElement;
          let index = options.indexOf(active);
          if (index < 0) {
            index = 0;
          }
          if (event.key === "Escape") {
            event.preventDefault();
            closeSortListbox();
            sortTrigger.focus();
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            const next = Math.min(index + 1, options.length - 1);
            options[next]?.focus();
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            if (index <= 0) {
              closeSortListbox();
              sortTrigger.focus();
              return;
            }
            options[index - 1]?.focus();
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            const current = options[index];
            const nextValue = current?.dataset.value || "";
            if (nextValue) {
              selectSortValue(nextValue);
            }
          }
        });

        const onDocumentPointerDown = (event) => {
          const target = event.target;
          if (!(target instanceof Node)) {
            return;
          }
          if (
            !sortListbox.hidden &&
            sortCombobox instanceof HTMLElement &&
            !sortCombobox.contains(target)
          ) {
            closeSortListbox();
          }
          const madeByCombobox = getMadeByCombobox();
          const madeByListbox = getMadeByListbox();
          if (
            madeByListbox instanceof HTMLElement &&
            !madeByListbox.hidden &&
            madeByCombobox instanceof HTMLElement &&
            !madeByCombobox.contains(target)
          ) {
            closeMadeByListbox();
          }
        };
        document.addEventListener("pointerdown", onDocumentPointerDown, true);
      }

      liveSearchForm.addEventListener("keydown", (event) => {
        const madeByTrigger = getMadeByTrigger();
        const madeByListbox = getMadeByListbox();
        if (!(madeByListbox instanceof HTMLElement)) {
          return;
        }
        if (event.target === madeByTrigger && madeByTrigger instanceof HTMLButtonElement) {
          if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            if (madeByListbox.hidden) {
              openMadeByListbox();
            } else {
              closeMadeByListbox();
            }
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (madeByListbox.hidden) {
              openMadeByListbox();
            }
            getMadeByOptions()[0]?.focus();
            return;
          }
          if (event.key === "Escape" && !madeByListbox.hidden) {
            event.preventDefault();
            closeMadeByListbox();
            madeByTrigger.focus();
          }
          return;
        }
        const active = document.activeElement;
        if (!(active instanceof HTMLElement) || !madeByListbox.contains(active)) {
          return;
        }
        const options = getMadeByOptions();
        let index = options.indexOf(active);
        if (index < 0) {
          index = 0;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          closeMadeByListbox();
          madeByTrigger?.focus();
          return;
        }
        if (event.key === "ArrowDown") {
          event.preventDefault();
          const next = Math.min(index + 1, options.length - 1);
          options[next]?.focus();
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          if (index <= 0) {
            closeMadeByListbox();
            madeByTrigger?.focus();
            return;
          }
          options[index - 1]?.focus();
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          const current = options[index];
          if (current instanceof HTMLElement) {
            selectMadeByValue(current.getAttribute("data-value") || "");
          }
        }
      });

      if (sortDirToggle instanceof HTMLButtonElement && sortDirInput instanceof HTMLInputElement) {
        sortDirToggle.addEventListener("click", () => {
          const next = getSortDirValue() === "asc" ? "desc" : "asc";
          sortDirInput.value = next;
          syncSortDirToggle(next);
          void runLiveSearch();
        });
      }

      persistListState();
      syncRandomPickHref();
      observeRecipeListSentinel();
    }
  }

  const updateReviewerName = (element, payload) => {
    const userName = payload.user_name || payload.reviewer_label;
    element.replaceChildren(document.createTextNode(userName));

    if (payload.reviewer_label === `${userName} (you)`) {
      element.append(" ");
      const currentUserMarker = document.createElement("span");
      currentUserMarker.className = "reviewer-you";
      currentUserMarker.textContent = "(you)";
      element.append(currentUserMarker);
    }
  };

  const updateRatingDisplay = (payload) => {
    if (!payload.ok) {
      return;
    }
    syncStarFormsFromPayload(payload);
    const summary = document.querySelector("[data-rating-summary]");
    const average = document.querySelector("[data-rating-average]");
    const averageMeter = document.querySelector("[data-average-star-meter]");
    const count = document.querySelector("[data-rating-count]");
    const breakdown = document.querySelector("[data-rating-breakdown]");
    let breakdownList = document.querySelector("[data-rating-breakdown-list]");
    const empty = document.querySelector("[data-rating-empty]");
    const averageText =
      payload.average === null ? "Not rated" : `${payload.average.toFixed(1)} out of 5`;

    if (summary) {
      summary.textContent =
        payload.average === null ? "Not rated" : `${payload.average.toFixed(1)} ★ (${payload.count})`;
    }
    if (average) {
      average.textContent = averageText;
    }
    if (averageMeter) {
      const ratingPercent = payload.average_percent || 0;
      averageMeter.style.setProperty("--rating-percent", `${ratingPercent}%`);
      averageMeter.setAttribute(
        "aria-label",
        payload.average === null ? "No ratings yet" : `${payload.average.toFixed(1)} out of 5 stars`,
      );
    }
    if (count) {
      count.textContent =
        payload.count === 0 ? "No reviews yet" : `${payload.count} review${payload.count === 1 ? "" : "s"}`;
    }
    if (breakdown && !breakdownList) {
      empty?.remove();
      breakdownList = document.createElement("ul");
      breakdownList.dataset.ratingBreakdownList = "";
      (empty?.parentElement || breakdown).append(breakdownList);
    }
    if (breakdownList) {
      const selector = `[data-rating-user-id="${payload.user_id}"]`;
      const item = breakdownList.querySelector(selector);
      if (payload.rating == null) {
        item?.remove();
        if (breakdownList.children.length === 0) {
          breakdownList.remove();
          if (breakdown && !empty) {
            const emptyMessage = document.createElement("p");
            emptyMessage.dataset.ratingEmpty = "";
            emptyMessage.textContent = "No one has rated this recipe yet.";
            breakdown.append(emptyMessage);
          } else if (empty instanceof HTMLElement) {
            empty.hidden = false;
          }
        }
        return;
      }
      let row = item;
      if (!row) {
        row = document.createElement("li");
        row.dataset.ratingUserId = String(payload.user_id);
        row.innerHTML = `
          <span data-rating-reviewer-name></span>
          <strong class="star-meter reviewer-stars">
            <span class="star-meter-empty" aria-hidden="true">★★★★★</span>
            <span class="star-meter-fill" aria-hidden="true">★★★★★</span>
          </strong>
        `;
      }
      breakdownList.prepend(row);
      let reviewerName = row.querySelector("[data-rating-reviewer-name]");
      if (!reviewerName) {
        reviewerName = row.querySelector("span") || document.createElement("span");
        reviewerName.dataset.ratingReviewerName = "";
        row.prepend(reviewerName);
      }
      updateReviewerName(reviewerName, payload);
      const stars = row.querySelector("strong");
      stars.style.setProperty("--rating-percent", `${payload.rating * 20}%`);
      stars.setAttribute("aria-label", `${payload.rating} out of 5 stars`);
    }
  };

  const createSimilarTagOverlayController = (overlay, panel, onEscape) => {
    const prevBodyOverflow = document.body.style.overflow;

    const listFocusables = () =>
      Array.from(
        panel.querySelectorAll(
          "button:not([disabled]), [href], input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled])",
        ),
      ).filter((el) => el instanceof HTMLElement);

    const onKeydown = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onEscape();
        return;
      }
      if (e.key !== "Tab") {
        return;
      }
      const list = listFocusables();
      if (list.length === 0) {
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    const deactivate = () => {
      overlay.setAttribute("hidden", "");
      document.body.style.overflow = prevBodyOverflow;
      overlay.removeEventListener("keydown", onKeydown);
    };

    const activate = () => {
      overlay.removeAttribute("hidden");
      document.body.style.overflow = "hidden";
      overlay.addEventListener("keydown", onKeydown);
      const list = listFocusables();
      (list[0] ?? panel).focus();
    };

    const armVisible = () => {
      if (overlay.hasAttribute("hidden")) {
        return;
      }
      document.body.style.overflow = "hidden";
      overlay.addEventListener("keydown", onKeydown);
      const list = listFocusables();
      window.setTimeout(() => {
        (list[0] ?? panel).focus();
      }, 0);
    };

    return { activate, deactivate, armVisible };
  };

  const initRecipeEditSimilarTagModal = () => {
    const overlay = document.getElementById("recipe-similar-tag-modal");
    const dataEl = document.getElementById("recipe-similar-tag-pairs-json");
    const form = document.querySelector("form[data-unsaved-warning]");
    const ackInput = form?.querySelector("input[name='similar_tags_ack']");
    const panel = overlay?.querySelector('[role="alertdialog"]');
    if (
      !(overlay instanceof HTMLElement) ||
      !(panel instanceof HTMLElement) ||
      !dataEl ||
      !(form instanceof HTMLFormElement) ||
      !(ackInput instanceof HTMLInputElement)
    ) {
      return;
    }
    let pairs;
    try {
      pairs = JSON.parse(dataEl.textContent);
    } catch {
      return;
    }
    if (!Array.isArray(pairs) || pairs.length === 0) {
      return;
    }
    const yes = panel.querySelector("[data-similar-tag-yes]");
    const no = panel.querySelector("[data-similar-tag-no]");
    const finish = () => {
      yes?.removeEventListener("click", onYes);
      no?.removeEventListener("click", onNo);
    };
    let overlayControl = { deactivate() {}, armVisible() {} };
    const onYes = () => {
      for (const row of pairs) {
        if (!row || typeof row !== "object" || !("typed" in row) || !("suggested" in row)) {
          continue;
        }
        const typed = String(row.typed);
        const suggested = String(row.suggested);
        form.querySelectorAll("input[name$='-tag_name']").forEach((inp) => {
          if (inp instanceof HTMLInputElement && inp.value.trim() === typed) {
            inp.value = suggested;
          }
        });
      }
      ackInput.value = "accepted";
      overlayControl.deactivate();
      finish();
      HTMLFormElement.prototype.submit.call(form);
    };
    const onNo = () => {
      ackInput.value = "skipped";
      overlayControl.deactivate();
      finish();
      HTMLFormElement.prototype.submit.call(form);
    };
    overlayControl = createSimilarTagOverlayController(overlay, panel, onNo);
    yes?.addEventListener("click", onYes);
    no?.addEventListener("click", onNo);
    overlayControl.armVisible();
  };

  const initRecipeQuickTagSimilarConfirm = () => {
    document.querySelectorAll("[data-recipe-quick-tag-form]").forEach((form) => {
      if (!(form instanceof HTMLFormElement)) {
        return;
      }
      form.addEventListener("submit", async (event) => {
        const tagInput = form.querySelector("input[name='tag']");
        if (!(tagInput instanceof HTMLInputElement) || !tagInput.value.trim()) {
          return;
        }
        event.preventDefault();
        const fd = new FormData(form);
        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: fd,
            credentials: "same-origin",
            headers: {
              "X-Recipe-Similar-Tag-Check": "1",
              "X-Requested-With": "XMLHttpRequest",
            },
          });
          const data = await response.json();
          if (!data || data.ok !== true) {
            HTMLFormElement.prototype.submit.call(form);
            return;
          }
          if (!data.need_confirm || !Array.isArray(data.pairs) || data.pairs.length === 0) {
            HTMLFormElement.prototype.submit.call(form);
            return;
          }
          const overlay = document.getElementById("recipe-quick-tag-similar-modal");
          const panel = overlay?.querySelector('[role="alertdialog"]');
          const body = panel?.querySelector("[data-quick-tag-similar-body]");
          const pair = data.pairs[0];
          if (!(overlay instanceof HTMLElement) || !(panel instanceof HTMLElement) || !body || !pair) {
            HTMLFormElement.prototype.submit.call(form);
            return;
          }
          const ackInput = form.querySelector("input[name='similar_tag_ack']");
          if (!(ackInput instanceof HTMLInputElement)) {
            HTMLFormElement.prototype.submit.call(form);
            return;
          }
          body.textContent = `You entered “${pair.typed}”, which is close to the existing tag “${pair.suggested}”. Use the suggested spelling instead?`;
          const yesBtn = panel.querySelector("[data-quick-tag-similar-yes]");
          const noBtn = panel.querySelector("[data-quick-tag-similar-no]");
          const cleanup = () => {
            yesBtn?.removeEventListener("click", onYes);
            noBtn?.removeEventListener("click", onNo);
          };
          let overlayControl = { deactivate() {} };
          const onYes = () => {
            tagInput.value = pair.suggested;
            ackInput.value = "accepted";
            overlayControl.deactivate();
            cleanup();
            HTMLFormElement.prototype.submit.call(form);
          };
          const onNo = () => {
            ackInput.value = "skipped";
            overlayControl.deactivate();
            cleanup();
            HTMLFormElement.prototype.submit.call(form);
          };
          overlayControl = createSimilarTagOverlayController(overlay, panel, onNo);
          yesBtn?.addEventListener("click", onYes);
          noBtn?.addEventListener("click", onNo);
          overlayControl.activate();
        } catch {
          HTMLFormElement.prototype.submit.call(form);
        }
      });
    });
  };

  initRecipeEditSimilarTagModal();
  initRecipeQuickTagSimilarConfirm();

  const promptReviewSection = document.querySelector("[data-prompt-review]");
  if (promptReviewSection instanceof HTMLElement) {
    promptReviewSection.scrollIntoView({ behavior: "smooth", block: "start" });
    const ratingCard = promptReviewSection.querySelector(".is-prompt-review-focus");
    const firstStar = promptReviewSection.querySelector(".star-rating-form input[type='radio']");
    if (firstStar instanceof HTMLInputElement) {
      window.setTimeout(() => firstStar.focus(), 400);
    } else if (ratingCard instanceof HTMLElement) {
      ratingCard.setAttribute("tabindex", "-1");
      window.setTimeout(() => ratingCard.focus(), 400);
    }
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete("review");
    const search = cleanUrl.searchParams.toString();
    window.history.replaceState(
      {},
      "",
      `${cleanUrl.pathname}${search ? `?${search}` : ""}${cleanUrl.hash}`,
    );
  }

  const RECENTLY_MADE_THANKS_MS = 1400;
  const RECENTLY_MADE_OVERLAY_FADE_MS = 280;

  const showRecentlyMadeThankYouAndDismiss = (overlay) => {
    const panel = overlay.querySelector("[data-recently-made-review-panel]");
    const thanks = overlay.querySelector("[data-recently-made-review-thanks]");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const thanksMs = reducedMotion ? 400 : RECENTLY_MADE_THANKS_MS;
    const fadeMs = reducedMotion ? 0 : RECENTLY_MADE_OVERLAY_FADE_MS;

    overlay.classList.add("is-thanks");
    if (panel instanceof HTMLElement) {
      panel.setAttribute("aria-hidden", "true");
    }
    if (thanks instanceof HTMLElement) {
      thanks.hidden = false;
      thanks.setAttribute("aria-hidden", "false");
      requestAnimationFrame(() => {
        overlay.classList.add("is-thanks-visible");
      });
    }

    window.setTimeout(() => {
      overlay.classList.add("is-dismissed");
      window.setTimeout(() => {
        overlay.remove();
      }, fadeMs);
    }, thanksMs);
  };

  document.querySelectorAll("[data-recently-made-rating]").forEach((form) => {
    bindStarRatingForm(form, {
      onSaved: (payload, response) => {
        const overlay = form.closest("[data-recently-made-review-overlay]");
        if (payload.ok && !payload.cleared && overlay instanceof HTMLElement) {
          showRecentlyMadeThankYouAndDismiss(overlay);
          return;
        }
        showToast(payload.message, response.ok && payload.ok ? "success" : "error");
      },
    });
  });

  document
    .querySelectorAll(".star-rating-form:not([data-make-exit-rating]):not([data-recently-made-rating])")
    .forEach((form) => {
      bindStarRatingForm(form, {
        onSaved: (payload, response) => {
          updateRatingDisplay(payload);
          showToast(payload.message, response.ok && payload.ok ? "success" : "error");
        },
      });
    });
});
