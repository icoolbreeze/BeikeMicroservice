const params = new URLSearchParams(location.search);
const storeId = params.get('store_id');
const stage = document.querySelector('#stage');
const progress = document.querySelector('#image-progress');
const progressBar = progress.querySelector('div');
let items = [];
let index = 0;
let timer = null;
let signature = '';
let mediaNodes = new Map();

function hideProgress() {
  progress.classList.add('hidden');
  progressBar.style.transition = 'none';
  progressBar.style.transform = 'scaleX(0)';
}

function startProgress(durationSeconds) {
  progress.classList.remove('hidden');
  progressBar.style.transition = 'none';
  progressBar.style.transform = 'scaleX(0)';
  requestAnimationFrame(() => requestAnimationFrame(() => {
    progressBar.style.transition = `transform ${durationSeconds}s linear`;
    progressBar.style.transform = 'scaleX(1)';
  }));
}

function showEmpty() {
  clearTimeout(timer);
  hideProgress();
  stage.replaceChildren();
}

function buildMediaNodes() {
  const nextNodes = new Map();
  for (const item of items) {
    if (item.media_type === 'image') {
      const image = new Image();
      image.src = item.content_url;
      image.alt = '';
      image.decoding = 'async';
      nextNodes.set(item.id, image);
    } else {
      const video = document.createElement('video');
      video.src = item.content_url;
      video.preload = 'auto';
      video.muted = true;
      video.playsInline = true;
      nextNodes.set(item.id, video);
    }
  }
  mediaNodes = nextNodes;
}

function next() {
  index = (index + 1) % Math.max(items.length, 1);
  play();
}

function play() {
  clearTimeout(timer);
  hideProgress();
  if (!items.length) {
    showEmpty();
    return;
  }
  const item = items[index % items.length];
  const node = mediaNodes.get(item.id);
  stage.replaceChildren(node);

  if (item.media_type === 'image') {
    const duration = item.image_duration_seconds || 8;
    const schedule = () => {
      clearTimeout(timer);
      startProgress(duration);
      timer = setTimeout(next, duration * 1000);
    };
    if (node.complete && node.naturalWidth > 0) schedule();
    else {
      node.onload = schedule;
      node.onerror = () => { timer = setTimeout(next, 3000); };
    }
    return;
  }

  node.onended = next;
  node.onerror = () => { timer = setTimeout(next, 3000); };
  node.currentTime = 0;
  node.play().catch(() => { node.controls = true; });
}

async function refresh() {
  if (!storeId) {
    showEmpty();
    return;
  }
  try {
    const response = await fetch(`/api/v1/display/${encodeURIComponent(storeId)}/playlist`, {cache: 'no-store'});
    if (!response.ok) throw new Error();
    const data = await response.json();
    const nextSignature = JSON.stringify(data.items);
    if (nextSignature !== signature) {
      signature = nextSignature;
      items = data.items;
      index = 0;
      buildMediaNodes();
      play();
    }
  } catch {
    showEmpty();
  }
}

refresh();
setInterval(refresh, 30000);
