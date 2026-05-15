document.addEventListener("DOMContentLoaded", () => {
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

  const dismissToast = (toast) => {
    const timeoutId = Number.parseInt(toast.dataset.timeoutId || "", 10);
    if (timeoutId) {
      window.clearTimeout(timeoutId);
    }
    toast.classList.add("is-hiding");
    window.setTimeout(() => toast.remove(), 260);
  };

  const scheduleToast = (toast, duration = 4200) => {
    const close = toast.querySelector(".toast-close");
    close?.addEventListener("click", () => dismissToast(toast));
    const timeoutId = window.setTimeout(() => dismissToast(toast), duration);
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
    scheduleToast(toast, options.duration || 4200);
  };

  document.querySelectorAll("[data-toast]").forEach(scheduleToast);

  document.querySelectorAll("[data-print-recipe]").forEach((button) => {
    button.addEventListener("click", () => window.print());
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
        duration: 7000,
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
      rows.addEventListener("dragstart", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const handle = target.closest("[data-drag-handle]");
        if (!handle) {
          event.preventDefault();
          return;
        }
        const row = handle.closest("[data-form-row]");
        if (!row || row.classList.contains("is-removed")) {
          event.preventDefault();
          return;
        }
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
        }
        draggingRow = row;
        row.classList.add("dragging");
      });

      rows.addEventListener("dragend", () => {
        draggingRow?.classList.remove("dragging");
        draggingRow = null;
        syncOrder();
      });

      rows.addEventListener("dragover", (event) => {
        if (!draggingRow) {
          return;
        }
        event.preventDefault();
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const overRow = target.closest("[data-form-row]");
        if (!overRow || overRow === draggingRow || overRow.classList.contains("is-removed")) {
          return;
        }
        const rect = overRow.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        rows.insertBefore(draggingRow, before ? overRow : overRow.nextSibling);
      });
    }
  });

  document.querySelectorAll("[data-recipe-carousel]").forEach((carousel) => {
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

  const updateStars = (form) => {
    const checked = form.querySelector("input[type='radio']:checked");
    const checkedValue = checked ? Number.parseInt(checked.value, 10) : 0;
    form.querySelectorAll(".star-choice").forEach((choice) => {
      const input = choice.querySelector("input[type='radio']");
      const value = input ? Number.parseInt(input.value, 10) : 0;
      choice.classList.toggle("is-filled", value <= checkedValue);
    });
  };

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
      let item = breakdownList.querySelector(selector);
      if (!item) {
        item = document.createElement("li");
        item.dataset.ratingUserId = String(payload.user_id);
        item.innerHTML = `
          <span data-rating-reviewer-name></span>
          <strong class="star-meter reviewer-stars">
            <span class="star-meter-empty" aria-hidden="true">★★★★★</span>
            <span class="star-meter-fill" aria-hidden="true">★★★★★</span>
          </strong>
        `;
      }
      breakdownList.prepend(item);
      let reviewerName = item.querySelector("[data-rating-reviewer-name]");
      if (!reviewerName) {
        reviewerName = item.querySelector("span") || document.createElement("span");
        reviewerName.dataset.ratingReviewerName = "";
        item.prepend(reviewerName);
      }
      updateReviewerName(reviewerName, payload);
      const stars = item.querySelector("strong");
      stars.style.setProperty("--rating-percent", `${payload.rating * 20}%`);
      stars.setAttribute("aria-label", `${payload.rating} out of 5 stars`);
    }
  };

  document.querySelectorAll(".star-rating-form").forEach((form) => {
    updateStars(form);

    form.querySelectorAll("input[type='radio']").forEach((input) => {
      input.addEventListener("change", async () => {
        updateStars(form);

        try {
          const response = await fetch(form.action.split("#")[0], {
            method: "POST",
            body: new FormData(form),
            headers: {
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
          });
          const payload = await response.json();
          updateRatingDisplay(payload);
          showToast(payload.message, response.ok && payload.ok ? "success" : "error");
        } catch {
          showToast("Rating could not be saved. Please try again.", "error");
        }
      });
    });
  });
});
