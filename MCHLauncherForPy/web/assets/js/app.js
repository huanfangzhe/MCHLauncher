/* ==========================================================================
   MCHLauncherForPy - 界面交互
   开发者：幻方者（https://space.bilibili.com/3546815194467241）
   说明：当前版本仅实现界面层的交互反馈，版本选择、登录、启动等业务逻辑
        统一留空，等待后续接入。
   ========================================================================== */

(function () {
  'use strict';

  // 开发者：幻方者 —— 设置页“关于”里的 B 站主页
  var AUTHOR_URL = 'https://space.bilibili.com/3546815194467241';

  var $ = function (selector, scope) {
    return (scope || document).querySelector(selector);
  };
  var $$ = function (selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  };

  /* ---------- Python 桥接 ---------- */
  var api = null;
  var bridgeReady = new Promise(function (resolve) {
    function boot() {
      api = (window.pywebview && window.pywebview.api) || null;
      resolve(api);
    }
    if (window.pywebview && window.pywebview.api) {
      boot();
    } else {
      window.addEventListener('pywebviewready', boot, { once: true });
      // 脱离 pywebview 直接预览时兜底
      setTimeout(function () {
        if (!api) resolve(null);
      }, 2500);
    }
  });

  function callApi(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    return bridgeReady.then(function (bridge) {
      if (!bridge || typeof bridge[name] !== 'function') return null;
      try {
        return Promise.resolve(bridge[name].apply(bridge, args)).catch(function () {
          return null;
        });
      } catch (err) {
        return null;
      }
    });
  }

  /* ---------- 自定义下拉框（替换原生 select 弹层） ---------- */
  var closeActiveSelect = null;

  function patchSelectValueSetter() {
    var proto = window.HTMLSelectElement && HTMLSelectElement.prototype;
    if (!proto || proto.__uiSelectPatched) return;
    var descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!descriptor || !descriptor.set) return;
    Object.defineProperty(proto, 'value', {
      configurable: true,
      enumerable: descriptor.enumerable,
      get: descriptor.get,
      set: function (value) {
        descriptor.set.call(this, value);
        if (typeof this.__uiSelectSync === 'function') this.__uiSelectSync();
      }
    });
    proto.__uiSelectPatched = true;
  }

  function enhanceSelect(select) {
    if (!select || select.classList.contains('ui-select-enhanced')) return;

    var wrap = document.createElement('div');
    wrap.className = 'ui-select';
    if (select.id) wrap.classList.add('ui-select--' + select.id);
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('ui-select-enhanced');

    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ui-select-trigger';
    trigger.innerHTML =
      '<span class="ui-select-label"></span>' +
      '<span class="ui-select-caret">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="m7 10 5 5 5-5"/></svg>' +
      '</span>';
    wrap.appendChild(trigger);
    var labelEl = trigger.querySelector('.ui-select-label');

    var menu = document.createElement('div');
    menu.className = 'ui-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.style.display = 'none';
    document.body.appendChild(menu);

    function isOpen() {
      return wrap.classList.contains('is-open');
    }

    function positionMenu() {
      var rect = trigger.getBoundingClientRect();
      menu.style.width = Math.max(rect.width, 160) + 'px';
      menu.style.left = Math.round(rect.left) + 'px';
      menu.style.display = 'flex';
      var height = menu.offsetHeight;
      var below = window.innerHeight - rect.bottom - 10;
      if (height > below && rect.top - 10 > below) {
        menu.style.top = Math.max(8, Math.round(rect.top - height - 6)) + 'px';
      } else {
        menu.style.top = Math.round(rect.bottom + 6) + 'px';
      }
    }

    function syncLabel() {
      var option = select.options[select.selectedIndex] || null;
      labelEl.textContent = option ? option.textContent : '';
      trigger.title = option ? option.textContent : '';
      trigger.disabled = !!select.disabled;
      if (isOpen()) positionMenu();
    }

    function renderMenu() {
      menu.innerHTML = '';
      Array.prototype.forEach.call(select.options, function (option, index) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'ui-select-option';
        item.setAttribute('role', 'option');
        item.textContent = option.textContent;
        item.title = option.textContent;
        if (index === select.selectedIndex) item.classList.add('is-selected');
        if (option.disabled) item.disabled = true;
        item.addEventListener('click', function (event) {
          event.stopPropagation();
          event.preventDefault();
          select.selectedIndex = index;
          syncLabel();
          close();
          select.dispatchEvent(new Event('change', { bubbles: true }));
        });
        menu.appendChild(item);
      });
    }

    function open() {
      if (closeActiveSelect) closeActiveSelect();
      renderMenu();
      wrap.classList.add('is-open');
      positionMenu();
      closeActiveSelect = close;
      var selected = menu.querySelector('.is-selected');
      if (selected && selected.scrollIntoView) {
        selected.scrollIntoView({ block: 'nearest' });
      }
    }

    function close() {
      wrap.classList.remove('is-open');
      menu.style.display = 'none';
      if (closeActiveSelect === close) closeActiveSelect = null;
    }

    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (select.disabled) return;
      if (isOpen()) {
        close();
        return;
      }
      open();
    });

    document.addEventListener('mousedown', function (event) {
      if (!isOpen()) return;
      if (wrap.contains(event.target) || menu.contains(event.target)) return;
      close();
    });

    window.addEventListener('resize', close);
    window.addEventListener(
      'scroll',
      function (event) {
        if (!isOpen()) return;
        if (menu.contains(event.target) || wrap.contains(event.target)) return;
        close();
      },
      true
    );

    var observer = new MutationObserver(function () {
      syncLabel();
      if (isOpen()) {
        renderMenu();
        positionMenu();
      }
    });
    observer.observe(select, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['selected', 'disabled', 'value']
    });

    select.__uiSelectSync = syncLabel;
    syncLabel();
  }

  function enhanceAllSelects() {
    Array.prototype.forEach.call(document.querySelectorAll('select'), enhanceSelect);
  }

  patchSelectValueSetter();
  enhanceAllSelects();

  /* ---------- 右下角通知 ---------- */
  var toastStack = $('#toastStack');
  var TOAST_DURATION = 2400;
  var TOAST_LEAVE = 260;
  var TOAST_MAX = 8;

  function captureToastPositions() {
    var positions = new Map();
    Array.prototype.forEach.call(toastStack.children, function (item) {
      positions.set(item, item.getBoundingClientRect().top);
    });
    return positions;
  }

  // 某条通知移除后，其余通知平滑移动到新位置（FLIP）
  function shiftToastStack(prevPositions) {
    Array.prototype.forEach.call(toastStack.children, function (item) {
      if (!prevPositions.has(item)) return;
      var delta = prevPositions.get(item) - item.getBoundingClientRect().top;
      if (!delta) return;
      item.style.transform = 'translateY(' + delta + 'px)';
      void item.offsetHeight;
      window.requestAnimationFrame(function () {
        item.style.transform = '';
      });
    });
  }

  function removeToast(item) {
    if (!item || !item.parentNode) return;
    var prevPositions = captureToastPositions();
    item.parentNode.removeChild(item);
    shiftToastStack(prevPositions);
  }

  function dismissToast(item) {
    if (!item || item.classList.contains('is-out')) return;
    item.classList.add('is-out');
    window.setTimeout(function () {
      removeToast(item);
    }, TOAST_LEAVE);
  }

  function toast(message) {
    if (!toastStack) return;

    var item = document.createElement('div');
    item.className = 'toast';

    var text = document.createElement('div');
    text.className = 'toast-text';
    text.textContent = message;

    var bar = document.createElement('span');
    bar.className = 'toast-bar';
    bar.style.animationDuration = TOAST_DURATION + 'ms';

    item.appendChild(text);
    item.appendChild(bar);

    // 记录旧位置，用于新通知把整列顶上去时的平滑位移
    var prevPositions = captureToastPositions();

    // 按弹出顺序自上而下堆叠，超出上限时先移除最早的一条
    var overflow = toastStack.children.length + 1 - TOAST_MAX;
    while (overflow > 0 && toastStack.firstElementChild) {
      toastStack.removeChild(toastStack.firstElementChild);
      overflow -= 1;
    }

    toastStack.appendChild(item);
    shiftToastStack(prevPositions);

    window.requestAnimationFrame(function () {
      item.classList.add('is-in');
    });

    var closed = false;
    var fallback = null;

    function close() {
      if (closed) return;
      closed = true;
      window.clearTimeout(fallback);
      dismissToast(item);
    }

    // 进度线走完即关闭
    bar.addEventListener('animationend', close);
    fallback = window.setTimeout(close, TOAST_DURATION + 80);
  }

  /* ---------- 视图切换 ---------- */
  var navItems = $$('.nav-item');
  var views = $$('.view');

  function switchView(name) {
    var secondary =
      name === 'version' ||
      name === 'versions' ||
      name === 'launching' ||
      name === 'download-detail' ||
      name === 'mod-detail';
    navItems.forEach(function (item) {
      // 二级页面保持上一个入口的高亮
      if (secondary) return;
      item.classList.toggle('is-active', item.dataset.target === name);
    });
    views.forEach(function (view) {
      view.classList.toggle('is-active', view.dataset.view === name);
    });
    // 窗口壳层是固定高度布局，根滚动层一旦被滚动，整个界面就会整体上移
    var root = document.scrollingElement || document.documentElement;
    if (root && root.scrollTop) root.scrollTop = 0;
    if (name === 'bedrock') {
      ensureBedrockReady();
      loadBedrockVersions();
      if (bedrockSource === 'mirror') loadBedrockMirror();
    }
    if (name === 'download') {
      ensureDownloadReady();
      // 下载板块记忆上次看的内容类型；不是“游戏版本”就把内容列表准备好
      if (typeof downloadContent !== 'undefined' && downloadContent !== 'game') {
        ensureModsReady();
      }
    }
  }

  navItems.forEach(function (item) {
    item.addEventListener('click', function () {
      viewPickedByUser = true;
      var target = item.dataset.target;
      // 启动进行中：切走再点回启动板块时仍停在启动进度页
      if (target === 'launch' && launching) {
        navItems.forEach(function (nav) {
          nav.classList.toggle('is-active', nav === item);
        });
        switchView('launching');
        return;
      }
      switchView(target);
    });
  });

  /* ---------- 默认板块 ---------- */
  var DEFAULT_VIEW_NAMES = ['home', 'launch', 'bedrock', 'download', 'settings'];
  var defaultViewSelect = $('#defaultViewSelect');
  // 用户在状态回包前就点过导航，就不要再把他拽回默认板块
  var viewPickedByUser = false;

  function applyDefaultView(view) {
    if (viewPickedByUser) return;
    var target = DEFAULT_VIEW_NAMES.indexOf(view) >= 0 ? view : 'home';
    switchView(target);
  }

  if (defaultViewSelect) {
    defaultViewSelect.addEventListener('change', function () {
      var view = defaultViewSelect.value;
      callApi('set_settings', { default_view: view }).then(function (settings) {
        if (settings && settings.default_view) {
          defaultViewSelect.value = settings.default_view;
        }
        toast('默认板块已保存');
      });
    });
  }

  /* ---------- 窗口控制 ---------- */
  var btnMinimize = $('#btnMinimize');
  var btnClose = $('#btnClose');

  if (btnMinimize) {
    btnMinimize.addEventListener('click', function () {
      callApi('minimize');
    });
  }

  if (btnClose) {
    btnClose.addEventListener('click', function () {
      callApi('close');
    });
  }

  /* ---------- 无边框窗口拖动 ---------- */
  var INTERACTIVE = 'button, a, input, select, textarea, label, .no-drag';

  function startWindowDrag(startEvent) {
    var originX = startEvent.clientX;
    var originY = startEvent.clientY;
    var pending = false;

    function onMove(event) {
      if (pending) return;
      pending = true;
      // 合并高频鼠标移动，避免过多跨进程调用
      window.requestAnimationFrame(function () {
        pending = false;
        try {
          window.pywebview._jsApiCallback(
            'pywebviewMoveWindow',
            [event.screenX - originX, event.screenY - originY],
            'move'
          );
        } catch (err) {
          /* 忽略：非 pywebview 环境 */
        }
      });
    }

    function onUp() {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  document.addEventListener('mousedown', function (event) {
    if (event.button !== 0) return;
    if (event.target.closest(INTERACTIVE)) return;
    // 仅顶部拖动区域（标题栏、左上角标志）可移动窗口
    if (!event.target.closest('.drag-region')) return;
    startWindowDrag(event);
  });

  /* ---------- 账号（含离线登录） ---------- */
  var loginOptions = $$('.login-opt');
  var accountModeLabel = $('#accountModeLabel');
  var accountStateEl = $('#accountState');
  var accountHintEl = $('#accountHint');
  var accountNameEl = $('#playerName');
  var offlineNameInput = $('#offlineNameInput');
  var offlineNameGroup = $('#offlineNameGroup');

  var LOGIN_LABELS = {
    offline: '离线登录',
    microsoft: '微软登录',
    authlib: '第三方登录'
  };
  var loginMode = 'offline';
  // 恢复完上次的登录方式之前不写盘，避免启动时把默认值覆盖上去
  var loginModeReady = false;
  var loginModeSaved = '';
  var savedUsername = '';

  /* 微软正版账号：令牌只留在 Python 侧，这里只保存名字与 UUID */
  var msAccount = null;
  var msLoginTimer = null;
  var msLoginSession = null;
  var accountAvatarImg = document.querySelector('.account-avatar .avatar-img');
  var AVATAR_FALLBACK = 'assets/img/steve.png';
  var msAvatarToken = 0;

  /* 修改皮肤：离线用本地 PNG，正版上传到 Mojang */
  var accountSkinRow = $('#accountSkinRow');
  var btnChangeSkin = $('#btnChangeSkin');
  var btnExportSkin = $('#btnExportSkin');
  var btnClearSkin = $('#btnClearSkin');
  var skinVariantEl = $('#skinVariant');
  var offlineSkinCustom = false;
  var avatarNameTimer = null;

  var msModal = $('#msLoginModal');
  var msMask = $('#msLoginMask');
  var msCodeEl = $('#msLoginCode');
  var msUriEl = $('#msLoginUri');
  var msStatusEl = $('#msLoginStatus');
  var btnMsClose = $('#msLoginClose');
  var btnMsCopyCode = $('#btnMsCopyCode');
  var btnMsOpenUri = $('#btnMsOpenUri');
  var btnMsLogout = $('#btnMsLogout');

  /* 第三方外置登录（authlib-injector）：表单直接放在账号卡片里 */
  var authAccount = null;
  var AUTHLIB_DEFAULT_SERVER = 'https://littleskin.cn/api/yggdrasil';
  var authlibFormGroup = $('#authlibFormGroup');
  var authlibServerInput = $('#authlibServer');
  var authlibUserInput = $('#authlibUser');
  var authlibPasswordInput = $('#authlibPassword');
  var authlibStatusEl = $('#authlibStatus');
  var btnAuthlibSubmit = $('#btnAuthlibSubmit');
  var btnAuthlibLogout = $('#btnAuthlibLogout');

  if (authlibServerInput && !authlibServerInput.value) {
    authlibServerInput.value = AUTHLIB_DEFAULT_SERVER;
  }

  function msLoggedIn() {
    return !!(msAccount && msAccount.logged_in);
  }

  function authlibLoggedIn() {
    return !!(authAccount && authAccount.logged_in);
  }

  function updateAccountAvatar() {
    if (!accountAvatarImg) return;
    var fallbackSvg = accountAvatarImg.nextElementSibling;
    accountAvatarImg.style.display = '';
    if (fallbackSvg && fallbackSvg.classList.contains('avatar-fallback')) {
      fallbackSvg.style.display = 'none';
    }

    msAvatarToken += 1;
    var token = msAvatarToken;
    accountAvatarImg.src = AVATAR_FALLBACK;

    if (loginMode === 'microsoft' && msLoggedIn() && msAccount.uuid) {
      // 正版账号：由后端裁出皮肤头颅（首次下载皮肤，之后直接用缓存）
      callApi('get_account_avatar').then(function (res) {
        if (token !== msAvatarToken) return;
        if (res && res.ok && res.avatar) accountAvatarImg.src = res.avatar;
      });
      return;
    }
    if (loginMode === 'authlib' && authlibLoggedIn()) {
      callApi('get_authlib_avatar').then(function (res) {
        if (token !== msAvatarToken) return;
        if (res && res.ok && res.avatar) accountAvatarImg.src = res.avatar;
      });
      return;
    }
    if (loginMode === 'offline') {
      var offlineNameText = offlineName() || savedUsername;
      setOfflineSkinCustom(false);
      if (!offlineNameText) return;
      // 离线账号：设置过自定义皮肤就用自己的头颅，否则保持史蒂夫
      callApi('get_offline_avatar', offlineNameText).then(function (res) {
        if (token !== msAvatarToken) return;
        if (res && res.ok && res.avatar) {
          accountAvatarImg.src = res.avatar;
          setOfflineSkinCustom(!!res.custom);
        }
      });
      return;
    }
    setOfflineSkinCustom(false);
  }

  function setOfflineSkinCustom(custom) {
    offlineSkinCustom = !!custom;
    refreshSkinControls();
  }

  function refreshSkinControls() {
    var msReady = loginMode === 'microsoft' && msLoggedIn();
    var offlineReady = isOfflineMode() && !!offlineName();
    if (accountSkinRow) {
      accountSkinRow.classList.toggle('is-collapsed', !(msReady || offlineReady));
    }
    // 皮肤模型（经典 / 纤细）只在上传正版皮肤时用得到
    if (skinVariantEl) skinVariantEl.classList.toggle('is-collapsed', !msReady);
    if (btnClearSkin) {
      // 离线是删除本地皮肤，正版是恢复默认皮肤，共用一个按钮
      btnClearSkin.textContent = msReady ? '重置皮肤' : '清除皮肤';
      btnClearSkin.classList.toggle(
        'is-collapsed', !(msReady || (offlineReady && offlineSkinCustom))
      );
    }
  }

  function applyAccount(account) {
    msAccount = account && account.logged_in ? account : null;
    if (msLoggedIn()) {
      // 统一走 setLoginMode，顺带把登录方式记下来
      setLoginMode('microsoft');
      return;
    }
    updateAccountAvatar();
    renderAccountName();
    refreshLaunchAvailability();
  }

  function applyAuthlibAccount(account) {
    authAccount = account && account.logged_in ? account : null;
    if (authAccount && authAccount.server && authlibServerInput) {
      authlibServerInput.value = authAccount.server;
    }
    if (authlibLoggedIn()) {
      // 统一走 setLoginMode，顺带把登录方式记下来
      setLoginMode('authlib');
      return;
    }
    updateAccountAvatar();
    renderAccountName();
    refreshLaunchAvailability();
  }

  function setAuthlibStatus(text, kind) {
    if (!authlibStatusEl) return;
    authlibStatusEl.textContent = text || '';
    authlibStatusEl.classList.toggle('is-error', kind === 'error');
    authlibStatusEl.classList.toggle('is-ok', kind === 'ok');
  }

  function focusAuthlibForm() {
    if (authlibServerInput && !authlibServerInput.value) {
      authlibServerInput.value = AUTHLIB_DEFAULT_SERVER;
    }
    if (!authlibLoggedIn() && authlibUserInput) authlibUserInput.focus();
  }

  function submitAuthlibLogin() {
    if (authlibLoggedIn()) return;
    var server = ((authlibServerInput && authlibServerInput.value) || '').trim();
    var user = ((authlibUserInput && authlibUserInput.value) || '').trim();
    var password = (authlibPasswordInput && authlibPasswordInput.value) || '';
    if (!user || !password) {
      setAuthlibStatus('请先填写账号和密码', 'error');
      if (!user && authlibUserInput) {
        authlibUserInput.focus();
      } else if (authlibPasswordInput) {
        authlibPasswordInput.focus();
      }
      return;
    }

    if (btnAuthlibSubmit) {
      btnAuthlibSubmit.disabled = true;
      btnAuthlibSubmit.textContent = '登录中…';
    }
    setAuthlibStatus('正在登录…');
    callApi('authlib_login', server, user, password).then(function (res) {
      if (btnAuthlibSubmit) {
        btnAuthlibSubmit.disabled = false;
        btnAuthlibSubmit.textContent = '登录';
      }
      if (!res || !res.ok) {
        setAuthlibStatus((res && res.error) || '登录失败', 'error');
        return;
      }
      if (authlibPasswordInput) authlibPasswordInput.value = '';
      setAuthlibStatus('');
      applyAuthlibAccount(res.account);
      toast('第三方登录成功：' + ((res.account && res.account.name) || ''));
    });
  }

  function setMsStatus(text, kind) {
    if (!msStatusEl) return;
    msStatusEl.textContent = text || '';
    msStatusEl.classList.toggle('is-error', kind === 'error');
    msStatusEl.classList.toggle('is-ok', kind === 'ok');
  }

  function openMsModal() {
    if (!msModal) return;
    msModal.classList.add('is-open');
    msModal.setAttribute('aria-hidden', 'false');
  }

  function closeMsModal() {
    if (!msModal) return;
    msModal.classList.remove('is-open');
    msModal.setAttribute('aria-hidden', 'true');
  }

  function stopMsPolling() {
    if (msLoginTimer) {
      clearTimeout(msLoginTimer);
      msLoginTimer = null;
    }
    // 让后端把仍在轮询的线程停掉，避免关掉弹窗后继续请求
    if (msLoginSession) callApi('ms_login_cancel');
    msLoginSession = null;
  }

  function scheduleMsPoll(delayMs) {
    if (msLoginTimer) clearTimeout(msLoginTimer);
    msLoginTimer = setTimeout(pollMsLogin, Math.max(300, delayMs || 600));
  }

  function pollMsLogin() {
    msLoginTimer = null;
    if (!msLoginSession) return;
    // 只查后端内存里的进度，本地 0.6 秒一轮，授权一完成界面立刻跟上
    callApi('ms_login_status').then(function (res) {
      if (!msLoginSession) return;
      if (!res) {
        scheduleMsPoll(1500);
        return;
      }
      if (res.state === 'ok') {
        stopMsPolling();
        closeMsModal();
        applyAccount(res.account);
        toast('已登录正版账号：' + ((res.account && res.account.name) || ''));
        return;
      }
      if (res.state === 'error') {
        stopMsPolling();
        setMsStatus(res.error || '登录失败', 'error');
        toast(res.error || '微软登录失败');
        refreshLaunchAvailability();
        return;
      }
      setMsStatus(res.stage || '等待在浏览器中完成授权…');
      scheduleMsPoll(600);
    });
  }

  function startMsLogin() {
    if (msLoginSession) return;
    msLoginSession = { user_code: '' };
    openMsModal();
    if (msCodeEl) msCodeEl.textContent = '········';
    if (msUriEl) msUriEl.textContent = '';
    setMsStatus('正在获取登录码…');
    callApi('ms_login_start').then(function (res) {
      if (!msLoginSession) return;
      if (!res || !res.ok) {
        stopMsPolling();
        setMsStatus((res && res.error) || '无法获取登录码', 'error');
        toast((res && res.error) || '无法发起微软登录');
        return;
      }
      msLoginSession = res;
      if (msCodeEl) msCodeEl.textContent = res.user_code || '';
      if (msUriEl) msUriEl.textContent = res.verification_uri || '';
      setMsStatus('等待在浏览器中完成授权…');
      if (res.verification_uri) callApi('open_external_url', res.verification_uri);
      scheduleMsPoll(600);
    });
  }

  function copyText(text) {
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return legacyCopy(text); }
      );
    }
    return Promise.resolve(legacyCopy(text));
  }

  // WebView 里 clipboard API 可能被限制，退回到 execCommand 方案
  function legacyCopy(text) {
    try {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', 'readonly');
      area.style.position = 'fixed';
      area.style.top = '-9999px';
      area.style.left = '-9999px';
      document.body.appendChild(area);
      area.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(area);
      return !!ok;
    } catch (err) {
      return false;
    }
  }

  function offlineName() {
    return ((offlineNameInput && offlineNameInput.value) || '').trim();
  }

  function isOfflineMode() {
    return loginMode === 'offline';
  }

  function renderAccountName() {
    if (!accountNameEl) return;
    if (loginMode === 'microsoft' && msLoggedIn()) {
      accountNameEl.textContent = msAccount.name || '正版账号';
      return;
    }
    if (loginMode === 'authlib' && authlibLoggedIn()) {
      accountNameEl.textContent = authAccount.name || '第三方账号';
      return;
    }
    var name = offlineName();
    if (!name && isOfflineMode()) {
      accountNameEl.textContent = '未命名';
      return;
    }
    accountNameEl.textContent = name || savedUsername || '未命名';
  }

  function refreshLaunchAvailability() {
    var needName = isOfflineMode() && !offlineName();
    var needLogin =
      (loginMode === 'microsoft' && !msLoggedIn()) ||
      (loginMode === 'authlib' && !authlibLoggedIn());

    if (accountModeLabel) accountModeLabel.textContent = LOGIN_LABELS[loginMode] || '未登录';
    if (accountStateEl) {
      if (loginMode === 'microsoft' && !msLoggedIn()) {
        accountStateEl.textContent = '未登录 · 需要完成微软授权';
      } else if (loginMode === 'microsoft') {
        accountStateEl.textContent = '正版登录 · ' + (msAccount.name || '已授权');
      } else if (loginMode === 'authlib' && !authlibLoggedIn()) {
        accountStateEl.textContent = '未登录 · 需要第三方账号';
      } else if (loginMode === 'authlib') {
        accountStateEl.textContent = '外置登录 · ' + (authAccount.name || '已登录');
      } else if (needName) {
        accountStateEl.textContent = '未填写玩家名';
      } else if (isOfflineMode()) {
        accountStateEl.textContent = '离线登录 · 无需密码';
      } else {
        accountStateEl.textContent = '该登录方式尚未接入，将以离线玩家名启动';
      }
    }
    if (accountHintEl) {
      if (needLogin) {
        accountHintEl.textContent = loginMode === 'authlib'
          ? '第三方登录后才能启动游戏'
          : '微软正版登录后才能启动游戏';
      } else if (needName) {
        accountHintEl.textContent = '离线登录需要先填写玩家名才能启动';
      } else {
        accountHintEl.textContent = '';
      }
    }
    if (offlineNameInput) offlineNameInput.disabled = !isOfflineMode();
    // 非离线登录时整块「玩家名」输入直接收起，不留空白
    if (offlineNameGroup) offlineNameGroup.classList.toggle('is-hidden', !isOfflineMode());
    // 第三方登录：未登录时卡片里直接显示服务器 / 账号 / 密码表单，登录后收起
    var authlibFormVisible = loginMode === 'authlib' && !authlibLoggedIn();
    if (authlibFormGroup) {
      authlibFormGroup.classList.toggle('is-collapsed', !authlibFormVisible);
    }
    // 退出按钮只在对应登录方式且已登录时出现
    if (btnMsLogout) {
      btnMsLogout.classList.toggle(
        'is-hidden', !(loginMode === 'microsoft' && msLoggedIn())
      );
    }
    if (btnAuthlibLogout) {
      btnAuthlibLogout.classList.toggle(
        'is-hidden', !(loginMode === 'authlib' && authlibLoggedIn())
      );
    }
    if (btnLaunch && !launching) {
      // 允许多开：游戏运行中仍可继续启动新实例
      btnLaunch.disabled = needName || needLogin;
    }
    refreshSkinControls();
  }

  function persistOfflineName() {
    var name = offlineName();
    if (!name || name === savedUsername) return Promise.resolve(savedUsername);
    return callApi('set_settings', { username: name }).then(function (settings) {
      if (settings && settings.username) savedUsername = settings.username;
      return savedUsername;
    });
  }

  function setLoginMode(mode) {
    loginMode = mode || 'offline';
    loginOptions.forEach(function (item) {
      item.classList.toggle('is-active',
        item.getAttribute('data-login') === loginMode);
    });
    updateAccountAvatar();
    renderAccountName();
    refreshLaunchAvailability();

    // 记住最后使用的登录方式；未登录的微软 / 第三方账号不写盘，
    // 免得下次启动停在需要登录的界面上
    var persistable =
      loginMode === 'offline' ||
      (loginMode === 'microsoft' && msLoggedIn()) ||
      (loginMode === 'authlib' && authlibLoggedIn());
    if (loginModeReady && persistable && loginMode !== loginModeSaved) {
      loginModeSaved = loginMode;
      callApi('set_settings', { login_mode: loginMode });
    }
  }

  loginOptions.forEach(function (option) {
    option.addEventListener('click', function () {
      var mode = option.getAttribute('data-login') || 'offline';
      if (mode === 'microsoft' && !msLoggedIn()) {
        // 还没有正版账号：先走设备码授权，成功后自动切到微软登录
        setLoginMode('microsoft');
        startMsLogin();
        return;
      }
      setLoginMode(mode);
      if (mode === 'authlib') {
        // 第三方登录：表单就在卡片里，未登录时直接把光标放到账号上
        focusAuthlibForm();
      }
    });
  });

  if (btnMsClose) btnMsClose.addEventListener('click', function () {
    stopMsPolling();
    closeMsModal();
  });
  if (msMask) msMask.addEventListener('click', function () {
    stopMsPolling();
    closeMsModal();
  });
  if (btnMsCopyCode) {
    btnMsCopyCode.addEventListener('click', function () {
      var code = (msLoginSession && msLoginSession.user_code) || '';
      copyText(code).then(function (ok) {
        toast(ok ? '登录码已复制' : '复制失败，请手动选中代码');
      });
    });
  }
  if (btnMsOpenUri) {
    btnMsOpenUri.addEventListener('click', function () {
      var uri = (msLoginSession && msLoginSession.verification_uri) || '';
      if (uri) callApi('open_external_url', uri);
    });
  }
  if (btnMsLogout) {
    btnMsLogout.addEventListener('click', function () {
      askConfirm('退出后需要重新授权才能使用正版身份启动，确定退出吗？', function () {
        callApi('ms_logout').then(function (res) {
          applyAccount(res && res.account);
          setLoginMode('offline');
          toast('已退出正版账号');
        });
      }, '退出登录');
    });
  }

  if (btnAuthlibSubmit) {
    btnAuthlibSubmit.addEventListener('click', submitAuthlibLogin);
  }
  [authlibServerInput, authlibUserInput, authlibPasswordInput].forEach(function (input) {
    if (!input) return;
    input.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      submitAuthlibLogin();
    });
  });
  if (btnAuthlibLogout) {
    btnAuthlibLogout.addEventListener('click', function () {
      askConfirm('退出后需要重新登录才能使用该第三方账号启动，确定退出吗？', function () {
        callApi('authlib_logout').then(function (res) {
          applyAuthlibAccount(res && res.account);
          setLoginMode('offline');
          setAuthlibStatus('');
          toast('已退出第三方账号');
        });
      }, '退出登录');
    });
  }

  /* ---------- 修改皮肤 ---------- */
  function scheduleAvatarRefresh() {
    if (avatarNameTimer) clearTimeout(avatarNameTimer);
    avatarNameTimer = setTimeout(function () {
      avatarNameTimer = null;
      updateAccountAvatar();
    }, 350);
  }

  function applySkinAvatar(avatar) {
    if (avatar && accountAvatarImg) accountAvatarImg.src = avatar;
  }

  function changeOfflineSkin() {
    var name = offlineName();
    if (!name) {
      toast('请先填写玩家名');
      if (offlineNameInput) offlineNameInput.focus();
      return;
    }
    if (btnChangeSkin) btnChangeSkin.disabled = true;
    callApi('pick_offline_skin', name).then(function (res) {
      if (btnChangeSkin) btnChangeSkin.disabled = false;
      if (!res) {
        toast('启动器接口未就绪');
        return;
      }
      if (res.cancelled) return;
      if (!res.ok) {
        toast(res.error || '设置皮肤失败');
        return;
      }
      applySkinAvatar(res.avatar);
      setOfflineSkinCustom(!!res.custom);
      toast(res.synced
        ? '皮肤已应用，并同步到 CustomSkinLoader'
        : '皮肤已应用；安装 CustomSkinLoader 后可在游戏内显示');
    });
  }

  function uploadMsSkin() {
    var variant = (skinVariantEl && skinVariantEl.value) || 'classic';
    if (btnChangeSkin) btnChangeSkin.disabled = true;
    callApi('pick_ms_skin', variant).then(function (res) {
      if (btnChangeSkin) btnChangeSkin.disabled = false;
      if (!res) {
        toast('启动器接口未就绪');
        return;
      }
      if (res.cancelled) return;
      if (!res.ok) {
        toast(res.error || '上传皮肤失败');
        return;
      }
      applySkinAvatar(res.avatar);
      if (res.account) applyAccount(res.account);
      toast(res.warning || '正版皮肤已更新');
    });
  }

  if (btnChangeSkin) {
    btnChangeSkin.addEventListener('click', function () {
      if (loginMode === 'microsoft' && msLoggedIn()) {
        askConfirm(
          '将把选择的皮肤上传到你的正版账号，确定继续吗？',
          uploadMsSkin,
          '修改皮肤'
        );
        return;
      }
      changeOfflineSkin();
    });
  }

  function exportSkin() {
    var useMs = loginMode === 'microsoft' && msLoggedIn();
    if (btnExportSkin) btnExportSkin.disabled = true;
    callApi(
      'export_skin',
      useMs ? 'microsoft' : 'offline',
      useMs ? '' : offlineName()
    ).then(function (res) {
      if (btnExportSkin) btnExportSkin.disabled = false;
      if (!res) {
        toast('启动器接口未就绪');
        return;
      }
      if (res.cancelled) return;
      if (!res.ok) {
        toast(res.error || '导出皮肤失败');
        return;
      }
      toast('皮肤已导出');
    });
  }

  function resetMsSkin() {
    if (btnClearSkin) btnClearSkin.disabled = true;
    callApi('reset_ms_skin').then(function (res) {
      if (btnClearSkin) btnClearSkin.disabled = false;
      if (!res || !res.ok) {
        toast((res && res.error) || '重置皮肤失败');
        return;
      }
      if (res.account) {
        applyAccount(res.account);
      } else {
        updateAccountAvatar();
      }
      toast(res.backup ? '已恢复默认皮肤，原皮肤已备份到 skins/backup' : '已恢复默认皮肤');
    });
  }

  if (btnExportSkin) {
    btnExportSkin.addEventListener('click', exportSkin);
  }

  if (btnClearSkin) {
    btnClearSkin.addEventListener('click', function () {
      if (loginMode === 'microsoft' && msLoggedIn()) {
        askConfirm(
          '将把正版账号的皮肤恢复为默认皮肤，确定继续吗？',
          resetMsSkin,
          '重置皮肤'
        );
        return;
      }
      var name = offlineName();
      if (!name) return;
      askConfirm('清除当前玩家的自定义皮肤？', function () {
        callApi('clear_offline_skin', name).then(function (res) {
          if (!res || !res.ok) {
            toast((res && res.error) || '清除皮肤失败');
            return;
          }
          setOfflineSkinCustom(false);
          updateAccountAvatar();
          toast('已清除自定义皮肤');
        });
      }, '清除皮肤');
    });
  }

  if (offlineNameInput) {
    offlineNameInput.addEventListener('input', function () {
      renderAccountName();
      refreshLaunchAvailability();
      scheduleAvatarRefresh();
    });
    offlineNameInput.addEventListener('change', function () {
      persistOfflineName();
    });
    offlineNameInput.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      startLaunchFlow();
    });
  }

  var btnLaunch = $('#btnLaunch');
  var btnSelectVersion = $('#btnSelectVersion');
  var btnVersionSettings = $('#btnVersionSettings');
  var btnImportModpack = $('#btnImportModpack');

  /* ---------- 启动面板信息 ---------- */
  var launchVersionIconEl = $('#launchVersionIcon');
  var launchVersionNameEl = $('#launchVersionName');
  var launchStateValueEl = $('#launchStateValue');
  var launchJavaValueEl = $('#launchJavaValue');
  var launchMemoryValueEl = $('#launchMemoryValue');
  var launchWindowValueEl = $('#launchWindowValue');
  var btnOpenGameDir = $('#btnOpenGameDir');

  var WINDOW_MODE_LABELS = {
    default: '默认',
    launcher: '与启动器相同',
    maximized: '最大化',
    minimized: '最小化'
  };

  function formatMemory(megabytes) {
    var gb = (Number(megabytes) || 0) / 1024;
    return (Number.isInteger(gb) ? gb : gb.toFixed(1)) + ' GB';
  }

  function applyLaunchInfo(settings, java) {
    if (settings) {
      if (launchMemoryValueEl) {
        launchMemoryValueEl.textContent =
          formatMemory(settings.memory_mb || 8192) + (settings.memory_auto ? '（自动）' : '');
      }
      if (launchWindowValueEl) {
        launchWindowValueEl.textContent = WINDOW_MODE_LABELS[settings.window_mode] || '默认';
      }
    }
    if (java !== undefined) {
      if (launchJavaValueEl) {
        if (java) {
          launchJavaValueEl.textContent =
            'Java ' + (java.major || '?') + (java.version ? ' · ' + java.version : '');
          launchJavaValueEl.title = java.path || '';
        } else {
          launchJavaValueEl.textContent = '未检测到';
          launchJavaValueEl.title = '';
        }
      }
    }
  }

  function updateLaunchStateLabel() {
    if (!launchStateValueEl) return;
    if (gameRunning) {
      launchStateValueEl.textContent =
        gameRunningCount > 1 ? '游戏运行中 ×' + gameRunningCount : '游戏运行中';
    } else if (launching) {
      launchStateValueEl.textContent = '正在启动';
    } else {
      launchStateValueEl.textContent = selectedVersion ? '就绪' : '未选择版本';
    }
    launchStateValueEl.classList.toggle('is-running', !!gameRunning);
  }

  /* ---------- 启动流程与进度 ---------- */
  var launchStatusEl = $('#launchStatus');
  var launchStatusText = $('#launchStatusText');
  var launchProgressBar = $('#launchProgressBar');
  var launching = false;

  var PROGRESS_LABELS = {
    java: 'Java 运行时',
    libraries: '依赖库',
    assets: '游戏资源',
    deps: '游戏依赖',
    download: '下载游戏',
    version: '版本信息',
    loader: '模组适配器',
    fabricapi: 'Fabric API',
    mod: '模组下载',
    modpack: '整合包安装',
    bedrock: '基岩版',
    done: '完成'
  };

  // 进度条组件：启动页 / 下载页 / 适配器各一份
  var launchWidget = {
    el: launchStatusEl,
    text: launchStatusText,
    bar: launchProgressBar
  };
  var downloadWidget = {
    el: $('#downloadStatus'),
    text: $('#downloadStatusText'),
    bar: $('#downloadProgressBar')
  };
  var dlModalWidget = {
    el: $('#dlModalStatus'),
    text: $('#dlModalStatusText'),
    bar: $('#dlModalProgressBar')
  };
  var modWidget = {
    el: $('#modStatus'),
    text: $('#modStatusText'),
    bar: $('#modProgressBar')
  };
  var modDetailWidget = {
    el: $('#modDetailStatus'),
    text: $('#modDetailStatusText'),
    bar: $('#modDetailProgressBar')
  };

  // 当前耗时任务对应的进度组件
  var activeTask = 'launch';

  function applyStatus(widget, text, percent, busy) {
    if (!widget) return;
    if (widget.text) widget.text.textContent = text || '';
    if (widget.el) widget.el.classList.toggle('is-busy', !!busy);
    if (widget.bar) {
      var value = typeof percent === 'number' && percent >= 0 ? Math.min(100, percent) : 0;
      widget.bar.style.width = value + '%';
    }
  }

  function setLaunchStatus(text, percent, busy) {
    activeTask = 'launch';
    applyStatus(launchWidget, text, percent, busy);
  }

  function setDownloadStatus(text, percent, busy) {
    activeTask = 'download';
    applyStatus(downloadWidget, text, percent, busy);
  }

  function setModalStatus(text, percent, busy) {
    activeTask = 'modal';
    applyStatus(dlModalWidget, text, percent, busy);
  }

  function setModStatus(text, percent, busy) {
    activeTask = 'mod';
    applyStatus(modWidget, text, percent, busy);
  }

  function setModDetailStatus(text, percent, busy) {
    activeTask = 'mod-detail';
    applyStatus(modDetailWidget, text, percent, busy);
  }

  /* ---------- 下载任务（右下角进度圆环） ---------- */
  var dlTaskRootEl = $('#dlTaskRoot');
  var dlTaskPanelEl = $('#dlTaskPanel');
  var dlTaskListEl = $('#dlTaskList');
  var dlTaskFabEl = $('#dlTaskFab');
  var dlTaskRingEl = $('#dlTaskRing');
  var dlTaskFabTextEl = $('#dlTaskFabText');
  var dlTaskCloseBtn = $('#dlTaskClose');
  var dlTaskClearBtn = $('#dlTaskClear');

  // 圆环周长：2 * π * r（r = 18）
  var DL_TASK_RING_LENGTH = 113.1;
  var DL_TASK_KINDS = {
    download: 1,
    version: 1,
    libraries: 1,
    assets: 1,
    loader: 1,
    fabricapi: 1,
    java: 1,
    bedrock: 1,
    mod: 1,
    modpack: 1,
    done: 1
  };
  var dlTasks = { items: [], activeId: '', seq: 0, panelOpen: false };

  function downloadTaskStatusText(task) {
    if (task.status === 'done') return '已完成';
    if (task.status === 'failed') return '失败';
    return '进行中';
  }

  function findDownloadTask(id) {
    for (var i = 0; i < dlTasks.items.length; i += 1) {
      if (dlTasks.items[i].id === id) return dlTasks.items[i];
    }
    return null;
  }

  function buildDownloadTaskRow(task) {
    var row = document.createElement('div');
    row.className = 'dl-task-item is-' + task.status;

    var head = document.createElement('div');
    head.className = 'dl-task-row';

    var name = document.createElement('span');
    name.className = 'dl-task-name';
    name.textContent = task.name;
    name.title = task.name;

    var state = document.createElement('span');
    state.className = 'dl-task-state';
    state.textContent = downloadTaskStatusText(task);

    head.appendChild(name);
    head.appendChild(state);

    var sub = document.createElement('div');
    sub.className = 'dl-task-sub';
    sub.textContent = task.text || '';

    var bar = document.createElement('div');
    bar.className = 'dl-task-bar';
    var fill = document.createElement('i');
    var percent = task.status === 'done' ? 100 : Math.max(0, task.percent || 0);
    fill.style.width = percent + '%';
    bar.appendChild(fill);

    row.appendChild(head);
    row.appendChild(sub);
    row.appendChild(bar);
    return row;
  }

  function renderDownloadTasks() {
    if (!dlTaskRootEl) return;

    var items = dlTasks.items;
    var hasItems = items.length > 0;
    dlTaskRootEl.hidden = !hasItems;
    document.body.classList.toggle('has-download-tasks', hasItems);

    var running = items.filter(function (task) {
      return task.status === 'running';
    });
    // 有进行中的任务时显示最新一条，否则显示最后一条结果
    var focus = running.length ? running[running.length - 1] : items[items.length - 1];

    dlTaskRootEl.classList.toggle('is-done', !!focus && focus.status === 'done');
    dlTaskRootEl.classList.toggle('is-failed', !!focus && focus.status === 'failed');

    var percent = focus ? focus.percent : 0;
    if (typeof percent !== 'number' || percent < 0) percent = 0;
    percent = Math.min(100, percent);

    if (dlTaskRingEl) {
      dlTaskRingEl.style.strokeDashoffset = String(
        DL_TASK_RING_LENGTH * (1 - percent / 100)
      );
    }

    if (dlTaskFabTextEl) {
      if (focus && focus.status === 'failed') {
        dlTaskFabTextEl.textContent = '!';
      } else if (focus && focus.status === 'done') {
        dlTaskFabTextEl.textContent = '✓';
      } else if (running.length) {
        dlTaskFabTextEl.textContent = Math.round(percent) + '%';
      } else {
        dlTaskFabTextEl.textContent = String(items.length);
      }
    }

    if (dlTaskFabEl) {
      dlTaskFabEl.title = focus
        ? focus.name + ' · ' + (focus.text || downloadTaskStatusText(focus))
        : '下载任务';
      dlTaskFabEl.setAttribute('aria-expanded', dlTasks.panelOpen ? 'true' : 'false');
    }

    if (dlTaskPanelEl) dlTaskPanelEl.hidden = !dlTasks.panelOpen;
    if (!dlTasks.panelOpen || !dlTaskListEl) return;

    dlTaskListEl.innerHTML = '';
    items.forEach(function (task) {
      dlTaskListEl.appendChild(buildDownloadTaskRow(task));
    });
  }

  function createDownloadTask(info) {
    dlTasks.seq += 1;
    var task = {
      id: info.id || 'dl-task-' + dlTasks.seq,
      name: info.name || info.versionId || '下载任务',
      versionId: info.versionId || '',
      loader: info.loader || 'vanilla',
      percent: 0,
      text: '准备中',
      status: 'running'
    };
    dlTasks.items.push(task);
    dlTasks.activeId = task.id;
    renderDownloadTasks();
    return task.id;
  }

  function finishDownloadTask(id, ok, message) {
    var task = findDownloadTask(id);
    if (!task) return;

    task.status = ok ? 'done' : 'failed';
    if (ok) task.percent = 100;
    task.text = message || (ok ? '下载完成' : '下载失败');
    if (dlTasks.activeId === id) dlTasks.activeId = '';
    renderDownloadTasks();
  }

  // 用户取消或任务无效时直接移除条目
  function removeDownloadTask(id) {
    if (!id) return;
    dlTasks.items = dlTasks.items.filter(function (task) {
      return task.id !== id;
    });
    if (dlTasks.activeId === id) dlTasks.activeId = '';
    renderDownloadTasks();
  }

  // Python 推送的进度：归属指定的下载任务，未指定时归属当前进行中的任务
  function handleDownloadTaskProgress(kind, percent, text, taskId) {
    if (!DL_TASK_KINDS[kind]) return;

    var id = taskId || dlTasks.activeId;
    if (!id) return;

    var task = findDownloadTask(id);
    if (!task || task.status !== 'running') return;

    var stage = PROGRESS_LABELS[kind] || '准备中';
    var detail = text || stage;
    if (kind !== 'done' && percent >= 0) {
      detail += ' ' + percent + '%';
    }
    task.text = detail;
    if (kind === 'done') {
      task.percent = 100;
    } else if (percent >= 0) {
      task.percent = percent;
    }
    renderDownloadTasks();
  }

  if (dlTaskFabEl) {
    dlTaskFabEl.addEventListener('click', function () {
      dlTasks.panelOpen = !dlTasks.panelOpen;
      renderDownloadTasks();
    });
  }

  if (dlTaskCloseBtn) {
    dlTaskCloseBtn.addEventListener('click', function () {
      dlTasks.panelOpen = false;
      renderDownloadTasks();
    });
  }

  if (dlTaskClearBtn) {
    dlTaskClearBtn.addEventListener('click', function () {
      dlTasks.items = dlTasks.items.filter(function (task) {
        return task.status === 'running';
      });
      renderDownloadTasks();
    });
  }

  // 供 Python 主动推送进度
  window.mchl = {
    progress: function (payload) {
      if (!payload) return;
      var kind = payload.kind || '';
      var label = PROGRESS_LABELS[kind] || '准备中';
      var percent =
        payload.total && payload.total > 0
          ? Math.round((payload.done / payload.total) * 100)
          : -1;
      var text = payload.text || label;
      if (percent >= 0 && kind !== 'done') {
        text += ' ' + percent + '%';
      }

      // 基岩版：页面进度条 + 右下角下载任务一起更新
      if (kind === 'bedrock') {
        var bedrockText = percent >= 100 ? payload.text || label : text;
        applyStatus(bedrockWidget, bedrockText, percent, percent !== 100);
        handleDownloadTaskProgress(
          percent >= 100 ? 'done' : 'bedrock',
          percent,
          payload.text || label,
          payload.task || 'bedrock'
        );
        return;
      }

      handleDownloadTaskProgress(kind, percent, payload.text || label, payload.task);

      // 直接导入整合包（按钮 / 拖放）：只更新右下角任务，不动任何页面进度条
      if (payload.import) return;

      // 模组下载 / 整合包安装只刷新模组页自己的进度条，避免占用其它页面的进度
      if (kind === 'mod' || kind === 'modpack' || (kind === 'done' && payload.task)) {
        if (activeTask === 'mod') applyStatus(modWidget, text, percent, kind !== 'done');
        else if (activeTask === 'mod-detail') {
          applyStatus(modDetailWidget, text, percent, kind !== 'done');
        }
        return;
      }

      var widget = launchWidget;
      if (activeTask === 'download') widget = downloadWidget;
      else if (activeTask === 'modal') widget = dlModalWidget;
      applyStatus(widget, text, percent, kind !== 'done');

      // 启动进度页同步显示准备阶段的进度
      if (activeTask === 'launch') {
        applyLaunchingStatus(text, percent);
      }
    }
  };

  // 拖入整合包：Python 侧解析 WM_DROPFILES 后回调这里
  var dropTaskId = '';
  window.mchl.modpackDrop = function (payload) {
    if (!payload) return;
    if (payload.started) {
      // 用后端给的 id 建任务，后续进度直接落到这一条上
      dropTaskId = createDownloadTask({
        id: payload.task,
        name: '整合包 ' + (payload.name || '')
      });
      toast('正在导入整合包：' + (payload.name || ''));
      return;
    }
    var taskId = payload.task || dropTaskId;
    dropTaskId = '';
    if (!payload.ok) {
      finishDownloadTask(taskId, false, payload.error || '导入失败');
      toast(payload.error || '导入失败');
      return;
    }
    finishDownloadTask(taskId, true, '已导入整合包：' + (payload.version || ''));
    refreshVersionList();
    loadVersionRoots(true);
    if (payload.entry) applyVersion(payload.entry);
    var extra = payload.merged ? '（合并 ' + payload.merged + ' 个公共文件）' : '';
    toast('已导入整合包：' + payload.version + extra);
  };

  var launchPollTimer = null;
  var launchStartedAt = 0;
  var cancelRequested = false;

  // 启动进度页
  var launchingTitle = $('#launchingTitle');
  var launchingVersionEl = $('#launchingVersion');
  var launchingBar = $('#launchingBar');
  var launchingTextEl = $('#launchingText');
  var launchingElapsedEl = $('#launchingElapsed');
  var launchingLogEl = $('#launchingLog');
  var btnCancelLaunch = $('#btnCancelLaunch');
  var btnBackFromLaunching = $('#btnBackFromLaunching');

  /* ---------- 启动日志查看 ---------- */
  var logModal = $('#logModal');
  var logMask = $('#logMask');
  var logCloseBtn = $('#logClose');
  var logViewerEl = $('#logViewer');
  var logPathEl = $('#logPath');
  var btnRefreshLog = $('#btnRefreshLog');
  var btnOpenLogFolder = $('#btnOpenLogFolder');
  var btnCopyLog = $('#btnCopyLog');
  var btnOpenLaunchLog = $('#btnOpenLaunchLog');
  var logRefreshTimer = null;

  function refreshLaunchLog() {
    return callApi('read_launch_log', 500).then(function (res) {
      if (!res) return;
      if (!res.ok) {
        if (logViewerEl) logViewerEl.textContent = res.error || '读取日志失败';
        return;
      }
      if (logViewerEl) {
        // 用户往上翻看历史时不要强行拉到底部
        var atBottom =
          logViewerEl.scrollHeight - logViewerEl.scrollTop - logViewerEl.clientHeight < 40;
        logViewerEl.textContent = res.text || '日志为空。';
        if (atBottom) logViewerEl.scrollTop = logViewerEl.scrollHeight;
      }
      if (logPathEl) {
        logPathEl.textContent = res.path || '';
        logPathEl.title = res.path || '';
      }
    });
  }

  function openLogModal() {
    if (!logModal) return;
    logModal.classList.add('is-open');
    logModal.setAttribute('aria-hidden', 'false');
    refreshLaunchLog();
    if (logRefreshTimer) window.clearInterval(logRefreshTimer);
    // 启动过程中日志会持续增长，打开时自动刷新
    logRefreshTimer = window.setInterval(refreshLaunchLog, 1500);
  }

  function closeLogModal() {
    if (!logModal) return;
    logModal.classList.remove('is-open');
    logModal.setAttribute('aria-hidden', 'true');
    if (logRefreshTimer) {
      window.clearInterval(logRefreshTimer);
      logRefreshTimer = null;
    }
  }

  if (logCloseBtn) logCloseBtn.addEventListener('click', closeLogModal);
  if (logMask) logMask.addEventListener('click', closeLogModal);
  if (btnRefreshLog) btnRefreshLog.addEventListener('click', refreshLaunchLog);
  if (btnOpenLaunchLog) btnOpenLaunchLog.addEventListener('click', openLogModal);
  if (btnCopyLog) {
    btnCopyLog.addEventListener('click', function () {
      var text = logViewerEl ? logViewerEl.textContent : '';
      if (!text) {
        toast('日志还没有内容');
        return;
      }
      copyText(text).then(function (ok) {
        if (!ok) {
          toast('复制失败，请手动选中日志复制');
          return;
        }
        toast('日志已复制到剪贴板');
        btnCopyLog.textContent = '已复制';
        btnCopyLog.disabled = true;
        window.setTimeout(function () {
          btnCopyLog.textContent = '复制日志';
          btnCopyLog.disabled = false;
        }, 1200);
      });
    });
  }
  if (btnOpenLogFolder) {
    btnOpenLogFolder.addEventListener('click', function () {
      callApi('open_logs_dir').then(function (ok) {
        if (!ok) toast('无法打开日志文件夹');
      });
    });
  }

  function applyLaunchingStatus(text, percent) {
    if (launchingTextEl) launchingTextEl.textContent = text || '';
    if (launchingBar && typeof percent === 'number' && percent >= 0) {
      launchingBar.style.width = Math.min(100, percent) + '%';
    }
    if (launchingElapsedEl && launchStartedAt) {
      var seconds = Math.max(0, Math.round((Date.now() - launchStartedAt) / 1000));
      launchingElapsedEl.textContent = seconds + ' 秒';
    }
  }

  function stopLaunchTracking() {
    if (launchPollTimer) {
      window.clearTimeout(launchPollTimer);
      launchPollTimer = null;
    }
    launching = false;
    if (btnLaunch) {
      btnLaunch.textContent = '启动游戏';
    }
    updateLaunchStateLabel();
    refreshLaunchAvailability();
  }

  function showLaunchResult(title, text, percent) {
    if (launchingTitle) launchingTitle.textContent = title;
    applyLaunchingStatus(text, percent);
    if (btnCancelLaunch) btnCancelLaunch.style.display = 'none';
  }

  function failLaunch(text, lines) {
    stopLaunchTracking();
    stopGameWatch();
    // 多开时可能有其他实例仍在运行，按真实状态刷新
    pollGameState();
    showLaunchResult('启动失败', text, 0);
    if (launchingLogEl) launchingLogEl.textContent = (lines || []).join('\n');
    setLaunchStatus(text, -1, false);
    toast('游戏启动失败，请查看日志');
  }

  function trackLaunchProgress() {
    var attempts = 0;

    function poll() {
      callApi('get_launch_progress').then(function (state) {
        if (cancelRequested) return;

        if (!state) {
          failLaunch('无法获取启动状态', []);
          return;
        }

        if (state.state === 'ready') {
          stopLaunchTracking();
          showLaunchResult('游戏已启动', state.text || '游戏已启动', 100);
          setLaunchStatus(state.text || '游戏已启动', 100, false);
          toast('游戏已启动');
          return;
        }

        if (state.state === 'exited') {
          var lines = state.tail || [];
          var last = lines.length ? lines[lines.length - 1] : '';
          failLaunch('启动失败：' + (last || '游戏进程已退出（' + state.code + '）'), lines);
          return;
        }

        attempts += 1;
        setLaunchStatus(state.text || '正在启动游戏…', state.percent || 30, true);
        applyLaunchingStatus(state.text || '正在启动游戏…', state.percent || 30);

        // 约 3 分钟后停止轮询，避免无限等待
        if (attempts > 240) {
          stopLaunchTracking();
          showLaunchResult('仍在启动中', '游戏仍在启动中，请查看游戏窗口或日志', 95);
          setLaunchStatus('游戏仍在启动中，请查看游戏窗口或日志', 95, false);
          return;
        }

        launchPollTimer = window.setTimeout(poll, 800);
      });
    }

    poll();
  }

  function cancelCurrentLaunch() {
    cancelRequested = true;
    callApi('cancel_launch').then(function (result) {
      if (result && result.ok) {
        stopLaunchTracking();
        showLaunchResult('已取消启动', '已取消启动', 0);
        setLaunchStatus('已取消启动', -1, false);
        toast('已取消启动');
        return;
      }
      toast((result && result.error) || '取消失败');
    });
  }

  /* ---------- 游戏进程：运行状态与强制结束 ---------- */
  var btnKillGame = $('#btnKillGame');
  var gameWatchTimer = null;
  var gameRunning = false;
  var gameRunningCount = 0;

  function setGameRunning(running, count) {
    gameRunning = !!running;
    gameRunningCount = gameRunning ? Math.max(1, Number(count) || 1) : 0;
    document.body.classList.toggle('is-game-running', gameRunning);
    if (btnKillGame) btnKillGame.classList.toggle('is-visible', gameRunning);
    updateLaunchStateLabel();
    refreshLaunchAvailability();
    redrawWidget('game-status');
  }

  function stopGameWatch() {
    if (gameWatchTimer) {
      window.clearTimeout(gameWatchTimer);
      gameWatchTimer = null;
    }
  }

  function pollGameState() {
    gameWatchTimer = null;
    callApi('get_game_state').then(function (state) {
      var running = !!(state && state.running);
      setGameRunning(running, state && state.count);
      if (!running) return;
      gameWatchTimer = window.setTimeout(pollGameState, 2000);
    });
  }

  function startGameWatch() {
    stopGameWatch();
    gameRunning = true;
    setGameRunning(true, Math.max(1, gameRunningCount + 1));
    gameWatchTimer = window.setTimeout(pollGameState, 1500);
  }

  function killGameProcess() {
    callApi('kill_game').then(function (result) {
      if (!result || !result.ok) {
        toast((result && result.error) || '结束进程失败');
        pollGameState();
        return;
      }
      cancelRequested = true;
      stopLaunchTracking();
      stopGameWatch();
      setGameRunning(false);
      if (currentView() === 'launching') switchView('launch');
      var killed = (result.killed || []).length;
      toast(killed > 1 ? '已强制结束 ' + killed + ' 个游戏进程' : '已强制结束游戏进程');
    });
  }

  if (btnKillGame) {
    btnKillGame.addEventListener('click', function () {
      if (!gameRunning) return;
      askConfirm(
        '强制结束所有游戏进程？未保存的游戏进度将会丢失。',
        killGameProcess,
        '结束进程'
      );
    });
  }

  function startLaunchFlow() {
    if (launching) return;

    if (isOfflineMode() && !offlineName()) {
      toast('离线登录需要先填写玩家名');
      if (offlineNameInput) offlineNameInput.focus();
      refreshLaunchAvailability();
      return;
    }

    if (loginMode === 'microsoft' && !msLoggedIn()) {
      toast('请先完成微软正版登录');
      startMsLogin();
      return;
    }

    if (loginMode === 'authlib' && !authlibLoggedIn()) {
      toast('请先完成第三方账号登录');
      focusAuthlibForm();
      return;
    }

    launching = true;
    cancelRequested = false;
    launchStartedAt = Date.now();
    updateLaunchStateLabel();
    recordLastLaunch();

    if (btnLaunch) {
      btnLaunch.disabled = true;
      btnLaunch.textContent = '启动中…';
    }
    if (launchingTitle) launchingTitle.textContent = '正在启动游戏';
    if (launchingVersionEl) {
      launchingVersionEl.textContent = selectedVersion ? selectedVersion.name : '—';
    }
    if (launchingLogEl) launchingLogEl.textContent = '';
    if (btnCancelLaunch) btnCancelLaunch.style.display = '';

    applyLaunchingStatus('正在检查版本与 Java…', 3);
    setLaunchStatus('正在检查版本与 Java…', -1, true);
    switchView('launching');

    persistOfflineName()
      .then(function () {
        // 把界面选择的登录方式交给后端：offline / microsoft / authlib
        return callApi('launch_game', loginMode);
      })
      .then(function (result) {
        if (!result) {
          failLaunch('启动器接口未就绪', []);
          return;
        }
        if (!result.ok) {
          failLaunch(result.error || '启动失败', []);
          return;
        }
        if (cancelRequested) {
          callApi('cancel_launch');
          stopLaunchTracking();
          showLaunchResult('已取消启动', '已取消启动', 0);
          return;
        }

        applyLaunchingStatus('正在启动游戏进程…', 30);
        startGameWatch();
        trackLaunchProgress();
      });
  }

  if (btnLaunch) {
    btnLaunch.addEventListener('click', startLaunchFlow);
  }

  if (btnCancelLaunch) {
    btnCancelLaunch.addEventListener('click', cancelCurrentLaunch);
  }

  if (btnBackFromLaunching) {
    btnBackFromLaunching.addEventListener('click', function () {
      switchView('launch');
    });
  }

  if (btnSelectVersion) {
    btnSelectVersion.addEventListener('click', openVersionsPage);
  }

  if (btnImportModpack) {
    btnImportModpack.addEventListener('click', importModpackFile);
  }

  if (btnVersionSettings) {
    btnVersionSettings.addEventListener('click', function () {
      if (!selectedVersion) {
        toast('请先选择一个版本');
        return;
      }
      openVersionPage(selectedVersion.name, versionRoot);
    });
  }

  if (btnOpenGameDir) {
    btnOpenGameDir.addEventListener('click', function () {
      if (!selectedVersion) {
        toast('请先选择一个版本');
        return;
      }
      callApi('open_version_dir', selectedVersion.name, versionRoot).then(function (ok) {
        if (ok === false) toast('无法打开版本目录');
      });
    });
  }

  /* ---------- 版本选择 ---------- */
  var versionListEl = $('#versionList');
  var rootListEl = $('#rootList');
  var currentVersionEl = $('#currentVersion');
  var cardVersionName = $('#cardVersionName');
  var cardVersionDesc = $('#cardVersionDesc');
  var cardVersionIcon = $('#cardVersionIcon');
  var selectedVersion = null;
  var versionRoots = [];
  var versionRoot = '';

  var CUBE_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M12 2.6 21 7.2v9.6L12 21.4 3 16.8V7.2z" fill="#63b73a"/>' +
    '<path d="M3 7.2 12 11.8v9.6L3 16.8z" fill="#8b5a2b"/>' +
    '<path d="M21 7.2 12 11.8v9.6l9-4.6z" fill="#6a4321"/>' +
    '<path d="M12 2.6 21 7.2 12 11.8 3 7.2z" fill="#7ecb4b"/>' +
    '</svg>';

  function fillIconBox(box, src) {
    if (!box) return;
    box.innerHTML = '';
    if (src) {
      var img = document.createElement('img');
      img.src = src;
      img.alt = '';
      img.draggable = false;
      box.appendChild(img);
    } else {
      box.innerHTML = CUBE_SVG;
    }
  }

  function applyVersion(entry) {
    selectedVersion = entry || null;

    var headline = '未选择版本';
    if (entry) {
      var parts = [];
      if (entry.mc_version) parts.push(entry.mc_version);
      if (entry.loader) parts.push(entry.loader);
      headline = parts.join(' · ') || entry.name;
    }

    fillIconBox(cardVersionIcon, entry && entry.icon ? entry.icon : '');
    fillIconBox(launchVersionIconEl, entry && entry.icon ? entry.icon : '');
    if (launchVersionNameEl) launchVersionNameEl.textContent = entry ? entry.name : '未选择版本';
    if (btnOpenGameDir) btnOpenGameDir.disabled = !entry;
    updateLaunchStateLabel();
    if (currentVersionEl) currentVersionEl.textContent = headline;
    if (cardVersionName) cardVersionName.textContent = entry ? entry.name : '未选择版本';
    if (cardVersionDesc) {
      cardVersionDesc.textContent = entry ? entry.info || '已就绪' : '点击“选择版本”开始配置';
    }
    redrawWidget('quick-launch');
  }

  var GEAR_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="3.2"/>' +
    '<path d="M12 3.4v2.2M12 18.4v2.2M3.4 12h2.2M18.4 12h2.2"/>' +
    '<path d="M6 6l1.6 1.6M16.4 16.4L18 18M18 6l-1.6 1.6M7.6 16.4L6 18"/>' +
    '</svg>';

  var TRASH_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4.6 7h14.8"/><path d="M9.6 7V5.3h4.8V7"/>' +
    '<path d="M6.6 7l.8 12.1a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5L17.4 7"/>' +
    '<path d="M10.4 11v5.4M13.6 11v5.4"/>' +
    '</svg>';

  var EXPORT_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 3.6v10.2"/><path d="M8.2 7.4 12 3.6l3.8 3.8"/>' +
    '<path d="M4.8 14.4v4.2a1.8 1.8 0 0 0 1.8 1.8h10.8a1.8 1.8 0 0 0 1.8-1.8v-4.2"/>' +
    '</svg>';

  function makeIconButton(svg, title, danger) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'vi-icon-btn' + (danger ? ' is-danger' : '');
    button.title = title;
    button.innerHTML = svg;
    // 阻止冒泡，避免同时触发“选择版本”
    button.addEventListener('click', function (event) {
      event.stopPropagation();
    });
    return button;
  }

  function renderVersions(versions) {
    if (!versionListEl) return;
    versionListEl.innerHTML = '';

    if (!versions.length) {
      var empty = document.createElement('div');
      empty.className = 'version-empty';
      empty.textContent = '未在 .minecraft/versions 中找到可用版本';
      var hint = document.createElement('div');
      hint.textContent = '可在“下载”页下载游戏，或把版本文件夹放到 .minecraft/versions 下';
      empty.appendChild(hint);
      versionListEl.appendChild(empty);
      return;
    }

    versions.forEach(function (entry) {
      var item = document.createElement('div');
      item.className = 'version-item';
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      if (selectedVersion && selectedVersion.name === entry.name) {
        item.classList.add('is-selected');
      }

      var icon = document.createElement('span');
      icon.className = 'vi-icon';
      fillIconBox(icon, entry.icon || '');

      var main = document.createElement('span');
      main.className = 'vi-main';
      var name = document.createElement('span');
      name.className = 'vi-name';
      name.textContent = entry.name;
      var desc = document.createElement('span');
      desc.className = 'vi-desc';
      desc.textContent = entry.info || entry.path;
      main.appendChild(name);
      main.appendChild(desc);

      var side = document.createElement('span');
      side.className = 'vi-side';
      if (entry.loader) {
        var tag = document.createElement('span');
        tag.className = 'vi-tag';
        tag.textContent = entry.loader;
        side.appendChild(tag);
      }
      if (entry.release_time) {
        var time = document.createElement('span');
        time.className = 'vi-time';
        time.textContent = entry.release_time;
        side.appendChild(time);
      }

      var actions = document.createElement('span');
      actions.className = 'vi-actions';

      var btnSettings = makeIconButton(GEAR_SVG, '版本设置');
      btnSettings.addEventListener('click', function () {
        openVersionPage(entry.name, versionRoot);
      });

      var btnDelete = makeIconButton(TRASH_SVG, '删除版本', true);
      btnDelete.addEventListener('click', function () {
        askConfirm(
          '确定要删除版本“' + entry.name + '”吗？该操作会删除整个版本目录，且不可恢复。',
          function () {
            callApi('delete_version', entry.name, versionRoot).then(function (result) {
              if (!result) return;
              if (result.ok) {
                toast('已删除 ' + entry.name);
                if (selectedVersion && selectedVersion.name === entry.name) {
                  applyVersion(null);
                }
                refreshVersionList();
                loadVersionRoots(true);
              } else {
                toast(result.error || '删除失败');
              }
            });
          },
          '删除'
        );
      });

      var btnExport = makeIconButton(EXPORT_SVG, '导出整合包');
      btnExport.addEventListener('click', function () {
        exportModpack(entry);
      });

      actions.appendChild(btnSettings);
      actions.appendChild(btnExport);
      actions.appendChild(btnDelete);

      item.appendChild(icon);
      item.appendChild(main);
      item.appendChild(side);
      item.appendChild(actions);
      item.addEventListener('click', function () {
        chooseVersion(entry);
      });
      item.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          chooseVersion(entry);
        }
      });

      versionListEl.appendChild(item);
    });
  }

  function refreshVersionList() {
    return callApi('list_versions', versionRoot).then(function (versions) {
      renderVersions(versions || []);
    });
  }

  // 导入整合包：支持 MCHL / PCL 导出的 zip 与 .mrpack
  function importModpackFile() {
    if (!btnImportModpack || btnImportModpack.disabled) return;
    var taskId = createDownloadTask({ name: '整合包导入' });
    btnImportModpack.disabled = true;
    btnImportModpack.textContent = '导入中…';
    toast('正在导入整合包，请稍候…');
    callApi('import_modpack', taskId).then(function (result) {
      btnImportModpack.disabled = false;
      btnImportModpack.textContent = '导入整合包';
      if (!result) {
        finishDownloadTask(taskId, false, '启动器接口未就绪');
        toast('启动器接口未就绪');
        return;
      }
      if (result.cancelled) {
        removeDownloadTask(taskId);
        return;
      }
      if (!result.ok) {
        finishDownloadTask(taskId, false, result.error || '导入失败');
        toast(result.error || '导入失败');
        return;
      }
      finishDownloadTask(taskId, true, '已导入整合包：' + (result.version || ''));
      refreshVersionList();
      loadVersionRoots(true);
      if (result.entry) applyVersion(result.entry);
      var extra = result.merged ? '（合并 ' + result.merged + ' 个公共文件）' : '';
      toast('已导入整合包：' + result.version + extra);
    });
  }

  // 导出整合包：选路径后打包成 zip
  function exportModpack(entry) {
    if (!entry) return;
    toast('正在打包整合包，请稍候…');
    callApi('export_modpack', entry.name, versionRoot).then(function (result) {
      if (!result) {
        toast('启动器接口未就绪');
        return;
      }
      if (result.cancelled) return;
      if (!result.ok) {
        toast(result.error || '导出失败');
        return;
      }
      var fileName = String(result.path || '').split(/[\\/]/).pop();
      toast('已导出整合包：' + fileName);
    });
  }

  function renderRoots() {
    if (!rootListEl) return;
    rootListEl.innerHTML = '';

    if (!versionRoots.length) {
      var empty = document.createElement('div');
      empty.className = 'version-empty';
      empty.textContent = '暂无游戏文件夹';
      rootListEl.appendChild(empty);
      return;
    }

    versionRoots.forEach(function (root) {
      var item = document.createElement('div');
      item.className = 'root-item';
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      if (root.path === versionRoot) item.classList.add('is-active');

      var name = document.createElement('span');
      name.className = 'root-name';
      name.textContent = root.is_default ? '启动器目录' : (root.name || '游戏文件夹');

      var path = document.createElement('span');
      path.className = 'root-path';
      path.textContent = root.path;
      path.title = root.path;

      var meta = document.createElement('span');
      meta.className = 'root-meta';
      if (!root.exists) {
        meta.textContent = '目录不存在';
      } else {
        meta.innerHTML = '共 <strong>' + root.version_count + '</strong> 个版本';
      }

      item.appendChild(name);
      item.appendChild(path);
      item.appendChild(meta);

      function selectRoot() {
        if (versionRoot === root.path) return;
        versionRoot = root.path;
        renderRoots();
        if (versionListEl) {
          versionListEl.innerHTML = '<div class="version-empty">正在读取版本列表…</div>';
        }
        refreshVersionList();
      }

      item.addEventListener('click', selectRoot);
      item.addEventListener('keydown', function (event) {
        if (event.target !== item) return;
        if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
        event.preventDefault();
        selectRoot();
      });

      if (!root.is_default) {
        var removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'root-remove';
        removeBtn.textContent = '×';
        removeBtn.title = '从列表中移除（不会删除磁盘上的文件）';
        removeBtn.setAttribute('aria-label', '从列表中移除');
        removeBtn.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          askConfirm(
            '从列表中移除「' + (root.name || root.path) + '」？\n仅移出列表，不会删除磁盘上的任何文件。',
            function () {
              callApi('remove_version_root', root.path).then(function (result) {
                if (!result) {
                  toast('启动器接口未就绪');
                  return;
                }
                if (!result.ok) {
                  toast(result.error || '移除失败');
                  return;
                }
                versionRoots = result.roots || [];
                var stillExists = versionRoots.some(function (item) {
                  return item.path === versionRoot;
                });
                if (!stillExists) {
                  versionRoot = versionRoots.length ? versionRoots[0].path : '';
                }
                renderRoots();
                refreshVersionList();
                toast('已从列表移除，磁盘文件未改动');
              });
            },
            '移除'
          );
        });
        item.appendChild(removeBtn);
      }

      rootListEl.appendChild(item);
    });
  }

  function loadVersionRoots(keepSelection) {
    return callApi('list_version_roots').then(function (roots) {
      versionRoots = roots || [];

      var exists = versionRoots.some(function (root) {
        return root.path === versionRoot;
      });
      if (!keepSelection || !exists) {
        versionRoot = versionRoots.length ? versionRoots[0].path : '';
      }

      renderRoots();
      return refreshVersionList();
    });
  }

  function openVersionsPage() {
    var from = currentView();
    versionReturnView =
      from === 'download' || from === 'settings' || from === 'home' ? from : 'launch';
    switchView('versions');
    if (versionListEl) {
      versionListEl.innerHTML = '<div class="version-empty">正在读取版本列表…</div>';
    }
    loadVersionRoots(true);
  }

  function chooseVersion(entry) {
    callApi('select_version', entry.name, versionRoot).then(function (saved) {
      applyVersion(saved || entry);
      switchView('launch');
      toast('已选择版本 ' + entry.name);
    });
  }

  var btnBackFromVersions = $('#btnBackFromVersions');
  var btnAddVersionRoot = $('#btnAddVersionRoot');

  if (btnBackFromVersions) {
    btnBackFromVersions.addEventListener('click', function () {
      switchView(versionReturnView || 'launch');
    });
  }

  if (btnAddVersionRoot) {
    btnAddVersionRoot.addEventListener('click', function () {
      callApi('pick_version_root').then(function (result) {
        if (!result || result.cancelled) return;
        if (!result.ok) {
          toast(result.error || '添加失败');
          return;
        }
        versionRoots = result.roots || versionRoots;
        versionRoot = result.path || versionRoot;
        renderRoots();
        refreshVersionList();
        loadVersionRoots(true);
        toast('已添加游戏文件夹');
      });
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    backToDownloadPage();
    closeConfirm();
    closeLogModal();
  });

  /* ---------- 二次确认 ---------- */
  var confirmModal = $('#confirmModal');
  var confirmMask = $('#confirmMask');
  var confirmTextEl = $('#confirmText');
  var confirmInputRow = $('#confirmInputRow');
  var confirmInputEl = $('#confirmInput');
  var btnConfirmOk = $('#btnConfirmOk');
  var btnConfirmCancel = $('#btnConfirmCancel');
  var confirmAction = null;
  var confirmCancelAction = null;
  var confirmInputMode = false;

  // defaultValue 传字符串时显示输入框，确认后把输入内容回传给 onOk
  function askConfirm(text, onOk, okLabel, defaultValue, onCancel) {
    if (!confirmModal) {
      if (window.confirm(text)) onOk(defaultValue || '');
      return;
    }
    var withInput = typeof defaultValue === 'string';
    confirmInputMode = withInput;
    if (confirmTextEl) confirmTextEl.textContent = text;
    if (btnConfirmOk) btnConfirmOk.textContent = okLabel || '确认';
    if (confirmInputRow) confirmInputRow.classList.toggle('is-collapsed', !withInput);
    if (confirmInputEl) confirmInputEl.value = withInput ? defaultValue : '';
    confirmAction = typeof onOk === 'function' ? onOk : null;
    confirmCancelAction = typeof onCancel === 'function' ? onCancel : null;
    confirmModal.classList.add('is-open');
    confirmModal.setAttribute('aria-hidden', 'false');
    if (withInput && confirmInputEl) {
      window.setTimeout(function () {
        confirmInputEl.focus();
        confirmInputEl.select();
      }, 60);
    }
  }

  function closeConfirm(silent) {
    if (!confirmModal) return;
    confirmModal.classList.remove('is-open');
    confirmModal.setAttribute('aria-hidden', 'true');
    confirmAction = null;
    confirmInputMode = false;
    if (confirmInputRow) confirmInputRow.classList.add('is-collapsed');
    var cancel = silent ? null : confirmCancelAction;
    confirmCancelAction = null;
    if (cancel) cancel();
  }

  if (btnConfirmCancel) {
    btnConfirmCancel.addEventListener('click', function () {
      closeConfirm();
    });
  }
  if (confirmMask) {
    confirmMask.addEventListener('click', function () {
      closeConfirm();
    });
  }
  if (btnConfirmOk) {
    btnConfirmOk.addEventListener('click', function () {
      var action = confirmAction;
      var value = confirmInputMode && confirmInputEl ? confirmInputEl.value.trim() : '';
      closeConfirm(true);
      if (action) action(value);
    });
  }
  if (confirmInputEl) {
    confirmInputEl.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      if (btnConfirmOk) btnConfirmOk.click();
    });
  }

  /* ---------- 版本设置（独立页面） ---------- */
  var vsNameInput = $('#vsNameInput');
  var vsDirHint = $('#vsDirHint');
  var vsModsBlock = $('#vsModsBlock');
  var vsModsHint = $('#vsModsHint');
  var vsMcVersionEl = $('#vsMcVersion');
  var vsLoaderEl = $('#vsLoader');
  var versionPageTitle = $('#versionPageTitle');
  var versionPageSubtitle = $('#versionPageSubtitle');
  var modDropZone = $('#modDropZone');
  var modListEl = $('#modList');
  var vsJavaSelect = $('#vsJavaSelect');
  var vsCheckFiles = $('#vsCheckFiles');
  var vsMemory = $('#vsMemory');
  var vsExtraJvm = $('#vsExtraJvm');
  var vsExtraGame = $('#vsExtraGame');
  var vsSettingsHint = $('#vsSettingsHint');
  var vsIconPreview = $('#vsIconPreview');
  var vsIconOptions = $('#vsIconOptions');
  var iconPresets = null;
  var vsJavaCandidates = [];
  var vsJavaLoaded = false;
  var vsStatusWidget = {
    el: $('#vsStatus'),
    text: $('#vsStatusText'),
    bar: $('#vsProgressBar')
  };
  var vsBusy = false;
  var vsEntry = null;
  var vsRoot = '';
  var versionReturnView = 'launch';

  /* 右侧板块导航：一次只显示一个板块 */
  var vsNav = $('#vsNav');
  var vsCardsEl = document.querySelector('.view-version .cards');
  var vsNavItems = vsNav ? $$('.vs-nav-item', vsNav) : [];
  var vsSections = ['vsSectionBase', 'vsSectionLaunch', 'vsModsBlock'];
  var vsModsAvailable = false;

  function setActiveVsNav(target) {
    vsNavItems.forEach(function (item) {
      item.classList.toggle('is-active', item.dataset.target === target);
    });
  }

  function showVsSection(target) {
    if (vsSections.indexOf(target) < 0) target = 'vsSectionBase';
    if (target === 'vsModsBlock' && !vsModsAvailable) target = 'vsSectionBase';

    vsSections.forEach(function (id) {
      var section = document.getElementById(id);
      if (!section) return;
      section.style.display = '';
      section.classList.toggle('is-collapsed', id !== target);
    });
    setActiveVsNav(target);
    if (vsCardsEl) vsCardsEl.scrollTop = 0;
  }

  if (vsNav) {
    vsNavItems.forEach(function (item) {
      item.addEventListener('click', function () {
        showVsSection(item.dataset.target);
      });
    });
  }

  function currentView() {
    var active = document.querySelector('.view.is-active');
    return active ? active.dataset.view : '';
  }

  function setVsStatus(text, percent, busy) {
    applyStatus(vsStatusWidget, text, percent, busy);
  }

  // 记住展开详情的模组，重新渲染后保持原状
  var vsExpandedMods = {};

  function buildModAction(label, danger, onClick) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'mod-act' + (danger ? ' is-danger' : '');
    button.textContent = label;
    button.addEventListener('click', function (event) {
      event.stopPropagation();
      onClick();
    });
    return button;
  }

  function buildModDetail(mod) {
    var box = document.createElement('div');
    box.className = 'mod-detail';

    function addField(name, value, always) {
      if (!value && !always) return;
      var row = document.createElement('div');
      row.className = 'mod-detail-row';
      var key = document.createElement('span');
      key.className = 'mod-detail-key';
      key.textContent = name;
      var val = document.createElement('span');
      val.className = 'mod-detail-val';
      val.textContent = value || '—';
      row.appendChild(key);
      row.appendChild(val);
      box.appendChild(row);
    }

    addField('模组名', mod.name);
    addField('Mod ID', mod.modid);
    addField('版本', mod.version);
    addField('作者', mod.authors);
    addField('加载器', mod.loader);
    addField('文件', mod.file);
    addField('大小', formatSize(mod.size));
    addField('依赖', (mod.dependencies || []).join('、'));
    addField('简介', mod.description, true);
    return box;
  }

  function modLetter(mod) {
    var text = String((mod && (mod.name || mod.file)) || '?').trim();
    return (text.charAt(0) || '?').toUpperCase();
  }

  // 模组图标：jar 里带图标就显示，否则用首字母占位
  function buildModIcon(mod) {
    var box = document.createElement('div');
    box.className = 'mod-item-icon';
    if (mod && mod.icon) {
      var img = document.createElement('img');
      img.src = mod.icon;
      img.alt = '';
      img.addEventListener('error', function () {
        if (img.parentNode) img.parentNode.removeChild(img);
        box.classList.add('is-letter');
        box.textContent = modLetter(mod);
      });
      box.appendChild(img);
    } else {
      box.classList.add('is-letter');
      box.textContent = modLetter(mod);
    }
    return box;
  }

  function renderModList(mods) {
    if (!modListEl) return;
    modListEl.innerHTML = '';

    if (!mods || !mods.length) {
      var empty = document.createElement('div');
      empty.className = 'mod-empty';
      empty.textContent = '暂无 mod';
      modListEl.appendChild(empty);
      return;
    }

    mods.forEach(function (mod) {
      var enabled = mod.enabled !== false;
      var card = document.createElement('div');
      card.className = 'mod-entry' + (enabled ? '' : ' is-disabled');

      var row = document.createElement('div');
      row.className = 'mod-item';

      row.appendChild(buildModIcon(mod));

      var main = document.createElement('div');
      main.className = 'mod-item-main';
      var name = document.createElement('span');
      name.className = 'mod-item-name';
      name.textContent = mod.name || mod.file;
      name.title = mod.file;
      var meta = document.createElement('span');
      meta.className = 'mod-item-meta';
      meta.textContent = [
        mod.version ? 'v' + mod.version : '',
        mod.loader,
        formatSize(mod.size),
        mod.file
      ]
        .filter(Boolean)
        .join(' · ');
      meta.title = mod.file;
      main.appendChild(name);
      main.appendChild(meta);

      var actions = document.createElement('div');
      actions.className = 'mod-item-actions';

      var state = document.createElement('span');
      state.className = 'mod-state' + (enabled ? '' : ' is-off');
      state.textContent = enabled ? '已启用' : '已禁用';
      actions.appendChild(state);

      var detailBox = buildModDetail(mod);
      var expanded = !!vsExpandedMods[mod.file];
      detailBox.hidden = !expanded;

      var infoButton = buildModAction(expanded ? '收起' : '详情', false, function () {
        var open = detailBox.hidden;
        detailBox.hidden = !open;
        vsExpandedMods[mod.file] = open;
        infoButton.textContent = open ? '收起' : '详情';
      });
      actions.appendChild(infoButton);

      actions.appendChild(
        buildModAction(enabled ? '禁用' : '启用', false, function () {
          setModEnabled(mod, !enabled);
        })
      );
      actions.appendChild(
        buildModAction('删除', true, function () {
          deleteVersionMod(mod);
        })
      );

      row.appendChild(main);
      row.appendChild(actions);
      card.appendChild(row);
      card.appendChild(detailBox);
      modListEl.appendChild(card);
    });
  }

  // 重新渲染并保持滚动位置
  function refreshModList(mods) {
    var top = modListEl ? modListEl.scrollTop : 0;
    renderModList(mods);
    if (modListEl) modListEl.scrollTop = top;
  }

  function setModEnabled(mod, enabled) {
    if (!vsEntry) return;
    setVsStatus(enabled ? '正在启用模组…' : '正在禁用模组…', -1, true);
    callApi('set_mod_enabled', vsEntry.name, vsRoot, mod.file, enabled).then(function (result) {
      setVsStatus('', -1, false);
      if (!result) {
        toast('启动器接口未就绪');
        return;
      }
      if (!result.ok) {
        toast(result.error || '操作失败');
        return;
      }
      refreshModList(result.mods || []);
      toast((enabled ? '已启用 ' : '已禁用 ') + (mod.name || mod.file));
    });
  }

  function deleteVersionMod(mod) {
    if (!vsEntry) return;
    var label = mod.name || mod.file;
    askConfirm('确定删除模组「' + label + '」？文件会从 mods 目录中移除，无法恢复。', function () {
      setVsStatus('正在删除模组…', -1, true);
      callApi('delete_version_mod', vsEntry.name, vsRoot, mod.file).then(function (result) {
        setVsStatus('', -1, false);
        if (!result) {
          toast('启动器接口未就绪');
          return;
        }
        if (!result.ok) {
          toast(result.error || '删除失败');
          return;
        }
        delete vsExpandedMods[mod.file];
        refreshModList(result.mods || []);
        toast('已删除 ' + label);
      });
    }, '删除');
  }

  function javaOptionLabel(item) {
    var suffix = item.version ? ' ' + item.version : '';
    return 'Java ' + (item.major || '?') + suffix + ' · ' + item.path;
  }

  function renderJavaOptions(selectedPath) {
    if (!vsJavaSelect) return;
    vsJavaSelect.innerHTML = '';

    var auto = document.createElement('option');
    auto.value = '';
    auto.textContent = '自动选择（推荐）';
    vsJavaSelect.appendChild(auto);

    var found = false;
    vsJavaCandidates.forEach(function (item) {
      var option = document.createElement('option');
      option.value = item.path;
      option.textContent = javaOptionLabel(item);
      vsJavaSelect.appendChild(option);
      if (selectedPath && item.path === selectedPath) found = true;
    });

    if (selectedPath && !found) {
      var custom = document.createElement('option');
      custom.value = selectedPath;
      custom.textContent = '自定义 · ' + selectedPath;
      vsJavaSelect.appendChild(custom);
    }

    vsJavaSelect.value = selectedPath || '';
  }

  function loadJavaCandidates() {
    if (vsJavaLoaded) return Promise.resolve(vsJavaCandidates);
    return callApi('get_java_state').then(function (state) {
      vsJavaLoaded = true;
      vsJavaCandidates = (state && state.candidates) || [];
      return vsJavaCandidates;
    });
  }

  function loadIconPresets() {
    if (iconPresets) return Promise.resolve(iconPresets);
    return callApi('get_icon_presets').then(function (presets) {
      iconPresets = presets || {};
      var nodes = document.querySelectorAll('[data-icon-preset]');
      Array.prototype.forEach.call(nodes, function (img) {
        var src = iconPresets[img.getAttribute('data-icon-preset')];
        if (src) img.src = src;
      });
      return iconPresets;
    });
  }

  function applyVersionIcon(detail) {
    var entry = detail || {};
    fillIconBox(vsIconPreview, entry.icon || '');
    var key = entry.icon_key || 'auto';
    var buttons = document.querySelectorAll('#vsIconOptions [data-icon]');
    Array.prototype.forEach.call(buttons, function (button) {
      button.classList.toggle('is-active', button.getAttribute('data-icon') === key);
    });
    var uploadBtn = $('#btnUploadVersionIcon');
    if (uploadBtn) uploadBtn.classList.toggle('is-active', key === 'custom');
  }

  function updateVersionIcon(result) {
    if (!result) return;
    if (!result.ok) {
      toast(result.error || '设置图标失败');
      return;
    }
    var entry = result.entry;
    if (!entry) return;
    if (vsEntry) {
      vsEntry.icon = entry.icon;
      vsEntry.icon_key = entry.icon_key;
    }
    applyVersionIcon(vsEntry);
    if (selectedVersion && selectedVersion.name === entry.name) {
      applyVersion(entry);
    }
    refreshVersionList();
    toast('版本图标已更新');
  }

  function setVersionIcon(value) {
    if (!vsEntry) return;
    callApi('update_version_settings', vsEntry.name, { icon: value }, vsRoot).then(
      updateVersionIcon
    );
  }

  function uploadVersionIcon() {
    if (!vsEntry) return;
    callApi('pick_version_icon', vsEntry.name, vsRoot).then(function (result) {
      if (result && result.cancelled) return;
      updateVersionIcon(result);
    });
  }

  function applyVersionSettings(settings) {
    var config = settings || {};
    renderJavaOptions(config.java_path || '');
    if (vsCheckFiles) vsCheckFiles.checked = config.check_files !== false;
    if (vsMemory) vsMemory.value = config.memory_mb ? String(config.memory_mb) : '';
    if (vsExtraJvm) vsExtraJvm.value = config.extra_jvm || '';
    if (vsExtraGame) vsExtraGame.value = config.extra_game || '';
    if (vsSettingsHint) vsSettingsHint.textContent = '';
  }

  function openVersionPage(name, root) {
    var from = currentView();
    versionReturnView = from && from !== 'version' ? from : 'versions';
    vsRoot = root || '';
    switchView('version');

    vsEntry = null;
    vsExpandedMods = {};
    if (modListEl) modListEl.innerHTML = '';
    showVsSection('vsSectionBase');
    if (versionPageTitle) versionPageTitle.textContent = name;
    if (versionPageSubtitle) versionPageSubtitle.textContent = '正在读取版本信息…';
    if (vsNameInput) vsNameInput.value = name;
    if (vsMcVersionEl) vsMcVersionEl.textContent = '—';
    if (vsLoaderEl) vsLoaderEl.textContent = '—';
    if (vsDirHint) {
      vsDirHint.textContent = '正在读取…';
      vsDirHint.title = '';
    }
    applyVersionSettings({});
    applyVersionIcon({});
    setVsStatus('正在读取版本信息…', -1, true);

    Promise.all([
      callApi('get_version_detail', name, vsRoot),
      loadJavaCandidates(),
      loadIconPresets()
    ]).then(function (results) {
      var detail = results[0];
      if (!detail) {
        if (versionPageSubtitle) versionPageSubtitle.textContent = '版本不存在或已被删除';
        setVsStatus('版本不存在或已被删除', -1, false);
        return;
      }
      vsEntry = detail;
      if (vsNameInput) vsNameInput.value = detail.name || name;
      if (versionPageTitle) versionPageTitle.textContent = detail.name || name;
      if (versionPageSubtitle) {
        versionPageSubtitle.textContent = detail.info || detail.path;
      }
      if (vsMcVersionEl) vsMcVersionEl.textContent = detail.mc_version || '—';
      if (vsLoaderEl) vsLoaderEl.textContent = detail.loader || '原版';
      if (vsDirHint) {
        vsDirHint.textContent = detail.path;
        vsDirHint.title = detail.path;
      }
      vsModsAvailable = !!detail.can_mod;
      var modsNavItem = vsNav
        ? vsNav.querySelector('[data-target="vsModsBlock"]')
        : null;
      if (modsNavItem) {
        modsNavItem.classList.toggle('is-collapsed', !detail.can_mod);
      }
      showVsSection('vsSectionBase');
      if (vsModsHint) {
        vsModsHint.textContent = detail.can_mod
          ? '把 .jar 拖到下方区域，或点击“选择文件”添加'
          : '该版本未安装模组加载器，无法添加 mod';
      }
      applyVersionSettings(detail.settings);
      applyVersionIcon(detail);
      renderModList(detail.mods || []);
      setVsStatus('', -1, false);
    });
  }

  function backFromVersionPage() {
    if (vsBusy) return;
    setVsStatus('', -1, false);
    if (versionReturnView === 'versions') {
      openVersionsPage();
      return;
    }
    switchView(versionReturnView || 'launch');
  }

  function saveVersionName() {
    if (!vsEntry) return;
    var oldName = vsEntry.name;
    var newName = ((vsNameInput && vsNameInput.value) || '').trim();
    if (!newName) {
      toast('版本名不能为空');
      return;
    }
    if (newName === oldName) return;

    vsBusy = true;
    callApi('rename_version', oldName, newName, vsRoot).then(function (result) {
      vsBusy = false;
      if (!result) return;
      if (!result.ok) {
        toast(result.error || '重命名失败');
        return;
      }

      vsEntry = result.entry || vsEntry;
      if (vsDirHint && vsEntry) {
        vsDirHint.textContent = vsEntry.path;
        vsDirHint.title = vsEntry.path;
      }
      if (versionPageTitle && vsEntry) versionPageTitle.textContent = vsEntry.name;
      if (versionPageSubtitle && vsEntry) {
        versionPageSubtitle.textContent = vsEntry.info || vsEntry.path;
      }
      toast('已重命名为 ' + newName);
      if (selectedVersion && selectedVersion.name === oldName && result.entry) {
        applyVersion(result.entry);
      }
      refreshVersionList();
    });
  }

  function saveVersionSettings() {
    if (!vsEntry) return;

    var payload = {
      java_path: vsJavaSelect ? vsJavaSelect.value : '',
      check_files: !!(vsCheckFiles && vsCheckFiles.checked),
      memory_mb: vsMemory && vsMemory.value ? Number(vsMemory.value) : 0,
      extra_jvm: vsExtraJvm ? vsExtraJvm.value : '',
      extra_game: vsExtraGame ? vsExtraGame.value : ''
    };

    var button = $('#btnSaveVersionSettings');
    if (button) button.disabled = true;
    if (vsSettingsHint) vsSettingsHint.textContent = '正在保存…';

    callApi('update_version_settings', vsEntry.name, payload, vsRoot).then(function (result) {
      if (button) button.disabled = false;
      if (!result) return;
      if (!result.ok) {
        if (vsSettingsHint) vsSettingsHint.textContent = result.error || '保存失败';
        toast(result.error || '保存失败');
        return;
      }
      if (vsEntry) vsEntry.settings = result.settings || payload;
      if (vsSettingsHint) vsSettingsHint.textContent = '已保存';
      toast('启动设置已保存');
    });
  }

  function browseJava() {
    callApi('pick_java_file', false).then(function (result) {
      if (!result || result.cancelled) return;
      if (!result.ok) {
        toast(result.error || '选择 Java 失败');
        return;
      }
      var info = result.java;
      var exists = false;
      vsJavaCandidates.forEach(function (item) {
        if (item.path === info.path) exists = true;
      });
      if (!exists) vsJavaCandidates.unshift(info);
      renderJavaOptions(info.path);
      toast('已选择 Java ' + info.major);
    });
  }

  function applyModResult(result) {
    if (!result) return;
    if (result.cancelled) return;
    if (!result.ok) {
      toast(result.error || '添加失败');
      return;
    }
    refreshModList(result.mods || []);
    if (result.added && result.added.length) {
      toast('已添加 ' + result.added.length + ' 个 mod');
    } else if (result.skipped && result.skipped.length) {
      toast('未添加：文件重名或不是 .jar');
    }
  }

  function pickMods() {
    if (!vsEntry) return;
    setVsStatus('正在添加 mod…', -1, true);
    callApi('pick_mod_files', vsEntry.name, vsRoot).then(function (result) {
      setVsStatus('', -1, false);
      applyModResult(result);
    });
  }

  var btnRenameVersion = $('#btnRenameVersion');
  var btnOpenVersionDir = $('#btnOpenVersionDir');
  var btnPickMods = $('#btnPickMods');
  var btnBackFromVersion = $('#btnBackFromVersion');

  if (btnRenameVersion) btnRenameVersion.addEventListener('click', saveVersionName);

  if (vsNameInput) {
    vsNameInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        saveVersionName();
      }
    });
  }

  if (btnOpenVersionDir) {
    btnOpenVersionDir.addEventListener('click', function () {
      if (!vsEntry) return;
      callApi('open_version_dir', vsEntry.name, vsRoot).then(function (ok) {
        if (ok === false) toast('无法打开版本目录');
      });
    });
  }

  if (btnPickMods) btnPickMods.addEventListener('click', pickMods);

  var btnSaveVersionSettings = $('#btnSaveVersionSettings');
  var btnBrowseJava = $('#btnBrowseJava');

  if (btnSaveVersionSettings) {
    btnSaveVersionSettings.addEventListener('click', saveVersionSettings);
  }
  if (btnBrowseJava) btnBrowseJava.addEventListener('click', browseJava);

  if (vsIconOptions) {
    vsIconOptions.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-icon]');
      if (!button) return;
      var key = button.getAttribute('data-icon');
      setVersionIcon(key === 'auto' ? '' : key);
    });
  }

  var btnUploadVersionIcon = $('#btnUploadVersionIcon');
  if (btnUploadVersionIcon) {
    btnUploadVersionIcon.addEventListener('click', uploadVersionIcon);
  }

  if (btnBackFromVersion) {
    btnBackFromVersion.addEventListener('click', backFromVersionPage);
  }

  if (modDropZone) {
    ['dragenter', 'dragover'].forEach(function (type) {
      modDropZone.addEventListener(type, function (event) {
        event.preventDefault();
        modDropZone.classList.add('is-over');
      });
    });

    ['dragleave', 'drop'].forEach(function (type) {
      modDropZone.addEventListener(type, function (event) {
        event.preventDefault();
        modDropZone.classList.remove('is-over');
      });
    });

    modDropZone.addEventListener('drop', function (event) {
      if (!vsEntry) return;
      var files = event.dataTransfer && event.dataTransfer.files;
      if (!files || !files.length) return;

      var paths = [];
      for (var index = 0; index < files.length; index += 1) {
        // WebView2 通常不暴露真实路径，能拿到时直接复制
        if (files[index].path) paths.push(files[index].path);
      }

      if (paths.length) {
        setVsStatus('正在添加 mod…', -1, true);
        callApi('add_mod_files', vsEntry.name, paths, vsRoot).then(function (result) {
          setVsStatus('', -1, false);
          applyModResult(result);
        });
      } else {
        toast('当前环境无法读取拖拽路径，请在弹窗中选择文件');
        pickMods();
      }
    });
  }

  // 阻止文件被拖到页面其他位置时触发浏览器打开
  document.addEventListener('dragover', function (event) {
    event.preventDefault();
  });
  document.addEventListener('drop', function (event) {
    event.preventDefault();
  });

  /* ---------- 设置页交互 ---------- */
  var memoryRange = $('#memoryRange');
  var memoryValue = $('#memoryValue');
  var memoryAutoSwitch = $('#memoryAutoSwitch');
  var memoryAutoHint = $('#memoryAutoHint');

  function memoryAutoEnabled() {
    return !!(memoryAutoSwitch && memoryAutoSwitch.checked);
  }

  function syncMemoryLabel() {
    if (!memoryRange || !memoryValue) return;
    memoryValue.textContent =
      formatMemory(Number(memoryRange.value)) + (memoryAutoEnabled() ? '（自动）' : '');
  }

  var memoryAutoState = false;

  function applyMemorySettings(settings) {
    if (!settings) return;

    var auto = !!settings.memory_auto;
    memoryAutoState = auto;
    var total = Number(settings.memory_total_mb) || 0;
    var available = Number(settings.memory_available_mb) || 0;

    if (memoryAutoSwitch) memoryAutoSwitch.checked = auto;
    if (memoryRange) {
      if (settings.memory_mb) memoryRange.value = settings.memory_mb;
      memoryRange.disabled = auto;
    }
    syncMemoryLabel();

    if (memoryAutoHint) {
      if (auto) {
        var parts = [];
        if (total) parts.push('物理内存 ' + formatMemory(total));
        if (available) parts.push('当前可用 ' + formatMemory(available));
        parts.push('自动分配 ' + formatMemory(settings.memory_mb));
        memoryAutoHint.textContent = parts.join(' · ');
      } else if (total) {
        memoryAutoHint.textContent = '物理内存 ' + formatMemory(total) + '，可手动调整上限';
      } else {
        memoryAutoHint.textContent = '根据当前可用内存实时分配上限';
      }
    }

    applyLaunchInfo(settings);
  }

  var memorySaveTimer = null;

  if (memoryRange && memoryValue) {
    memoryRange.addEventListener('input', function () {
      syncMemoryLabel();
      clearTimeout(memorySaveTimer);
      memorySaveTimer = setTimeout(function () {
        callApi('set_settings', { memory_mb: Number(memoryRange.value) }).then(function (settings) {
          applyLaunchInfo(settings);
        });
      }, 400);
    });
    syncMemoryLabel();
  }

  if (memoryAutoSwitch) {
    memoryAutoSwitch.addEventListener('change', function () {
      var auto = memoryAutoSwitch.checked;
      callApi('set_settings', { memory_auto: auto }).then(function (settings) {
        applyMemorySettings(settings);
        toast(auto ? '已开启自动分配内存' : '已关闭自动分配内存，可手动调整');
      });
    });
  }

  // 自动分配开启时，跟随系统内存占用实时刷新
  window.setInterval(function () {
    if (!memoryAutoState) return;
    callApi('get_memory_state').then(function (state) {
      if (state) applyMemorySettings(state);
    });
  }, 5000);

  var windowModeSelect = $('#windowModeSelect');

  if (windowModeSelect) {
    windowModeSelect.addEventListener('change', function () {
      callApi('set_settings', { window_mode: windowModeSelect.value }).then(function (settings) {
        if (settings) windowModeSelect.value = settings.window_mode || 'default';
        applyLaunchInfo(settings);
        toast('窗口大小设置已保存');
      });
    });
  }

  /* ---------- 个性化设置 ---------- */
  var bgImageEl = $('#bgImage');
  var bgImageNameEl = $('#bgImageName');
  var bgBlurRange = $('#bgBlurRange');
  var bgBlurValue = $('#bgBlurValue');
  var themeSwatchesEl = $('#themeSwatches');
  var btnPickBackground = $('#btnPickBackground');
  var btnClearBackground = $('#btnClearBackground');

  var THEME_COLORS = ['#2f7ff0', '#0f9aa8', '#35a45c', '#e0862f', '#7a5cf0', '#d94f8a'];

  function hexToRgb(hex) {
    var text = String(hex || '').replace('#', '');
    if (text.length !== 6) return [47, 127, 240];
    return [
      parseInt(text.slice(0, 2), 16),
      parseInt(text.slice(2, 4), 16),
      parseInt(text.slice(4, 6), 16)
    ];
  }

  function shadeHex(hex, ratio) {
    var channels = hexToRgb(hex).map(function (value) {
      var scaled = Math.max(0, Math.min(255, Math.round(value * ratio)));
      return ('0' + scaled.toString(16)).slice(-2);
    });
    return '#' + channels.join('');
  }

  function applyThemeColor(color) {
    var root = document.documentElement;
    if (!color) {
      root.style.removeProperty('--accent');
      root.style.removeProperty('--accent-deep');
      root.style.removeProperty('--accent-light');
      root.style.removeProperty('--accent-rgb');
      return;
    }
    root.style.setProperty('--accent', color);
    root.style.setProperty('--accent-deep', shadeHex(color, 0.82));
    root.style.setProperty('--accent-light', shadeHex(color, 1.24));
    root.style.setProperty('--accent-rgb', hexToRgb(color).join(', '));
  }

  var appliedBlur = null;

  function applyBackgroundBlur(value) {
    var blur = Math.max(0, Math.min(24, Number(value) || 0));
    // 值没变就别写 CSS 变量，否则整层模糊会重新计算
    if (blur !== appliedBlur) {
      appliedBlur = blur;
      document.documentElement.style.setProperty('--bg-blur', blur + 'px');
    }
    if (bgBlurValue) bgBlurValue.textContent = blur + ' px';
    if (bgBlurRange && String(blur) !== bgBlurRange.value) {
      bgBlurRange.value = String(blur);
    }
  }

  var appliedBgFile = null;

  function applyBackgroundImage(file, blur, force) {
    var name = file || '';
    // 只有背景真的变了才重新设 url：否则每次改主题/模糊都会让浏览器重新解码整张图
    if (bgImageEl && (force || name !== appliedBgFile)) {
      appliedBgFile = name;
      bgImageEl.style.backgroundImage = name
        ? 'url("assets/img/' + name + '?v=' + Date.now() + '")'
        : '';
    }
    document.body.classList.toggle('has-bg-image', !!name);
    applyBackgroundBlur(blur);
    if (bgImageNameEl) {
      bgImageNameEl.textContent = name
        ? '当前背景：' + name
        : '正在使用默认渐变背景';
    }
  }

  function renderThemeSwatches(active) {
    if (!themeSwatchesEl) return;
    themeSwatchesEl.innerHTML = '';
    var current = String(active || '').toLowerCase();

    THEME_COLORS.forEach(function (color) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'theme-swatch' + (color === current ? ' is-active' : '');
      button.style.background = color;
      button.title = color;
      button.setAttribute('aria-label', '主题色 ' + color);
      button.addEventListener('click', function () {
        applyThemeColor(color);
        renderThemeSwatches(color);
        callApi('set_settings', { theme_color: color }).then(function (settings) {
          if (settings) applyAppearance(settings);
        });
      });
      themeSwatchesEl.appendChild(button);
    });
  }

  function applyAppearance(settings, force) {
    var config = settings || {};
    applyThemeColor(config.theme_color || '');
    applyBackgroundImage(
      config.background_image || '',
      config.background_blur || 0,
      !!force
    );
    renderThemeSwatches(config.theme_color || '');
  }

  if (bgBlurRange) {
    bgBlurRange.addEventListener('input', function () {
      applyBackgroundBlur(bgBlurRange.value);
    });
    bgBlurRange.addEventListener('change', function () {
      callApi('set_settings', {
        background_blur: Number(bgBlurRange.value) || 0
      }).then(function (settings) {
        if (settings) applyAppearance(settings);
      });
    });
  }

  if (btnPickBackground) {
    btnPickBackground.addEventListener('click', function () {
      btnPickBackground.disabled = true;
      callApi('choose_background_image').then(function (result) {
        btnPickBackground.disabled = false;
        if (!result) {
          toast('启动器接口未就绪');
          return;
        }
        if (result.cancelled) return;
        if (!result.ok) {
          toast(result.error || '选择背景图片失败');
          return;
        }
        toast('背景图片已更新');
        callApi('get_settings').then(function (settings) {
          if (settings) applyAppearance(settings, true);
        });
      });
    });
  }

  if (btnClearBackground) {
    btnClearBackground.addEventListener('click', function () {
      callApi('clear_background_image').then(function (result) {
        if (!result || !result.ok) {
          toast('恢复默认背景失败');
          return;
        }
        toast('已恢复默认背景');
        callApi('get_settings').then(function (settings) {
          if (settings) applyAppearance(settings, true);
        });
      });
    });
  }

  var autoLangSwitch = $('#autoLangSwitch');

  if (autoLangSwitch) {
    autoLangSwitch.addEventListener('change', function () {
      var enabled = autoLangSwitch.checked;
      callApi('set_settings', { auto_lang_zh: enabled }).then(function (settings) {
        if (settings) autoLangSwitch.checked = settings.auto_lang_zh !== false;
        toast(enabled ? '已开启新版本首次启动使用中文' : '已关闭自动设置中文');
      });
    });
  }

  var versionIsolationSwitch = $('#versionIsolationSwitch');

  if (versionIsolationSwitch) {
    versionIsolationSwitch.addEventListener('change', function () {
      var enabled = versionIsolationSwitch.checked;
      callApi('set_settings', { version_isolation: enabled }).then(function (settings) {
        if (settings) versionIsolationSwitch.checked = settings.version_isolation !== false;
        toast(enabled ? '已开启版本隔离' : '已关闭版本隔离，所有版本共用数据根目录');
      });
    });
  }

  /* ---------- Java 运行时管理 ---------- */
  var javaSummaryEl = $('#javaSummary');
  var javaListEl = $('#javaList');
  var currentJava = null;

  function javaLabel(info) {
    if (!info) return '未知 Java';
    return 'Java ' + info.major + ' · ' + (info.vendor || '未知发行版') + ' ' + info.version;
  }

  function renderJavaState(state) {
    if (!state) return;
    currentJava = state.current || null;
    var candidates = state.candidates || [];
    redrawWidget('java-status');

    if (javaSummaryEl) {
      javaSummaryEl.textContent = currentJava
        ? javaLabel(currentJava)
        : '未检测到 Java，启动游戏时会自动下载';
    }

    if (!javaListEl) return;
    javaListEl.innerHTML = '';

    if (!candidates.length) {
      var empty = document.createElement('div');
      empty.className = 'java-empty';
      var hint = document.createElement('span');
      hint.textContent = '未找到已安装的 Java';
      var downloadBtn = document.createElement('button');
      downloadBtn.type = 'button';
      downloadBtn.className = 'btn-ghost';
      downloadBtn.textContent = '下载 Java 17';
      downloadBtn.addEventListener('click', function () {
        downloadJava(17);
      });
      empty.appendChild(hint);
      empty.appendChild(downloadBtn);
      javaListEl.appendChild(empty);
      return;
    }

    candidates.forEach(function (info) {
      var inUse = currentJava && currentJava.path === info.path;
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'java-item';
      if (inUse) item.classList.add('is-active');

      var main = document.createElement('span');
      main.className = 'ji-main';
      var version = document.createElement('span');
      version.className = 'ji-version';
      version.textContent = javaLabel(info);
      var path = document.createElement('span');
      path.className = 'ji-path';
      path.textContent = info.path;
      main.appendChild(version);
      main.appendChild(path);

      var badge = document.createElement('span');
      badge.className = 'ji-badge';
      badge.textContent = inUse ? '使用中' : '选用';

      item.appendChild(main);
      item.appendChild(badge);
      item.addEventListener('click', function () {
        selectJava(info.path);
      });
      javaListEl.appendChild(item);
    });
  }

  function resetJavaScanButton() {
    if (!btnScanJava) return;
    btnScanJava.disabled = false;
    btnScanJava.textContent = '自动扫描';
  }

  // Python 侧全盘扫描期间推送的进度
  window.mchl.javaScan = function (payload) {
    if (!payload) return;
    if (payload.done) {
      resetJavaScanButton();
      return;
    }
    if (javaSummaryEl) {
      javaSummaryEl.textContent =
        '正在全盘扫描 Java…（已检查 ' + (payload.count || 0) + ' 个目录）';
    }
  };

  function loadJavaState(rescan) {
    callApi(rescan ? 'scan_java_env' : 'get_java_state').then(function (state) {
      resetJavaScanButton();
      if (!state) return;
      renderJavaState(state);
      if (rescan) {
        // 同步刷新版本设置页的 Java 下拉
        vsJavaCandidates = state.candidates || [];
        vsJavaLoaded = true;
        toast((state.candidates || []).length ? '扫描完成' : '未找到 Java');
      }
    });
  }

  function selectJava(path) {
    callApi('set_java_path', path).then(function (result) {
      if (!result) return;
      if (result.ok) {
        toast('已切换到 ' + javaLabel(result.java));
        loadJavaState(false);
      } else {
        toast(result.error || '切换失败');
      }
    });
  }

  function downloadJava(major) {
    setLaunchStatus('准备下载 Java ' + major, -1, true);
    callApi('download_java', major).then(function (result) {
      setLaunchStatus('', -1, false);
      if (!result) return;
      if (result.ok) {
        toast('Java ' + result.java.major + ' 已就绪');
        loadJavaState(false);
      } else {
        toast(result.error || 'Java 下载失败');
      }
    });
  }

  var btnScanJava = $('#btnScanJava');
  var btnPickJava = $('#btnPickJava');

  if (btnScanJava) {
    btnScanJava.addEventListener('click', function () {
      if (btnScanJava.disabled) return;
      // 全盘扫描要遍历所有本地磁盘，先给个可见状态
      btnScanJava.disabled = true;
      btnScanJava.textContent = '扫描中…';
      if (javaSummaryEl) javaSummaryEl.textContent = '正在全盘扫描 Java…';
      toast('正在全盘扫描 Java，可能需要一会儿');
      loadJavaState(true);
    });
  }

  if (btnPickJava) {
    btnPickJava.addEventListener('click', function () {
      callApi('pick_java_file').then(function (result) {
        if (!result || result.cancelled) return;
        if (result.ok) {
          toast('已添加 ' + javaLabel(result.java));
          loadJavaState(false);
        } else {
          toast(result.error || '添加失败');
        }
      });
    });
  }

  $$('[data-toast]').forEach(function (element) {
    element.addEventListener('click', function () {
      toast(element.dataset.toast);
    });
  });

  var btnOpenDataDir = $('#btnOpenDataDir');
  if (btnOpenDataDir) {
    btnOpenDataDir.addEventListener('click', function () {
      callApi('open_data_dir').then(function (ok) {
        if (ok === false) toast('无法打开数据目录');
      });
    });
  }

  /* ---------- 下载板块 ---------- */
  var sourceSwitch = $('#sourceSwitch');
  var versionSearch = $('#versionSearch');
  var versionFilters = $('#versionFilters');
  var downloadListEl = $('#downloadList');
  var btnRefreshVersions = $('#btnRefreshVersions');

  // 下载设置页
  var dlModalVersionEl = $('#dlModalVersion');
  var dlModalDescEl = $('#dlModalDesc');
  var dlNameInput = $('#dlNameInput');
  var dlNameEdited = false;
  if (dlNameInput) {
    dlNameInput.addEventListener('input', function () {
      dlNameEdited = true;
    });
  }
  var dlLoaderChipsEl = $('#dlLoaderChips');
  var dlLoaderVersionRow = $('#dlLoaderVersionRow');
  var dlLoaderVersionListEl = $('#dlLoaderVersionList');
  var btnStartDownload = $('#btnStartDownload');
  var btnBackFromDownload = $('#btnBackFromDownload');
  var dlFabricApiBlock = $('#dlFabricApiBlock');
  var dlFabricApiDescEl = $('#dlFabricApiDesc');
  var dlFabricApiSwitch = $('#dlFabricApiSwitch');
  var dlFabricApiListEl = $('#dlFabricApiList');

  var downloadState = {
    loaded: false,
    loading: false,
    mirror: false,
    filter: 'release',
    keyword: '',
    versions: [],
    busy: false
  };

  // 下载设置页的配置
  var dlModal = {
    versionId: '',
    typeLabel: '',
    loader: 'vanilla',
    loaderVersion: '',
    loaderVersionsOk: true,
    loaderVersionsLoading: false,
    loaderVersionCache: {},
    fabricApi: null,
    fabricApiLoading: false,
    fabricApiVersions: [],
    fabricApiVersion: '',
    fabricApiCache: {},
    busy: false
  };

  function formatDay(raw) {
    return (raw || '').slice(0, 10);
  }

  function renderDownloadList() {
    if (!downloadListEl) return;
    var keyword = downloadState.keyword.trim().toLowerCase();

    var rows = downloadState.versions.filter(function (item) {
      if (downloadState.filter !== 'all' && item.type !== downloadState.filter) return false;
      if (keyword && item.id.toLowerCase().indexOf(keyword) < 0) return false;
      return true;
    });

    downloadListEl.innerHTML = '';

    if (!rows.length) {
      var empty = document.createElement('div');
      empty.className = 'version-empty';
      empty.textContent = downloadState.loading ? '正在读取版本清单…' : '没有匹配的版本';
      downloadListEl.appendChild(empty);
      return;
    }

    rows.slice(0, 150).forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'dl-item';
      row.setAttribute('role', 'button');
      row.tabIndex = 0;

      var main = document.createElement('div');
      main.className = 'dl-main';
      var name = document.createElement('span');
      name.className = 'dl-name';
      name.textContent = item.id;
      var meta = document.createElement('span');
      meta.className = 'dl-meta';
      meta.textContent = [item.type_label || item.type, formatDay(item.release_time)]
        .filter(Boolean)
        .join(' · ');
      main.appendChild(name);
      main.appendChild(meta);

      var side = document.createElement('div');
      side.className = 'dl-side';
      var chevron = document.createElement('span');
      chevron.className = 'dl-chevron';
      chevron.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="m9 5 7 7-7 7"/></svg>';
      side.appendChild(chevron);

      row.appendChild(main);
      row.appendChild(side);
      row.addEventListener('click', function () {
        openDownloadPage(item);
      });
      row.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openDownloadPage(item);
      });
      downloadListEl.appendChild(row);
    });
  }

  var manifestRequestId = 0;

  function loadManifest(force) {
    // 正在加载时默认忽略重复请求；切换下载源/手动刷新允许打断上一次加载
    if (downloadState.loading && !force) return;

    var requestId = ++manifestRequestId;
    var mirror = downloadState.mirror;

    downloadState.loading = true;
    // 新的清单拿回来之前不展示任何版本（切换下载源时尤其明显）
    downloadState.loaded = false;
    downloadState.versions = [];
    renderDownloadList();
    setDownloadStatus('正在获取版本清单…', -1, true);

    callApi('fetch_versions', mirror).then(function (result) {
      // 已经发起更新的请求（切换了下载源），丢弃这次结果
      if (requestId !== manifestRequestId) return;

      downloadState.loading = false;
      if (!result) {
        setDownloadStatus('启动器接口未就绪', -1, false);
        return;
      }
      if (!result.ok) {
        downloadState.versions = [];
        renderDownloadList();
        setDownloadStatus(result.error || '获取版本清单失败', -1, false);
        return;
      }
      downloadState.versions = result.versions || [];
      downloadState.loaded = true;
      renderDownloadList();
      var countText = '共 ' + downloadState.versions.length + ' 个版本';
      if (result.fallback && result.source) {
        countText =
          (result.source === 'official'
            ? '镜像源不可用，已自动改用官方源'
            : '官方源不可用，已自动改用镜像源') +
          ' · ' +
          countText;
      }
      setDownloadStatus(countText, 100, false);
    });
  }

  /* ---------- 下载配置弹窗（下载时选择模组加载器） ---------- */

  var LOADER_OPTIONS = [
    { id: 'vanilla', label: '原版' },
    { id: 'fabric', label: 'Fabric' },
    { id: 'quilt', label: 'Quilt' },
    { id: 'forge', label: 'Forge' },
    { id: 'neoforge', label: 'NeoForge' }
  ];

  function fillLoaderVersions(versions) {
    if (!dlLoaderVersionListEl) return;
    dlLoaderVersionListEl.innerHTML = '';
    dlModal.loaderVersion = '';

    var list = versions || [];
    if (!list.length) {
      var empty = document.createElement('div');
      empty.className = 'dl-version-empty';
      empty.textContent = '暂无可选版本';
      dlLoaderVersionListEl.appendChild(empty);
      return;
    }

    dlModal.loaderVersion = list[0];

    list.forEach(function (version, index) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'dl-version-item';
      if (index === 0) item.classList.add('is-active');

      var name = document.createElement('span');
      name.className = 'dl-version-name';
      name.textContent = version;
      item.appendChild(name);

      if (index === 0) {
        var tag = document.createElement('span');
        tag.className = 'dl-version-tag';
        tag.textContent = '最新';
        item.appendChild(tag);
      }

      item.addEventListener('click', function () {
        dlModal.loaderVersion = version;
        Array.prototype.forEach.call(dlLoaderVersionListEl.children, function (child) {
          child.classList.toggle('is-active', child === item);
        });
      });

      dlLoaderVersionListEl.appendChild(item);
    });
  }

  function formatSize(bytes) {
    var value = Number(bytes) || 0;
    if (value >= 1048576) return (value / 1048576).toFixed(1) + ' MB';
    if (value >= 1024) return Math.round(value / 1024) + ' KB';
    return value + ' B';
  }

  function showFabricApiBlock(visible) {
    if (dlFabricApiBlock) dlFabricApiBlock.style.display = visible ? '' : 'none';
  }

  function renderFabricApiList() {
    if (!dlFabricApiListEl) return;
    dlFabricApiListEl.innerHTML = '';

    var versions = dlModal.fabricApiVersions || [];
    versions.forEach(function (item, index) {
      var row = document.createElement('button');
      row.type = 'button';
      row.className = 'dl-version-item';
      if (item.version === dlModal.fabricApiVersion) row.classList.add('is-active');

      var name = document.createElement('span');
      name.className = 'dl-version-name';
      name.textContent = item.version;
      row.appendChild(name);

      var tag = document.createElement('span');
      tag.className = 'dl-version-tag';
      if (index === 0) {
        tag.textContent = '推荐';
      } else {
        tag.textContent = formatSize(item.size);
        tag.classList.add('is-muted');
      }
      row.appendChild(tag);

      row.addEventListener('click', function () {
        dlModal.fabricApiVersion = item.version;
        Array.prototype.forEach.call(dlFabricApiListEl.children, function (child) {
          child.classList.toggle('is-active', child === row);
        });
      });

      dlFabricApiListEl.appendChild(row);
    });
  }

  function renderFabricApi() {
    if (!dlFabricApiDescEl) return;

    if (dlModal.fabricApiLoading) {
      dlFabricApiDescEl.textContent = '正在匹配适配当前版本的 Fabric API…';
      if (dlFabricApiListEl) dlFabricApiListEl.innerHTML = '';
      if (dlFabricApiSwitch) {
        dlFabricApiSwitch.disabled = true;
        dlFabricApiSwitch.checked = false;
      }
      return;
    }

    var info = dlModal.fabricApi;
    var versions = info && info.ok ? info.versions || [] : [];

    if (versions.length) {
      dlModal.fabricApiVersions = versions;
      var recommended = info.recommended || versions[0].version;
      var exists = versions.some(function (item) {
        return item.version === dlModal.fabricApiVersion;
      });
      if (!exists) dlModal.fabricApiVersion = recommended;

      dlFabricApiDescEl.textContent =
        '已匹配 ' + versions.length + ' 个版本，默认选择推荐版本';
      if (dlFabricApiSwitch) {
        dlFabricApiSwitch.disabled = false;
        dlFabricApiSwitch.checked = true;
      }
      if (dlFabricApiListEl) dlFabricApiListEl.style.display = '';
      renderFabricApiList();
      return;
    }

    dlModal.fabricApiVersions = [];
    dlModal.fabricApiVersion = '';
    if (dlFabricApiListEl) {
      dlFabricApiListEl.innerHTML = '';
      dlFabricApiListEl.style.display = 'none';
    }
    dlFabricApiDescEl.textContent =
      (info && info.error) || '未找到适配的 Fabric API，将不安装';
    if (dlFabricApiSwitch) {
      dlFabricApiSwitch.disabled = true;
      dlFabricApiSwitch.checked = false;
    }
  }

  function ensureFabricApi(versionId) {
    var cached = dlModal.fabricApiCache[versionId];
    if (cached) {
      dlModal.fabricApi = cached;
      dlModal.fabricApiLoading = false;
      renderFabricApi();
      return;
    }

    dlModal.fabricApi = null;
    dlModal.fabricApiLoading = true;
    renderFabricApi();

    callApi('fetch_fabric_api', versionId).then(function (result) {
      if (dlModal.versionId !== versionId) return;
      dlModal.fabricApiLoading = false;
      dlModal.fabricApi = result || { ok: false, error: '无法获取 Fabric API 信息' };
      if (dlModal.fabricApi.ok) dlModal.fabricApiCache[versionId] = dlModal.fabricApi;
      renderFabricApi();
    });
  }

  function loaderLabel(loaderId) {
    var label = '加载器';
    LOADER_OPTIONS.forEach(function (option) {
      if (option.id === loaderId) label = option.label;
    });
    return label;
  }

  // 用户没改过名称时跟随加载器补后缀，避免与原版目录重名
  function syncDownloadName(loaderId) {
    if (dlNameEdited || !dlNameInput) return;
    var base = dlModal.versionId;
    if (!base) return;
    dlNameInput.value =
      loaderId === 'vanilla' ? base : base + '-' + loaderLabel(loaderId);
  }

  function setLoaderVersionsHint(text) {
    if (!dlLoaderVersionListEl) return;
    dlLoaderVersionListEl.innerHTML = '';
    var hint = document.createElement('div');
    hint.className = 'dl-version-empty';
    hint.textContent = text;
    dlLoaderVersionListEl.appendChild(hint);
    dlModal.loaderVersion = '';
  }

  function renderLoaderChips() {
    if (!dlLoaderChipsEl) return;
    dlLoaderChipsEl.innerHTML = '';

    LOADER_OPTIONS.forEach(function (option) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'loader-chip';
      chip.textContent = option.label;
      chip.title = option.id === 'vanilla' ? '不安装模组加载器' : '选择后再获取可用版本';

      if (dlModal.loader === option.id) chip.classList.add('is-active');

      chip.addEventListener('click', function () {
        selectLoader(option.id);
      });

      dlLoaderChipsEl.appendChild(chip);
    });
  }

  // 加载器版本不可用时禁用“开始下载”，避免点了才知道下不了
  function updateStartDownloadState() {
    if (!btnStartDownload || dlModal.busy) return;

    var disabled = false;
    var hint = '';
    if (dlModal.loader !== 'vanilla') {
      if (dlModal.loaderVersionsLoading) {
        disabled = true;
        hint = '正在获取加载器版本…';
      } else if (!dlModal.loaderVersionsOk || !dlModal.loaderVersion) {
        disabled = true;
        hint = '当前版本没有可用的加载器版本';
      }
    }

    btnStartDownload.disabled = disabled;
    btnStartDownload.title = hint;
  }

  function selectLoader(loaderId) {
    dlModal.loader = loaderId;
    syncDownloadName(loaderId);
    renderLoaderChips();

    if (loaderId === 'vanilla') {
      dlModal.loaderVersionsOk = true;
      dlModal.loaderVersionsLoading = false;
      if (dlLoaderVersionRow) dlLoaderVersionRow.style.display = 'none';
      fillLoaderVersions([]);
      showFabricApiBlock(false);
      dlModal.fabricApi = null;
      updateStartDownloadState();
      return;
    }

    if (dlLoaderVersionRow) dlLoaderVersionRow.style.display = '';

    if (loaderId === 'fabric') {
      showFabricApiBlock(true);
      ensureFabricApi(dlModal.versionId);
    } else {
      showFabricApiBlock(false);
      dlModal.fabricApi = null;
    }

    loadLoaderVersions(loaderId);
  }

  function loadLoaderVersions(loaderId) {
    var versionId = dlModal.versionId;
    var cacheKey =
      loaderId + '@' + versionId + '@' + (downloadState.mirror ? '1' : '0');
    var cached = dlModal.loaderVersionCache[cacheKey];

    if (cached && cached.length) {
      dlModal.loaderVersionsOk = true;
      dlModal.loaderVersionsLoading = false;
      fillLoaderVersions(cached);
      updateStartDownloadState();
      return;
    }

    dlModal.loaderVersionsOk = false;
    dlModal.loaderVersionsLoading = true;
    setLoaderVersionsHint('正在获取 ' + loaderLabel(loaderId) + ' 可用版本…');
    updateStartDownloadState();

    callApi('fetch_loader_versions', loaderId, versionId, downloadState.mirror).then(
      function (result) {
        if (dlModal.loader !== loaderId || dlModal.versionId !== versionId) return;

        dlModal.loaderVersionsLoading = false;
        var versions = result && result.ok ? result.versions || [] : [];
        if (versions.length) {
          dlModal.loaderVersionCache[cacheKey] = versions;
          dlModal.loaderVersionsOk = true;
          fillLoaderVersions(versions);
        } else {
          dlModal.loaderVersionsOk = false;
          fillLoaderVersions([]);
          setLoaderVersionsHint(
            (result && result.error) || '未找到可用的加载器版本'
          );
        }
        updateStartDownloadState();
      }
    );
  }

  function openDownloadPage(item) {
    if (dlModal.busy) {
      toast('正在下载中，请稍候');
      return;
    }

    dlModal.versionId = item.id;
    dlModal.typeLabel = item.type_label || item.type || '';
    dlModal.loader = 'vanilla';
    dlModal.loaderVersionsOk = true;
    dlModal.loaderVersionsLoading = false;

    if (dlModalVersionEl) dlModalVersionEl.textContent = item.id;
    if (dlModalDescEl) {
      dlModalDescEl.textContent = [dlModal.typeLabel, formatDay(item.release_time)]
        .filter(Boolean)
        .join(' · ');
    }
    dlNameEdited = false;
    if (dlNameInput) dlNameInput.value = item.id;
    if (dlLoaderVersionRow) dlLoaderVersionRow.style.display = 'none';
    fillLoaderVersions([]);
    dlModal.fabricApi = null;
    dlModal.fabricApiLoading = false;
    dlModal.fabricApiVersions = [];
    dlModal.fabricApiVersion = '';
    showFabricApiBlock(false);
    renderFabricApi();
    renderLoaderChips();
    updateStartDownloadState();

    switchView('download-detail');
    setModalStatus('选择模组加载器后开始下载', -1, false);
  }

  function backToDownloadPage() {
    // 下载中允许返回列表，进度改由右下角下载任务查看
    if (!dlModal.busy) setModalStatus('', -1, false);
    if (currentView() === 'download-detail') switchView('download');
  }

  function startDownload() {
    if (dlModal.busy) return;
    if (!dlModal.versionId) {
      toast('请选择游戏版本');
      return;
    }

    var versionName = ((dlNameInput && dlNameInput.value) || '').trim();
    if (!versionName) {
      toast('版本名不能为空');
      return;
    }
    if (/[<>:"/\\|?*]/.test(versionName)) {
      toast('版本名不能包含 < > : " / \\ | ? *');
      return;
    }

    if (dlModal.loader !== 'vanilla') {
      if (dlModal.loaderVersionsLoading) {
        toast('正在获取加载器版本，请稍候');
        return;
      }
      if (!dlModal.loaderVersionsOk || !dlModal.loaderVersion) {
        toast('当前版本没有可用的 ' + loaderLabel(dlModal.loader) + ' 版本');
        return;
      }
    }

    var loaderVersion = dlModal.loader === 'vanilla' ? '' : dlModal.loaderVersion || '';
    var withFabricApi =
      dlModal.loader === 'fabric' &&
      !!dlFabricApiSwitch &&
      dlFabricApiSwitch.checked &&
      !dlFabricApiSwitch.disabled &&
      !!dlModal.fabricApiVersion;

    dlModal.busy = true;
    if (btnStartDownload) {
      btnStartDownload.disabled = true;
      btnStartDownload.textContent = '下载中…';
    }
    setModalStatus('准备下载 ' + dlModal.versionId, -1, true);

    var taskLabel = versionName;
    if (dlModal.loader !== 'vanilla') {
      taskLabel += ' · ' + loaderLabel(dlModal.loader);
      if (loaderVersion) taskLabel += ' ' + loaderVersion;
    }
    var taskId = createDownloadTask({
      name: taskLabel,
      versionId: dlModal.versionId,
      loader: dlModal.loader
    });

    callApi(
      'download_game',
      dlModal.versionId,
      dlModal.loader,
      loaderVersion,
      downloadState.mirror,
      versionName,
      withFabricApi,
      withFabricApi ? dlModal.fabricApiVersion : ''
    ).then(function (result) {
      dlModal.busy = false;
      if (btnStartDownload) {
        btnStartDownload.textContent = '开始下载';
      }
      updateStartDownloadState();

      if (!result) {
        finishDownloadTask(taskId, false, '启动器接口未就绪');
        setModalStatus('启动器接口未就绪', -1, false);
        return;
      }

      if (result.ok) {
        var name = result.version || versionName;
        finishDownloadTask(
          taskId,
          true,
          (dlModal.loader === 'vanilla' ? '已下载 ' : '已安装 ') + name
        );
        if (dlNameInput) dlNameInput.value = name;
        setModalStatus((dlModal.loader === 'vanilla' ? '已下载 ' : '已安装 ') + name, 100, false);
        toast((dlModal.loader === 'vanilla' ? '已下载 ' : '已安装 ') + name);
        if (result.warning) toast(result.warning);
        refreshVersionList();
        window.setTimeout(backToDownloadPage, 600);
      } else {
        finishDownloadTask(taskId, false, result.error || '下载失败');
        setModalStatus(result.error || '下载失败', -1, false);
        toast(result.error || '下载失败');
        // 名称冲突等错误时同步一次已安装列表，避免基于旧列表重复尝试
        refreshVersionList();
      }
    });
  }

  function ensureDownloadReady() {
    if (!downloadState.loaded) loadManifest();
  }

  if (sourceSwitch) {
    $$('.seg', sourceSwitch).forEach(function (button) {
      button.addEventListener('click', function () {
        var mirror = button.dataset.mirror === '1';
        if (mirror === downloadState.mirror) return;
        downloadState.mirror = mirror;
        $$('.seg', sourceSwitch).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        downloadState.loaded = false;
        dlModal.loaderVersionCache = {};
        loadManifest(true);
      });
    });
  }

  if (versionFilters) {
    $$('.chip', versionFilters).forEach(function (chip) {
      chip.addEventListener('click', function () {
        downloadState.filter = chip.dataset.filter;
        $$('.chip', versionFilters).forEach(function (item) {
          item.classList.toggle('is-active', item === chip);
        });
        renderDownloadList();
      });
    });
  }

  if (versionSearch) {
    versionSearch.addEventListener('input', function () {
      downloadState.keyword = versionSearch.value || '';
      renderDownloadList();
    });
  }

  if (btnRefreshVersions) {
    btnRefreshVersions.addEventListener('click', function () {
      loadManifest(true);
    });
  }

  if (btnStartDownload) {
    btnStartDownload.addEventListener('click', startDownload);
  }

  if (btnBackFromDownload) {
    btnBackFromDownload.addEventListener('click', backToDownloadPage);
  }

  if (dlFabricApiSwitch) {
    dlFabricApiSwitch.addEventListener('change', function () {
      if (dlFabricApiListEl) {
        dlFabricApiListEl.style.display = dlFabricApiSwitch.checked ? '' : 'none';
      }
    });
  }

  /* ---------- 模组下载（CurseForge / Modrinth） ---------- */
  var modSourceSwitch = $('#modSourceSwitch');
  var modSearchInput = $('#modSearchInput');
  var btnModSearch = $('#btnModSearch');
  var modSearchListEl = $('#modSearchList');
  var btnModMore = $('#btnModMore');

  // 下载板块：游戏版本 / 模组 / 整合包 / 资源包 / 数据包
  var downloadTabs = $('#downloadTabs');
  var gameDownloadPanel = $('#gameDownloadPanel');
  var contentDownloadPanel = $('#contentDownloadPanel');
  var downloadContent = 'game';

  var CONTENT_TYPE_LABELS = {
    mod: '模组',
    modpack: '整合包',
    resourcepack: '资源包',
    shaderpack: '光影包',
    datapack: '数据包'
  };

  var CONTENT_SEARCH_PLACEHOLDERS = {
    mod: '搜索模组，例如 JEI / Sodium',
    modpack: '搜索整合包，例如 苍穹 / Fabulously Optimized',
    resourcepack: '搜索资源包，例如 材质包名',
    shaderpack: '搜索光影包，例如 BSL / Complementary',
    datapack: '搜索数据包，例如 数据包名'
  };

  function contentLabel(type) {
    return CONTENT_TYPE_LABELS[type] || CONTENT_TYPE_LABELS.mod;
  }

  function setDownloadContent(type) {
    downloadContent = type;
    var isGame = type === 'game';

    if (gameDownloadPanel) {
      gameDownloadPanel.classList.toggle('is-collapsed', !isGame);
    }
    if (contentDownloadPanel) {
      contentDownloadPanel.classList.toggle('is-collapsed', isGame);
    }
    if (downloadTabs) {
      $$('[data-content]', downloadTabs).forEach(function (button) {
        button.classList.toggle('is-active', button.dataset.content === type);
      });
    }
    if (isGame) return;

    var changed = modState.type !== type;
    modState.type = type;

    // 数据包在 CurseForge 没有独立分类，隐藏这个来源
    if (modSourceSwitch) {
      $$('.seg', modSourceSwitch).forEach(function (button) {
        var hide = type === 'datapack' && button.dataset.source === 'curseforge';
        button.classList.toggle('is-collapsed', hide);
      });
      if (type === 'datapack' && modState.source === 'curseforge') {
        modState.source = 'all';
        $$('.seg', modSourceSwitch).forEach(function (button) {
          button.classList.toggle('is-active', button.dataset.source === 'all');
        });
      }
    }

    // 整合包和模组一样分加载器；资源包 / 光影包 / 数据包不需要这行筛选
    if (modLoaderFilters) {
      modLoaderFilters.classList.toggle(
        'is-collapsed',
        !(type === 'mod' || type === 'modpack')
      );
      $$('[data-loader]', modLoaderFilters).forEach(function (button) {
        button.classList.toggle('is-active', !button.dataset.loader);
      });
    }
    modState.loader = '';
    if (modSearchInput) {
      modSearchInput.placeholder =
        CONTENT_SEARCH_PLACEHOLDERS[type] || CONTENT_SEARCH_PLACEHOLDERS.mod;
    }

    if (changed) {
      searchMods();
    } else if (!modState.loaded && !modState.loading) {
      searchMods();
    }
  }

  if (downloadTabs) {
    $$('[data-content]', downloadTabs).forEach(function (button) {
      button.addEventListener('click', function () {
        var type = button.dataset.content || 'game';
        if (type === downloadContent) return;
        setDownloadContent(type);
      });
    });
  }

  // 加载器筛选：模组与整合包都按 Fabric / Forge / NeoForge / Quilt 过滤
  if (modLoaderFilters) {
    $$('[data-loader]', modLoaderFilters).forEach(function (button) {
      button.addEventListener('click', function () {
        var loader = button.dataset.loader || '';
        if (loader === modState.loader) return;
        modState.loader = loader;
        $$('[data-loader]', modLoaderFilters).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        searchMods();
      });
    });
  }

  var modDetailTitleEl = $('#modDetailTitle');
  var modDetailSubtitleEl = $('#modDetailSubtitle');
  var modDetailIconEl = $('#modDetailIcon');
  var modDetailNameEl = $('#modDetailName');
  var modDetailDescEl = $('#modDetailDesc');
  var modDetailSourceEl = $('#modDetailSource');

  var btnRefreshModVersions = $('#btnRefreshModVersions');
  var modDetailLoaderFilters = $('#modDetailLoaderFilters');
  var modVersionListEl = $('#modVersionList');
  var modVersionDescEl = $('#modVersionDesc');
  var btnDownloadMod = $('#btnDownloadMod');
  var btnBackFromMod = $('#btnBackFromMod');

  var MOD_LOADER_OPTIONS = [
    { id: '', label: '全部加载器' },
    { id: 'forge', label: 'Forge' },
    { id: 'fabric', label: 'Fabric' },
    { id: 'quilt', label: 'Quilt' },
    { id: 'neoforge', label: 'NeoForge' }
  ];

  var MOD_SOURCE_LABELS = {
    curseforge: 'CurseForge',
    modrinth: 'Modrinth',
    all: '全部'
  };

  var modState = {
    source: 'all',
    type: 'mod',
    keyword: '',
    loader: '',
    loading: false,
    loadingMore: false,
    loaded: false,
    items: [],
    pending: [],
    failed: [],
    offsets: {},
    more: {},
    requestId: 0
  };

  var modLoaderFilters = $('#modLoaderFilters');

  var modDetail = {
    source: '',
    id: '',
    name: '',
    slug: '',
    icon: '',
    summary: '',
    author: '',
    downloads: 0,
    loader: '',
    versions: [],
    filtered: [],
    groups: [],
    available: [],
    expandedGame: '',
    selected: '',
    history: [],
    loading: false,
    busy: false,
    requestId: 0
  };

  function formatCount(value) {
    var num = Number(value) || 0;
    if (num >= 100000000) return (num / 100000000).toFixed(1) + ' 亿';
    if (num >= 10000) return (num / 10000).toFixed(1) + ' 万';
    return String(num);
  }

  function fillModIcon(box, url, name) {
    if (!box) return;
    box.innerHTML = '';
    if (url) {
      var img = document.createElement('img');
      img.src = url;
      img.alt = '';
      img.draggable = false;
      img.addEventListener('error', function () {
        img.remove();
        var fallback = document.createElement('span');
        fallback.textContent = (name || '?').slice(0, 1).toUpperCase();
        box.appendChild(fallback);
      });
      box.appendChild(img);
      return;
    }
    var span = document.createElement('span');
    span.textContent = (name || '?').slice(0, 1).toUpperCase();
    box.appendChild(span);
  }

  function renderModLoaderChips(container, current, onSelect, options) {
    if (!container) return;
    container.innerHTML = '';
    (options || MOD_LOADER_OPTIONS).forEach(function (option) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'loader-chip';
      chip.textContent = option.label;
      if (option.id === current) chip.classList.add('is-active');
      chip.addEventListener('click', function () {
        $$('.loader-chip', container).forEach(function (item) {
          item.classList.toggle('is-active', item === chip);
        });
        onSelect(option.id);
      });
      container.appendChild(chip);
    });
  }

  function buildModRow(item) {
    var row = document.createElement('div');
    row.className = 'dl-item';
    row.setAttribute('role', 'button');
    row.tabIndex = 0;

    var icon = document.createElement('div');
    icon.className = 'mod-icon';
    fillModIcon(icon, item.icon, item.name);
    row.appendChild(icon);

    var main = document.createElement('div');
    main.className = 'dl-main';
    var name = document.createElement('span');
    name.className = 'dl-name';
    name.textContent = item.name || item.slug || item.id;
    name.title = name.textContent;
    var meta = document.createElement('span');
    meta.className = 'dl-meta';
    meta.textContent = [
      item.summary,
      item.author,
      formatCount(item.downloads) + ' 次下载'
    ]
      .filter(Boolean)
      .join(' · ');
    meta.title = meta.textContent;
    main.appendChild(name);
    main.appendChild(meta);

    var side = document.createElement('div');
    side.className = 'dl-side';
    var tag = document.createElement('span');
    tag.className = 'mod-tag is-source';
    tag.textContent = MOD_SOURCE_LABELS[item.source] || item.source;
    side.appendChild(tag);

    var chevron = document.createElement('span');
    chevron.className = 'dl-chevron';
    chevron.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="m9 5 7 7-7 7"/></svg>';
    side.appendChild(chevron);

    row.appendChild(main);
    row.appendChild(side);
    row.addEventListener('click', function () {
      openModPage(item);
    });
    row.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      openModPage(item);
    });
    return row;
  }

  // 搜索结果列表（与版本设置页的已安装模组列表分开，避免同名函数互相覆盖）
  function renderModSearchList() {
    if (!modSearchListEl) return;
    modSearchListEl.innerHTML = '';

    if (!modState.items.length) {
      var empty = document.createElement('div');
      empty.className = 'version-empty';
      empty.textContent = modState.loading
        ? '正在搜索' + contentLabel(modState.type) + '…'
        : '没有匹配的' + contentLabel(modState.type);
      modSearchListEl.appendChild(empty);
      return;
    }

    modState.items.forEach(function (item) {
      modSearchListEl.appendChild(buildModRow(item));
    });
  }

  // 全部来源时两个站点各取一半，先到的先显示
  function modSearchSites() {
    // 数据包在 CurseForge 没有独立分类，只查 Modrinth
    if (modState.type === 'datapack') return ['modrinth'];
    if (modState.source === 'all') return ['modrinth', 'curseforge'];
    return [modState.source];
  }

  function modSearchLimit() {
    return modState.source === 'all' ? 15 : 30;
  }

  function sortModItems() {
    modState.items.sort(function (a, b) {
      return (b.downloads || 0) - (a.downloads || 0);
    });
  }

  function modHasMore() {
    var sites = Object.keys(modState.more);
    for (var i = 0; i < sites.length; i += 1) {
      if (modState.more[sites[i]]) return true;
    }
    return false;
  }

  function updateModMoreButton() {
    if (!btnModMore) return;
    var more = modHasMore();
    btnModMore.hidden = !more;
    btnModMore.disabled = modState.loading || modState.loadingMore;
    btnModMore.textContent = modState.loadingMore ? '加载中…' : '加载更多';
  }

  function updateModStatus() {
    if (modState.pending.length) {
      var waiting = modState.pending
        .map(function (site) {
          return MOD_SOURCE_LABELS[site] || site;
        })
        .join('、');
      if (modState.loadingMore) {
        setModStatus('正在加载更多 ' + waiting + '…', -1, true);
        return;
      }
      var prefix = modState.items.length
        ? '已显示 ' + modState.items.length + ' 个结果，'
        : '';
      setModStatus(prefix + '正在加载 ' + waiting + '…', -1, true);
      return;
    }

    if (!modState.items.length) {
      renderModSearchList();
      setModStatus(
        modState.failed.join('；') || '没有匹配的' + contentLabel(modState.type),
        -1,
        false
      );
      return;
    }

    var text = '已显示 ' + modState.items.length + ' 个结果';
    if (modHasMore()) text += '，可继续加载更多';
    if (modState.failed.length) text += '（' + modState.failed.join('；') + '）';
    setModStatus(text, 100, false);
  }

  function requestMods(requestId, site) {
    var offset = modState.offsets[site] || 0;
    callApi(
      'search_mods',
      site,
      modState.keyword,
      '',
      modState.loader,
      modSearchLimit(),
      offset,
      modState.type
    ).then(function (result) {
      if (requestId !== modState.requestId) return;

      modState.pending = modState.pending.filter(function (name) {
        return name !== site;
      });

      if (!result) {
        modState.failed.push('启动器接口未就绪');
        modState.more[site] = false;
      } else if (!result.ok) {
        modState.failed.push(result.error || MOD_SOURCE_LABELS[site] + ' 搜索失败');
        modState.more[site] = false;
      } else {
        var incoming = result.items || [];
        modState.offsets[site] = offset + incoming.length;
        modState.more[site] = !!result.has_more;
        for (var i = 0; i < incoming.length; i += 1) {
          modState.items.push(incoming[i]);
        }
        sortModItems();
      }

      if (!modState.pending.length) {
        modState.loading = false;
        modState.loadingMore = false;
      }
      modState.loaded = !modState.loading && !modState.loadingMore && modState.items.length > 0;
      renderModSearchList();
      updateModStatus();
      updateModMoreButton();
    });
  }

  function searchMods() {
    var requestId = ++modState.requestId;
    modState.keyword = modSearchInput ? modSearchInput.value.trim() : '';
    modState.loading = true;
    modState.loadingMore = false;
    modState.items = [];
    modState.failed = [];
    modState.offsets = {};
    modState.more = {};
    modState.pending = modSearchSites();
    renderModSearchList();
    updateModMoreButton();
    setModStatus('正在搜索' + contentLabel(modState.type) + '…', -1, true);

    var sites = modState.pending.slice();
    for (var i = 0; i < sites.length; i += 1) {
      requestMods(requestId, sites[i]);
    }
  }

  // 继续加载：各来源按已加载条数取下一页
  function loadMoreMods() {
    if (modState.loading || modState.loadingMore) return;

    var sites = [];
    modSearchSites().forEach(function (site) {
      if (modState.more[site]) sites.push(site);
    });
    if (!sites.length) return;

    modState.loadingMore = true;
    modState.pending = sites.slice();
    updateModMoreButton();
    updateModStatus();

    var requestId = modState.requestId;
    for (var i = 0; i < sites.length; i += 1) {
      requestMods(requestId, sites[i]);
    }
  }

  function ensureModsReady() {
    if (modState.loaded || modState.loading || modState.loadingMore) return;
    searchMods();
  }

  function currentModGroup() {
    var groups = modDetail.groups || [];
    for (var i = 0; i < groups.length; i += 1) {
      if (groups[i].version === modDetail.expandedGame) return groups[i];
    }
    return null;
  }

  function selectedModFile() {
    var list = modDetail.versions || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].id === modDetail.selected) return list[i];
    }
    return null;
  }

  var MOD_DEP_LABELS = {
    required: '必需',
    optional: '可选',
    embedded: '内置',
    incompatible: '冲突',
    tool: '工具'
  };

  // 前置模组大卡片：点击直接进入该前置的下载页
  function buildModDepCard(dep) {
    var card = document.createElement('button');
    card.type = 'button';
    card.className = 'mod-dep-card';

    var icon = document.createElement('div');
    icon.className = 'mod-dep-card-icon';
    fillModIcon(icon, dep.icon, dep.name);
    card.appendChild(icon);

    var main = document.createElement('div');
    main.className = 'mod-dep-card-main';
    var name = document.createElement('span');
    name.className = 'mod-dep-card-name';
    name.textContent = dep.name || dep.id;
    var desc = document.createElement('span');
    desc.className = 'mod-dep-card-desc';
    desc.textContent = dep.summary || '点击查看该前置模组';
    desc.title = dep.summary || '';
    main.appendChild(name);
    main.appendChild(desc);
    card.appendChild(main);

    var tag = document.createElement('span');
    tag.className = 'mod-dep-card-tag';
    if (dep.type === 'required') tag.classList.add('is-required');
    tag.textContent = MOD_DEP_LABELS[dep.type] || '前置';
    card.appendChild(tag);

    card.addEventListener('click', function () {
      openDependencyPage(dep);
    });
    return card;
  }

  // 渲染某个文件的前置模组卡片
  function renderModDepCards(container, file) {
    if (!container) return;
    container.innerHTML = '';

    // 只有模组才有前置组件；整合包 / 资源包 / 光影包 / 数据包不显示这一块
    if (modState.type !== 'mod') {
      container.classList.add('is-collapsed');
      return;
    }
    container.classList.remove('is-collapsed');

    var deps = (file && file.dependencies) || [];
    deps = deps.filter(function (dep) {
      return dep.type !== 'incompatible' && dep.type !== 'tool';
    });

    var title = document.createElement('span');
    title.className = 'mod-deps-title';
    title.textContent = deps.length ? '前置模组（' + deps.length + '）' : '前置模组';
    container.appendChild(title);

    if (!deps.length) {
      var none = document.createElement('span');
      none.className = 'mod-deps-empty';
      none.textContent = '该文件未声明前置模组';
      container.appendChild(none);
      return;
    }

    deps.forEach(function (dep) {
      container.appendChild(buildModDepCard(dep));
    });
  }

  function openDependencyPage(dep) {
    openModPage(
      {
        source: dep.source || modDetail.source,
        id: dep.id,
        name: dep.name,
        slug: dep.slug || '',
        icon: dep.icon || '',
        summary: dep.summary || '',
        downloads: dep.downloads || 0
      },
      true,
      false
    );
  }

  function updateModDownloadState() {
    var file = selectedModFile();

    if (modVersionDescEl) {
      var total = (modDetail.versions || []).length;
      var shown = (modDetail.filtered || []).length;
      var head = total
        ? '共 ' + total + ' 个文件' + (shown !== total ? '，筛选后 ' + shown + ' 个' : '')
        : '';
      var detail = '';
      if (file) {
        var parts = [];
        if (file.file_name) parts.push(file.file_name);
        if (file.size) parts.push(formatSize(file.size));
        if (file.date) parts.push(file.date);
        detail = '已选择：' + parts.join(' · ');
      }
      var emptyHint =
        modState.type === 'modpack'
          ? '展开游戏版本并选择文件后，点「安装整合包」会自动下载对应游戏本体与全部模组'
          : '点击游戏版本展开，再选择要下载的文件';
      modVersionDescEl.textContent =
        [head, detail].filter(Boolean).join(' · ') || emptyHint;
    }

    if (btnDownloadMod) {
      btnDownloadMod.textContent =
        modState.type === 'modpack' ? '安装整合包' : '下载';
      btnDownloadMod.disabled = !file || modDetail.busy;
      btnDownloadMod.title = file && !file.url ? '该文件不允许第三方下载' : '';
    }
  }

  // 单个文件行
  function buildModFileRow(item) {
    var row = document.createElement('button');
    row.type = 'button';
    row.className = 'dl-version-item';
    row.dataset.id = item.id;
    if (item.id === modDetail.selected) row.classList.add('is-active');

    var name = document.createElement('span');
    name.className = 'dl-version-name';
    name.textContent = item.name || item.file_name || item.id;
    if (item.file_name) name.title = item.file_name;
    row.appendChild(name);

    var tag = document.createElement('span');
    tag.className = 'dl-version-tag';
    if (item.type) {
      tag.textContent = item.type;
      if (item.type !== '正式版') tag.classList.add('is-muted');
    } else {
      tag.textContent = formatSize(item.size);
      tag.classList.add('is-muted');
    }
    row.appendChild(tag);

    // 整合包的文件同样是分加载器的，标出来避免装错版本
    if (modState.type === 'modpack' && (item.loaders || []).length) {
      var names = [];
      item.loaders.forEach(function (loaderId) {
        var key = String(loaderId || '').toLowerCase();
        var label = '';
        MOD_LOADER_OPTIONS.forEach(function (option) {
          if (option.id === key) label = option.label;
        });
        if (label) names.push(label);
      });
      if (names.length) {
        var loaderTag = document.createElement('span');
        loaderTag.className = 'dl-version-tag is-muted';
        loaderTag.textContent = names.join(' / ');
        row.appendChild(loaderTag);
      }
    }

    var required = (item.dependencies || []).filter(function (dep) {
      return dep.type === 'required';
    });
    if (required.length) {
      var depTag = document.createElement('span');
      depTag.className = 'dl-version-tag is-dep';
      depTag.textContent = '+' + required.length + ' 前置';
      depTag.title = required
        .map(function (dep) {
          return dep.name || dep.id;
        })
        .join('、');
      row.appendChild(depTag);
    }

    row.addEventListener('click', function () {
      modDetail.selected = item.id;
      if (modVersionListEl) {
        var rows = modVersionListEl.querySelectorAll('.dl-version-item');
        Array.prototype.forEach.call(rows, function (child) {
          child.classList.toggle('is-active', child.dataset.id === item.id);
        });

        // 同组内的前置卡片跟着选中的文件走
        var wrap = row.closest ? row.closest('.mod-vgroup') : null;
        if (wrap) {
          renderModDepCards(wrap.querySelector('.mod-vgroup-deps'), item);
        }
      }
      updateModDownloadState();
    });

    return row;
  }

  // 展开 / 收起某个游戏版本分组（原地切换，保留过渡动画）
  function toggleModGroup(version) {
    if (!version || !modVersionListEl) return;

    var opening = modDetail.expandedGame !== version;
    modDetail.expandedGame = opening ? version : '';

    var group = opening ? currentModGroup() : null;
    if (opening) {
      modDetail.selected = group && group.files.length ? group.files[0].id : '';
    }

    var wraps = modVersionListEl.querySelectorAll('.mod-vgroup');
    Array.prototype.forEach.call(wraps, function (el) {
      el.classList.toggle('is-open', el.dataset.version === version && opening);
    });

    var rows = modVersionListEl.querySelectorAll('.dl-version-item');
    Array.prototype.forEach.call(rows, function (row) {
      row.classList.toggle('is-active', row.dataset.id === modDetail.selected);
    });

    if (opening && group) {
      var current = modVersionListEl.querySelector(
        '.mod-vgroup[data-version="' + version + '"]'
      );
      if (current) {
        renderModDepCards(
          current.querySelector('.mod-vgroup-deps'),
          group.files.length ? group.files[0] : null
        );
      }
    }

    updateModDownloadState();
  }

  function renderModVersions(hint) {
    if (!modVersionListEl) return;
    modVersionListEl.innerHTML = '';

    if (hint) {
      var tip = document.createElement('div');
      tip.className = 'dl-version-empty';
      tip.textContent = hint;
      modVersionListEl.appendChild(tip);
      return;
    }

    var groups = modDetail.groups || [];
    if (!groups.length) {
      var empty = document.createElement('div');
      empty.className = 'dl-version-empty';
      empty.textContent = modDetail.versions.length
        ? '当前筛选条件下没有文件'
        : '暂无可选文件';
      modVersionListEl.appendChild(empty);
      return;
    }

    groups.forEach(function (group) {
      var wrap = document.createElement('div');
      wrap.className = 'mod-vgroup';
      wrap.dataset.version = group.version;
      var open = group.version === modDetail.expandedGame;
      if (open) wrap.classList.add('is-open');

      var head = document.createElement('button');
      head.type = 'button';
      head.className = 'mod-vgroup-head';

      var name = document.createElement('span');
      name.className = 'mod-vgroup-name';
      name.textContent = group.version;
      head.appendChild(name);

      var count = document.createElement('span');
      count.className = 'mod-vgroup-count';
      count.textContent = group.files.length + ' 个文件';
      head.appendChild(count);

      var chevron = document.createElement('span');
      chevron.className = 'mod-vgroup-chevron';
      chevron.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="m9 5 7 7-7 7"/></svg>';
      head.appendChild(chevron);

      head.addEventListener('click', function () {
        toggleModGroup(group.version);
      });
      wrap.appendChild(head);

      // grid 0fr→1fr 过渡实现展开动画，内层负责裁剪
      var body = document.createElement('div');
      body.className = 'mod-vgroup-body';

      var inner = document.createElement('div');
      inner.className = 'mod-vgroup-inner';

      // 前置模组放在文件列表上方
      var depsBox = document.createElement('div');
      depsBox.className = 'mod-vgroup-deps';
      var depsFile = null;
      if (itemInGroup(group, modDetail.selected)) {
        depsFile = fileInGroup(group, modDetail.selected);
      } else if (group.files.length) {
        depsFile = group.files[0];
      }
      renderModDepCards(depsBox, depsFile);
      inner.appendChild(depsBox);

      group.files.forEach(function (item) {
        inner.appendChild(buildModFileRow(item));
      });

      body.appendChild(inner);
      wrap.appendChild(body);

      modVersionListEl.appendChild(wrap);
    });
  }

  function itemInGroup(group, fileId) {
    for (var i = 0; i < group.files.length; i += 1) {
      if (group.files[i].id === fileId) return true;
    }
    return false;
  }

  function fileInGroup(group, fileId) {
    for (var i = 0; i < group.files.length; i += 1) {
      if (group.files[i].id === fileId) return group.files[i];
    }
    return null;
  }

  // 加载器标识归一化：CurseForge 给的是 "Fabric"/"NeoForge"，Modrinth 是小写
  function modLoaderKey(value) {
    var text = String(value || '').trim().toLowerCase();
    if (text === 'forge' || text === 'fabric' || text === 'quilt' || text === 'neoforge') {
      return text;
    }
    return '';
  }

  function collectModLoaders(versions) {
    var found = [];
    (versions || []).forEach(function (item) {
      (item.loaders || []).forEach(function (raw) {
        var key = modLoaderKey(raw);
        if (key && found.indexOf(key) === -1) found.push(key);
      });
    });
    return found;
  }

  function parseGameVersionNumbers(value) {
    var parts = String(value || '').split(/[^0-9]+/);
    var numbers = [];
    for (var i = 0; i < parts.length; i += 1) {
      if (parts[i]) numbers.push(Number(parts[i]));
    }
    return numbers;
  }

  function compareGameVersionDesc(a, b) {
    var left = parseGameVersionNumbers(a);
    var right = parseGameVersionNumbers(b);
    if (!left.length || !right.length) {
      // 认不出数字的版本（如“未知版本”）排到最后
      if (!left.length && !right.length) return 0;
      return left.length ? -1 : 1;
    }
    var length = Math.max(left.length, right.length);
    for (var i = 0; i < length; i += 1) {
      var va = i < left.length ? left[i] : -1;
      var vb = i < right.length ? right[i] : -1;
      if (va !== vb) return vb - va;
    }
    return String(b).localeCompare(String(a));
  }

  // 按游戏版本把文件分组：1.21、1.20 这样的分组，点开才显示具体文件
  function groupModVersions(list) {
    var groups = [];
    (list || []).forEach(function (item) {
      var key = (item.game_versions && item.game_versions[0]) || '未知版本';
      var target = null;
      for (var i = 0; i < groups.length; i += 1) {
        if (groups[i].version === key) {
          target = groups[i];
          break;
        }
      }
      if (!target) {
        target = { version: key, files: [] };
        groups.push(target);
      }
      target.files.push(item);
    });
    groups.sort(function (a, b) {
      return compareGameVersionDesc(a.version, b.version);
    });
    return groups;
  }

  // 详情页的加载器筛选：模组与整合包都按加载器过滤文件
  function syncModDetailLoaderChips() {
    if (!modDetailLoaderFilters) return;
    $$('[data-loader]', modDetailLoaderFilters).forEach(function (button) {
      button.classList.toggle(
        'is-active',
        (button.dataset.loader || '') === (modDetail.loader || '')
      );
    });
  }

  function updateModDetailLoaderVisibility() {
    if (!modDetailLoaderFilters) return;
    modDetailLoaderFilters.classList.toggle(
      'is-collapsed',
      !(modState.type === 'mod' || modState.type === 'modpack')
    );
  }

  // 加载器筛选 + 分组，全部在前端完成
  function applyModFilter() {
    // 模组与整合包按加载器过滤，资源包 / 光影包 / 数据包不过滤
    var loader =
      modState.type === 'mod' || modState.type === 'modpack' ? modDetail.loader : '';
    modDetail.filtered = (modDetail.versions || []).filter(function (item) {
      if (!loader) return true;
      var loaders = item.loaders || [];
      for (var i = 0; i < loaders.length; i += 1) {
        if (modLoaderKey(loaders[i]) === loader) return true;
      }
      return false;
    });

    modDetail.groups = groupModVersions(modDetail.filtered);

    // 展开的分组：保持原来的，失效则默认展开第一个
    var expanded = modDetail.expandedGame;
    var stillThere = false;
    for (var i = 0; i < modDetail.groups.length; i += 1) {
      if (modDetail.groups[i].version === expanded) {
        stillThere = true;
        break;
      }
    }
    if (!stillThere) {
      modDetail.expandedGame = modDetail.groups.length
        ? modDetail.groups[0].version
        : '';
    }

    // 选中的文件必须属于已展开的分组
    var group = currentModGroup();
    var exists = false;
    if (group) {
      for (var j = 0; j < group.files.length; j += 1) {
        if (group.files[j].id === modDetail.selected) {
          exists = true;
          break;
        }
      }
    }
    if (!exists) {
      modDetail.selected = group && group.files.length ? group.files[0].id : '';
    }

    renderModVersions('');
    updateModDownloadState();
  }

  function loadModVersions() {
    if (!modDetail.source || !modDetail.id) return;

    var requestId = ++modDetail.requestId;
    modDetail.loading = true;
    modDetail.versions = [];
    modDetail.filtered = [];
    modDetail.groups = [];
    modDetail.available = [];
    modDetail.selected = '';
    renderModVersions('正在获取全部文件版本…');
    updateModDownloadState();
    setModDetailStatus('正在获取全部文件版本…', -1, true);

    // 不带游戏版本 / 加载器条件，先拿全部文件
    callApi(
      'fetch_mod_versions',
      modDetail.source,
      modDetail.id,
      '',
      '',
      60,
      modState.type
    ).then(
      function (result) {
        if (requestId !== modDetail.requestId) return;

        modDetail.loading = false;
        if (!result) {
          renderModVersions('启动器接口未就绪');
          setModDetailStatus('启动器接口未就绪', -1, false);
          return;
        }
        if (!result.ok) {
          renderModVersions(result.error || '获取文件版本失败');
          setModDetailStatus(result.error || '获取文件版本失败', -1, false);
          return;
        }

        modDetail.versions = result.versions || [];
        applyModFilter();
        setModDetailStatus('共 ' + modDetail.versions.length + ' 个文件版本', 100, false);
      }
    );
  }

  // pushHistory: 从“前置模组卡片”进来时，把当前模组压栈，返回可回到上一个
  function openModPage(item, pushHistory, keepHistory) {
    if (pushHistory && modDetail.id) {
      modDetail.history.push({
        source: modDetail.source,
        id: modDetail.id,
        name: modDetail.name,
        slug: modDetail.slug,
        icon: modDetail.icon,
        summary: modDetail.summary,
        author: modDetail.author,
        downloads: modDetail.downloads
      });
    } else if (!keepHistory) {
      modDetail.history = [];
    }

    modDetail.source = item.source;
    modDetail.id = item.id;
    modDetail.name = item.name || item.slug || item.id;
    modDetail.slug = item.slug || '';
    modDetail.icon = item.icon || '';
    modDetail.summary = item.summary || '';
    modDetail.author = item.author || '';
    modDetail.downloads = item.downloads || 0;
    modDetail.loader =
      modState.type === 'mod' || modState.type === 'modpack' ? modState.loader : '';
    updateModDetailLoaderVisibility();
    syncModDetailLoaderChips();
    modDetail.versions = [];
    modDetail.filtered = [];
    modDetail.groups = [];
    modDetail.available = [];
    modDetail.expandedGame = '';
    modDetail.selected = '';
    modDetail.busy = false;

    if (modDetailTitleEl) {
      modDetailTitleEl.textContent = '下载' + contentLabel(modState.type);
    }
    if (modDetailSubtitleEl) {
      modDetailSubtitleEl.textContent = [
        MOD_SOURCE_LABELS[item.source] || item.source,
        item.author,
        formatCount(item.downloads) + ' 次下载'
      ]
        .filter(Boolean)
        .join(' · ');
    }
    if (modDetailNameEl) modDetailNameEl.textContent = modDetail.name;
    if (modDetailDescEl) modDetailDescEl.textContent = item.summary || '';
    if (modDetailSourceEl) {
      modDetailSourceEl.textContent = MOD_SOURCE_LABELS[item.source] || item.source;
    }
    fillModIcon(modDetailIconEl, modDetail.icon, modDetail.name);
    renderModVersions('正在获取全部文件版本…');

    switchView('mod-detail');
    loadModVersions();
  }

  function startModDownload(file, dest) {
    var fileLabel = file.name || file.file_name || modDetail.name;
    var taskId = createDownloadTask({
      name: modDetail.name + ' · ' + fileLabel,
      versionId: modDetail.id,
      loader: modDetail.loader
    });

    setModDetailStatus('正在下载 ' + (file.file_name || fileLabel), -1, true);
    callApi(
      'download_mod',
      file.url,
      dest,
      taskId,
      file.file_name || fileLabel,
      modState.type
    ).then(function (result) {
      modDetail.busy = false;
      updateModDownloadState();

      if (!result) {
        finishDownloadTask(taskId, false, '启动器接口未就绪');
        setModDetailStatus('启动器接口未就绪', -1, false);
        return;
      }
      if (result.ok) {
        finishDownloadTask(taskId, true, '已保存到 ' + result.path);
        setModDetailStatus('已保存到 ' + result.path, 100, false);
        toast('已保存 ' + (file.file_name || contentLabel(modState.type) + '文件'));
      } else {
        finishDownloadTask(taskId, false, result.error || '下载失败');
        setModDetailStatus(result.error || '下载失败', -1, false);
        toast(result.error || '下载失败');
      }
    });
  }

  // 整合包：下载本体 + 加载器 + 包内模组，一步装成可启动的版本
  // 整合包：先读取包信息，确认版本名后再下载本体 + 加载器 + 包内模组
  function installModpack() {
    if (modDetail.busy) return;

    var file = selectedModFile();
    if (!file) {
      toast('请先选择整合包版本');
      return;
    }
    if (!file.url) {
      toast('该文件不允许第三方下载，请到源站点获取');
      return;
    }

    modDetail.busy = true;
    updateModDownloadState();

    var taskId = createDownloadTask({
      name: modDetail.name + ' · ' + (file.name || file.file_name || '整合包'),
      versionId: modDetail.id,
      loader: modDetail.loader
    });

    var settled = false;
    function cancelInstall(message) {
      if (settled) return;
      settled = true;
      modDetail.busy = false;
      updateModDownloadState();
      finishDownloadTask(taskId, false, message || '已取消安装');
      setModDetailStatus(message || '已取消安装', -1, false);
    }

    setModDetailStatus('正在读取整合包信息…', -1, true);

    callApi(
      'prepare_modpack',
      file.url,
      file.file_name || file.name || '',
      modDetail.name,
      taskId
    ).then(function (info) {
      if (settled) return;
      if (!info) {
        cancelInstall('启动器接口未就绪');
        return;
      }
      if (!info.ok) {
        cancelInstall(info.error || '整合包读取失败');
        return;
      }

      var detail = [info.name || modDetail.name, info.mc_version]
        .filter(Boolean)
        .join(' · ');
      if (info.loader && info.loader !== 'vanilla') {
        detail +=
          '（' + loaderLabel(info.loader) + ' ' + (info.loader_version || '') + '）';
      }
      var tip =
        '整合包：' +
        detail +
        '，共 ' +
        (info.file_count || 0) +
        ' 个文件。安装会下载对应游戏本体，请输入新版本的名字：';

      askConfirm(
        tip,
        function (name) {
          if (settled) return;
          if (!name) {
            cancelInstall('已取消安装');
            toast('已取消安装整合包');
            return;
          }
          settled = true;
          runModpackInstall(info, file, name, taskId);
        },
        '开始安装',
        info.suggest_name || (info.mc_version || '') + '-' + modDetail.name,
        function () {
          cancelInstall('已取消安装');
        }
      );
    });
  }

  function runModpackInstall(info, file, name, taskId) {
    setModDetailStatus('正在安装整合包 ' + name + '…', -1, true);

    callApi(
      'install_modpack',
      file ? file.url : '',
      file ? file.file_name || file.name || '' : '',
      modDetail.name,
      !!downloadState.mirror,
      taskId,
      name,
      info.archive || ''
    ).then(function (result) {
      modDetail.busy = false;
      updateModDownloadState();

      if (!result) {
        finishDownloadTask(taskId, false, '启动器接口未就绪');
        setModDetailStatus('启动器接口未就绪', -1, false);
        return;
      }
      if (!result.ok) {
        finishDownloadTask(taskId, false, result.error || '安装失败');
        setModDetailStatus(result.error || '安装失败', -1, false);
        toast(result.error || '安装失败');
        return;
      }

      var loaderText = '';
      if (result.loader && result.loader !== 'vanilla') {
        loaderText =
          ' · ' + loaderLabel(result.loader) + ' ' + (result.loader_version || '');
      }

      finishDownloadTask(taskId, true, result.summary || '整合包安装完成');
      setModDetailStatus(
        '已创建版本 ' + result.version + loaderText + '，' + (result.summary || ''),
        100,
        false
      );
      toast(
        result.warning
          ? '整合包已安装，但' + result.warning
          : '整合包安装完成：' + result.version + loaderText
      );
      // 新版本要立刻出现在启动页的版本列表里
      refreshVersionList();
    });
  }

  function downloadModFile() {
    if (modDetail.busy) return;

    var file = selectedModFile();
    if (!file) {
      toast('请先选择文件版本');
      return;
    }
    if (!file.url) {
      toast('该文件不允许第三方下载，请到源站点获取');
      return;
    }

    modDetail.busy = true;
    updateModDownloadState();
    // 打开保存对话框，默认定位到当前版本的对应文件夹
    chooseModSavePath(file);
  }

  function chooseModSavePath(file) {
    setModDetailStatus('正在选择保存位置…', -1, true);
    callApi(
      'pick_mod_save_path',
      file.file_name || '',
      selectedVersion ? selectedVersion.name : '',
      versionRoot,
      modState.type
    ).then(function (chosen) {
      if (!chosen) {
        modDetail.busy = false;
        updateModDownloadState();
        setModDetailStatus('启动器接口未就绪', -1, false);
        return;
      }
      if (!chosen.ok) {
        modDetail.busy = false;
        updateModDownloadState();
        if (chosen.cancelled) {
          setModDetailStatus('已取消下载', -1, false);
          return;
        }
        setModDetailStatus(chosen.error || '无法选择保存位置', -1, false);
        toast(chosen.error || '无法选择保存位置');
        return;
      }
      startModDownload(file, chosen.path);
    });
  }

  if (modSourceSwitch) {
    $$('.seg', modSourceSwitch).forEach(function (button) {
      button.addEventListener('click', function () {
        var next = button.dataset.source || 'all';
        if (next === modState.source) return;
        modState.source = next;
        $$('.seg', modSourceSwitch).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        searchMods();
      });
    });
  }

  if (btnModSearch) {
    btnModSearch.addEventListener('click', searchMods);
  }

  if (btnModMore) {
    btnModMore.addEventListener('click', loadMoreMods);
  }

  // 列表滚到底部时自动续页
  if (modSearchListEl) {
    modSearchListEl.addEventListener('scroll', function () {
      if (modState.loading || modState.loadingMore || !modHasMore()) return;
      var rest =
        modSearchListEl.scrollHeight - modSearchListEl.scrollTop - modSearchListEl.clientHeight;
      if (rest < 60) loadMoreMods();
    });
  }

  if (modSearchInput) {
    modSearchInput.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      searchMods();
    });
  }

  if (btnRefreshModVersions) {
    btnRefreshModVersions.addEventListener('click', loadModVersions);
  }

  // 详情页切换加载器：就地过滤已拉取的文件列表
  if (modDetailLoaderFilters) {
    $$('[data-loader]', modDetailLoaderFilters).forEach(function (button) {
      button.addEventListener('click', function () {
        var loader = button.dataset.loader || '';
        if (loader === modDetail.loader) return;
        modDetail.loader = loader;
        syncModDetailLoaderChips();
        applyModFilter();
      });
    });
  }

  if (btnDownloadMod) {
    btnDownloadMod.addEventListener('click', function () {
      if (modState.type === 'modpack') installModpack();
      else downloadModFile();
    });
  }

  if (btnBackFromMod) {
    btnBackFromMod.addEventListener('click', function () {
      if (modDetail.busy) return;
      setModDetailStatus('', -1, false);

      // 从“前置模组”点进来时，返回上一个模组页
      var previous = modDetail.history.pop();
      if (previous) {
        openModPage(previous, false, true);
        return;
      }
      // 返回合并后的下载板块，并停留在原来的内容类型
      switchView('download');
      setDownloadContent(modState.type);
    });
  }

  /* ---------- 应用信息 ---------- */
  var btnOpenAuthorPage = $('#btnOpenAuthorPage');
  if (btnOpenAuthorPage) {
    btnOpenAuthorPage.addEventListener('click', function () {
      callApi('open_external_url', AUTHOR_URL).then(function (result) {
        if (result && !result.ok) toast(result.error || '无法打开浏览器');
      });
    });
  }

  callApi('get_app_info').then(function (info) {
    if (!info) return;
    var versionEl = $('#appVersion');
    if (versionEl && info.version) versionEl.textContent = 'v' + info.version;

    var aboutVersionEl = $('#aboutVersion');
    if (aboutVersionEl && info.version) aboutVersionEl.textContent = 'v' + info.version;

    var aboutAuthorEl = $('#aboutAuthor');
    if (aboutAuthorEl && info.author) aboutAuthorEl.textContent = info.author;

    var dataDirEl = $('#dataDirPath');
    if (dataDirEl && info.data_dir) {
      dataDirEl.textContent = info.data_dir;
      dataDirEl.title = info.data_dir;
    }
  });

  /* ---------- 恢复上次状态 ---------- */
  callApi('get_state').then(function (state) {
    if (!state) return;
    if (state.selected_root) {
      versionRoot = state.selected_root;
      loadVersionRoots(true);
    }
    if (state.selected_version) applyVersion(state.selected_version);

    var settings = state.settings || {};
    applyMemorySettings(settings);
    if (windowModeSelect) {
      windowModeSelect.value = settings.window_mode || 'default';
    }
    if (autoLangSwitch) {
      autoLangSwitch.checked = settings.auto_lang_zh !== false;
    }
    if (versionIsolationSwitch) {
      versionIsolationSwitch.checked = settings.version_isolation !== false;
    }
    applyAppearance(settings);
    applyLaunchInfo(settings, state.java || null);

    // 按全局设置决定先显示哪个板块（未设置时进首页）
    if (defaultViewSelect) {
      defaultViewSelect.value = settings.default_view || 'home';
    }
    applyDefaultView(settings.default_view);

    if (settings.username) {
      savedUsername = settings.username;
      if (offlineNameInput) offlineNameInput.value = savedUsername;
    }
    // 已登录过正版 / 第三方账号就默认用对应身份，否则保持离线登录
    applyAccount(state.account);
    applyAuthlibAccount(state.authlib);

    // 优先还原上次关闭时用的登录方式；对应账号已失效则退回离线
    var savedMode = String(settings.login_mode || '').toLowerCase();
    if (savedMode === 'microsoft' && !msLoggedIn()) savedMode = 'offline';
    if (savedMode === 'authlib' && !authlibLoggedIn()) savedMode = 'offline';
    if (savedMode && LOGIN_LABELS[savedMode]) {
      setLoginMode(savedMode);
      loginModeSaved = savedMode;
    } else {
      loginModeSaved = loginMode;
    }

    loginModeReady = true;
    renderAccountName();
    refreshLaunchAvailability();
  });

  /* ---------- 首页小组件 ---------- */
  var widgetGridEl = $('#widgetGrid');
  var widgetPickerEl = $('#widgetPicker');
  var widgetModal = $('#widgetModal');
  var btnManageWidgets = $('#btnManageWidgets');
  var widgetCloseEl = $('#widgetClose');
  var widgetMaskEl = $('#widgetMask');
  var btnWidgetDone = $('#btnWidgetDone');
  var btnWidgetReset = $('#btnWidgetReset');

  var WIDGET_ICONS = {
    rocket:
      '<svg viewBox="0 0 24 24"><path d="M12 3.6c3.1 1.5 4.9 4.5 4.9 7.9 0 1.9-.9 3.7-2.5 5l-2.4 2-2.4-2c-1.6-1.3-2.5-3.1-2.5-5 0-3.4 1.8-6.4 4.9-7.9z"/><circle cx="12" cy="10.6" r="1.8"/><path d="M9.4 17.2 7.6 19.8M14.6 17.2l1.8 2.6"/></svg>',
    clock:
      '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.2"/><path d="M12 7.4V12l3.1 1.9"/></svg>',
    heart:
      '<svg viewBox="0 0 24 24"><path d="M12 19.8S5.2 15.6 5.2 10.9A3.9 3.9 0 0 1 12 8.2a3.9 3.9 0 0 1 6.8 2.7c0 4.7-6.8 8.9-6.8 8.9z"/></svg>',
    book:
      '<svg viewBox="0 0 24 24"><path d="M5 5.6A2.6 2.6 0 0 1 7.6 3H19v15H7.6A2.6 2.6 0 0 0 5 20.6z"/><path d="M5 5.6v15"/><path d="M9 7.4h6M9 10.8h6"/></svg>',
    coffee:
      '<svg viewBox="0 0 24 24"><path d="M5 8h11v5.4a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/><path d="M16 9.4h1.6a2.4 2.4 0 0 1 0 4.8H16"/><path d="M6.5 20.8h9"/></svg>',
    chip:
      '<svg viewBox="0 0 24 24"><rect x="3.2" y="8" width="17.6" height="8" rx="1.8"/><path d="M7 8V6M11 8V6M15 8V6M7 18v-2M11 18v-2M15 18v-2"/><path d="M7.4 10.8h2.2v2.4H7.4zM12 10.8h2.2v2.4H12z"/></svg>',
    gamepad:
      '<svg viewBox="0 0 24 24"><rect x="3" y="9" width="18" height="7.6" rx="3.4"/><path d="M8 11.4v2.8M6.6 12.8h2.8"/><circle cx="16" cy="12" r=".9"/><circle cx="18" cy="14" r=".9"/></svg>',
    bulb:
      '<svg viewBox="0 0 24 24"><path d="M12 3.6a5.5 5.5 0 0 1 3.1 10.1c-.6.4-.9 1-.9 1.7v.4H9.8v-.4c0-.7-.3-1.3-.9-1.7A5.5 5.5 0 0 1 12 3.6z"/><path d="M9.9 18.2h4.2M10.6 20.5h2.8"/></svg>',
    code:
      '<svg viewBox="0 0 24 24"><path d="M8.6 8.2 4.4 12l4.2 3.8"/><path d="M15.4 8.2 19.6 12l-4.2 3.8"/><path d="M13.4 5.4l-2.8 13.2"/></svg>'
  };

  var WIDGET_TIPS = [
    'Forge 1.12.2 只能用 Java 8 启动，换成 Java 17 会报 launchwrapper 错误。',
    '整合包可以直接把 zip 拖进窗口，省掉点按钮这一步。',
    '版本设置里可以单独指定 Java，不同版本互不影响。',
    '内存分配不是越大越好，给系统留 2 GB 以上会更稳。',
    '启动失败时点「查看日志」，最后几十行通常就能看出原因。',
    '在版本设置里可以单独调整窗口大小和 JVM 参数。',
    '删除版本前先确认没有其它加载器版本依赖它。',
    '把整合包放进 versions 之外的文件夹，也能通过「添加文件夹」加载。'
  ];

  var DEFAULT_WIDGETS = ['quick-launch', 'last-launch', 'play-time', 'mc-wiki'];
  var homeState = { widgets: null, play_seconds: 0, last_launch: null, custom_widgets: [] };
  var homePlayBase = 0;
  var homeSessionStart = Date.now();
  var widgetTickers = [];

  function pad2(value) {
    return value < 10 ? '0' + value : String(value);
  }

  function formatPlayTime(seconds) {
    var total = Math.max(0, Math.floor(Number(seconds) || 0));
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    if (hours > 0) return hours + ' 小时 ' + minutes + ' 分';
    if (minutes > 0) return minutes + ' 分钟';
    return total + ' 秒';
  }

  function formatRelative(timestamp) {
    var time = Number(timestamp) || 0;
    if (!time) return '';
    var seconds = Math.max(0, Math.floor(Date.now() / 1000 - time));
    if (seconds < 60) return '刚刚启动';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    if (seconds < 86400 * 7) return Math.floor(seconds / 86400) + ' 天前';
    var date = new Date(time * 1000);
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function widgetNode(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function currentWidgetIds() {
    return Array.isArray(homeState.widgets) ? homeState.widgets : DEFAULT_WIDGETS.slice();
  }

  // 只滚动指定的滚动容器：scrollIntoView 会连带把祖先和窗口根滚动层一起滚，
  // 表现为切到设置页后整个界面向上浮动
  function scrollInsideScroller(target) {
    var scroller = target.closest('.cards') || target.closest('.widget-grid');
    if (!scroller) return;
    var area = scroller.getBoundingClientRect();
    var rect = target.getBoundingClientRect();
    // 已经完整可见就不动，避免无谓的整页位移
    if (rect.top >= area.top && rect.bottom <= area.bottom) return;
    var top = scroller.scrollTop + (rect.top - area.top) - 8;
    if (top < 0) top = 0;
    scroller.scrollTo({ top: top, behavior: 'smooth' });
  }

  function goSettingsJava() {
    switchView('settings');
    if (!javaSummaryEl) return;
    var field = javaSummaryEl.closest('.field') || javaSummaryEl.closest('.card');
    if (field) scrollInsideScroller(field);
  }

  var WIDGET_DEFS = {
    'quick-launch': {
      name: '快速启动',
      desc: '当前选中的版本，一键开玩',
      icon: 'rocket',
      wide: true,
      render: function (body) {
        var entry = selectedVersion;
        var hasVersion = !!(entry && entry.name);
        body.appendChild(
          widgetNode('div', 'widget-value', hasVersion ? entry.name : '还没有选择版本')
        );
        body.appendChild(
          widgetNode(
            'div',
            'widget-note',
            hasVersion
              ? [entry.version || '', entry.loader || ''].filter(Boolean).join(' · ') || '点击开始游戏'
              : '先去「选择版本」挑一个'
          )
        );
        var btn = widgetNode('button', 'widget-btn', hasVersion ? '启动游戏' : '选择版本');
        btn.type = 'button';
        btn.addEventListener('click', function (event) {
          event.stopPropagation();
          if (hasVersion) startLaunchFlow();
          else openVersionsPage();
        });
        body.appendChild(btn);
      }
    },
    'last-launch': {
      name: '上次启动',
      desc: '最近一次启动的版本与时间',
      icon: 'clock',
      render: function (body) {
        var record = homeState.last_launch;
        body.appendChild(
          widgetNode('div', 'widget-value', record && record.name ? record.name : '暂无记录')
        );
        body.appendChild(
          widgetNode(
            'div',
            'widget-note',
            record && record.time ? formatRelative(record.time) : '启动一次游戏后这里会显示'
          )
        );
      }
    },
    'play-time': {
      name: '陪伴时间',
      desc: '启动器累计陪伴你的时长',
      icon: 'heart',
      render: function (body, card) {
        var value = widgetNode('div', 'widget-value', formatPlayTime(homeState.play_seconds));
        body.appendChild(value);
        body.appendChild(widgetNode('div', 'widget-note', '从第一次打开启动器算起'));
        widgetTickers.push({
          node: card,
          fn: function () {
            value.textContent = formatPlayTime(homeState.play_seconds);
          }
        });
      }
    },
    'mc-wiki': {
      name: 'Minecraft Wiki',
      desc: '合成表、教程与版本资料',
      icon: 'book',
      render: function (body, card) {
        body.appendChild(widgetNode('div', 'widget-value', 'Minecraft Wiki'));
        body.appendChild(widgetNode('div', 'widget-note', '点击用浏览器打开中文 Wiki'));
        card.classList.add('is-clickable');
        card.addEventListener('click', function () {
          callApi('open_external_url', 'https://zh.minecraft.wiki/').then(function (result) {
            if (result && !result.ok) toast(result.error || '无法打开浏览器');
          });
        });
      }
    },
    'java-status': {
      name: 'Java 环境',
      desc: '当前使用的 Java 版本',
      icon: 'coffee',
      render: function (body, card) {
        var info = currentJava;
        body.appendChild(
          widgetNode('div', 'widget-value', info ? 'Java ' + info.major : '读取中…')
        );
        body.appendChild(
          widgetNode(
            'div',
            'widget-note',
            info ? info.vendor || '未知发行版' : '正在读取 Java 信息'
          )
        );
        card.classList.add('is-clickable');
        card.addEventListener('click', goSettingsJava);
      }
    },
    'memory': {
      name: '内存',
      desc: '物理内存与当前可用量',
      icon: 'chip',
      render: function (body, card) {
        var value = widgetNode('div', 'widget-value', '读取中…');
        var note = widgetNode('div', 'widget-note', '正在读取内存信息');
        body.appendChild(value);
        body.appendChild(note);
        callApi('get_memory_state').then(function (state) {
          if (!state || !card.isConnected) return;
          var total = Number(state.memory_total_mb) || 0;
          var available = Number(state.memory_available_mb) || 0;
          value.textContent = total ? formatMemory(available) + ' 可用' : '未知';
          note.textContent = total ? '物理内存共 ' + formatMemory(total) : '系统信息不可用';
        });
        card.classList.add('is-clickable');
        card.addEventListener('click', goSettingsJava);
      }
    },
    'game-status': {
      name: '游戏状态',
      desc: '是否正在运行，可强制结束',
      icon: 'gamepad',
      render: function (body, card) {
        body.appendChild(
          widgetNode(
            'div',
            'widget-value',
            gameRunning ? '运行中 · ' + gameRunningCount + ' 个实例' : '未在运行'
          )
        );
        body.appendChild(
          widgetNode(
            'div',
            'widget-note',
            gameRunning ? '点击可以强制结束进程' : '启动游戏后这里会显示状态'
          )
        );
        if (gameRunning) {
          card.classList.add('is-clickable');
          card.addEventListener('click', function () {
            askConfirm(
              '强制结束所有游戏进程？未保存的游戏进度将会丢失。',
              killGameProcess,
              '结束进程'
            );
          });
        }
      }
    },
    'clock': {
      name: '时钟',
      desc: '当前日期与时间',
      icon: 'clock',
      render: function (body, card) {
        var value = widgetNode('div', 'widget-value', '');
        var note = widgetNode('div', 'widget-note', '');
        body.appendChild(value);
        body.appendChild(note);
        function paint() {
          var now = new Date();
          value.textContent =
            pad2(now.getHours()) + ':' + pad2(now.getMinutes()) + ':' + pad2(now.getSeconds());
          note.textContent =
            now.getFullYear() +
            ' 年 ' +
            (now.getMonth() + 1) +
            ' 月 ' +
            now.getDate() +
            ' 日 星期' +
            '日一二三四五六'.charAt(now.getDay());
        }
        paint();
        widgetTickers.push({ node: card, fn: paint });
      }
    },
    'tip': {
      name: '小贴士',
      desc: '随机一条启动器使用提示',
      icon: 'bulb',
      wide: true,
      render: function (body, card) {
        var text = widgetNode(
          'div',
          'widget-note is-wrap',
          WIDGET_TIPS[Math.floor(Math.random() * WIDGET_TIPS.length)]
        );
        body.appendChild(text);
        card.classList.add('is-clickable');
        card.addEventListener('click', function () {
          var rest = WIDGET_TIPS.filter(function (item) {
            return item !== text.textContent;
          });
          text.textContent = rest[Math.floor(Math.random() * rest.length)] || text.textContent;
        });
      }
    }
  };

  function findCustomWidget(id) {
    var list = Array.isArray(homeState.custom_widgets) ? homeState.custom_widgets : [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i] && list[i].id === id) return list[i];
    }
    return null;
  }

  // 自定义小组件用 iframe + sandbox 呈现：用户写的样式和脚本
  // 都不会污染启动器界面，也拿不到启动器的接口
  function customWidgetDef(id) {
    var item = findCustomWidget(id);
    if (!item) return null;
    return {
      name: item.name || '自定义小组件',
      desc: '自己编写的 HTML',
      icon: 'code',
      wide: !!item.wide,
      custom: true,
      render: function (body) {
        var frame = document.createElement('iframe');
        frame.className = 'widget-frame';
        frame.setAttribute(
          'sandbox',
          'allow-scripts allow-forms allow-popups allow-modals allow-pointer-lock'
        );
        frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.title = item.name || '自定义小组件';
        frame.srcdoc = item.html || '';
        body.appendChild(frame);
      }
    };
  }

  function makeWidget(id) {
    var def = WIDGET_DEFS[id] || customWidgetDef(id);
    if (!def) return null;

    var card = document.createElement('article');
    card.className = 'widget' + (def.wide ? ' is-wide' : '') + (def.custom ? ' is-custom' : '');
    card.dataset.widget = id;

    var head = widgetNode('div', 'widget-head');
    var icon = widgetNode('span', 'widget-icon');
    icon.innerHTML = WIDGET_ICONS[def.icon] || '';
    head.appendChild(icon);
    head.appendChild(widgetNode('span', '', def.name));
    card.appendChild(head);

    var body = widgetNode('div', 'widget-body');
    card.appendChild(body);

    var remove = widgetNode('button', 'widget-remove');
    remove.type = 'button';
    remove.title = '移除小组件';
    remove.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    remove.addEventListener('click', function (event) {
      event.stopPropagation();
      toggleWidget(id, false);
      renderWidgetPicker();
    });
    card.appendChild(remove);

    if (def.custom) {
      // 自定义内容里可能有可以点的东西，只让标题栏充当拖拽把手
      head.classList.add('is-drag-handle');
      head.addEventListener('pointerdown', function (event) {
        beginWidgetDrag(event, card);
      });
    } else {
      card.addEventListener('pointerdown', function (event) {
        beginWidgetDrag(event, card);
      });
    }

    if (def.render) def.render(body, card, id);
    return card;
  }

  /* ---------- 小组件拖拽排序 ---------- */
  var widgetDrag = null;
  var suppressWidgetClick = false;
  var widgetDragFrame = 0;
  var widgetDragPoint = null;
  var widgetFlipTimers = new WeakMap();

  function collectWidgetOrder() {
    if (!widgetGridEl) return [];
    var ids = [];
    Array.prototype.forEach.call(
      widgetGridEl.querySelectorAll('.widget'),
      function (el) {
        if (el.dataset && el.dataset.widget) ids.push(el.dataset.widget);
      }
    );
    return ids;
  }

  function isWidgetCard(el) {
    return !!el && !!el.classList && el.classList.contains('widget');
  }

  function isWidgetSlot(el) {
    return isWidgetCard(el) || (!!el && !!el.classList && el.classList.contains('widget-add'));
  }

  // 读取“未经过变换”的布局位置：offsetLeft/Top 不受正在播放的让位动画影响。
  // 之前用 getBoundingClientRect() 会读到动画中间态，于是判定目标卡片时来回改主意，
  // 卡片就一直抖、鼠标不动也飘，同时每帧都强制重排导致卡顿。
  function widgetLayoutRects() {
    var list = [];
    if (!widgetGridEl) return list;
    var origin = widgetGridEl.getBoundingClientRect();
    var baseX = origin.left - widgetGridEl.scrollLeft;
    var baseY = origin.top - widgetGridEl.scrollTop;
    Array.prototype.forEach.call(widgetGridEl.children, function (el) {
      if (!isWidgetSlot(el)) return;
      list.push({
        el: el,
        left: baseX + el.offsetLeft,
        top: baseY + el.offsetTop,
        width: el.offsetWidth,
        height: el.offsetHeight
      });
    });
    return list;
  }

  function clearWidgetFlip(el) {
    var timer = widgetFlipTimers.get(el);
    if (timer) {
      window.clearTimeout(timer);
      widgetFlipTimers.delete(el);
    }
  }

  // 只在播放让位动画时读视觉位置：带着动画中间态继续插值，卡片才不会跳
  function widgetVisualRects() {
    var list = [];
    if (!widgetGridEl) return list;
    Array.prototype.forEach.call(widgetGridEl.children, function (el) {
      if (!isWidgetSlot(el)) return;
      var rect = el.getBoundingClientRect();
      list.push({ el: el, left: rect.left, top: rect.top });
    });
    return list;
  }

  // FLIP：位置变化先用旧坐标反向偏移，再过渡回 0，形成磁吸让位效果
  function playWidgetFlip(before) {
    if (!before || !before.length) return;
    var now = new Map();
    widgetVisualRects().forEach(function (item) {
      now.set(item.el, item);
    });

    var pending = [];
    before.forEach(function (item) {
      var el = item.el;
      var next = now.get(el);
      if (!el || !el.isConnected || !next) return;
      var dx = item.left - next.left;
      var dy = item.top - next.top;
      if (!dx && !dy) return;
      clearWidgetFlip(el);
      el.style.transition = 'none';
      el.style.transform = 'translate3d(' + dx + 'px,' + dy + 'px,0)';
      pending.push(el);
    });
    if (!pending.length) return;

    // 先写完全部反向偏移，再统一提交一次布局，避免逐张卡片强制重排
    void widgetGridEl.offsetWidth;

    pending.forEach(function (el) {
      el.style.transition = 'transform 0.18s cubic-bezier(0.22, 0.61, 0.36, 1)';
      el.style.transform = '';
      widgetFlipTimers.set(
        el,
        window.setTimeout(function () {
          widgetFlipTimers.delete(el);
          el.style.transition = '';
          el.style.transform = '';
        }, 210)
      );
    });
  }

  function beginWidgetDrag(event, card) {
    if (!widgetGridEl || widgetDrag) return;
    if (event.button || event.isPrimary === false) return;
    var target = event.target;
    if (
      target &&
      target.closest &&
      target.closest('button, a, input, select, textarea, .widget-btn, .widget-remove')
    ) {
      return;
    }
    widgetDrag = {
      card: card,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
      ghost: null,
      ghostX: 0,
      ghostY: 0,
      offsetX: 0,
      offsetY: 0,
      startOrder: '',
      pointerId: event.pointerId,
      captureEl: event.currentTarget
    };
    // 捕获指针：自定义小组件里的 iframe 会吞掉 pointermove，
    // 不捕获的话鼠标一进 iframe 拖拽就僵住，甚至在松手后卡在拖动状态
    if (
      widgetDrag.captureEl &&
      widgetDrag.captureEl.setPointerCapture &&
      typeof event.pointerId === 'number'
    ) {
      try {
        widgetDrag.captureEl.setPointerCapture(event.pointerId);
      } catch (error) {
        /* 捕获失败不影响拖动，只是少了这层保险 */
      }
    }
    document.addEventListener('pointermove', onWidgetPointerMove);
    document.addEventListener('pointerup', endWidgetDrag);
    document.addEventListener('pointercancel', endWidgetDrag);
  }

  function widgetGhostTransform(x, y) {
    return 'translate3d(' + x + 'px,' + y + 'px,0) scale(1.03) rotate(1.2deg)';
  }

  function startWidgetDrag(event) {
    var card = widgetDrag.card;
    var rect = card.getBoundingClientRect();
    widgetDrag.active = true;
    widgetDrag.offsetX = event.clientX - rect.left;
    widgetDrag.offsetY = event.clientY - rect.top;
    widgetDrag.startOrder = collectWidgetOrder().join(',');
    widgetDrag.ghostX = rect.left;
    widgetDrag.ghostY = rect.top;

    var ghost = card.cloneNode(true);
    ghost.classList.add('widget-ghost');
    ghost.style.width = rect.width + 'px';
    ghost.style.height = rect.height + 'px';
    ghost.style.transform = widgetGhostTransform(widgetDrag.ghostX, widgetDrag.ghostY);
    document.body.appendChild(ghost);
    widgetDrag.ghost = ghost;

    card.classList.add('is-placeholder');
    widgetGridEl.classList.add('is-sorting');
    document.body.classList.add('is-widget-dragging');
  }

  function moveWidgetGhost(x, y) {
    if (!widgetDrag || !widgetDrag.ghost) return;
    widgetDrag.ghostX = x - widgetDrag.offsetX;
    widgetDrag.ghostY = y - widgetDrag.offsetY;
    widgetDrag.ghost.style.transform = widgetGhostTransform(
      widgetDrag.ghostX,
      widgetDrag.ghostY
    );
  }

  function updateWidgetTarget(x, y) {
    var card = widgetDrag.card;
    // 目标判定只看稳定布局，动画中间态不参与比较
    var layout = widgetLayoutRects();
    var best = null;
    var bestScore = Infinity;
    layout.forEach(function (item) {
      if (item.el === card || !isWidgetCard(item.el)) return;
      var cx = item.left + item.width / 2;
      var cy = item.top + item.height / 2;
      var score = Math.abs(x - cx) + Math.abs(y - cy) * 1.6;
      if (score < bestScore) {
        bestScore = score;
        best = item;
      }
    });
    if (!best) return;

    var after = x > best.left + best.width / 2;
    var reference = after ? best.el.nextElementSibling : best.el;
    if (reference === card) return;
    if (!after && card.nextElementSibling === best.el) return;

    var before = widgetVisualRects();
    widgetGridEl.insertBefore(card, reference || null);
    playWidgetFlip(before);
  }

  // 合并到每帧更新：一帧只做一次测量与一次让位动画，
  // 不再按 pointermove 的高频事件重复强制重排
  function flushWidgetDrag() {
    widgetDragFrame = 0;
    if (!widgetDrag || !widgetDrag.active || !widgetDragPoint) return;
    var x = widgetDragPoint.x;
    var y = widgetDragPoint.y;

    moveWidgetGhost(x, y);

    // 拖到网格上下边缘时持续自动滚动，直到滚不动为止
    var gridRect = widgetGridEl.getBoundingClientRect();
    var scrolled = false;
    var top = widgetGridEl.scrollTop;
    if (y < gridRect.top + 56) {
      widgetGridEl.scrollTop = Math.max(0, top - 16);
      scrolled = widgetGridEl.scrollTop !== top;
    } else if (y > gridRect.bottom - 56) {
      widgetGridEl.scrollTop = top + 16;
      scrolled = widgetGridEl.scrollTop !== top;
    }

    updateWidgetTarget(x, y);

    if (scrolled) widgetDragFrame = window.requestAnimationFrame(flushWidgetDrag);
  }

  function onWidgetPointerMove(event) {
    if (!widgetDrag) return;
    if (!widgetDrag.active) {
      var dx = event.clientX - widgetDrag.startX;
      var dy = event.clientY - widgetDrag.startY;
      if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
      startWidgetDrag(event);
    }
    if (!widgetDrag || !widgetDrag.active) return;

    if (event.cancelable) event.preventDefault();
    widgetDragPoint = { x: event.clientX, y: event.clientY };
    if (!widgetDragFrame) {
      widgetDragFrame = window.requestAnimationFrame(flushWidgetDrag);
    }
  }

  function endWidgetDrag() {
    if (widgetDrag && widgetDrag.captureEl && widgetDrag.captureEl.releasePointerCapture) {
      try {
        widgetDrag.captureEl.releasePointerCapture(widgetDrag.pointerId);
      } catch (error) {
        /* 指针已经失效，忽略 */
      }
    }
    document.removeEventListener('pointermove', onWidgetPointerMove);
    document.removeEventListener('pointerup', endWidgetDrag);
    document.removeEventListener('pointercancel', endWidgetDrag);
    if (widgetDragFrame) {
      window.cancelAnimationFrame(widgetDragFrame);
      widgetDragFrame = 0;
    }
    widgetDragPoint = null;
    if (!widgetDrag) return;

    var drag = widgetDrag;
    widgetDrag = null;
    if (!drag.active) return;

    drag.card.classList.remove('is-placeholder');
    widgetGridEl.classList.remove('is-sorting');
    document.body.classList.remove('is-widget-dragging');

    if (drag.ghost && drag.ghost.parentNode) {
      drag.ghost.parentNode.removeChild(drag.ghost);

      // 纸张落下：卡片自身可能还带着让位动画，先清掉再量真实位置
      var landed = drag.card;
      clearWidgetFlip(landed);
      landed.style.transition = 'none';
      landed.style.transform = '';
      void landed.offsetWidth;
      var cardRect = landed.getBoundingClientRect();
      var offsetX = drag.ghostX - cardRect.left;
      var offsetY = drag.ghostY - cardRect.top;
      if (offsetX || offsetY) {
        landed.style.transform =
          'translate3d(' + offsetX + 'px,' + offsetY + 'px,0) scale(1.03)';
        void landed.offsetWidth;
        landed.style.transition = 'transform 0.18s ease';
        landed.style.transform = '';
        window.setTimeout(function () {
          landed.style.transition = '';
          landed.style.transform = '';
        }, 220);
      } else {
        landed.style.transition = '';
      }
    }

    var order = collectWidgetOrder();
    if (order.join(',') !== drag.startOrder) {
      homeState.widgets = order;
      saveHomeState({ widgets: order });
    }

    // 拖拽结束后紧跟的 click 不应该触发卡片自身的点击行为
    suppressWidgetClick = true;
    window.setTimeout(function () {
      suppressWidgetClick = false;
    }, 320);
  }

  if (widgetGridEl) {
    widgetGridEl.addEventListener(
      'click',
      function (event) {
        if (!suppressWidgetClick) return;
        event.preventDefault();
        event.stopPropagation();
      },
      true
    );
  }

  function renderWidgets() {
    if (!widgetGridEl) return;
    widgetTickers = [];
    widgetGridEl.innerHTML = '';

    var ids = currentWidgetIds();
    ids.forEach(function (id) {
      var card = makeWidget(id);
      if (card) widgetGridEl.appendChild(card);
    });

    var add = widgetNode('button', 'widget-add');
    add.type = 'button';
    add.innerHTML =
      '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg><span>添加小组件</span>';
    add.addEventListener('click', openWidgetModal);
    widgetGridEl.appendChild(add);
  }

  function renderWidgetPicker() {
    if (!widgetPickerEl) return;
    widgetPickerEl.innerHTML = '';
    var ids = currentWidgetIds();

    Object.keys(WIDGET_DEFS).forEach(function (id) {
      var def = WIDGET_DEFS[id];
      var on = ids.indexOf(id) >= 0;

      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'widget-option' + (on ? ' is-on' : '');
      item.dataset.widget = id;

      var icon = widgetNode('span', 'widget-icon');
      icon.innerHTML = WIDGET_ICONS[def.icon] || '';
      item.appendChild(icon);

      var text = widgetNode('span', 'widget-option-text');
      text.appendChild(widgetNode('span', 'widget-option-name', def.name));
      text.appendChild(widgetNode('span', 'widget-option-desc', def.desc));
      item.appendChild(text);

      var check = widgetNode('span', 'widget-option-check');
      check.innerHTML = '<svg viewBox="0 0 24 24"><path d="M5 12.5 10 17l9-10"/></svg>';
      item.appendChild(check);

      item.addEventListener('click', function () {
        toggleWidget(id, !on);
        renderWidgetPicker();
      });
      widgetPickerEl.appendChild(item);
    });

    widgetPickerEl.appendChild(
      widgetNode('span', 'widget-picker-group-title', '自定义 HTML')
    );

    var customs = Array.isArray(homeState.custom_widgets) ? homeState.custom_widgets : [];
    customs.forEach(function (entry) {
      widgetPickerEl.appendChild(makeCustomPickerItem(entry, ids.indexOf(entry.id) >= 0));
    });

    var create = document.createElement('button');
    create.type = 'button';
    create.className = 'widget-option is-add';
    var createIcon = widgetNode('span', 'widget-icon');
    createIcon.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>';
    create.appendChild(createIcon);
    var createText = widgetNode('span', 'widget-option-text');
    createText.appendChild(widgetNode('span', 'widget-option-name', '新建自定义小组件'));
    createText.appendChild(widgetNode('span', 'widget-option-desc', '粘贴 HTML 或从文件导入'));
    create.appendChild(createText);
    create.addEventListener('click', function () {
      openCustomWidgetEditor(null);
    });
    widgetPickerEl.appendChild(create);
  }

  function makeCustomPickerItem(entry, on) {
    var item = document.createElement('div');
    item.className = 'widget-option has-actions' + (on ? ' is-on' : '');
    item.dataset.widget = entry.id;

    var icon = widgetNode('span', 'widget-icon');
    icon.innerHTML = WIDGET_ICONS.code || '';
    item.appendChild(icon);

    var text = widgetNode('span', 'widget-option-text');
    text.appendChild(widgetNode('span', 'widget-option-name', entry.name || '自定义小组件'));
    text.appendChild(widgetNode('span', 'widget-option-desc', '自己编写的 HTML'));
    item.appendChild(text);

    var actions = widgetNode('span', 'widget-option-actions');
    actions.appendChild(
      widgetOptionAction('编辑', '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14.5 5.5 18.5 9.5"/>', function () {
        openCustomWidgetEditor(entry);
      })
    );
    actions.appendChild(
      widgetOptionAction('删除', '<path d="M5 7h14"/><path d="M9 7V5h6v2"/><path d="M7 7l1 12h8l1-12"/>', function () {
        askConfirm(
          '删除自定义小组件「' + (entry.name || '') + '」？删除后内容无法恢复。',
          function () {
            callApi('delete_custom_widget', entry.id).then(function (state) {
              if (!state || !state.ok) {
                toast((state && state.error) || '删除失败');
                return;
              }
              applyHomeState(state);
              renderWidgets();
              renderWidgetPicker();
              toast('已删除自定义小组件');
            });
          },
          '删除'
        );
      })
    );
    item.appendChild(actions);

    var check = widgetNode('span', 'widget-option-check');
    check.innerHTML = '<svg viewBox="0 0 24 24"><path d="M5 12.5 10 17l9-10"/></svg>';
    item.appendChild(check);

    item.addEventListener('click', function () {
      toggleWidget(entry.id, !on);
      renderWidgetPicker();
    });
    return item;
  }

  function widgetOptionAction(title, path, onRun) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'widget-option-action';
    button.title = title;
    button.innerHTML = '<svg viewBox="0 0 24 24">' + path + '</svg>';
    button.addEventListener('click', function (event) {
      event.stopPropagation();
      onRun();
    });
    return button;
  }

  function toggleWidget(id, enable) {
    var list = currentWidgetIds().slice();
    var index = list.indexOf(id);
    if (enable && index < 0) list.push(id);
    if (!enable && index >= 0) list.splice(index, 1);
    homeState.widgets = list;
    saveHomeState({ widgets: list });
    renderWidgets();
  }

  function redrawWidget(id) {
    // 早期调用（小组件模块尚未初始化）、或正在拖动排序时不重绘
    if (!widgetGridEl || !WIDGET_DEFS || widgetDrag) return;
    var old = widgetGridEl.querySelector('.widget[data-widget="' + id + '"]');
    if (!old || !old.parentNode) return;
    var fresh = makeWidget(id);
    if (fresh) old.parentNode.replaceChild(fresh, old);
  }

  function tickWidgets() {
    for (var i = 0; i < widgetTickers.length; i += 1) {
      var item = widgetTickers[i];
      if (!item || !item.node || !item.node.isConnected) continue;
      try {
        item.fn();
      } catch (error) {
        /* 单个小组件出错不影响其它 */
      }
    }
  }

  // 后端每次都会回传完整首页状态，统一在这里同步
  function applyHomeState(state) {
    if (!state) return;
    if (Array.isArray(state.widgets)) homeState.widgets = state.widgets;
    if (Array.isArray(state.custom_widgets)) homeState.custom_widgets = state.custom_widgets;
    if (typeof state.play_seconds === 'number') homeState.play_seconds = state.play_seconds;
    if (state.last_launch) homeState.last_launch = state.last_launch;
  }

  function saveHomeState(patch) {
    callApi('save_home_state', patch).then(function (state) {
      if (!state || !state.ok) return;
      applyHomeState(state);
    });
  }

  function recordLastLaunch() {
    if (!selectedVersion || !selectedVersion.name) return;
    var record = {
      name: selectedVersion.name,
      time: Math.floor(Date.now() / 1000)
    };
    homeState.last_launch = record;
    saveHomeState({ last_launch: record });
    redrawWidget('last-launch');
  }

  function openWidgetModal() {
    if (!widgetModal) return;
    renderWidgetPicker();
    widgetModal.classList.add('is-open');
    widgetModal.setAttribute('aria-hidden', 'false');
  }

  function closeWidgetModal() {
    if (!widgetModal) return;
    widgetModal.classList.remove('is-open');
    widgetModal.setAttribute('aria-hidden', 'true');
  }

  if (btnManageWidgets) btnManageWidgets.addEventListener('click', openWidgetModal);
  if (widgetCloseEl) widgetCloseEl.addEventListener('click', closeWidgetModal);
  if (widgetMaskEl) widgetMaskEl.addEventListener('click', closeWidgetModal);
  if (btnWidgetDone) btnWidgetDone.addEventListener('click', closeWidgetModal);
  if (btnWidgetReset) {
    btnWidgetReset.addEventListener('click', function () {
      homeState.widgets = DEFAULT_WIDGETS.slice();
      saveHomeState({ widgets: homeState.widgets });
      renderWidgets();
      renderWidgetPicker();
      toast('已恢复默认小组件');
    });
  }

  /* ---------- 自定义 HTML 小组件 ---------- */
  var customWidgetModal = $('#customWidgetModal');
  var customWidgetMask = $('#customWidgetMask');
  var customWidgetClose = $('#customWidgetClose');
  var customWidgetTitleEl = $('#customWidgetTitle');
  var customWidgetNameEl = $('#customWidgetName');
  var customWidgetHtmlEl = $('#customWidgetHtml');
  var customWidgetWideEl = $('#customWidgetWide');
  var btnCustomWidgetSave = $('#btnCustomWidgetSave');
  var btnCustomWidgetCancel = $('#btnCustomWidgetCancel');
  var btnImportCustomHtml = $('#btnImportCustomHtml');
  var editingCustomWidgetId = '';

  function openCustomWidgetEditor(entry) {
    if (!customWidgetModal) return;
    editingCustomWidgetId = entry && entry.id ? entry.id : '';
    if (customWidgetTitleEl) {
      customWidgetTitleEl.textContent = editingCustomWidgetId
        ? '编辑自定义小组件'
        : '新建自定义小组件';
    }
    if (customWidgetNameEl) customWidgetNameEl.value = entry ? entry.name || '' : '';
    if (customWidgetHtmlEl) customWidgetHtmlEl.value = entry ? entry.html || '' : '';
    if (customWidgetWideEl) customWidgetWideEl.checked = !!(entry && entry.wide);
    customWidgetModal.classList.add('is-open');
    customWidgetModal.setAttribute('aria-hidden', 'false');
    if (customWidgetNameEl) {
      window.setTimeout(function () {
        customWidgetNameEl.focus();
      }, 80);
    }
  }

  function closeCustomWidgetEditor() {
    if (!customWidgetModal) return;
    customWidgetModal.classList.remove('is-open');
    customWidgetModal.setAttribute('aria-hidden', 'true');
  }

  function saveCustomWidget() {
    var html = customWidgetHtmlEl ? customWidgetHtmlEl.value : '';
    if (!html.trim()) {
      toast('HTML 内容不能为空');
      if (customWidgetHtmlEl) customWidgetHtmlEl.focus();
      return;
    }
    var payload = {
      name: (customWidgetNameEl ? customWidgetNameEl.value : '').trim() || '自定义小组件',
      html: html,
      wide: !!(customWidgetWideEl && customWidgetWideEl.checked)
    };
    if (editingCustomWidgetId) payload.id = editingCustomWidgetId;

    callApi('save_custom_widget', payload).then(function (state) {
      if (!state || !state.ok) {
        toast((state && state.error) || '保存自定义小组件失败');
        return;
      }
      applyHomeState(state);
      renderWidgets();
      renderWidgetPicker();
      closeCustomWidgetEditor();
      toast(editingCustomWidgetId ? '自定义小组件已更新' : '已添加自定义小组件');
    });
  }

  if (customWidgetClose) customWidgetClose.addEventListener('click', closeCustomWidgetEditor);
  if (customWidgetMask) customWidgetMask.addEventListener('click', closeCustomWidgetEditor);
  if (btnCustomWidgetCancel) {
    btnCustomWidgetCancel.addEventListener('click', closeCustomWidgetEditor);
  }
  if (btnCustomWidgetSave) btnCustomWidgetSave.addEventListener('click', saveCustomWidget);

  if (btnImportCustomHtml) {
    btnImportCustomHtml.addEventListener('click', function () {
      callApi('import_custom_widget_file').then(function (result) {
        if (!result) {
          toast('启动器接口未就绪');
          return;
        }
        if (result.cancelled) return;
        if (!result.ok) {
          toast(result.error || '导入 HTML 失败');
          return;
        }
        if (customWidgetHtmlEl) customWidgetHtmlEl.value = result.html || '';
        if (customWidgetNameEl && !customWidgetNameEl.value.trim() && result.name) {
          customWidgetNameEl.value = result.name;
        }
        toast('已导入 HTML 文件');
      });
    });
  }

  function initHomeWidgets() {
    // 先用默认布局占位，等后端数据回来再替换
    renderWidgets();

    callApi('get_home_state').then(function (state) {
      if (!state) return;
      homeState.play_seconds = Number(state.play_seconds) || 0;
      homePlayBase = homeState.play_seconds;
      homeState.last_launch =
        state.last_launch && state.last_launch.name ? state.last_launch : null;
      homeState.custom_widgets = Array.isArray(state.custom_widgets)
        ? state.custom_widgets
        : [];

      var widgets = Array.isArray(state.widgets) ? state.widgets : [];
      var firstRun =
        !widgets.length && !homeState.play_seconds && !homeState.last_launch;
      homeState.widgets = firstRun ? DEFAULT_WIDGETS.slice() : widgets;
      if (firstRun) saveHomeState({ widgets: homeState.widgets });
      renderWidgets();
      renderWidgetPicker();
    });

    window.setInterval(function () {
      var elapsed = Math.floor((Date.now() - homeSessionStart) / 1000);
      homeState.play_seconds = homePlayBase + elapsed;
      tickWidgets();
    }, 1000);

    // 陪伴时长每 30 秒落盘一次，避免频繁写文件
    window.setInterval(function () {
      saveHomeState({ play_seconds: homeState.play_seconds });
    }, 30000);

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') {
        saveHomeState({ play_seconds: homeState.play_seconds });
      }
    });
  }

  /* ---------- 基岩版 ---------- */
  var bedrockSupportHintEl = $('#bedrockSupportHint');
  var bedrockSupportTagEl = $('#bedrockSupportTag');
  var bedrockInstallHintEl = $('#bedrockInstallHint');
  var bedrockInstallTagEl = $('#bedrockInstallTag');
  var bedrockVersionHintEl = $('#bedrockVersionHint');
  var bedrockCacheHintEl = $('#bedrockCacheHint');
  var btnLaunchBedrock = $('#btnLaunchBedrock');
  var btnRefreshBedrock = $('#btnRefreshBedrock');
  var bedrockVersionListEl = $('#bedrockVersionList');
  var btnRefreshBedrockVersions = $('#btnRefreshBedrockVersions');
  var bedrockSourceSwitch = $('#bedrockSourceSwitch');
  var bedrockSourceHintEl = $('#bedrockSourceHint');
  var bedrockMirrorTools = $('#bedrockMirrorTools');
  var bedrockMirrorSearch = $('#bedrockMirrorSearch');
  var bedrockMirrorTypes = $('#bedrockMirrorTypes');
  var bedrockMirrorBuilds = $('#bedrockMirrorBuilds');
  var btnBedrockSaveDir = $('#btnBedrockSaveDir');
  var btnBedrockLocal = $('#btnBedrockLocal');
  var btnBedrockStore = $('#btnBedrockStore');
  var btnBedrockXbox = $('#btnBedrockXbox');
  var btnBedrockDir = $('#btnBedrockDir');
  var bedrockWidget = {
    el: $('#bedrockStatus'),
    text: $('#bedrockStatusText'),
    bar: $('#bedrockProgressBar')
  };

  var bedrockState = null;
  var bedrockLoaded = false;
  var bedrockInstalling = false;
  var bedrockVersions = null; // null 表示还没读过版本列表
  var bedrockVersionsLoading = false;
  var bedrockChannel = ''; // 启动按钮对应的渠道（优先已安装的那个）
  var bedrockSource = 'official'; // official=官方最新 / mirror=历史版本库
  var bedrockMirror = null;
  var bedrockMirrorLoading = false;
  var bedrockMirrorKeyword = '';
  var bedrockMirrorType = 'all';
  var bedrockMirrorBuild = 'all';
  var BEDROCK_MIRROR_LIMIT = 150;

  function setBedrockTag(el, text, tone) {
    if (!el) return;
    el.textContent = text;
    el.classList.remove('is-ok', 'is-warn');
    if (tone) el.classList.add(tone);
  }

  function bedrockChannelState(channelId) {
    var list = (bedrockState && bedrockState.channels) || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i] && list[i].id === channelId) return list[i];
    }
    return null;
  }

  function bedrockInstalledChannels() {
    var list = (bedrockState && bedrockState.channels) || [];
    return list.filter(function (item) {
      return item && item.installed;
    });
  }

  function formatBedrockSize(bytes) {
    var size = Number(bytes) || 0;
    if (!size) return '';
    var units = ['B', 'KB', 'MB', 'GB'];
    var index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return (size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)) + ' ' + units[index];
  }

  function renderBedrockState(state) {
    bedrockState = state || null;
    var supported = !!(state && state.supported);
    var installedList = bedrockInstalledChannels();
    var firstInstalled = installedList.length ? installedList[0].installed : null;
    bedrockChannel = installedList.length ? installedList[0].id : '';

    if (bedrockSupportHintEl) {
      bedrockSupportHintEl.textContent = supported
        ? '系统为 Windows 10 1809 及以上，可以运行基岩版'
        : '系统版本过低，基岩版需要 Windows 10 1809（17763）及以上';
    }
    setBedrockTag(bedrockSupportTagEl, supported ? '支持' : '不支持', supported ? 'is-ok' : 'is-warn');

    if (bedrockInstallHintEl) {
      bedrockInstallHintEl.textContent = installedList.length
        ? installedList
            .map(function (item) {
              return item.label + ' ' + (item.installed.version || '');
            })
            .join(' · ') + ' 已安装'
        : '未检测到基岩版，可从下面的版本列表安装';
    }
    setBedrockTag(
      bedrockInstallTagEl,
      installedList.length ? '已安装' : '未安装',
      installedList.length ? 'is-ok' : 'is-warn'
    );

    if (bedrockVersionHintEl) {
      bedrockVersionHintEl.textContent = firstInstalled
        ? (firstInstalled.version || '未知版本') +
          (firstInstalled.location ? ' · ' + firstInstalled.location : '')
        : '—';
      bedrockVersionHintEl.title = firstInstalled ? firstInstalled.location || '' : '';
    }
    if (bedrockCacheHintEl && state && state.download_dir) {
      bedrockCacheHintEl.textContent = '下载的安装包保存在 ' + state.download_dir;
    }

    if (btnLaunchBedrock) {
      btnLaunchBedrock.disabled = !bedrockChannel || bedrockInstalling;
      btnLaunchBedrock.textContent = bedrockChannel ? '启动基岩版' : '尚未安装基岩版';
    }
    renderBedrockVersions();
  }

  function ensureBedrockReady(force) {
    if (bedrockLoaded && !force) return Promise.resolve(bedrockState);
    return callApi('get_bedrock_state').then(function (state) {
      if (!state) return bedrockState;
      bedrockLoaded = true;
      renderBedrockState(state);
      return state;
    });
  }

  function bedrockVersionAction(item) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dl-action';
    btn.textContent = item.cached ? '安装' : '下载';
    btn.disabled = bedrockInstalling;
    btn.addEventListener('click', function (event) {
      event.stopPropagation();
      installBedrockVersion(item.id);
    });
    return btn;
  }

  function bedrockVersionRow(item) {
    var row = document.createElement('div');
    row.className = 'dl-item bedrock-version-item';
    if (item.channel === 'cached') row.classList.add('is-cached');

    var main = document.createElement('div');
    main.className = 'dl-main';
    var name = document.createElement('span');
    name.className = 'dl-name';
    name.textContent = (item.name || '基岩版') + (item.version ? ' ' + item.version : '');
    var meta = document.createElement('span');
    meta.className = 'dl-meta';
    var bits = [item.label];
    var size = formatBedrockSize(item.size);
    if (size) bits.push('约 ' + size);
    if (item.cached) bits.push(item.channel === 'cached' ? '已下载' : '已缓存');
    var channelState = bedrockChannelState(item.channel);
    if (channelState && channelState.installed && channelState.installed.version) {
      bits.push('已安装 ' + channelState.installed.version);
    }
    meta.textContent = bits.filter(Boolean).join(' · ');
    main.appendChild(name);
    main.appendChild(meta);

    var side = document.createElement('div');
    side.className = 'dl-side';
    side.appendChild(bedrockVersionAction(item));

    row.appendChild(main);
    row.appendChild(side);
    return row;
  }

  function bedrockTypeLabel(type) {
    var key = String(type || '').toLowerCase();
    if (key === 'release') return '正式版';
    if (key === 'preview') return '预览版';
    if (key === 'beta') return '测试版';
    return type || '未知类型';
  }

  function bedrockMirrorChannel(item) {
    return /preview|beta/i.test(String(item.release_type || '')) ? 'preview' : 'release';
  }

  function bedrockMirrorInstalled(item) {
    if (String(item.build_type || '').toUpperCase() !== 'UWP') return '';
    var map = (bedrockMirror && bedrockMirror.installed) || {};
    var version = map[bedrockMirrorChannel(item)] || '';
    if (!version) return '';
    var packageId = String(item.package_id || '');
    if (packageId && version.indexOf(packageId) === 0) return version;
    return '';
  }

  function bedrockStatusLabel(item) {
    var status = Number(item.status);
    if (status === 3) return '官方有记录';
    if (status === 2) return '无官方记录';
    if (status === 1) return '未发布';
    if (status === 0) return '已丢失';
    return '';
  }

  function bedrockMirrorRow(item) {
    var row = document.createElement('div');
    row.className = 'dl-item bedrock-version-item';

    var main = document.createElement('div');
    main.className = 'dl-main';
    var name = document.createElement('span');
    name.className = 'dl-name';
    name.textContent = item.version;
    var meta = document.createElement('span');
    meta.className = 'dl-meta';
    var bits = [
      String(item.build_type || '').toUpperCase() || 'UWP',
      bedrockTypeLabel(item.release_type),
      bedrockStatusLabel(item),
      item.date,
      '来源 mcappx.com'
    ];
    var installed = bedrockMirrorInstalled(item);
    if (installed) bits.push('已安装 ' + installed);
    meta.textContent = bits.filter(Boolean).join(' · ');
    main.appendChild(name);
    main.appendChild(meta);

    var gdkOnly = String(item.build_type || '').toUpperCase() === 'GDK';
    var side = document.createElement('div');
    side.className = 'dl-side';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dl-action';
    btn.textContent = item.available ? (gdkOnly ? '下载' : '下载安装') : '不可用';
    btn.title = !item.available
      ? '该版本安装包已无法获取（' + (bedrockStatusLabel(item) || '状态未知') + '）'
      : gdkOnly
        ? 'GDK 版 .msixvc 只能下载，需通过 Microsoft Store 安装'
        : '下载 ' + item.version + ' 并安装';
    btn.disabled = !item.available || bedrockInstalling;
    btn.addEventListener('click', function (event) {
      event.stopPropagation();
      if (gdkOnly) downloadBedrockMirror(item.version);
      else installBedrockMirror(item.version);
    });
    side.appendChild(btn);

    row.appendChild(main);
    row.appendChild(side);
    return row;
  }

  function bedrockMirrorMatches(item) {
    if (bedrockMirrorType !== 'all') {
      if (String(item.release_type || '').toLowerCase() !== bedrockMirrorType.toLowerCase()) {
        return false;
      }
    }
    if (bedrockMirrorBuild !== 'all') {
      if (String(item.build_type || '').toUpperCase() !== bedrockMirrorBuild) return false;
    }
    if (!bedrockMirrorKeyword) return true;
    return String(item.version || '').toLowerCase().indexOf(bedrockMirrorKeyword) >= 0;
  }

  function setBedrockMirrorTools() {
    if (bedrockMirrorTools) {
      bedrockMirrorTools.classList.toggle('is-collapsed', bedrockSource !== 'mirror');
    }
  }

  function bedrockEmptyNote(text) {
    var note = document.createElement('div');
    note.className = 'version-empty';
    note.textContent = text;
    bedrockVersionListEl.appendChild(note);
  }

  function renderBedrockMirrorVersions() {
    if (!bedrockMirror) {
      bedrockEmptyNote(
        bedrockMirrorLoading ? '正在读取历史版本库…' : '尚未读取历史版本库，点「刷新」加载'
      );
      return;
    }
    var list = (bedrockMirror.versions || []).filter(bedrockMirrorMatches);
    if (!list.length) {
      bedrockEmptyNote('没有匹配的版本');
      return;
    }

    var shown = list.slice(0, BEDROCK_MIRROR_LIMIT);
    shown.forEach(function (item) {
      bedrockVersionListEl.appendChild(bedrockMirrorRow(item));
    });
    if (list.length > shown.length) {
      bedrockEmptyNote(
        '仅显示前 ' + shown.length + ' 个版本（共 ' + list.length + ' 个），可用搜索缩小范围'
      );
    }
  }

  function renderBedrockVersions() {
    if (!bedrockVersionListEl) return;
    setBedrockMirrorTools();
    bedrockVersionListEl.innerHTML = '';

    if (bedrockSource === 'mirror') {
      renderBedrockMirrorVersions();
      return;
    }

    if (!bedrockVersions) {
      bedrockEmptyNote(
        bedrockVersionsLoading ? '正在读取可下载的基岩版版本…' : '尚未读取版本列表'
      );
      return;
    }

    if (!bedrockVersions.length) {
      bedrockEmptyNote('没有获取到可下载的版本，可以打开商店安装，或选择本地安装包');
      return;
    }

    bedrockVersions.forEach(function (item) {
      bedrockVersionListEl.appendChild(bedrockVersionRow(item));
    });
  }

  function loadBedrockMirror(force) {
    if (bedrockMirrorLoading) return Promise.resolve(bedrockMirror);
    if (bedrockMirror && !force) return Promise.resolve(bedrockMirror);

    bedrockMirrorLoading = true;
    renderBedrockVersions();

    return callApi('list_bedrock_mirror', !!force).then(function (result) {
      bedrockMirrorLoading = false;
      if (!result || !result.ok) {
        bedrockMirror = null;
        renderBedrockVersions();
        toast((result && result.error) || '无法读取历史版本库');
        return bedrockMirror;
      }

      bedrockMirror = {
        versions: Array.isArray(result.versions) ? result.versions : [],
        installed: result.installed || {},
        source: result.source || 'mcappx.com',
        updated: result.updated || ''
      };
      if (bedrockSourceHintEl) {
        var uwpCount = 0;
        bedrockMirror.versions.forEach(function (item) {
          if (String(item.build_type || '').toUpperCase() !== 'GDK') uwpCount += 1;
        });
        bedrockSourceHintEl.textContent =
          '历史版本来自第三方版本库 ' +
          bedrockMirror.source +
          (bedrockMirror.updated ? '（数据更新于 ' + bedrockMirror.updated + '）' : '') +
          '，共 ' +
          bedrockMirror.versions.length +
          ' 个版本（UWP ' +
          uwpCount +
          ' / GDK ' +
          (bedrockMirror.versions.length - uwpCount) +
          '）；UWP 可直接安装，GDK 的 .msixvc 只能下载';
      }
      renderBedrockVersions();
      return bedrockMirror;
    });
  }

  function loadBedrockVersions(force) {
    if (bedrockVersionsLoading) return Promise.resolve(bedrockVersions);
    if (bedrockVersions && !force) return Promise.resolve(bedrockVersions);

    bedrockVersionsLoading = true;
    if (btnRefreshBedrockVersions) btnRefreshBedrockVersions.disabled = true;
    renderBedrockVersions();

    return callApi('list_bedrock_versions').then(function (result) {
      bedrockVersionsLoading = false;
      if (btnRefreshBedrockVersions) btnRefreshBedrockVersions.disabled = false;
      if (!result) {
        bedrockVersions = [];
        renderBedrockVersions();
        return bedrockVersions;
      }

      bedrockVersions = Array.isArray(result.versions) ? result.versions : [];
      if (result.download_dir && bedrockCacheHintEl) {
        bedrockCacheHintEl.textContent = '下载的安装包保存在 ' + result.download_dir;
      }
      if (Array.isArray(result.errors) && result.errors.length) {
        toast('部分版本读取失败：' + result.errors.join('；'));
      }
      renderBedrockVersions();
      return bedrockVersions;
    });
  }

  function setBedrockBusy(busy, text, percent) {
    bedrockInstalling = !!busy;
    applyStatus(bedrockWidget, text || '', typeof percent === 'number' ? percent : -1, !!busy);
    [btnRefreshBedrock, btnRefreshBedrockVersions, btnBedrockLocal].forEach(function (btn) {
      if (btn) btn.disabled = !!busy;
    });
    if (bedrockVersionListEl) {
      Array.prototype.forEach.call(
        bedrockVersionListEl.querySelectorAll('.dl-action'),
        function (btn) {
          btn.disabled = !!busy;
        }
      );
    }
    if (btnLaunchBedrock) btnLaunchBedrock.disabled = !!busy || !bedrockChannel;
  }

  function launchBedrock() {
    if (!bedrockChannel) {
      ensureBedrockReady().then(function () {
        if (!bedrockChannel) {
          toast('尚未安装基岩版，请先在版本列表里安装');
          return;
        }
        launchBedrock();
      });
      return;
    }
    if (btnLaunchBedrock) btnLaunchBedrock.disabled = true;
    callApi('launch_bedrock', bedrockChannel).then(function (result) {
      if (btnLaunchBedrock) btnLaunchBedrock.disabled = false;
      if (result && result.ok) {
        toast('基岩版已启动');
        return;
      }
      toast((result && result.error) || '启动基岩版失败');
    });
  }

  // 基岩版的下载/安装统一挂到右下角的同一个任务上
  function startBedrockTask(name) {
    removeDownloadTask('bedrock');
    createDownloadTask({ id: 'bedrock', name: '基岩版 ' + (name || '') });
  }

  function finishBedrockInstall(result) {
    var done = [result.label || '', result.version || ''].filter(Boolean).join(' ');
    var message = (done || '基岩版') + ' 安装完成';
    setBedrockBusy(false, message, 100);
    finishDownloadTask('bedrock', true, message);
    toast('基岩版安装完成');
    ensureBedrockReady(true);
    loadBedrockVersions(true);
    if (bedrockSource === 'mirror') loadBedrockMirror(true);
  }

  function failBedrockInstall(result) {
    var message = (result && result.error) || '安装基岩版失败';
    setBedrockBusy(false, message, -1);
    finishDownloadTask('bedrock', false, message);
    toast(message);
  }

  function installBedrockVersion(versionId) {
    if (bedrockInstalling) return;
    var item = null;
    (bedrockVersions || []).forEach(function (entry) {
      if (entry.id === versionId) item = entry;
    });
    var label = item ? ((item.label || '') + ' ' + (item.version || '')).trim() : '基岩版';
    startBedrockTask(label);
    setBedrockBusy(true, '正在准备安装 ' + label + '…', -1);
    callApi('install_bedrock_version', versionId).then(function (result) {
      if (result && result.ok) {
        finishBedrockInstall(result);
        return;
      }
      failBedrockInstall(result);
    });
  }

  function installBedrockMirror(version) {
    if (bedrockInstalling) return;
    startBedrockTask(version);
    setBedrockBusy(true, '正在准备安装 ' + version + '…', -1);
    callApi('install_bedrock_mirror', version, false).then(function (result) {
      if (result && result.ok) {
        finishBedrockInstall(result);
        return;
      }
      if (result && result.need_downgrade) {
        setBedrockBusy(false, '', -1);
        finishDownloadTask('bedrock', false, '需要先卸载当前版本');
        askConfirm(
          '已安装 ' +
            result.installed +
            '，安装 ' +
            version +
            ' 需要先卸载现有版本，卸载前请先备份存档（可用「打开存档目录」）。确认继续？',
          function () {
            startBedrockTask(version);
            setBedrockBusy(true, '正在安装 ' + version + '…', -1);
            callApi('install_bedrock_mirror', version, true).then(function (second) {
              if (second && second.ok) {
                finishBedrockInstall(second);
                return;
              }
              failBedrockInstall(second);
            });
          },
          '卸载并安装',
          undefined,
          function () {
            setBedrockBusy(false, '已取消安装', -1);
          }
        );
        return;
      }
      failBedrockInstall(result);
    });
  }

  // GDK 版只有 .msixvc，启动器装不了，只能下到安装包目录交给商店
  function downloadBedrockMirror(version) {
    if (bedrockInstalling) return;
    startBedrockTask(version);
    setBedrockBusy(true, '正在准备下载 ' + version + '…', -1);
    callApi('download_bedrock_mirror', version).then(function (result) {
      if (result && result.ok) {
        setBedrockBusy(false, version + ' 安装包下载完成', 100);
        finishDownloadTask('bedrock', true, '安装包已下载：' + version);
        toast('安装包已保存：' + (result.path || '安装包目录'));
        if (bedrockSource === 'mirror') loadBedrockMirror(true);
        return;
      }
      failBedrockInstall(result);
    });
  }

  function installBedrockLocal() {
    if (bedrockInstalling) return;
    startBedrockTask('本地安装包');
    setBedrockBusy(true, '等待选择安装包…', -1);
    callApi('install_bedrock_local').then(function (result) {
      if (result && result.cancelled) {
        removeDownloadTask('bedrock');
        setBedrockBusy(false, '已取消安装', -1);
        return;
      }
      if (result && result.ok) {
        setBedrockBusy(false, '安装完成', 100);
        finishDownloadTask('bedrock', true, '本地安装包安装完成');
        toast('基岩版安装完成');
        ensureBedrockReady(true);
        return;
      }
      var message = (result && result.error) || '安装基岩版失败';
      setBedrockBusy(false, message, -1);
      finishDownloadTask('bedrock', false, message);
      toast(message);
    });
  }

  function openBedrockAction(method, label) {
    callApi(method).then(function (result) {
      if (result && result.ok === false) {
        toast(result.error || label + '失败');
      }
    });
  }

  if (btnLaunchBedrock) btnLaunchBedrock.addEventListener('click', launchBedrock);
  if (btnBedrockLocal) btnBedrockLocal.addEventListener('click', installBedrockLocal);
  if (btnBedrockStore) {
    btnBedrockStore.addEventListener('click', function () {
      callApi('open_bedrock_store', bedrockChannel || 'release').then(function (result) {
        if (result && result.ok === false) toast(result.error || '打开商店失败');
      });
    });
  }
  if (btnBedrockXbox) {
    btnBedrockXbox.addEventListener('click', function () {
      openBedrockAction('open_xbox_app', '打开 Xbox');
    });
  }
  if (btnBedrockDir) {
    btnBedrockDir.addEventListener('click', function () {
      callApi('open_bedrock_dir').then(function (ok) {
        if (!ok) toast('无法打开安装包目录');
      });
    });
  }
  if (btnRefreshBedrock) {
    btnRefreshBedrock.addEventListener('click', function () {
      btnRefreshBedrock.disabled = true;
      ensureBedrockReady(true).then(function (state) {
        btnRefreshBedrock.disabled = false;
        toast(state ? '已重新检测基岩版' : '检测失败');
      });
    });
  }
  if (btnRefreshBedrockVersions) {
    btnRefreshBedrockVersions.addEventListener('click', function () {
      if (bedrockSource === 'mirror') {
        loadBedrockMirror(true);
        return;
      }
      loadBedrockVersions(true);
    });
  }
  if (btnBedrockSaveDir) {
    btnBedrockSaveDir.addEventListener('click', function () {
      callApi('open_bedrock_save_dir').then(function (ok) {
        if (!ok) toast('无法打开存档目录');
      });
    });
  }
  if (bedrockSourceSwitch) {
    $$('.seg', bedrockSourceSwitch).forEach(function (button) {
      button.addEventListener('click', function () {
        var next = button.dataset.source || 'official';
        if (next === bedrockSource) return;
        bedrockSource = next;
        $$('.seg', bedrockSourceSwitch).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        if (bedrockSourceHintEl) {
          bedrockSourceHintEl.textContent =
            next === 'mirror'
              ? '历史版本来自第三方版本库 mcappx.com，安装包仍由微软 CDN 下载'
              : '官方渠道只提供最新版本；历史版本来自第三方版本库 mcappx.com';
        }
        renderBedrockVersions();
        if (next === 'mirror') loadBedrockMirror();
      });
    });
  }
  if (bedrockMirrorTypes) {
    $$('.seg', bedrockMirrorTypes).forEach(function (button) {
      button.addEventListener('click', function () {
        bedrockMirrorType = button.dataset.type || 'all';
        $$('.seg', bedrockMirrorTypes).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        renderBedrockVersions();
      });
    });
  }
  if (bedrockMirrorBuilds) {
    $$('.seg', bedrockMirrorBuilds).forEach(function (button) {
      button.addEventListener('click', function () {
        bedrockMirrorBuild = button.dataset.build || 'all';
        $$('.seg', bedrockMirrorBuilds).forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        renderBedrockVersions();
      });
    });
  }
  if (bedrockMirrorSearch) {
    bedrockMirrorSearch.addEventListener('input', function () {
      bedrockMirrorKeyword = bedrockMirrorSearch.value.trim().toLowerCase();
      renderBedrockVersions();
    });
  }

  // 进入板块前先把状态和版本列表读出来（历史版本库按需加载）
  ensureBedrockReady();
  loadBedrockVersions();

  initHomeWidgets();
  loadJavaState(false);
})();
