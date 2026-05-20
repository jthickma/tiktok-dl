// Modal lightbox + live status polling
(function () {
  // ---------- Lightbox ----------
  var modal = document.getElementById('media-modal');
  var container = document.getElementById('modal-video');
  var info = document.getElementById('modal-info');
  var closeBtn = document.getElementById('modal-close');
  var prevBtn = document.getElementById('modal-prev');
  var nextBtn = document.getElementById('modal-next');
  var currentThumb = null;

  function openModal(thumb) {
    if (!thumb || !thumb.dataset.media) return;
    currentThumb = thumb;
    var url = thumb.dataset.media;
    var poster = thumb.dataset.poster || '';
    var title = thumb.dataset.title || '';
    var creator = thumb.dataset.creator || '';
    var date = thumb.dataset.date || '';

    container.innerHTML = '';
    var video = document.createElement('video');
    video.controls = true;
    video.autoplay = true;
    video.preload = 'metadata';
    video.playsInline = true;
    if (poster) video.poster = poster;
    video.src = url;
    container.appendChild(video);

    if (info) {
      var metaParts = [];
      if (creator) metaParts.push('@' + creator);
      if (date) metaParts.push(date);
      info.innerHTML = '<h3></h3><div class="info-meta"></div>';
      info.querySelector('h3').textContent = title;
      info.querySelector('.info-meta').textContent = metaParts.join(' • ');
    }

    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    updateNavButtons();
  }

  function closeModal() {
    modal.classList.remove('open');
    container.innerHTML = '';
    document.body.style.overflow = '';
    currentThumb = null;
  }

  function siblings() {
    return Array.from(document.querySelectorAll('.thumb[data-media]'));
  }

  function updateNavButtons() {
    if (!currentThumb) return;
    var ts = siblings();
    var idx = ts.indexOf(currentThumb);
    prevBtn.style.display = idx > 0 ? 'grid' : 'none';
    nextBtn.style.display = idx >= 0 && idx < ts.length - 1 ? 'grid' : 'none';
  }

  function navigate(dir) {
    if (!currentThumb) return;
    var ts = siblings();
    var idx = ts.indexOf(currentThumb);
    var next = idx + dir;
    if (next >= 0 && next < ts.length) openModal(ts[next]);
  }

  document.addEventListener('click', function (ev) {
    var thumb = ev.target.closest('.thumb');
    if (thumb && thumb.dataset.media) {
      ev.preventDefault();
      openModal(thumb);
    }
  }, false);

  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (prevBtn) prevBtn.addEventListener('click', function () { navigate(-1); });
  if (nextBtn) nextBtn.addEventListener('click', function () { navigate(1); });
  if (modal) {
    modal.addEventListener('click', function (ev) {
      if (ev.target === modal || ev.target.classList.contains('modal-content') || ev.target.classList.contains('modal-video')) {
        closeModal();
      }
    });
  }
  document.addEventListener('keydown', function (ev) {
    if (!modal.classList.contains('open')) return;
    if (ev.key === 'Escape') closeModal();
    else if (ev.key === 'ArrowLeft') navigate(-1);
    else if (ev.key === 'ArrowRight') navigate(1);
    else if (ev.key === ' ') {
      ev.preventDefault();
      var v = container.querySelector('video');
      if (v) { v.paused ? v.play() : v.pause(); }
    }
  });

  // ---------- Auto-dismiss flash ----------
  var flash = document.querySelector('[data-flash]');
  if (flash) {
    setTimeout(function () {
      flash.style.transition = 'opacity 400ms ease';
      flash.style.opacity = '0';
      setTimeout(function () { flash.remove(); }, 450);
    }, 6000);
  }

  // ---------- Live status polling ----------
  var dot = document.querySelector('[data-status-dot]');
  var text = document.querySelector('[data-status-text]');
  var lastRun = document.querySelector('[data-last-run]');
  var logPane = document.querySelector('[data-log-pane]');
  if (!dot || !text) return;
  var lastState = '';
  function poll() {
    fetch('/api/status', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (s) {
      dot.classList.toggle('running', !!s.running);
      dot.classList.toggle('queued', !s.running && !!s.queued);
      text.textContent = s.state;
      if (lastRun) lastRun.textContent = s.last_run;
      if (logPane && s.logs) {
        var atBottom = (logPane.scrollTop + logPane.clientHeight) >= (logPane.scrollHeight - 8);
        logPane.textContent = s.logs;
        if (atBottom) logPane.scrollTop = logPane.scrollHeight;
      }
      if (lastState && lastState.indexOf('Syncing') === 0 && s.state === 'Idle') {
        window.location.reload();
      }
      lastState = s.state;
      var delay = s.running || s.queued ? 3000 : 12000;
      setTimeout(poll, delay);
    }).catch(function () { setTimeout(poll, 15000); });
  }
  setTimeout(poll, 2000);
})();
