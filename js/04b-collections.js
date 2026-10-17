// #region Коллекции в профиле (docs/UI_REVIEW.md, раздел 4)
// Карточки коллекций на экране профиля и шторка коллекции с сеткой частей.
// Данные берутся из каталога и списка своих предметов гардероба (wd.catalog, wd.mine в js/13-wardrobe.js).

async function openCollection(code) {
  if (typeof window.__openCollection === 'function') {
    return window.__openCollection(code);
  }
}

(() => {
  const collectionsEls = {
    card: document.getElementById('collections-card'),
    allBtn: document.getElementById('collections-all-btn'),
    feedWrap: document.getElementById('collections-feed-wrap'),
    feed: document.getElementById('collections-feed'),
    sheet: document.getElementById('collection-sheet'),
    dim: document.getElementById('collection-dim'),
    panel: document.querySelector('#collection-sheet .collection-panel'),
    title: document.getElementById('collection-title'),
    close: document.getElementById('collection-close'),
    progress: document.getElementById('collection-progress'),
    grid: document.getElementById('collection-grid'),
    sourceBox: document.getElementById('collection-source-box'),
    sourceBtn: document.getElementById('collection-source-btn')
  };

  let lastActiveBeforeSheet = null;

  // Определение слота предмета по его коду (SKIN_CODES из js/13a-wardrobe-catalog.js)
  function skinSlotOf(code) {
    if (typeof SKIN_CODES === 'object' && SKIN_CODES) {
      for (const [slot, codes] of Object.entries(SKIN_CODES)) {
        if (Array.isArray(codes) && codes.includes(code)) return slot;
      }
    }
    return null;
  }

  // Название предмета из каталога
  function skinNameOf(code) {
    if (typeof wd === 'object' && wd && Array.isArray(wd.catalog)) {
      const it = wd.catalog.find((i) => i && i.code === code);
      if (it && it.name) return it.name;
    }
    return code;
  }

  // Склонение дней для активной сезонной коллекции
  function daysLeftText(n) {
    if (typeof wdDaysLeftText === 'function') return wdDaysLeftText(n);
    const mod10 = n % 10;
    const mod100 = n % 100;
    let word = 'дней';
    if (mod100 < 11 || mod100 > 14) {
      if (mod10 === 1) word = 'день';
      else if (mod10 >= 2 && mod10 <= 4) word = 'дня';
    }
    return n + ' ' + word;
  }

  // Текст одной строки статуса карточки коллекции
  function collectionStatusText(c) {
    if (c.status === 'ended') return 'сезон завершён';
    if (c.complete) return 'собрана';
    if (c.status === 'active' && Number.isInteger(c.days_left)) return daysLeftText(c.days_left);
    if (c.source === 'farm') return 'за ферму';
    if (c.source === 'referral') return 'за приглашения';
    if (c.source === 'streak') return 'за серию входов';
    return c.owned + ' из ' + c.total;
  }

  // Подсказка прокрутки ленты карточек у правого края (градиентная маска)
  function updateFeedFade() {
    const f = collectionsEls.feed;
    const wrap = collectionsEls.feedWrap;
    if (!f || !wrap) return;
    const isGrid = collectionsEls.card.classList.contains('grid-view');
    const hasMore = f.scrollWidth > f.clientWidth + 2 && f.scrollLeft + f.clientWidth < f.scrollWidth - 2;
    wrap.classList.toggle('more', hasMore && !isGrid);
  }

  // Отрисовка блока коллекций в профиле
  function renderCollections() {
    const card = collectionsEls.card;
    const feed = collectionsEls.feed;
    if (!card || !feed) return;

    const list = typeof wd === 'object' && wd && wd.mine && Array.isArray(wd.mine.collections) ? wd.mine.collections : null;
    if (!list || !list.length) {
      card.hidden = true;
      return;
    }

    feed.textContent = '';
    list.forEach((col) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'collection-card' + (col.complete ? ' col-complete' : '') + (col.status === 'ended' ? ' col-ended' : '');
      btn.dataset.code = col.code;
      btn.setAttribute('aria-label', col.name + ': собрано ' + col.owned + ' из ' + col.total);

      // а) Обложка: статичный коллаж из первых 3 миниатюр на фоне градиента
      const cover = document.createElement('div');
      cover.className = 'col-card-cover';
      cover.dataset.col = col.code;
      const collage = document.createElement('div');
      collage.className = 'col-card-collage';
      const parts = Array.isArray(col.parts) ? col.parts.slice(0, 3) : [];
      parts.forEach((code) => {
        const slot = skinSlotOf(code);
        if (slot && typeof wdScene === 'function') {
          collage.appendChild(wdScene(slot, code, true));
        }
      });
      cover.appendChild(collage);

      // б) Название
      const name = document.createElement('span');
      name.className = 'col-card-name';
      name.textContent = col.name;

      // в) Прогресс сегментами по числу частей
      const segs = document.createElement('div');
      segs.className = 'col-card-segments';
      segs.setAttribute('aria-hidden', 'true');
      const total = Number.isInteger(col.total) && col.total > 0 ? col.total : 1;
      const owned = Number.isInteger(col.owned) ? col.owned : 0;
      for (let i = 0; i < total; i += 1) {
        const seg = document.createElement('div');
        seg.className = 'col-segment' + (i < owned ? ' done' : '');
        segs.appendChild(seg);
      }

      // г) Строка статуса
      const status = document.createElement('span');
      status.className = 'col-card-status';
      status.textContent = collectionStatusText(col);

      btn.append(cover, name, segs, status);
      btn.addEventListener('click', () => openCollectionSheet(col));
      feed.appendChild(btn);
    });

    card.hidden = false;
    updateFeedFade();
  }

  // Вспышка по рамке при первом открытии собранной коллекции на этом устройстве
  function checkFirstCompleteFlash(col) {
    const panel = collectionsEls.panel;
    if (!panel) return;
    panel.classList.remove('flash');
    if (!col || !col.complete) return;
    const key = 'col_seen_' + col.code;
    let seen = false;
    try {
      seen = !!localStorage.getItem(key);
    } catch (e) {
      seen = true;
    }
    if (!seen) {
      try {
        localStorage.setItem(key, '1');
      } catch (e) {}
      panel.classList.add('flash');
    }
  }

  // Открытие шторки коллекции
  function openCollectionSheet(col) {
    if (!col || !collectionsEls.sheet) return;
    lastActiveBeforeSheet = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;

    collectionsEls.title.textContent = col.name;
    collectionsEls.progress.textContent = 'Собрано ' + col.owned + ' из ' + col.total;

    // Сетка всех частей набора по слотам
    const grid = collectionsEls.grid;
    grid.textContent = '';
    const parts = Array.isArray(col.parts) ? col.parts : [];
    const ownedSet = wd && wd.mine && wd.mine.owned ? wd.mine.owned : new Set();

    parts.forEach((code) => {
      const isOwned = ownedSet.has(code);
      const slot = skinSlotOf(code);
      const cell = document.createElement('div');
      cell.className = 'collection-part' + (isOwned ? ' owned' : ' unowned');
      cell.dataset.code = code;

      const thumb = document.createElement('div');
      thumb.className = 'collection-part-thumb' + (isOwned ? '' : ' unowned');
      if (slot && typeof wdScene === 'function') {
        thumb.appendChild(wdScene(slot, code, true));
      }
      cell.appendChild(thumb);

      if (isOwned) {
        const nameEl = document.createElement('span');
        nameEl.className = 'collection-part-name';
        nameEl.textContent = skinNameOf(code);
        cell.appendChild(nameEl);
      } else {
        const labelEl = document.createElement('span');
        labelEl.className = 'collection-part-label';
        labelEl.textContent = 'Как получить';
        const howEl = document.createElement('span');
        howEl.className = 'collection-part-how';
        howEl.textContent = col.how;
        cell.append(labelEl, howEl);
      }

      grid.appendChild(cell);
    });

    // Строка-ссылка «Где получить» по source
    const srcBox = collectionsEls.sourceBox;
    const srcBtn = collectionsEls.sourceBtn;
    if (srcBox && srcBtn) {
      if (col.source === 'farm' || col.source === 'streak' || col.source === 'referral') {
        srcBox.hidden = false;
        srcBtn.textContent = 'Где получить →';
        srcBtn.onclick = () => {
          closeCollectionSheet();
          if (col.source === 'farm') {
            if (typeof showTab === 'function') showTab('farm');
          } else if (col.source === 'streak') {
            if (typeof currentGame !== 'undefined') currentGame = 'lobby';
            if (typeof showTab === 'function') showTab('play');
          } else if (col.source === 'referral') {
            if (typeof showTab === 'function') showTab('profile');
            const rc = document.getElementById('referral-card');
            if (rc && typeof rc.scrollIntoView === 'function') {
              rc.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }
        };
      } else {
        srcBox.hidden = true;
      }
    }

    checkFirstCompleteFlash(col);
    collectionsEls.sheet.hidden = false;

    setTimeout(() => {
      if (!collectionsEls.sheet.hidden && collectionsEls.close) {
        collectionsEls.close.focus({ preventScroll: true });
      }
    }, 0);
  }

  // Закрытие шторки коллекции
  function closeCollectionSheet() {
    const sheet = collectionsEls.sheet;
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    if (collectionsEls.panel) collectionsEls.panel.classList.remove('flash');
    if (lastActiveBeforeSheet && lastActiveBeforeSheet.isConnected && !lastActiveBeforeSheet.hidden) {
      lastActiveBeforeSheet.focus({ preventScroll: true });
    }
    lastActiveBeforeSheet = null;
  }

  // Загрузка гардероба для коллекций (только если ещё нет в памяти)
  async function ensureCollectionsLoaded() {
    if (typeof wd === 'object' && wd && wd.catalog && wd.mine) return;
    if (typeof loadWardrobe === 'function') {
      try {
        await loadWardrobe(false);
      } catch (e) {}
    }
  }

  // Внутренняя функция перехода и открытия шторки коллекции
  window.__openCollection = async function(code) {
    if (typeof showTab === 'function') showTab('profile');
    await ensureCollectionsLoaded();
    renderCollections();
    if (!code || typeof wd !== 'object' || !wd || !wd.mine || !Array.isArray(wd.mine.collections)) return;
    const col = wd.mine.collections.find((c) => c && c.code === code);
    if (col) {
      openCollectionSheet(col);
    }
  };

  // Переключение вида карточек: лента (scroll-snap) или сетка во всю ширину
  if (collectionsEls.allBtn && collectionsEls.card) {
    collectionsEls.allBtn.addEventListener('click', () => {
      const isGrid = collectionsEls.card.classList.toggle('grid-view');
      collectionsEls.allBtn.setAttribute('aria-expanded', String(isGrid));
      updateFeedFade();
    });
  }

  if (collectionsEls.feed) {
    collectionsEls.feed.addEventListener('scroll', updateFeedFade, { passive: true });
  }

  if (collectionsEls.close) {
    collectionsEls.close.addEventListener('click', closeCollectionSheet);
  }

  if (collectionsEls.dim && typeof closeOnBackdropTap === 'function') {
    closeOnBackdropTap(collectionsEls.dim, closeCollectionSheet);
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !e.defaultPrevented && collectionsEls.sheet && !collectionsEls.sheet.hidden) {
      e.preventDefault();
      closeCollectionSheet();
    }
  });

  // Следим за открытием вкладки «Профиль»
  const profileScreen = document.querySelector('[data-screen="profile"]');
  if (profileScreen) {
    new MutationObserver(() => {
      if (!profileScreen.hidden) {
        if (typeof wd === 'object' && wd && wd.catalog && wd.mine) {
          renderCollections();
        } else {
          ensureCollectionsLoaded().then(renderCollections);
        }
      }
    }).observe(profileScreen, { attributes: true, attributeFilter: ['hidden'] });
    if (!profileScreen.hidden) {
      ensureCollectionsLoaded().then(renderCollections);
    }
  }

  window.renderProfileCollections = renderCollections;
})();
// #endregion
