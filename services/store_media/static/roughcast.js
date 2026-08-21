(() => {
  "use strict";

  const endpoint = "/api/v1/display/roughcast-rentals";
  const retryButton = document.querySelector("#feedbackRefreshButton");
  const listingGrid = document.querySelector("#listingGrid");
  const feedback = document.querySelector("#feedback");
  const feedbackTitle = document.querySelector("#feedbackTitle");
  const feedbackDetail = document.querySelector("#feedbackDetail");
  const updatedAt = document.querySelector("#updatedAt");
  const pullRefresh = document.querySelector("#pullRefresh");
  const pullRefreshText = document.querySelector("#pullRefreshText");
  const loadMore = document.querySelector("#loadMore");
  const loadMoreText = document.querySelector("#loadMoreText");
  const loadMoreRetry = document.querySelector("#loadMoreRetry");
  const loadMoreSpinner = document.querySelector(".load-more-spinner");
  const loadMoreSentinel = document.querySelector("#loadMoreSentinel");
  const galleryModal = document.querySelector("#galleryModal");
  const galleryTitle = document.querySelector("#galleryTitle");
  const galleryClose = document.querySelector("#galleryClose");
  const galleryStatus = document.querySelector("#galleryStatus");
  const galleryViewport = document.querySelector("#galleryViewport");
  const galleryTrack = document.querySelector("#galleryTrack");
  const galleryFooter = document.querySelector("#galleryFooter");
  const galleryCounter = document.querySelector("#galleryCounter");
  const galleryPrevious = document.querySelector("#galleryPrevious");
  const galleryNext = document.querySelector("#galleryNext");

  let activeRequest = null;
  let galleryRequest = null;
  let renderedListings = [];
  let nextPage = 1;
  let hasMore = true;
  let loadingPage = false;
  let loadPaused = false;
  let activeGalleryCard = null;
  let galleryIndex = 0;
  let galleryPhotoCount = 0;
  let scrollFrame = null;
  let pullStartY = null;
  let pullDistance = 0;
  let refreshingViaPull = false;
  const pullThreshold = 64;

  function createElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function nonEmptyText(value, fallback) {
    if (typeof value !== "string") return fallback;
    const trimmed = value.trim();
    return trimmed || fallback;
  }

  function finiteNumber(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  }

  function safeImageUrl(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const parsed = new URL(value, window.location.origin);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch {
      return null;
    }
  }

  function normalizeListing(item) {
    const source = item && typeof item === "object" ? item : {};
    return {
      listingId: nonEmptyText(source.listing_id, ""),
      community: nonEmptyText(source.community, "小区信息待补充"),
      layout: nonEmptyText(source.layout, "户型待补充"),
      area: finiteNumber(source.area_sqm),
      monthlyRent: finiteNumber(source.monthly_rent_yuan),
      orientation: nonEmptyText(source.orientation, "朝向待补充"),
      floor: nonEmptyText(source.floor ?? source.floor_desc, "楼层待补充"),
      image: safeImageUrl(source.image ?? source.title_image_url),
    };
  }

  function formatNumber(value, fractionDigits) {
    return new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: fractionDigits,
      minimumFractionDigits: 0,
    }).format(value);
  }

  function formatArea(area) {
    if (area === null || area <= 0) return "面积待补充";
    return `${formatNumber(area, 1)} ㎡`;
  }

  function formatRent(rent) {
    if (rent === null || rent <= 0) return "租金待补充";
    return `${formatNumber(rent, 0)} 元/月`;
  }

  function createImagePlaceholder(label = "暂无封面") {
    const placeholder = createElement("div", "image-placeholder");
    placeholder.setAttribute("aria-label", label);
    const mark = createElement("span", "image-placeholder-mark", "房");
    mark.setAttribute("aria-hidden", "true");
    placeholder.append(mark, createElement("span", "", label));
    return placeholder;
  }

  function createMedia(listing) {
    const media = createElement("div", "listing-media");
    if (!listing.image) {
      media.append(createImagePlaceholder());
      return media;
    }

    const image = document.createElement("img");
    image.alt = `${listing.community} 房源封面`;
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("load", () => {
      if (image.naturalWidth > 0 && image.naturalHeight > 0) {
        const naturalRatio = image.naturalWidth / image.naturalHeight;
        const cardRatio = Math.max(1.15, Math.min(1.65, naturalRatio));
        media.style.aspectRatio = String(cardRatio);
      }
    }, { once: true });
    image.addEventListener("error", () => {
      if (image.parentElement === media) media.replaceChildren(createImagePlaceholder());
    }, { once: true });
    image.src = listing.image;
    media.append(image);
    return media;
  }

  function createFact(text) {
    return createElement("span", "listing-fact", text);
  }

  function createListingCard(listing) {
    const card = createElement("article", "listing-card");
    const content = createElement("div", "listing-content");
    const head = createElement("div", "listing-head");
    const type = createElement("p", "listing-type", "租赁 · 毛坯");
    const hasRent = listing.monthlyRent !== null && listing.monthlyRent > 0;
    const rentText = formatRent(listing.monthlyRent);
    const rentValue = createElement("strong", "rent-value", rentText);
    const title = createElement("h2", "listing-title", listing.community);
    const summary = createElement("p", "listing-summary", `${listing.layout} · ${formatArea(listing.area)}`);
    const facts = createElement("div", "listing-facts");

    rentValue.setAttribute("aria-label", hasRent ? `月租 ${rentText}` : rentText);
    facts.append(createFact(listing.orientation), createFact(listing.floor));
    head.append(type, rentValue);
    content.append(head, title, summary, facts);
    card.append(createMedia(listing), content);

    if (listing.listingId) {
      card.classList.add("is-actionable");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `查看 ${listing.community} 的实勘图片`);
      card.addEventListener("click", () => openGallery(listing, card));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openGallery(listing, card);
        }
      });
    }
    return card;
  }

  function createSkeletonCard() {
    const card = createElement("article", "skeleton-card");
    card.setAttribute("aria-hidden", "true");
    const media = createElement("div", "skeleton-media");
    const copy = createElement("div", "skeleton-copy");
    copy.append(
      createElement("span", "skeleton-line short"),
      createElement("span", "skeleton-line long"),
      createElement("span", "skeleton-line medium"),
      createElement("span", "skeleton-line long"),
    );
    card.append(media, copy);
    return card;
  }

  function createEmptyState(title, detail) {
    const state = createElement("div", "empty-state");
    const content = document.createElement("div");
    const mark = createElement("div", "empty-state-mark", "房");
    mark.setAttribute("aria-hidden", "true");
    content.append(mark, createElement("strong", "", title), createElement("p", "", detail));
    state.append(content);
    return state;
  }

  function showSkeletons() {
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < 4; index += 1) fragment.append(createSkeletonCard());
    listingGrid.replaceChildren(fragment);
  }

  function showFeedback(state, title, detail, showRetry) {
    feedback.hidden = false;
    feedback.dataset.state = state;
    feedback.setAttribute("role", state === "error" ? "alert" : "status");
    feedbackTitle.textContent = title;
    feedbackDetail.textContent = detail;
    retryButton.hidden = !showRetry;
  }

  function hideFeedback() {
    feedback.hidden = true;
    delete feedback.dataset.state;
  }

  function setBusy(isBusy) {
    listingGrid.setAttribute("aria-busy", String(isBusy));
  }

  function updatePullRefresh(distance) {
    pullDistance = Math.max(0, Math.min(96, distance));
    pullRefresh.style.setProperty("--pull-distance", `${pullDistance}px`);
    pullRefresh.classList.toggle("is-visible", pullDistance > 0);
    pullRefresh.setAttribute("aria-hidden", String(pullDistance <= 0));
    pullRefreshText.textContent = pullDistance >= pullThreshold ? "松开刷新" : "下拉刷新";
  }

  function finishPullRefresh() {
    refreshingViaPull = false;
    pullRefresh.classList.remove("is-refreshing");
    pullRefreshText.textContent = "刷新完成";
    window.setTimeout(() => updatePullRefresh(0), 260);
  }

  function setLoadMoreState(state, text) {
    loadMore.hidden = false;
    loadMore.dataset.state = state;
    loadMoreText.textContent = text;
    loadMoreSpinner.hidden = state !== "loading";
    loadMoreRetry.hidden = state !== "error";
  }

  function setUpdatedAt(value) {
    if (typeof value !== "string" || !value.trim()) {
      updatedAt.textContent = "已获取最新房源";
      return;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      updatedAt.textContent = "已获取最新房源";
      return;
    }
    const formatted = new Intl.DateTimeFormat("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
    updatedAt.textContent = `更新于 ${formatted}`;
  }

  function appendListings(listings, reset) {
    const fragment = document.createDocumentFragment();
    for (const listing of listings) fragment.append(createListingCard(listing));
    if (reset) listingGrid.replaceChildren(fragment);
    else listingGrid.append(fragment);
  }

  function uniqueNewListings(listings) {
    const seen = new Set(renderedListings.map((item) => item.listingId).filter(Boolean));
    return listings.filter((listing) => !listing.listingId || !seen.has(listing.listingId));
  }

  async function loadListings({ reset = false } = {}) {
    if (loadingPage && !reset) return;
    if (reset && activeRequest) activeRequest.abort();

    if (reset) {
      renderedListings = [];
      nextPage = 1;
      hasMore = true;
      loadPaused = false;
      showFeedback("loading", "正在获取房源", "仅展示租赁 · 毛坯房源。", false);
      showSkeletons();
      loadMore.hidden = true;
    } else if (!hasMore || loadPaused) {
      return;
    } else {
      setLoadMoreState("loading", "正在加载更多房源");
    }

    const requestedPage = nextPage;
    const controller = new AbortController();
    activeRequest = controller;
    loadingPage = true;
    setBusy(true);

    try {
      const response = await fetch(`${endpoint}?page=${requestedPage}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("roughcast listings request failed");

      const payload = await response.json();
      const sourceItems = Array.isArray(payload.items) ? payload.items : [];
      const listings = uniqueNewListings(sourceItems.map(normalizeListing));

      appendListings(listings, reset);
      renderedListings.push(...listings);
      nextPage = requestedPage + 1;
      hasMore = payload.has_more === true;
      setUpdatedAt(payload.updated_at);

      if (renderedListings.length) {
        hideFeedback();
        if (hasMore) loadMore.hidden = true;
        else setLoadMoreState("done", "已加载全部房源");
      } else {
        listingGrid.replaceChildren(createEmptyState("暂无毛坯租赁房源", "当前固定范围内没有可展示的房源，可稍后刷新查看。"));
        showFeedback("empty", "暂无可展示房源", "稍后刷新将重新获取 CRM 房源数据。", false);
        loadMore.hidden = true;
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (reset || !renderedListings.length) {
        showFeedback("error", "暂时无法获取房源", "请检查服务连接后重新刷新。", true);
        if (!renderedListings.length) {
          listingGrid.replaceChildren(createEmptyState("房源数据暂不可用", "服务恢复后，点击“重试”即可重新加载。"));
        }
      } else {
        loadPaused = true;
        setLoadMoreState("error", "更多房源加载失败");
      }
    } finally {
      if (activeRequest === controller) {
        activeRequest = null;
        loadingPage = false;
        setBusy(false);
        if (reset && refreshingViaPull) finishPullRefresh();
      }
    }
  }

  function normalizeGalleryPhoto(item) {
    const source = item && typeof item === "object" ? item : {};
    const url = safeImageUrl(source.url);
    if (!url) return null;
    return { url, label: nonEmptyText(source.label, "实勘图片") };
  }

  function renderGallery(photos) {
    const fragment = document.createDocumentFragment();
    photos.forEach((photo, index) => {
      const slide = createElement("figure", "gallery-slide");
      const image = document.createElement("img");
      image.alt = `${photo.label}，第 ${index + 1} 张实勘图片`;
      image.decoding = "async";
      image.loading = index === 0 ? "eager" : "lazy";
      image.addEventListener("error", () => {
        slide.classList.add("has-error");
        slide.replaceChildren(createImagePlaceholder("图片加载失败"));
      }, { once: true });
      image.src = photo.url;
      slide.append(image, createElement("figcaption", "", photo.label));
      fragment.append(slide);
    });
    galleryTrack.replaceChildren(fragment);
  }

  function updateGalleryControls(index) {
    galleryIndex = Math.max(0, Math.min(index, galleryPhotoCount - 1));
    galleryCounter.textContent = `${galleryIndex + 1} / ${galleryPhotoCount}`;
    galleryPrevious.disabled = galleryIndex <= 0;
    galleryNext.disabled = galleryIndex >= galleryPhotoCount - 1;
  }

  function goToGalleryPhoto(index, smooth = true) {
    if (!galleryPhotoCount) return;
    updateGalleryControls(index);
    galleryViewport.scrollTo({
      left: galleryViewport.clientWidth * galleryIndex,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  async function openGallery(listing, card) {
    if (!listing.listingId) return;
    if (galleryRequest) galleryRequest.abort();
    const controller = new AbortController();
    galleryRequest = controller;
    activeGalleryCard = card;
    galleryPhotoCount = 0;
    galleryIndex = 0;
    galleryTitle.textContent = `${listing.community} · 实勘图片`;
    galleryStatus.hidden = false;
    galleryStatus.textContent = "正在获取实勘图片";
    galleryViewport.hidden = true;
    galleryFooter.hidden = true;
    galleryTrack.replaceChildren();
    galleryModal.hidden = false;
    document.body.classList.add("gallery-open");
    galleryClose.focus();

    try {
      const response = await fetch(`${endpoint}/${encodeURIComponent(listing.listingId)}/prospect`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("prospect request failed");
      const payload = await response.json();
      const photos = (Array.isArray(payload.photos) ? payload.photos : [])
        .map(normalizeGalleryPhoto)
        .filter(Boolean);

      if (!photos.length) {
        galleryStatus.textContent = "该房源暂无实勘图片";
        return;
      }

      galleryPhotoCount = photos.length;
      renderGallery(photos);
      galleryStatus.hidden = true;
      galleryViewport.hidden = false;
      galleryFooter.hidden = false;
      updateGalleryControls(0);
      requestAnimationFrame(() => goToGalleryPhoto(0, false));
    } catch (error) {
      if (error && error.name === "AbortError") return;
      galleryStatus.hidden = false;
      galleryStatus.textContent = "实勘图片暂时无法加载，请稍后重试";
    } finally {
      if (galleryRequest === controller) galleryRequest = null;
    }
  }

  function closeGallery() {
    if (galleryRequest) galleryRequest.abort();
    galleryRequest = null;
    galleryModal.hidden = true;
    document.body.classList.remove("gallery-open");
    galleryTrack.replaceChildren();
    if (activeGalleryCard) activeGalleryCard.focus();
    activeGalleryCard = null;
  }

  galleryViewport.addEventListener("scroll", () => {
    if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null;
      if (!galleryViewport.clientWidth || !galleryPhotoCount) return;
      updateGalleryControls(Math.round(galleryViewport.scrollLeft / galleryViewport.clientWidth));
    });
  }, { passive: true });

  galleryPrevious.addEventListener("click", () => goToGalleryPhoto(galleryIndex - 1));
  galleryNext.addEventListener("click", () => goToGalleryPhoto(galleryIndex + 1));
  galleryClose.addEventListener("click", closeGallery);
  galleryModal.querySelector("[data-gallery-close]").addEventListener("click", closeGallery);
  galleryModal.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeGallery();
    if (event.key === "ArrowLeft") goToGalleryPhoto(galleryIndex - 1);
    if (event.key === "ArrowRight") goToGalleryPhoto(galleryIndex + 1);
  });

  const loadObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadListings();
  }, { rootMargin: "320px 0px" });
  loadObserver.observe(loadMoreSentinel);

  document.addEventListener("touchstart", (event) => {
    if (window.scrollY > 0 || !galleryModal.hidden || event.touches.length !== 1) return;
    pullStartY = event.touches[0].clientY;
  }, { passive: true });

  document.addEventListener("touchmove", (event) => {
    if (pullStartY === null || event.touches.length !== 1 || refreshingViaPull) return;
    const delta = event.touches[0].clientY - pullStartY;
    if (delta <= 0 || window.scrollY > 0) {
      updatePullRefresh(0);
      return;
    }
    event.preventDefault();
    updatePullRefresh(delta * .55);
  }, { passive: false });

  function releasePullRefresh() {
    if (pullStartY === null) return;
    pullStartY = null;
    if (pullDistance < pullThreshold || refreshingViaPull) {
      updatePullRefresh(0);
      return;
    }
    refreshingViaPull = true;
    pullRefresh.classList.add("is-refreshing", "is-visible");
    pullRefreshText.textContent = "正在刷新";
    pullRefresh.style.setProperty("--pull-distance", "52px");
    loadListings({ reset: true });
  }

  document.addEventListener("touchend", releasePullRefresh, { passive: true });
  document.addEventListener("touchcancel", releasePullRefresh, { passive: true });
  retryButton.addEventListener("click", () => loadListings({ reset: true }));
  loadMoreRetry.addEventListener("click", () => {
    loadPaused = false;
    loadListings();
  });
  loadListings({ reset: true });
})();
