/**
 * Main Application Controller (Alliance // Grid OS)
 * Handles state, strict auth gating, precision clock, comprehensive lobby controls,
 * real-time tactical telemetry, replay scrubber, and system administration.
 */
const AllianceApp = {
  currentUser: null,
  activeGameId: null,
  gameState: null,
  commandBatch: [],
  tacticalBoard: null,
  radialDock: null,
  replayController: null,
  simulator: null,
  websocket: null,
  clockInterval: null,
  adminSettings: null,

  async init() {
    this.startPrecisionClock();
    await I18N.init();
    this.initViews();
    this.initModals();
    this.initCollapsibleCards();
    this.initBoardAndRadial();
    this.initKeyboardShortcuts();
    this.initMobileDrawerGestures();
    await this.initPushNotifications();
    await this.checkSystemInfo();
    await this.checkAuth();

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.info-tip-btn')) {
        document.querySelectorAll('.info-tip-btn.active').forEach(b => b.classList.remove('active'));
      }
    });

    // High frequency state sync polling to guarantee players never need manual page reload
    setInterval(() => {
      if (this.currentUser) {
        this.loadNotifications();
      }
      if (this.activeGameId && document.getElementById('tactical-view') && document.getElementById('tactical-view').classList.contains('active')) {
        this.loadActiveGameState();
      }
    }, 3000);
  },

  serverTimeOffsetMs: 0,

  async syncServerTime() {
    try {
      const t0 = Date.now();
      const res = await fetch('/api/time');
      if (res.ok) {
        const data = await res.json();
        const t1 = Date.now();
        const latency = (t1 - t0) / 2;
        this.serverTimeOffsetMs = data.timestamp_ms + latency - t1;
        this.serverTzOffsetMs = (data.tz_offset_ms !== undefined) ? data.tz_offset_ms : 3 * 3600 * 1000;
        this.serverTimezoneName = data.timezone || 'Europe/Moscow';
      }
    } catch (_) {}
  },

  /**
   * 0.1-second high-precision live Server Time telemetry clock on top bar
   */
  startPrecisionClock() {
    const clockEl = document.getElementById('header-clock');
    if (!clockEl) return;

    this.syncServerTime();
    setInterval(() => this.syncServerTime(), 30000);

    const update = () => {
      // Calculate server time adjusted for server timezone offset so client always sees exact server time
      const serverEpochMs = Date.now() + (this.serverTimeOffsetMs || 0);
      const serverLocalizedMs = serverEpochMs + (this.serverTzOffsetMs !== undefined ? this.serverTzOffsetMs : 3 * 3600 * 1000);
      const serverDate = new Date(serverLocalizedMs);
      const h = String(serverDate.getUTCHours()).padStart(2, '0');
      const m = String(serverDate.getUTCMinutes()).padStart(2, '0');
      const s = String(serverDate.getUTCSeconds()).padStart(2, '0');
      const ds = Math.floor(serverDate.getUTCMilliseconds() / 100);
      clockEl.textContent = `${h}:${m}:${s}.${ds}`;

      if (ds === 0 && this.gameState && (this.gameState.conference_status === 'CONVENED' || this.gameState.conference_status === 'COOLDOWN')) {
        this.updateConferenceUI();
      }
    };

    update();
    this.clockInterval = setInterval(update, 50);
  },

  /**
   * Inspect system registration policy and invite links
   */
  async checkSystemInfo() {
    try {
      const res = await fetch('/api/system/info');
      if (res.ok) {
        const data = await res.json();
        const notice = document.getElementById('auth-reg-notice');
        if (notice) {
          notice.style.display = data.registrations_enabled ? 'none' : 'block';
        }
      }
    } catch (_) {}

    // Check for ?invite= query parameter
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const inviteParam = urlParams.get('invite');
      if (inviteParam) {
        const invInput = document.getElementById('auth-invite');
        if (invInput) invInput.value = inviteParam;
      }
    } catch (_) {}
  },

  /**
   * Strict Authentication Verification:
   * Forces users to log in before showing tactical matrices or rules.
   */
  async checkAuth() {
    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        const data = await res.json();
        this.currentUser = data.user;
        this.updateAuthUI();
        this.checkFirstLoginNotificationPrompt();
        await this.loadLobbies();
        await this.loadNotifications();
        
        const lastGameId = localStorage.getItem('alliance_last_game_id');
        let joinedLast = false;
        if (lastGameId) {
          try {
            await this.joinGame(lastGameId);
            joinedLast = true;
          } catch (_) {
            localStorage.removeItem('alliance_last_game_id');
          }
        }
        if (!joinedLast) {
          this.showView('lobbies-view');
        }
      } else {
        this.currentUser = null;
        this.updateAuthUI();
        this.showView('auth-view');
      }
    } catch (e) {
      this.currentUser = null;
      this.updateAuthUI();
      this.showView('auth-view');
    }
  },

  updateAuthUI() {
    const mainNav = document.getElementById('main-nav-links');
    const userBadge = document.getElementById('user-badge');
    const authBtns = document.getElementById('auth-nav-btns');
    const notifBtn = document.getElementById('notif-btn');
    const logoutBtn = document.getElementById('nav-logout');
    const adminNavBtn = document.getElementById('nav-admin');

    if (this.currentUser) {
      if (mainNav) mainNav.style.display = 'inline-flex';
      if (userBadge) {
        userBadge.textContent = `@${this.currentUser.username} [${this.currentUser.national_flag}]`;
        userBadge.style.display = 'inline-flex';
      }
      if (authBtns) authBtns.style.display = 'none';
      if (notifBtn) notifBtn.style.display = 'inline-flex';
      if (logoutBtn) logoutBtn.style.display = 'inline-flex';

      if (adminNavBtn) {
        adminNavBtn.style.display = this.currentUser.is_admin ? 'inline-flex' : 'none';
      }
    } else {
      if (mainNav) mainNav.style.display = 'none';
      if (userBadge) userBadge.style.display = 'none';
      if (authBtns) authBtns.style.display = 'inline-flex';
      if (notifBtn) notifBtn.style.display = 'none';
      if (logoutBtn) logoutBtn.style.display = 'none';
      if (adminNavBtn) adminNavBtn.style.display = 'none';
    }
  },

  initViews() {
    // Navigation routing
    document.querySelectorAll('[data-view-target]').forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.getAttribute('data-view-target');
        this.showView(target);
      });
    });

    // Language toggle
    const langBtn = document.getElementById('lang-toggle-btn');
    if (langBtn) {
      langBtn.textContent = I18N.currentLang.toUpperCase();
      langBtn.addEventListener('click', async () => {
        const next = I18N.currentLang === 'en' ? 'ru' : 'en';
        langBtn.textContent = next.toUpperCase();
        await I18N.setLanguage(next);
      });
    }
  },

  initModals() {
    document.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('active'));
      });
    });

    document.querySelectorAll('.modal-overlay').forEach(overlay => {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          overlay.classList.remove('active');
        }
      });
    });
  },

  closeAllModals() {
    document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('active'));
  },

  showView(viewId) {
    this.closeMobileMenu();

    // Allow guests and unauthenticated users to view the rulebook
    if (!this.currentUser && viewId !== 'auth-view' && viewId !== 'rules-view') {
      viewId = 'auth-view';
      this.showToast("Authentication required to access tactical operations.");
    }

    const unauthBanner = document.getElementById('rules-unauth-banner');
    if (unauthBanner) {
      unauthBanner.style.display = (!this.currentUser && viewId === 'rules-view') ? 'flex' : 'none';
    }

    const tacticalView = document.getElementById('tactical-view');
    if (tacticalView && viewId !== 'tactical-view') {
      tacticalView.classList.remove('staging-active');
      const boardEl = document.getElementById('board-container');
      if (boardEl) boardEl.classList.remove('fullscreen-map');
      const debriefCard = document.getElementById('game-concluded-card');
      if (debriefCard) debriefCard.style.display = 'none';
      const confTakeover = document.getElementById('conference-map-takeover');
      if (confTakeover) confTakeover.style.display = 'none';
    }

    document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));
    const target = document.getElementById(viewId);
    if (target) target.classList.add('active');

    if (viewId === 'tactical-view' && this.gameState && this.gameState.status === 'WAITING' && tacticalView) {
      tacticalView.classList.add('staging-active');
    }

    document.querySelectorAll('.nav-btn').forEach(b => {
      if (b.getAttribute('data-view-target') === viewId) b.classList.add('active');
      else b.classList.remove('active');
    });

    if (viewId === 'tactical-view') {
      setTimeout(() => {
        if (this.tacticalBoard) {
          this.tacticalBoard.resize();
          this.tacticalBoard.render();
        }
      }, 50);
    } else if (viewId === 'rules-view') {
      if (!this.simulator) {
        this.simulator = new RuleSimulator('simulator-canvas');
      }
      this.loadOfficialRules();
    } else if (viewId === 'admin-view') {
      this.loadAdminData();
    } else if (viewId === 'lobbies-view') {
      this.loadLobbies();
    } else if (viewId === 'changelog-view') {
      this.loadAuditLogs();
    }
  },

  codeToEmoji(code) {
    if (!code) return '🇺🇳';
    const val = String(code).trim();
    const map = {
      'UN': '🇺🇳', 'FI': '🇫🇮', 'PRIDE': '🏳️‍🌈', 'FINISH': '🏁', 'PIRATE': '🏴‍☠️',
      'PEACE': '🏳️', 'BLACK': '🏴', 'EU': '🇪🇺', 'US': '🇺🇸', 'GB': '🇬🇧',
      'RU': '🇷🇺', 'CN': '🇨🇳', 'DE': '🇩🇪', 'FR': '🇫🇷', 'JP': '🇯🇵',
      'KR': '🇰🇷', 'CA': '🇨🇦', 'AU': '🇦🇺', 'BR': '🇧🇷', 'IN': '🇮🇳',
      'IT': '🇮🇹', 'ES': '🇪🇸', 'MX': '🇲🇽', 'NL': '🇳🇱', 'SE': '🇸🇪',
      'NO': '🇳🇴', 'PL': '🇵🇱', 'UA': '🇺🇦', 'KZ': '🇰🇿', 'BY': '🇧🇾',
      'TR': '🇹🇷', 'SA': '🇸🇦', 'ZA': '🇿🇦', 'AR': '🇦🇷', 'SG': '🇸🇬',
      'CH': '🇨🇭', 'AT': '🇦🇹', 'BE': '🇧🇪', 'DK': '🇩🇰', 'IE': '🇮🇪',
      'NZ': '🇳🇿', 'IL': '🇮🇱', 'GR': '🇬🇷', 'PT': '🇵🇹', 'CZ': '🇨🇿',
      'HU': '🇭🇺', 'RO': '🇷🇴', 'BG': '🇧🇬', 'RS': '🇷🇸', 'HR': '🇭🇷',
      'AE': '🇦🇪', 'EG': '🇪🇬', 'NG': '🇳🇬', 'KE': '🇰🇪', 'CL': '🇨🇱',
      'CO': '🇨🇴', 'VN': '🇻🇳', 'TH': '🇹🇭', 'ID': '🇮🇩', 'MY': '🇲🇾',
      'PH': '🇵🇭', 'IS': '🇮🇸', 'EE': '🇪🇪', 'LV': '🇱🇻', 'LT': '🇱🇹',
      'GE': '🇬🇪', 'AM': '🇦🇲', 'AZ': '🇦🇿', 'UZ': '🇺🇿'
    };
    const upper = val.toUpperCase();
    if (map[upper]) return map[upper];
    if (upper.length === 2 && /^[A-Z]{2}$/.test(upper)) {
      try {
        return String.fromCodePoint(...[...upper].map(c => 127397 + c.charCodeAt(0)));
      } catch (_) {}
    }
    return val;
  },

  instantMode: false,

  toggleInstantMode(enabled) {
    this.instantMode = !!enabled;
    const toggleEl = document.getElementById('instant-mode-toggle');
    if (toggleEl) toggleEl.checked = this.instantMode;
    if (this.instantMode) {
      this.showToast(I18N.t('instant_mode_active'));
    }
  },

  toggleViewLock() {
    if (!this.tacticalBoard) return;
    const isLocked = this.tacticalBoard.toggleViewLock();
    const btn = document.getElementById('btn-view-lock');
    if (btn) {
      btn.textContent = isLocked ? I18N.t('view_lock_locked') : I18N.t('view_lock_unlocked');
      btn.style.borderColor = isLocked ? 'var(--energy-amber)' : '';
      btn.style.color = isLocked ? 'var(--energy-amber)' : '';
    }
    this.showToast(isLocked ? "Viewport locked (Pan/Zoom disabled)" : "Viewport unlocked");
  },

  resetBoardView() {
    if (this.tacticalBoard) {
      this.tacticalBoard.resetView();
    }
  },

  isMobileMapPinned: true,

  toggleMobileMapPin() {
    this.isMobileMapPinned = !this.isMobileMapPinned;
    const boardEl = document.getElementById('board-container');
    const btnPin = document.getElementById('btn-pin-map');
    if (boardEl) {
      if (this.isMobileMapPinned) {
        boardEl.classList.add('pinned-mobile');
      } else {
        boardEl.classList.remove('pinned-mobile');
      }
    }
    if (btnPin) {
      btnPin.textContent = this.isMobileMapPinned ? '📌 Pin Map' : '🔓 Unpin Map';
    }
    if (this.tacticalBoard) {
      setTimeout(() => this.tacticalBoard.resize(), 100);
    }
  },

  isCommandDrawerOpen: false,

  toggleCommandDrawer() {
    if (this.isCommandDrawerOpen) {
      this.collapseCommandDrawer();
    } else {
      this.expandCommandDrawer();
    }
  },

  expandCommandDrawer() {
    this.isCommandDrawerOpen = true;
    const drawer = document.getElementById('command-center-drawer');
    const handleIcon = document.getElementById('drawer-handle-icon');
    const handleText = document.getElementById('drawer-handle-text');
    const compactBar = document.getElementById('compact-quick-bar');
    if (drawer) drawer.classList.add('expanded');
    if (handleIcon) handleIcon.textContent = '▼';
    if (handleText) handleText.textContent = I18N.t('drawer_collapse_controls') || 'COLLAPSE CONTROLS';
    if (compactBar) compactBar.style.display = 'none';
  },

  collapseCommandDrawer() {
    this.isCommandDrawerOpen = false;
    const drawer = document.getElementById('command-center-drawer');
    const handleIcon = document.getElementById('drawer-handle-icon');
    const handleText = document.getElementById('drawer-handle-text');
    const compactBar = document.getElementById('compact-quick-bar');
    if (drawer) drawer.classList.remove('expanded');
    if (handleIcon) handleIcon.textContent = '▲';
    if (handleText) handleText.textContent = I18N.t('drawer_expand_controls') || 'EXPAND COMMAND CONTROLS';
    if (compactBar) compactBar.style.display = 'flex';
  },

  initMobileDrawerGestures() {
    const handle = document.getElementById('drawer-handle-bar');
    const footer = document.getElementById('pinned-batch-footer');
    let startY = 0;
    let currentY = 0;

    const onTouchStart = (e) => {
      if (e.touches && e.touches.length === 1) {
        startY = e.touches[0].clientY;
        currentY = startY;
      }
    };

    const onTouchMove = (e) => {
      if (e.touches && e.touches.length === 1) {
        currentY = e.touches[0].clientY;
      }
    };

    const onTouchEnd = () => {
      const deltaY = currentY - startY;
      if (deltaY < -35) {
        // Swiped UP -> expand drawer
        this.expandCommandDrawer();
      } else if (deltaY > 35) {
        // Swiped DOWN -> collapse drawer
        this.collapseCommandDrawer();
      }
    };

    if (handle) {
      handle.addEventListener('touchstart', onTouchStart, { passive: true });
      handle.addEventListener('touchmove', onTouchMove, { passive: true });
      handle.addEventListener('touchend', onTouchEnd, { passive: true });
    }
    if (footer) {
      footer.addEventListener('touchstart', onTouchStart, { passive: true });
      footer.addEventListener('touchmove', onTouchMove, { passive: true });
      footer.addEventListener('touchend', onTouchEnd, { passive: true });
    }
  },

  toggleMobileMenu() {
    const navMenu = document.getElementById('nav-links-menu');
    if (navMenu) {
      navMenu.classList.toggle('mobile-open');
    }
  },

  closeMobileMenu() {
    const navMenu = document.getElementById('nav-links-menu');
    if (navMenu) {
      navMenu.classList.remove('mobile-open');
    }
  },

  isWithinReach(x0, y0, x, y, rangeLevel) {
    if (x === x0 && y === y0) return false;
    const r = Math.max(1, (rangeLevel || 1) + 1);
    const dx = Math.abs(x - x0);
    const dy = Math.abs(y - y0);
    if (Math.max(dx, dy) <= r) return true;
    if ((dx === r + 1 && dy === 0) || (dy === r + 1 && dx === 0)) return true;
    return false;
  },

  handleOutcomeExclusivity(targetEl, groupClass) {
    // Legacy helper kept for backward compatibility
  },

  handleSingleOutcomeChange(selectEl) {
    const group = document.getElementById('cfg-rogue-bonus-group');
    if (group) {
      group.style.display = (selectEl && selectEl.value === 'GRANT_AP') ? 'block' : 'none';
    }
  },

  handleMultiOutcomeChange(selectEl) {
    const group = document.getElementById('cfg-multi-bonus-group');
    if (group) {
      group.style.display = (selectEl && selectEl.value === 'GRANT_AP') ? 'block' : 'none';
    }
  },

  handleEditSingleOutcomeChange(selectEl) {
    const group = document.getElementById('edit-cfg-rogue-bonus-group');
    if (group) {
      group.style.display = (selectEl && selectEl.value === 'GRANT_AP') ? 'block' : 'none';
    }
  },

  handleEditMultiOutcomeChange(selectEl) {
    const group = document.getElementById('edit-cfg-multi-bonus-group');
    if (group) {
      group.style.display = (selectEl && selectEl.value === 'GRANT_AP') ? 'block' : 'none';
    }
  },

  initBoardAndRadial() {
    this.radialDock = new RadialActionDock('radial-dock-container', (actionType, ctx) => {
      if (this.instantMode) {
        this.executeSingleAction(actionType, ctx);
      } else {
        this.enqueueAction(actionType, ctx);
      }
    });

    this.tacticalBoard = new TacticalBoard('tactical-canvas', (target) => {
      if (!this.gameState || this.gameState.status !== 'ACTIVE') return;
      const selfPlayer = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
      if (!selfPlayer || !selfPlayer.is_alive) {
        this.showToast(I18N.t('err_player_dead'));
        return;
      }

      const { tileX, tileY, player, clientX, clientY, isDoubleClick, isRightClick } = target;

      // 1. Clicking on the player themselves opens a menu to expand their reach
      if (player && player.id === selfPlayer.id) {
        this.radialDock.openAt(clientX, clientY, {
          tileX,
          tileY,
          player,
          selfPlayer,
          mode: 'UPGRADE_ONLY'
        });
        return;
      }

      // 2. Clicking on a different player within reach
      if (player && player.id !== selfPlayer.id) {
        if (!player.is_alive) {
          this.showActionError('invalid_target', I18N.t('err_invalid_target'));
          return;
        }
        const inReach = this.isWithinReach(selfPlayer.x, selfPlayer.y, player.x, player.y, selfPlayer.range_level || 1);
        
        // Double-click or right-click brings up the combat radial dock
        if (isDoubleClick || isRightClick) {
          if (inReach) {
            this.radialDock.openAt(clientX, clientY, {
              tileX,
              tileY,
              player,
              selfPlayer,
              mode: 'COMBAT_ONLY'
            });
          } else {
            this.showActionError('target_out_of_range', I18N.t('err_target_out_of_range'));
          }
        } else {
          // Single-click highlights that player in the Targets in Range card
          this.highlightTargetInPanel(player.id);
        }
        return;
      }

      // 3. Clicking on an empty tile next to the player moves them there
      if (!player) {
        let refX = selfPlayer.x;
        let refY = selfPlayer.y;
        if (!this.instantMode && this.commandBatch.length > 0) {
          for (let i = this.commandBatch.length - 1; i >= 0; i--) {
            if (this.commandBatch[i].action_type === 'MOVE') {
              refX = this.commandBatch[i].to_x;
              refY = this.commandBatch[i].to_y;
              break;
            }
          }
        }
        const dx = Math.abs(tileX - refX);
        const dy = Math.abs(tileY - refY);

        if (dx === 0 && dy === 0) {
          // Clicked on currently staged destination, do nothing silently
          return;
        }

        const isAdjacent = (dx + dy) === 1;
        if (isAdjacent) {
          if (this.instantMode) {
            this.executeSingleAction('MOVE', { tileX, tileY });
          } else {
            this.enqueueAction('MOVE', { tileX, tileY });
          }
        } else {
          this.showActionError('invalid_move', I18N.t('err_invalid_move'));
        }
        return;
      }
    });

    this.replayController = new ReplayController('replay-scrubber-slider', 'replay-status-label', (seq, isLive) => {
      if (isLive) {
        if (this.gameState) this.tacticalBoard.setState(this.gameState);
      } else {
        this.fetchReplayState(seq);
      }
    });
  },


  // ---------------- Authentication Operations ----------------
  async handleLogin() {
    const uInput = document.getElementById('auth-username');
    const pInput = document.getElementById('auth-password');
    const username = (uInput ? uInput.value : '').trim();
    const password = (pInput ? pInput.value : '').trim();

    if (!username || !password) {
      this.showToast("Please enter call-sign and passcode.");
      return;
    }

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      const data = await res.json();
      if (res.ok) {
        this.currentUser = data.user;
        this.updateAuthUI();
        this.checkFirstLoginNotificationPrompt();
        this.showToast(`Authenticated as @${data.user.username}`);
        await this.loadLobbies();
        await this.loadNotifications();
        this.showView('lobbies-view');
      } else {
        this.showToast(data.detail || "Authentication rejected");
      }
    } catch (e) {
      this.showToast("Network error contacting authentication gateway");
    }
  },

  async handleRegister() {
    const uInput = document.getElementById('auth-username');
    const pInput = document.getElementById('auth-password');
    const flagInput = document.getElementById('auth-flag');
    const invInput = document.getElementById('auth-invite');

    const username = (uInput ? uInput.value : '').trim();
    const password = (pInput ? pInput.value : '').trim();
    const national_flag = flagInput ? flagInput.value : 'UN';
    const invite_code = (invInput ? invInput.value : '').trim();

    if (!username || !password) {
      this.showToast("Username and password are required.");
      return;
    }

    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          national_flag,
          preferred_lang: I18N.currentLang,
          invite_code: invite_code || undefined
        })
      });
      const data = await res.json();
      if (res.ok) {
        this.currentUser = data.user;
        this.updateAuthUI();
        this.checkFirstLoginNotificationPrompt();
        this.showToast(`Enlisted player @${data.user.username}`);
        await this.loadLobbies();
        await this.loadNotifications();
        this.showView('lobbies-view');
      } else {
        this.showToast(data.detail || "Enlistment failed");
      }
    } catch (e) {
      this.showToast("Network error during registration");
    }
  },

  async logout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (_) {}
    this.currentUser = null;
    this.activeGameId = null;
    this.gameState = null;
    this.commandBatch = [];
    if (this.websocket) {
      this.websocket.close();
      this.websocket = null;
    }
    this.updateAuthUI();
    this.showView('auth-view');
    this.showToast("Logged out successfully");
  },

  // ---------------- Tactical Action Pipeline ----------------
  enqueueAction(actionType, ctx) {
    if (!this.gameState) return;
    if (this.gameState.status !== 'ACTIVE') {
      this.showToast(I18N.t('err_game_not_active'));
      return;
    }

    const selfPlayer = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
    if (!selfPlayer || !selfPlayer.is_alive) {
      this.showToast(I18N.t('err_player_dead'));
      return;
    }

    let cost = 1;
    if (actionType === 'UPGRADE_RANGE') {
      const currentLevel = selfPlayer.range_level || 1;
      const baseCost = (this.gameState.config && this.gameState.config.range_upgrade_cost_base) || 2;
      const scale = (this.gameState.config && this.gameState.config.range_upgrade_cost_scale) || 1.0;
      cost = Math.floor(baseCost * Math.pow(scale, currentLevel - 1));
    } else if (actionType === 'GIVE_AP') {
      const fee = (this.gameState.config && this.gameState.config.give_ap_cost_ap !== undefined) ? this.gameState.config.give_ap_cost_ap : 0;
      cost = 1 + fee;
    }

    const burstCap = (this.gameState.config && this.gameState.config.burst_cap_ap) || 3;
    const currentBatchCost = this.commandBatch.reduce((sum, a) => sum + (a.cost || 1), 0);

    if (currentBatchCost + cost > burstCap) {
      this.showToast(I18N.t('err_burst_cap_exceeded', { cap: burstCap }));
      return;
    }

    const command = {
      action_type: actionType,
      cost: cost
    };

    if (actionType === 'MOVE') {
      command.to_x = ctx.tileX;
      command.to_y = ctx.tileY;
    } else if (actionType === 'ATTACK' || actionType === 'GIVE_AP') {
      if (!ctx.player) return;
      command.target_player_id = ctx.player.id;
    }

    this.commandBatch.push(command);
    this.renderCommandBatch();
  },

  quickMove(dx, dy) {
    if (!this.gameState || this.gameState.status !== 'ACTIVE') {
      this.showToast(I18N.t('err_game_not_active'));
      return;
    }
    const selfPlayer = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
    if (!selfPlayer || !selfPlayer.is_alive) {
      this.showToast(I18N.t('err_player_dead'));
      return;
    }

    let origX = selfPlayer.x;
    let origY = selfPlayer.y;
    for (let i = this.commandBatch.length - 1; i >= 0; i--) {
      if (this.commandBatch[i].action_type === 'MOVE') {
        origX = this.commandBatch[i].to_x;
        origY = this.commandBatch[i].to_y;
        break;
      }
    }

    const tx = origX + dx;
    const ty = origY + dy;
    if (tx < 1 || tx > this.gameState.board_width || ty < 1 || ty > this.gameState.board_height) {
      this.showToast(I18N.t('err_invalid_move'));
      return;
    }

    this.enqueueAction('MOVE', { tileX: tx, tileY: ty });
  },

  quickUpgradeRange() {
    this.enqueueAction('UPGRADE_RANGE', {});
  },

  undoLastAction() {
    if (this.commandBatch.length > 0) {
      const removed = this.commandBatch.pop();
      this.renderCommandBatch();
      this.showToast(`Removed from batch: ${removed.action_type}`);
    }
  },

  initKeyboardShortcuts() {
    window.addEventListener('keydown', (e) => {
      const activeView = document.getElementById('tactical-view');
      if (!activeView || !activeView.classList.contains('active')) return;
      const tag = (e.target && e.target.tagName ? e.target.tagName : '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

      if (e.key === 'w' || e.key === 'W' || e.key === 'ArrowUp') {
        e.preventDefault();
        this.quickMove(0, -1);
      } else if (e.key === 's' || e.key === 'S' || e.key === 'ArrowDown') {
        e.preventDefault();
        this.quickMove(0, 1);
      } else if (e.key === 'a' || e.key === 'A' || e.key === 'ArrowLeft') {
        e.preventDefault();
        this.quickMove(-1, 0);
      } else if (e.key === 'd' || e.key === 'D' || e.key === 'ArrowRight') {
        e.preventDefault();
        this.quickMove(1, 0);
      } else if (e.key === 'u' || e.key === 'U') {
        e.preventDefault();
        this.quickUpgradeRange();
      } else if (e.key === 'z' || e.key === 'Z' || e.key === 'Backspace') {
        e.preventDefault();
        this.undoLastAction();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        this.submitBatch();
      }
    });
  },

  renderCommandBatch() {
    const batchList = document.getElementById('batch-list');
    const drawerBatchList = document.getElementById('drawer-batch-list');
    const submitBtn = document.getElementById('submit-batch-btn');
    const drawerSubmitBtn = document.getElementById('drawer-submit-batch-btn');
    const submitLabel = document.getElementById('submit-batch-label');
    const drawerSubmitLabel = document.getElementById('drawer-submit-batch-label');
    if (!batchList && !drawerBatchList) return;

    const totalCost = this.commandBatch.reduce((sum, a) => sum + (a.cost || 1), 0);
    const emptyHtml = `<span style="color: var(--text-muted); font-size: 0.78rem;">${I18N.t('batch_empty_hint') || 'No queued commands. Tap map, arrows, or targets.'}</span>`;

    [batchList, drawerBatchList].forEach(container => {
      if (!container) return;
      container.innerHTML = '';
      if (this.commandBatch.length === 0) {
        container.innerHTML = emptyHtml;
      } else {
        this.commandBatch.forEach((cmd, idx) => {
          const row = document.createElement('div');
          row.style.display = 'flex';
          row.style.justifyContent = 'space-between';
          row.style.alignItems = 'center';
          row.style.padding = '2px 4px';
          row.style.margin = '2px 0';
          row.style.fontSize = '0.78rem';
          row.style.fontFamily = 'var(--font-mono)';

          let desc = `#${idx + 1} ${cmd.action_type}`;
          if (cmd.action_type === 'MOVE') {
            desc = `#${idx + 1} Move [${cmd.to_x !== undefined ? cmd.to_x : cmd.target_x}, ${cmd.to_y !== undefined ? cmd.to_y : cmd.target_y}]`;
          } else if (cmd.action_type === 'ATTACK') {
            const tp = this.gameState && this.gameState.players.find(p => p.id === cmd.target_player_id);
            desc = `#${idx + 1} Strike ${tp ? '@' + tp.username : ''}`;
          } else if (cmd.action_type === 'GIVE_AP') {
            const tp = this.gameState && this.gameState.players.find(p => p.id === cmd.target_player_id);
            desc = `#${idx + 1} Gift AP to ${tp ? '@' + tp.username : ''}`;
          } else if (cmd.action_type === 'UPGRADE_RANGE') {
            desc = `#${idx + 1} Upgrade Reach`;
          }

          row.innerHTML = `<span style="color:var(--text-primary);">${desc}</span> <span class="range-badge" style="font-size:0.68rem; padding:1px 5px;">${cmd.cost || 1} AP</span>`;
          container.appendChild(row);
        });
      }
    });

    const labelText = `${I18N.t('btn_submit_actions')} (${totalCost} AP)`;
    [submitBtn, drawerSubmitBtn].forEach(btn => {
      if (!btn) return;
      btn.disabled = this.commandBatch.length === 0;
    });
    if (submitLabel) submitLabel.textContent = labelText;
    if (drawerSubmitLabel) drawerSubmitLabel.textContent = labelText;

    // Synchronize tactical board map preview!
    if (this.tacticalBoard && typeof this.tacticalBoard.setBatchPreview === 'function') {
      this.tacticalBoard.setBatchPreview(this.commandBatch);
    }
  },

  clearBatch() {
    this.commandBatch = [];
    this.renderCommandBatch();
  },

  async executeSingleAction(actionType, ctx) {
    if (!this.activeGameId || !this.gameState) return;
    if (this.gameState.status !== 'ACTIVE') {
      this.showToast(I18N.t('err_game_not_active'));
      return;
    }
    const selfPlayer = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
    if (!selfPlayer || !selfPlayer.is_alive) {
      this.showToast(I18N.t('err_player_dead'));
      return;
    }

    const command = { action_type: actionType };
    if (actionType === 'MOVE') {
      command.to_x = ctx.tileX;
      command.to_y = ctx.tileY;
    } else if (actionType === 'ATTACK' || actionType === 'GIVE_AP') {
      if (!ctx.player) return;
      command.target_player_id = ctx.player.id;
    }

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_seq: this.gameState ? this.gameState.last_event_seq : null,
          actions: [command]
        })
      });
      const data = await res.json();
      if (res.ok) {
        this.gameState = data.updated_state;
        this.tacticalBoard.setState(this.gameState);
        this.updateVitalsUI();
        this.showToast(`Action ${actionType} applied instantly`);
      } else {
        if (res.status === 409 || (data.detail && data.detail.includes('ERR_STALE_STATE'))) {
          this.showToast("Board state desynchronized. Reloading latest telemetry...");
          await this.loadActiveGameState();
        } else {
          this.showToast(data.detail || "Action rejected");
        }
      }
    } catch (e) {
      this.showToast("Network error executing action");
    }
  },

  async submitBatch() {
    if (!this.activeGameId || this.commandBatch.length === 0) return;

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          expected_seq: this.gameState ? this.gameState.last_event_seq : null,
          actions: this.commandBatch
        })
      });
      const data = await res.json();
      if (res.ok) {
        this.clearBatch();
        this.gameState = data.updated_state;
        this.tacticalBoard.setState(this.gameState);
        this.updateVitalsUI();
        this.showToast("Command batch executed successfully");
      } else {
        if (res.status === 409 || (data.detail && data.detail.includes('ERR_STALE_STATE'))) {
          this.showToast("Board state desynchronized. Matrix reloaded with latest state.");
          this.clearBatch();
          await this.loadActiveGameState();
        } else {
          this.showToast(data.detail || "Action rejected");
        }
      }
    } catch (e) {
      this.showToast("Network error transmitting batch");
    }
  },

  async togglePeaceVote() {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/armistice`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast(data.armistice_vote ? "Voted FOR Armistice peace treaty." : "Retracted Armistice vote.");
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Vote failed");
      }
    } catch (e) {
      this.showToast("Network error submitting peace vote");
    }
  },

  // ---------------- Lobby Management & Start Button ----------------
  openCreateGameModal() {
    if (!this.currentUser || !this.currentUser.is_admin) {
      this.showToast("Admin permissions required to create game lobbies.");
      return;
    }
    const modal = document.getElementById('modal-create-game');
    if (modal) modal.classList.add('active');
  },

  async createNewGame() {
    const nameInput = document.getElementById('cfg-name');
    const widthInput = document.getElementById('cfg-width');
    const heightInput = document.getElementById('cfg-height');
    const firstApInput = document.getElementById('cfg-first-ap-days');
    const dropAmtInput = document.getElementById('cfg-drop-amount');
    const burstInput = document.getElementById('cfg-burst-cap');
    const hpInput = document.getElementById('cfg-starting-hp');
    const apInput = document.getElementById('cfg-starting-ap');
    const rangeInput = document.getElementById('cfg-starting-range');
    const moveCostInput = document.getElementById('cfg-cost-move');
    const attackCostInput = document.getElementById('cfg-cost-attack');
    const giveCostInput = document.getElementById('cfg-cost-give');
    const upgradeBaseInput = document.getElementById('cfg-cost-upgrade-base');

    const isPlaytest = document.getElementById('cfg-is-playtest')?.checked ?? false;
    const winLastMan = document.getElementById('cfg-win-last-man')?.checked ?? true;
    const winArmistice = document.getElementById('cfg-win-armistice')?.checked ?? true;
    const hideArmisticeVotes = document.getElementById('cfg-armistice-hide-votes')?.checked ?? false;
    const confTransparency = document.getElementById('cfg-conference-transparency')?.value || 'REVEAL_COUNTS_AT_END';
    const confEndCond = document.getElementById('cfg-conference-end-condition')?.value || 'ON_ALL_VOTED';

    const singleOutcome = document.querySelector('input[name="cfg-single-outcome"]:checked')?.value || 'GRANT_AP';
    const multiOutcome = document.querySelector('input[name="cfg-multi-outcome"]:checked')?.value || 'RESUME_COMBAT';
    const rogueBonus = parseInt(document.getElementById('cfg-rogue-bonus')?.value || '3', 10);
    const multiBonus = parseInt(document.getElementById('cfg-multi-disagree-bonus')?.value || '2', 10);

    const winStagnation = document.getElementById('cfg-win-stagnation')?.checked ?? false;
    const stagnationDays = parseInt(document.getElementById('cfg-stagnation-days')?.value || '5', 10);
    const stagnationMode = document.getElementById('cfg-stagnation-mode')?.value || 'DRAW';

    const revealKiller = document.getElementById('cfg-reveal-killer')?.checked ?? false;
    const allowDeadChat = document.getElementById('cfg-allow-dead-chat')?.checked ?? true;
    const fogOfWar = document.getElementById('cfg-fog-of-war')?.checked ?? true;
    const auditLog = document.getElementById('cfg-audit-log')?.checked ?? true;

    const payload = {
      name: (nameInput ? nameInput.value : '').trim() || "Grid Arena Alpha",
      is_playtest: isPlaytest,
      config: {
        board_width: parseInt(widthInput?.value || '15', 10),
        board_height: parseInt(heightInput?.value || '15', 10),
        first_ap_drop_days: parseInt(firstApInput?.value || '1', 10),
        ap_drop_amount: parseInt(dropAmtInput?.value || '1', 10),
        burst_cap_ap: parseInt(burstInput?.value || '3', 10),
        starting_hp: parseInt(hpInput?.value || '3', 10),
        starting_ap: parseInt(apInput?.value || '0', 10),
        starting_range_level: parseInt(rangeInput?.value || '1', 10),
        move_cost_ap: parseInt(moveCostInput?.value || '1', 10),
        attack_cost_ap: parseInt(attackCostInput?.value || '1', 10),
        give_ap_cost_ap: parseInt(giveCostInput?.value || '0', 10),
        ap_drop_server_time: document.getElementById('cfg-ap-drop-server-time')?.value || '22:00',
        armistice_trigger_threshold: parseFloat(document.getElementById('cfg-armistice-trigger-threshold')?.value || '1.0'),
        armistice_hide_vote_count: hideArmisticeVotes,
        diplomatic_conference_duration_sec: parseInt(document.getElementById('cfg-conference-duration')?.value || '120', 10),
        conference_transparency: confTransparency,
        conference_end_condition: confEndCond,
        conference_single_disagree_outcomes: JSON.stringify([singleOutcome]),
        conference_multi_disagree_outcomes: JSON.stringify([multiOutcome]),
        range_upgrade_cost_base: parseInt(upgradeBaseInput?.value || '2', 10),
        win_cond_last_man: winLastMan,
        win_cond_armistice: winArmistice,
        win_cond_rogue: true,
        rogue_bonus_ap: rogueBonus,
        multi_disagree_bonus_ap: multiBonus,
        win_cond_stagnation: winStagnation,
        stagnation_days_limit: stagnationDays,
        stagnation_mode: stagnationMode,
        reveal_killer_on_death: revealKiller,
        allow_dead_chat: allowDeadChat,
        fog_of_war_stats: fogOfWar,
        audit_log_public: auditLog,
        allow_late_join: document.getElementById('cfg-allow-late-join')?.checked ?? false,
        map_voting_enabled: document.getElementById('cfg-map-voting-enabled')?.checked ?? false,
        map_voting_duration_minutes: parseInt(document.getElementById('cfg-map-voting-duration')?.value || '10', 10)
      }
    };

    try {
      const res = await fetch('/api/games', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('active'));
        this.showToast(`Lobby created successfully`);
        this.activeGameId = data.game_id;
        await this.loadActiveGameState();
        this.showView('tactical-view');
        this.connectWebSocket(data.game_id);
      } else {
        this.showToast(data.detail || "Failed to create game");
      }
    } catch (e) {
      this.showToast("Network error creating lobby");
    }
  },

  async loadLobbies() {
    try {
      const res = await fetch('/api/games');
      const data = await res.json();
      const list = document.getElementById('lobby-list');
      if (!list) return;
      list.innerHTML = '';

      const createBtn = document.querySelector('[data-i18n="btn_create_lobby"]') || document.getElementById('btn-open-create-lobby');
      if (createBtn) {
        createBtn.style.display = (this.currentUser && this.currentUser.is_admin) ? 'inline-block' : 'none';
      }

      if (!data.games || data.games.length === 0) {
        list.innerHTML = `<div class="acrylic-card" style="text-align: center; color: var(--text-muted); padding: 30px;">${I18N.t('lobby_empty')}</div>`;
        return;
      }

      data.games.forEach(g => {
        const item = document.createElement('div');
        item.className = 'acrylic-card';
        item.style.marginBottom = '12px';

        const isWaiting = g.status === 'WAITING';
        const isMapVoting = g.status === 'MAP_VOTING';
        const isHost = this.currentUser && this.currentUser.id === g.created_by;
        const isAdmin = this.currentUser && this.currentUser.is_admin;
        const canDelete = isAdmin;
        const statusLabel = isWaiting ? I18N.t('status_waiting') : (isMapVoting ? (I18N.t('status_map_voting') || 'MAP VOTING') : (g.status === 'ACTIVE' ? I18N.t('status_active') : I18N.t('status_ended')));

        item.innerHTML = `
          <div class="telemetry-row">
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <strong style="font-size: 1.05rem; color: var(--primary-cyan);">${g.name}</strong>
                ${g.is_playtest ? '<span class="badge-tag admin">PLAYTEST</span>' : ''}
              </div>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 2px;">
                ${I18N.t('lobby_host', { host: g.creator_name })} | ${I18N.t('lobby_players', { count: g.player_count })} | ${I18N.t('lobby_created', { time: g.created_at.substring(0, 16).replace('T', ' ') })}
              </div>
            </div>
            <span class="range-badge" style="${isWaiting || isMapVoting ? 'border-color: var(--energy-amber); color: var(--energy-amber);' : ''}">${statusLabel}</span>
          </div>
          <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; flex-wrap: wrap;">
            <button class="btn-outline" style="padding: 6px 12px; font-size: 0.82rem;" onclick="AllianceApp.openLobbySettingsAuditForGame('${g.id}')">
              ${I18N.t('btn_settings_audit')}
            </button>
            ${(isHost || isAdmin) ? `<button class="btn-cyan" style="padding: 6px 12px; font-size: 0.82rem;" onclick="AllianceApp.openRecreateLobbyModal('${g.id}')">${I18N.t('btn_recreate_lobby') || 'Recreate Lobby'}</button>` : ''}
            ${canDelete ? `<button class="btn-outline" style="border-color: #EF4444; color: #EF4444; padding: 6px 12px; font-size: 0.82rem;" onclick="AllianceApp.deleteGame('${g.id}')">${I18N.t('staging_delete_lobby')}</button>` : ''}
            ${isWaiting && (isHost || isAdmin) ? `<button class="btn-cyan" style="padding: 6px 14px; font-size: 0.85rem;" onclick="AllianceApp.startGame('${g.id}')">${I18N.t('btn_start_game')}</button>` : ''}
            ${g.status === 'ACTIVE' && g.allow_late_join ? `<button class="btn-outline" style="border-color: var(--primary-cyan); color: var(--primary-cyan); padding: 6px 12px; font-size: 0.82rem;" onclick="AllianceApp.requestLateJoin('${g.id}')">${I18N.t('btn_request_late_join') || 'Request to Join'}</button>` : ''}
            <button class="btn-cyan" style="padding: 6px 14px; font-size: 0.85rem;" onclick="AllianceApp.joinGame('${g.id}')">
              ${isWaiting ? I18N.t('btn_join_match') : (isMapVoting ? (I18N.t('btn_vote_placements') || 'Vote Placements') : I18N.t('btn_enter_match'))}
            </button>
          </div>
        `;
        list.appendChild(item);
      });
    } catch (e) {
      console.error(e);
    }
  },


  async joinGame(gameId) {
    try {
      await fetch(`/api/games/${gameId}/join`, { method: 'POST' });
      this.activeGameId = gameId;
      localStorage.setItem('alliance_last_game_id', gameId);
      await this.loadActiveGameState();
      this.showView('tactical-view');
      this.connectWebSocket(gameId);
    } catch (e) {
      this.showToast("Failed to join game");
    }
  },

  async startGame(gameId) {
    try {
      const res = await fetch(`/api/games/${gameId}/start`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Match has commenced! Starting coordinates randomly generated.");
        this.activeGameId = gameId;
        await this.loadActiveGameState();
        this.showView('tactical-view');
      } else {
        this.showToast(data.detail || "Failed to start match");
      }
    } catch (e) {
      this.showToast("Network error starting match");
    }
  },

  async startActiveGame() {
    if (!this.activeGameId) return;
    await this.startGame(this.activeGameId);
  },

  async addDummyToActiveGame() {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/dummy`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Added simulated player @${data.username} at (${data.x}, ${data.y})`);
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to add dummy");
      }
    } catch (e) {
      this.showToast("Network error adding dummy");
    }
  },

  async loadActiveGameState() {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}`);
      if (res.ok) {
        this.gameState = await res.json();
        const diffRes = await fetch(`/api/games/${this.activeGameId}/diff`);
        const diffData = diffRes.ok ? await diffRes.json() : null;

        const matrixLayout = document.getElementById('tactical-matrix-layout');
        const stagingView = document.getElementById('lobby-staging-view');
        const mapVotingView = document.getElementById('map-voting-view');

        const tacticalView = document.getElementById('tactical-view');
        if (this.gameState.status === 'WAITING') {
          // Pre-game staging screen: Matrix is NOT displayed before game starts!
          if (matrixLayout) matrixLayout.style.display = 'none';
          if (mapVotingView) mapVotingView.style.display = 'none';
          if (stagingView) stagingView.style.display = 'block';
          if (tacticalView) tacticalView.classList.add('staging-active');
          this.renderStagingView();
        } else if (this.gameState.status === 'MAP_VOTING') {
          // Candidate map placement voting screen
          if (matrixLayout) matrixLayout.style.display = 'none';
          if (stagingView) stagingView.style.display = 'none';
          if (mapVotingView) mapVotingView.style.display = 'block';
          if (tacticalView) tacticalView.classList.add('staging-active');
          await this.renderMapVotingView();
        } else {
          // Active or Finished: Matrix is displayed!
          if (tacticalView) tacticalView.classList.remove('staging-active');
          if (stagingView) stagingView.style.display = 'none';
          if (mapVotingView) mapVotingView.style.display = 'none';
          if (matrixLayout) matrixLayout.style.display = '';
          this.tacticalBoard.setState(this.gameState, diffData);
          this.replayController.setMaxSequence(this.gameState.last_event_seq);
          this.updateVitalsUI();
          this.updatePlaytestPanel();
          this.updateDebriefingUI();
        }
      } else if (res.status === 404 || res.status === 400) {
        this.showToast("Match lobby was erased or no longer exists.");
        this.activeGameId = null;
        this.gameState = null;
        localStorage.removeItem('alliance_last_game_id');
        this.showView('lobbies-view');
        await this.loadLobbies();
      }
    } catch (e) {
      console.error(e);
    }
  },

  updateDebriefingUI() {
    const card = document.getElementById('game-concluded-card');
    if (!card) return;
    if (!this.gameState || this.gameState.status !== 'ENDED') {
      card.style.display = 'none';
      return;
    }

    card.style.display = 'block';

    const condBadge = document.getElementById('debriefing-condition-badge');
    const winnerAvatar = document.getElementById('debriefing-winner-avatar');
    const winnerName = document.getElementById('debriefing-winner-name');
    const winnerDetails = document.getElementById('debriefing-winner-details');
    const statTotal = document.getElementById('debriefing-stat-total');
    const statCasualties = document.getElementById('debriefing-stat-casualties');
    const statSurvivors = document.getElementById('debriefing-stat-survivors');
    const statEvents = document.getElementById('debriefing-stat-events');

    const winnerType = this.gameState.winner_type;
    let badgeText = I18N.t('game_finished_sole_survivor');
    let avatarIcon = '🏆';
    let nameText = '@winner';

    if (winnerType === 'ARMISTICE') {
      badgeText = I18N.t('game_finished_armistice');
      avatarIcon = '🤝';
      const living = this.gameState.players.filter(p => p.is_alive);
      nameText = living.map(p => `@${p.username}`).join(', ') || 'All Surviving Players';
    } else if (winnerType === 'ROGUE') {
      badgeText = I18N.t('game_finished_dissenter');
      avatarIcon = '⚡';
      const winPlayer = this.gameState.players.find(p => p.id === this.gameState.winner_id);
      nameText = winPlayer ? `@${winPlayer.username}` : 'Lone Dissenter';
    } else if (winnerType === 'LAST_MAN') {
      badgeText = I18N.t('game_finished_sole_survivor');
      avatarIcon = '🏆';
      const winPlayer = this.gameState.players.find(p => p.id === this.gameState.winner_id || p.is_alive);
      nameText = winPlayer ? `@${winPlayer.username}` : 'Sole Survivor';
    } else if (winnerType === 'STAGNATION' || winnerType === 'DRAW') {
      badgeText = I18N.t('game_finished_inactivity');
      avatarIcon = '⏱️';
      nameText = 'No Victor (Stagnation Draw)';
    } else if (winnerType === 'ADMIN_TERMINATED') {
      badgeText = I18N.t('game_finished_admin');
      avatarIcon = '⚠️';
      nameText = 'Terminated by High Command';
    }

    if (condBadge) condBadge.textContent = badgeText;
    if (winnerAvatar) winnerAvatar.textContent = avatarIcon;
    if (winnerName) winnerName.textContent = nameText;
    if (winnerDetails) winnerDetails.textContent = this.gameState.winner_details || 'Match concluded by tactical protocol.';

    const totalCount = this.gameState.players.length;
    const livingCount = this.gameState.players.filter(p => p.is_alive).length;
    const casualtyCount = totalCount - livingCount;

    if (statTotal) statTotal.textContent = totalCount;
    if (statCasualties) statCasualties.textContent = casualtyCount;
    if (statSurvivors) statSurvivors.textContent = livingCount;
    if (statEvents) statEvents.textContent = `#${this.gameState.last_event_seq || 0}`;

    const isHostOrAdmin = this.currentUser && (this.currentUser.id === this.gameState.created_by || this.currentUser.is_admin);
    const btnRecreateEnded = document.getElementById('btn-recreate-lobby-ended');
    if (btnRecreateEnded) {
      btnRecreateEnded.style.display = isHostOrAdmin ? 'inline-block' : 'none';
    }
  },

  renderStagingView() {
    if (!this.gameState || this.gameState.status !== 'WAITING') return;
    const nameEl = document.getElementById('staging-game-name');
    const metaEl = document.getElementById('staging-game-meta');
    const badgeEl = document.getElementById('staging-playtest-badge');
    const countEl = document.getElementById('staging-player-count');
    const tbody = document.getElementById('staging-roster-tbody');
    const thAction = document.getElementById('staging-th-action');

    const btnStart = document.getElementById('staging-btn-start');
    const btnDummy = document.getElementById('staging-btn-dummy');
    const btnRecreate = document.getElementById('staging-btn-recreate');
    const btnDelete = document.getElementById('staging-btn-delete');

    if (nameEl) nameEl.textContent = this.gameState.name || 'Lobby Arena';
    if (metaEl) {
      metaEl.textContent = `Host: @${this.gameState.creator_name || 'host'} | Created: ${(this.gameState.created_at || '').substring(0, 16).replace('T', ' ')} SERVER`;
    }
    if (badgeEl) {
      badgeEl.style.display = this.gameState.is_playtest ? 'inline-block' : 'none';
    }
    if (countEl) {
      countEl.textContent = (this.gameState.players || []).length;
    }

    const isHostOrAdmin = this.currentUser && (this.currentUser.id === this.gameState.created_by || this.currentUser.is_admin);
    if (thAction) {
      thAction.style.display = isHostOrAdmin ? '' : 'none';
    }

    if (tbody) {
      tbody.innerHTML = '';
      if (!this.gameState.players || this.gameState.players.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${isHostOrAdmin ? 5 : 4}" style="text-align: center; color: var(--text-muted); padding: 12px;">No players enlisted.</td></tr>`;
      } else {
        this.gameState.players.forEach(p => {
          const tr = document.createElement('tr');
          const isSelf = p.id === this.gameState.self_player_id;
          const flagGlyph = this.codeToEmoji(p.national_flag);
          const canRemove = isHostOrAdmin && (p.user_id !== this.gameState.created_by || (this.currentUser.is_admin && !isSelf));

          let actionColHtml = '';
          if (isHostOrAdmin) {
            actionColHtml = canRemove
              ? `<td><button class="btn-outline" style="padding: 2px 8px; font-size: 0.72rem; border-color: #EF4444; color: #EF4444;" onclick="AllianceApp.removePlayerFromLobby('${p.id}')">${I18N.t('btn_remove_player') || 'Remove'}</button></td>`
              : `<td>-</td>`;
          }

          tr.innerHTML = `
            <td style="font-weight: 600;">@${p.username} ${isSelf ? '<span class="badge-tag user">YOU</span>' : ''}</td>
            <td style="font-size: 1.2rem;">${flagGlyph}</td>
            <td>${p.is_bot ? '<span class="badge-tag admin">🤖 BOT</span>' : '<span class="badge-tag user">PLAYER</span>'}</td>
            <td><span class="range-badge" style="border-color: var(--energy-amber); color: var(--energy-amber);">ENLISTED</span></td>
            ${actionColHtml}
          `;
          tbody.appendChild(tr);
        });
      }
    }

    if (btnStart) btnStart.style.display = isHostOrAdmin ? 'inline-block' : 'none';
    if (btnDummy) btnDummy.style.display = isHostOrAdmin ? 'inline-block' : 'none';
    if (btnRecreate) btnRecreate.style.display = isHostOrAdmin ? 'inline-block' : 'none';
    if (btnDelete) btnDelete.style.display = isHostOrAdmin ? 'inline-block' : 'none';
  },

  async removePlayerFromLobby(playerId) {
    if (!this.activeGameId || !playerId) return;
    const confirmMsg = I18N.t('confirm_remove_player') || 'Remove player from match lobby?';
    if (!confirm(confirmMsg)) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/remove-player/${playerId}`, {
        method: 'POST'
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Player @${data.username || playerId} removed from lobby.`);
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to remove player");
      }
    } catch (_) {
      this.showToast("Network error removing player");
    }
  },

  updatePlaytestPanel() {
    const panel = document.getElementById('playtest-admin-panel');
    const terminateBtn = document.getElementById('btn-admin-end-game');
    const isAdmin = this.currentUser && this.currentUser.is_admin;

    if (terminateBtn) {
      terminateBtn.style.display = (isAdmin && this.gameState.status === 'ACTIVE') ? 'block' : 'none';
    }

    if (!panel) return;
    if (this.gameState && this.gameState.is_playtest && isAdmin && this.gameState.status === 'ACTIVE') {
      panel.style.display = 'block';
      const botSelect = document.getElementById('playtest-bot-select');
      if (botSelect) {
        const aliveBots = this.gameState.players.filter(p => p.is_bot && p.is_alive);
        botSelect.innerHTML = aliveBots.length === 0
          ? '<option value="">No living bots on matrix</option>'
          : aliveBots.map(b => `<option value="${b.id}">Bot @${b.username} (AP: ${b.ap}, HP: ${b.hp || 3})</option>`).join('');
      }
    } else {
      panel.style.display = 'none';
    }
  },

  updateVitalsUI() {
    if (!this.gameState) return;
    const self = this.gameState.players.find(p => p.id === this.gameState.self_player_id);

    const burstCap = (this.gameState.config && this.gameState.config.burst_cap_ap) || 3;
    const burstCapTag = document.getElementById('vitals-burst-cap-tag');
    if (burstCapTag) burstCapTag.textContent = `Cap: ${burstCap}`;

    const dropTimeEl = document.getElementById('vitals-ap-drop-time');
    if (dropTimeEl && this.gameState.config) {
      dropTimeEl.textContent = `Drop: ${this.gameState.config.ap_drop_server_time || '22:00'}`;
    }

    const matchStatusEl = document.getElementById('telemetry-match-status');
    if (matchStatusEl) matchStatusEl.textContent = this.gameState.status;

    const living = this.gameState.players.filter(p => p.is_alive);
    const livingEl = document.getElementById('telemetry-living-players');
    if (livingEl) livingEl.textContent = `${living.length} / ${this.gameState.players.length}`;

    if (self) {
      const maxHp = (this.gameState.config && this.gameState.config.starting_hp) || 3;
      const hpVal = self.is_alive ? (self.hp !== null ? self.hp : maxHp) : 0;

      let heartsHtml = '';
      for (let i = 0; i < maxHp; i++) {
        heartsHtml += i < hpVal
          ? '<span class="heart-pip full">❤️</span>'
          : '<span class="heart-pip lost" style="opacity:0.25; filter:grayscale(1);">🖤</span>';
      }

      const hpText = self.is_alive ? `${hpVal} / ${maxHp} HP` : 'ELIMINATED';
      const hpColor = self.is_alive ? '#FECACA' : '#64748B';
      const apText = `${self.ap || 0} AP`;
      const capText = `Cap: ${(this.gameState.config && this.gameState.config.burst_cap_ap) || 3}`;
      const dropText = `Drop: ${(this.gameState.config && this.gameState.config.ap_drop_server_time) || '22:00'}`;

      // Update both mobile strip and desktop pinned header
      ['vitals-hearts-display', 'desktop-vitals-hearts-display'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = heartsHtml;
      });

      ['vitals-hp-numeric', 'desktop-vitals-hp-numeric'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          el.textContent = hpText;
          el.style.color = hpColor;
        }
      });

      ['vitals-ap-numeric', 'desktop-vitals-ap-numeric'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = apText;
      });

      ['vitals-burst-cap-tag', 'desktop-vitals-burst-cap-tag'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = capText;
      });

      ['vitals-ap-drop-time', 'desktop-vitals-ap-drop-time'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = dropText;
      });
    }

    // Armistice panel gating & diplomatic conference takeover
    const armisticeCard = document.getElementById('armistice-card');
    const conferenceCard = document.getElementById('conference-card');
    const batchFooter = document.getElementById('pinned-batch-footer');
    const drawerEl = document.getElementById('command-center-drawer');
    const targetsCard = document.getElementById('targets-in-range-card');
    const telemetryCard = document.getElementById('telemetry-status-card');
    const replayCard = document.getElementById('replay-scrubber-card');
    const playtestCard = document.getElementById('playtest-admin-panel');

    const armisticeEnabled = Boolean(this.gameState.config && this.gameState.config.win_cond_armistice);
    const isConferenceRunning = this.gameState.conference_status === 'CONVENED';
    const isGameActive = this.gameState.status === 'ACTIVE';

    if (isConferenceRunning) {
      if (armisticeCard) armisticeCard.style.display = 'none';
      if (batchFooter) batchFooter.style.display = 'none';
      if (drawerEl) drawerEl.style.display = 'none';
      if (targetsCard) targetsCard.style.display = 'none';
      if (telemetryCard) telemetryCard.style.display = 'none';
      if (replayCard) replayCard.style.display = 'none';
      if (playtestCard) playtestCard.style.display = 'none';
      if (conferenceCard) {
        conferenceCard.style.display = 'block';
        this.updateConferenceUI();
      }
    } else {
      if (conferenceCard) conferenceCard.style.display = 'none';
      if (batchFooter) batchFooter.style.display = 'flex';
      if (drawerEl) drawerEl.style.display = '';
      if (targetsCard) targetsCard.style.display = '';
      if (telemetryCard) telemetryCard.style.display = '';
      if (replayCard) replayCard.style.display = '';

      if (!armisticeEnabled || !isGameActive) {
        if (armisticeCard) armisticeCard.style.display = 'none';
      } else {
        if (armisticeCard) armisticeCard.style.display = 'block';
        const armisticeBtn = document.getElementById('vote-peace-btn');
        const armisticeStatus = document.getElementById('armistice-status');
        if (armisticeBtn) {
          armisticeBtn.disabled = false;
          armisticeBtn.style.color = '';
          if (self) {
            armisticeBtn.textContent = self.armistice_vote ? I18N.t('btn_retract_peace') : I18N.t('btn_vote_peace');
            armisticeBtn.style.borderColor = self.armistice_vote ? 'var(--accent-magenta)' : 'var(--matrix-mint)';
          }
        }
        if (armisticeStatus) {
          const hideVotes = this.gameState.armistice_votes_masked || (this.gameState.config && this.gameState.config.armistice_hide_vote_count);
          if (hideVotes) {
            armisticeStatus.textContent = "Consensus: Unanimous peace triggers collective victory (Voter count classified).";
            armisticeStatus.style.color = 'var(--text-secondary)';
          } else {
            const living = this.gameState.players.filter(p => p.is_alive);
            const votes = living.filter(p => p.armistice_vote).length;
            const pct = living.length > 0 ? Math.round((votes / living.length) * 100) : 0;
            armisticeStatus.textContent = I18N.t('peace_status', { votes, total: living.length, percent: pct });
            armisticeStatus.style.color = '';
          }
        }
      }
    }

    // Admin premature termination and erase match controls
    const btnAdminEnd = document.getElementById('btn-admin-end-game');
    const btnAdminErase = document.getElementById('btn-admin-erase-game');
    const isAdmin = this.currentUser && this.currentUser.is_admin;
    if (btnAdminEnd) {
      btnAdminEnd.style.display = (isAdmin && isGameActive) ? 'block' : 'none';
    }
    if (btnAdminErase) {
      btnAdminErase.style.display = isAdmin ? 'block' : 'none';
    }

    this.updateTargetsInRangeList();
  },

  updateTargetsInRangeList() {
    const listEl = document.getElementById('targets-in-range-list');
    const countBadge = document.getElementById('targets-in-range-count');
    const drawerListEl = document.getElementById('drawer-targets-list');
    const drawerCountBadge = document.getElementById('drawer-targets-count');
    if (!listEl && !drawerListEl) return;

    const emptyText = `<span style="color: var(--text-muted); font-size: 0.8rem;">${I18N.t('no_targets_in_range')}</span>`;

    if (!this.gameState || this.gameState.status !== 'ACTIVE') {
      if (listEl) listEl.innerHTML = emptyText;
      if (drawerListEl) drawerListEl.innerHTML = emptyText;
      if (countBadge) countBadge.textContent = '0';
      if (drawerCountBadge) drawerCountBadge.textContent = '0';
      return;
    }

    const selfPlayer = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
    if (!selfPlayer || !selfPlayer.is_alive) {
      if (listEl) listEl.innerHTML = emptyText;
      if (drawerListEl) drawerListEl.innerHTML = emptyText;
      if (countBadge) countBadge.textContent = '0';
      if (drawerCountBadge) drawerCountBadge.textContent = '0';
      return;
    }

    const targets = this.gameState.players.filter(p => {
      if (p.id === selfPlayer.id || !p.is_alive) return false;
      return this.isWithinReach(selfPlayer.x, selfPlayer.y, p.x, p.y, selfPlayer.range_level || 1);
    });

    const countStr = String(targets.length);
    if (countBadge) countBadge.textContent = countStr;
    if (drawerCountBadge) drawerCountBadge.textContent = countStr;

    if (targets.length === 0) {
      if (listEl) listEl.innerHTML = emptyText;
      if (drawerListEl) drawerListEl.innerHTML = emptyText;
      return;
    }

    [listEl, drawerListEl].forEach(container => {
      if (!container) return;
      container.innerHTML = '';
      targets.forEach(t => {
        const row = document.createElement('div');
        row.className = 'target-card-row';
        row.id = `${container.id}-row-${t.id}`;

        const avatarHtml = t.avatar_path
          ? `<img src="${t.avatar_path}" style="width: 22px; height: 22px; border-radius: 4px; object-fit: cover;">`
          : `<span style="font-size: 1.1rem; line-height: 1;">${I18N.getFlagEmoji(t.national_flag)}</span>`;

        row.innerHTML = `
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
            ${avatarHtml}
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <div style="font-weight: 600; font-size: 0.82rem; color: var(--text-primary);">${t.username}</div>
              <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">(${t.x}, ${t.y})</div>
            </div>
          </div>
          <div style="display: flex; gap: 6px; flex-shrink: 0;">
            <button type="button" class="target-strike-btn" onclick="AllianceApp.handleTargetAction('ATTACK', '${t.id}')" title="Strike">⚡</button>
            <button type="button" class="target-give-btn" onclick="AllianceApp.handleTargetAction('GIVE_AP', '${t.id}')" title="Transfer AP">🎁</button>
          </div>
        `;
        container.appendChild(row);
      });
    });
  },

  handleTargetAction(actionType, targetPlayerId) {
    if (!this.gameState) return;
    const targetPlayer = this.gameState.players.find(p => p.id === targetPlayerId);
    if (!targetPlayer) return;

    if (this.instantMode) {
      this.executeSingleAction(actionType, { player: targetPlayer, tileX: targetPlayer.x, tileY: targetPlayer.y });
    } else {
      this.enqueueAction(actionType, { player: targetPlayer, tileX: targetPlayer.x, tileY: targetPlayer.y });
    }
  },

  highlightTargetInPanel(playerId) {
    const row = document.getElementById(`target-row-${playerId}`);
    if (row) {
      document.querySelectorAll('.target-card-row').forEach(r => r.classList.remove('selected'));
      row.classList.add('selected');
      row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  },

  toggleCardCollapse(cardId) {
    const card = document.getElementById(cardId);
    if (!card) return;
    card.classList.toggle('collapsed');
    const isCollapsed = card.classList.contains('collapsed');
    try {
      localStorage.setItem(`alliance_collapse_${cardId}`, isCollapsed ? '1' : '0');
    } catch (_) {}
  },

  initCollapsibleCards() {
    ['targets-in-range-card', 'telemetry-status-card', 'replay-scrubber-card', 'playtest-admin-panel'].forEach(id => {
      const card = document.getElementById(id);
      if (!card) return;
      try {
        const saved = localStorage.getItem(`alliance_collapse_${id}`);
        if (saved === '1') {
          card.classList.add('collapsed');
        }
      } catch (_) {}
    });
  },

  toggleTooltip(btnEl, event) {
    if (event) event.stopPropagation();
    const isActive = btnEl.classList.contains('active');
    document.querySelectorAll('.info-tip-btn.active').forEach(b => b.classList.remove('active'));
    if (!isActive) {
      btnEl.classList.add('active');
    }
  },

  toggleFullscreenMap() {
    const boardEl = document.getElementById('board-container');
    const btn = document.getElementById('btn-fullscreen-map');
    if (!boardEl) return;
    const isFull = boardEl.classList.toggle('fullscreen-map');
    if (btn) {
      btn.textContent = isFull ? (I18N.t('btn_exit_expand_map') || '✕ Exit') : (I18N.t('btn_expand_map') || '⛶ Expand');
    }
    if (this.tacticalBoard && typeof this.tacticalBoard.resize === 'function') {
      setTimeout(() => this.tacticalBoard.resize(), 100);
    }
  },

  updateConferenceUI() {
    const takeoverEl = document.getElementById('conference-map-takeover');
    const matrixLayout = document.getElementById('tactical-matrix-layout');
    if (!this.gameState) {
      if (takeoverEl) takeoverEl.style.display = 'none';
      if (matrixLayout) matrixLayout.classList.remove('conference-mode');
      return;
    }

    const status = this.gameState.conference_status;
    if (status !== 'CONVENED' && status !== 'COOLDOWN') {
      if (takeoverEl) takeoverEl.style.display = 'none';
      if (matrixLayout) matrixLayout.classList.remove('conference-mode');
      return;
    }

    if (matrixLayout) matrixLayout.classList.add('conference-mode');
    if (takeoverEl) takeoverEl.style.display = 'flex';

    const votingSection = document.getElementById('conf-overlay-voting-section');
    const resultsSection = document.getElementById('conf-overlay-results-section');
    const titleEl = document.getElementById('conf-overlay-title');
    const badgeEl = document.getElementById('conf-overlay-badge');

    const nowServer = Date.now() + (this.serverTimeOffsetMs || 0);
    const ends = this.gameState.conference_ends_at ? new Date(this.gameState.conference_ends_at).getTime() : 0;
    const secLeft = Math.max(0, Math.ceil((ends - nowServer) / 1000));

    const explainerEl = document.getElementById('conf-overlay-explainer');

    if (status === 'CONVENED') {
      if (explainerEl) explainerEl.style.display = 'block';
      if (votingSection) votingSection.style.display = 'block';
      if (resultsSection) resultsSection.style.display = 'none';
      if (titleEl) titleEl.textContent = I18N.t('conference_title') || 'DIPLOMATIC CONFERENCE IN SESSION';
      if (badgeEl) badgeEl.textContent = I18N.t('conference_badge') || 'DIPLOMATIC CONFERENCE';

      const timerEl = document.getElementById('conf-overlay-timer');
      if (timerEl) {
        timerEl.textContent = I18N.t('conference_timer', { seconds: secLeft }) || `Time Remaining: ${secLeft}s`;
      }

      const living = this.gameState.players.filter(p => p.is_alive);
      const listEl = document.getElementById('conf-overlay-vote-list');
      const transparency = (this.gameState.config && this.gameState.config.conference_transparency) || 'SHOW_ALL_VOTES';

      if (listEl) {
        if (transparency === 'SHOW_ALL_VOTES') {
          listEl.innerHTML = living.map(p => {
            let statusText = 'PENDING';
            let color = 'var(--text-muted)';
            if (p.conference_vote === 1) {
              statusText = 'AGREED';
              color = 'var(--matrix-mint)';
            } else if (p.conference_vote === 0) {
              statusText = 'REJECTED';
              color = 'var(--accent-magenta)';
            }
            return `<div style="display:flex; justify-content:space-between; margin:2px 0;">
              <span>@${p.username}</span>
              <span style="font-family:var(--font-mono); font-weight:bold; color:${color};">${statusText}</span>
            </div>`;
          }).join('');
        } else if (transparency === 'SHOW_TOTAL_AGREE') {
          const agreeCount = this.gameState.conference_total_agree !== undefined ? this.gameState.conference_total_agree : living.filter(p => p.conference_vote === 1).length;
          listEl.innerHTML = `<div style="padding: 6px; font-weight:bold; color:var(--matrix-mint);">Agreed to lay arms down: ${agreeCount} / ${living.length} players</div>`;
        } else if (transparency === 'REVEAL_COUNTS_AT_END') {
          listEl.innerHTML = `<div style="padding: 6px; color:var(--text-muted); font-style:italic;">Votes classified during conference. Counts revealed upon conclusion.</div>`;
        } else if (transparency === 'SECRET_RESOLVE') {
          listEl.innerHTML = `<div style="padding: 6px; color:var(--text-muted); font-style:italic;">Secret conference ballots in session. Resolving silently.</div>`;
        }
      }

      const self = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
      const userVoteEl = document.getElementById('conf-overlay-my-vote');
      const agreeBtn = document.getElementById('conf-overlay-btn-agree');
      const rejectBtn = document.getElementById('conf-overlay-btn-reject');

      if (self) {
        if (self.conference_vote === 1) {
          if (userVoteEl) {
            userVoteEl.textContent = I18N.t('conference_voted_agree') || 'You voted: AGREE (PEACE)';
            userVoteEl.style.color = 'var(--matrix-mint)';
          }
          if (agreeBtn) agreeBtn.style.opacity = '1.0';
          if (rejectBtn) rejectBtn.style.opacity = '0.5';
        } else if (self.conference_vote === 0) {
          if (userVoteEl) {
            userVoteEl.textContent = I18N.t('conference_voted_reject') || 'You voted: REJECT (DISSENT)';
            userVoteEl.style.color = 'var(--accent-magenta)';
          }
          if (agreeBtn) agreeBtn.style.opacity = '0.5';
          if (rejectBtn) rejectBtn.style.opacity = '1.0';
        } else {
          if (userVoteEl) {
            userVoteEl.textContent = I18N.t('conference_pending') || 'Ballot pending...';
            userVoteEl.style.color = 'var(--energy-amber)';
          }
          if (agreeBtn) agreeBtn.style.opacity = '1.0';
          if (rejectBtn) rejectBtn.style.opacity = '1.0';
        }
      }
    } else if (status === 'COOLDOWN') {
      if (explainerEl) explainerEl.style.display = 'none';
      if (votingSection) votingSection.style.display = 'none';
      if (resultsSection) resultsSection.style.display = 'block';
      if (titleEl) titleEl.textContent = I18N.t('conference_cooldown_title') || 'CONFERENCE CONCLUDED // RESOLUTION COOLDOWN';
      if (badgeEl) badgeEl.textContent = '10S RESOLUTION COOLDOWN';

      const timerEl = document.getElementById('conf-overlay-timer');
      if (timerEl) timerEl.textContent = '';

      const cooldownTimerEl = document.getElementById('conf-cooldown-timer');
      if (cooldownTimerEl) cooldownTimerEl.textContent = `${secLeft}s`;

      const transparency = (this.gameState.config && this.gameState.config.conference_transparency) || 'SHOW_ALL_VOTES';
      const resData = this.gameState.conference_resolution_data || {};
      const msgEl = document.getElementById('conf-results-message');
      const votesEl = document.getElementById('conf-results-votes');
      const apRewardEl = document.getElementById('conf-results-ap-reward');

      let summaryHtml = '';
      let votesHtml = '';

      if (transparency === 'SECRET_RESOLVE') {
        summaryHtml = `<div style="font-size: 1.05rem; font-weight: bold; color: var(--accent-magenta); font-family: var(--font-display);">${I18N.t('conf_resolution_collapsed') || 'Conference collapsed.'}</div>
                       <div style="margin-top: 6px; color: var(--text-muted); font-size: 0.8rem;">Ballots and outcomes were classified by match parameters. Combat resumes shortly.</div>`;
      } else if (transparency === 'SHOW_TOTAL_AGREE' || transparency === 'REVEAL_COUNTS_AT_END') {
        const agreeCount = resData.agree_count !== undefined ? resData.agree_count : (this.gameState.conference_total_agree || 0);
        const disagreeCount = resData.disagree_count !== undefined ? resData.disagree_count : 0;
        summaryHtml = `<div style="font-size: 1.05rem; font-weight: bold; color: var(--energy-amber); font-family: var(--font-display);">${I18N.t('conf_resolution_collapsed') || 'Conference collapsed.'}</div>
                       <div style="font-size: 0.9rem; margin-top: 6px; color: var(--text-primary); font-family: var(--font-mono);">${agreeCount} agreed to peace, ${disagreeCount} dissented.</div>`;
      } else if (transparency === 'SHOW_ALL_VOTES') {
        const living = this.gameState.players.filter(p => p.is_alive);
        summaryHtml = `<div style="font-size: 1.05rem; font-weight: bold; color: var(--energy-amber); font-family: var(--font-display);">${I18N.t('conf_resolution_collapsed') || 'Conference collapsed.'}</div>`;
        votesHtml = `<div style="margin-top: 8px; max-height: 120px; overflow-y: auto; background: rgba(0,0,0,0.3); padding: 8px; border-radius: var(--radius-sm);">` +
          living.map(p => {
            const agreed = p.conference_vote === 1;
            const statusText = agreed ? 'AGREED (PEACE)' : 'REJECTED (DISSENT)';
            const color = agreed ? 'var(--matrix-mint)' : 'var(--accent-magenta)';
            return `<div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin: 3px 0;">
              <span>@${p.username}</span>
              <span style="font-weight: bold; color: ${color}; font-family: var(--font-mono);">${statusText}</span>
            </div>`;
          }).join('') + `</div>`;
      }

      if (msgEl) msgEl.innerHTML = summaryHtml;
      if (votesEl) votesEl.innerHTML = votesHtml;

      // Check if self received AP after collapse
      const self = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
      const awardedUserIds = resData.awarded_user_ids || [];
      const bonusAp = resData.bonus_ap || 0;
      const selfReceivedAp = self && (awardedUserIds.includes(self.user_id) || (resData.is_single && resData.dissenters && resData.dissenters.includes(self.username) && bonusAp > 0));

      if (apRewardEl) {
        if (selfReceivedAp && bonusAp > 0) {
          apRewardEl.style.display = 'block';
          apRewardEl.innerHTML = `<div style="background: rgba(16, 185, 129, 0.15); border: 1px solid var(--matrix-mint); color: #fff; padding: 8px 12px; border-radius: var(--radius-sm); font-size: 0.88rem; font-weight: 600;">⚡ +${bonusAp} AP granted to you following conference resolution.</div>`;
        } else {
          apRewardEl.style.display = 'none';
          apRewardEl.innerHTML = '';
        }
      }

      if (secLeft <= 0) {
        this.loadActiveGameState();
      }
    }
  },

  async submitConferenceVote(voteBool) {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/conference/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vote: voteBool })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(voteBool ? "Recorded vote: AGREE TO ARMISTICE" : "Recorded vote: REJECT ARMISTICE");
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Vote failed");
      }
    } catch (_) {
      this.showToast("Network error submitting conference vote");
    }
  },

  // ---------------- User Settings & Profile Operations ----------------
  openUserSettingsModal() {
    if (!this.currentUser) return;
    const modal = document.getElementById('modal-user-settings');
    const preview = document.getElementById('settings-avatar-preview');
    const fallback = document.getElementById('settings-avatar-fallback');
    const flagSelect = document.getElementById('settings-flag');
    const langSelect = document.getElementById('settings-lang');
    const usernameInput = document.getElementById('settings-username');
    const filenameEl = document.getElementById('settings-avatar-filename');

    const flagEmoji = I18N.getFlagEmoji(this.currentUser.national_flag || 'UN');

    if (this.currentUser.avatar_path) {
      if (preview) {
        preview.src = this.currentUser.avatar_path;
        preview.style.display = 'block';
        preview.onerror = () => {
          preview.style.display = 'none';
          if (fallback) {
            fallback.style.display = 'flex';
            fallback.textContent = flagEmoji;
          }
        };
      }
      if (fallback) fallback.style.display = 'none';
    } else {
      if (preview) {
        preview.src = '';
        preview.style.display = 'none';
      }
      if (fallback) {
        fallback.style.display = 'flex';
        fallback.textContent = flagEmoji;
      }
    }

    if (flagSelect) {
      const userFlag = (this.currentUser.national_flag || 'UN').trim();
      let matched = false;
      for (const opt of flagSelect.options) {
        if (opt.value === userFlag || opt.text.includes(userFlag) || opt.value === userFlag.toUpperCase()) {
          flagSelect.value = opt.value;
          matched = true;
          break;
        }
      }
      if (!matched) flagSelect.value = 'UN';

      flagSelect.onchange = () => {
        if (!this.currentUser.avatar_path && fallback) {
          fallback.textContent = I18N.getFlagEmoji(flagSelect.value);
        }
      };
    }

    if (langSelect) {
      langSelect.value = this.currentUser.preferred_lang || 'en';
    }
    if (usernameInput) {
      usernameInput.value = this.currentUser.username || '';
    }
    if (filenameEl) {
      filenameEl.textContent = 'No file selected';
    }

    const oldP = document.getElementById('settings-old-pass');
    const newP = document.getElementById('settings-new-pass');
    const confP = document.getElementById('settings-confirm-pass');
    if (oldP) oldP.value = '';
    if (newP) newP.value = '';
    if (confP) confP.value = '';

    if (modal) {
      modal.classList.add('active');
      this.loadAlertPreferences();
      this.updateBrowserNotifStatus();
      this.loadPushDevicesList();
      if ("Notification" in window && Notification.permission === 'granted' && !localStorage.getItem('alliance_device_disabled')) {
        this.registerDevicePushSubscription().then(() => {
          this.updateBrowserNotifStatus();
          this.loadPushDevicesList();
        });
      }
    }
  },

  handleAvatarFileChange(input) {
    const filenameEl = document.getElementById('settings-avatar-filename');
    if (!filenameEl) return;
    if (input.files && input.files[0]) {
      filenameEl.textContent = input.files[0].name;
    } else {
      filenameEl.textContent = 'No file selected';
    }
  },

  async resetAvatar() {
    try {
      const res = await fetch('/api/auth/avatar/reset', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.currentUser.avatar_path = null;
        const preview = document.getElementById('settings-avatar-preview');
        const fallback = document.getElementById('settings-avatar-fallback');
        const flagSelect = document.getElementById('settings-flag');
        if (preview) {
          preview.src = '';
          preview.style.display = 'none';
        }
        if (fallback) {
          fallback.style.display = 'flex';
          const currentCode = flagSelect ? flagSelect.value : (this.currentUser.national_flag || 'UN');
          fallback.textContent = I18N.getFlagEmoji(currentCode);
        }
        const fileInput = document.getElementById('settings-avatar-file');
        if (fileInput) fileInput.value = '';
        const filenameEl = document.getElementById('settings-avatar-filename');
        if (filenameEl) filenameEl.textContent = 'No file selected';
        if (this.tacticalBoard) {
          this.tacticalBoard.avatarCache.clear();
          if (this.gameState) {
            const sp = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
            if (sp) sp.avatar_path = null;
            this.tacticalBoard.setState(this.gameState);
          }
        }
        this.updateAuthUI();
        this.showToast("Avatar reset to national flag");
      } else {
        this.showToast(data.detail || "Failed to reset avatar");
      }
    } catch (_) {
      this.showToast("Network error resetting avatar");
    }
  },

  async uploadAvatar() {
    const fileInput = document.getElementById('settings-avatar-file');
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
      this.showToast("Please choose an image file (PNG, JPG, or WebP).");
      return;
    }
    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/auth/avatar', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (res.ok) {
        this.currentUser.avatar_path = data.avatar_path;
        const preview = document.getElementById('settings-avatar-preview');
        const fallback = document.getElementById('settings-avatar-fallback');
        if (preview) {
          preview.src = data.avatar_path;
          preview.style.display = 'block';
        }
        if (fallback) {
          fallback.style.display = 'none';
        }
        if (this.tacticalBoard) {
          this.tacticalBoard.avatarCache.delete(data.avatar_path);
          if (this.gameState) {
            const sp = this.gameState.players.find(p => p.id === this.gameState.self_player_id);
            if (sp) sp.avatar_path = data.avatar_path;
            this.tacticalBoard.setState(this.gameState);
          }
        }
        this.showToast("Custom avatar uploaded successfully");
      } else {
        this.showToast(data.detail || "Avatar upload failed");
      }
    } catch (_) {
      this.showToast("Network error uploading avatar");
    }
  },

  async saveProfileSettings() {
    const flagSelect = document.getElementById('settings-flag');
    const langSelect = document.getElementById('settings-lang');
    const usernameInput = document.getElementById('settings-username');
    const flag = flagSelect ? flagSelect.value : 'UN';
    const lang = langSelect ? langSelect.value : 'en';
    const newUsername = usernameInput ? usernameInput.value.trim() : '';

    const payload = { national_flag: flag, preferred_lang: lang };
    if (newUsername && newUsername !== this.currentUser.username) {
      payload.username = newUsername;
    }

    try {
      const res = await fetch('/api/auth/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        this.currentUser = data.user;
        this.updateAuthUI();
        if (I18N.currentLang !== lang) {
          await I18N.setLanguage(lang);
        }
        if (this.gameState) {
          await this.loadActiveGameState();
        }
        this.showToast("Player profile updated");
        const modal = document.getElementById('modal-user-settings');
        if (modal) modal.classList.remove('active');
      } else {
        this.showToast(data.detail || "Profile update failed");
      }
    } catch (_) {
      this.showToast("Network error saving profile");
    }
  },

  async changePassword() {
    const oldP = document.getElementById('settings-old-pass')?.value || '';
    const newP = document.getElementById('settings-new-pass')?.value || '';
    const confP = document.getElementById('settings-confirm-pass')?.value || '';

    if (!oldP || !newP) {
      this.showToast("Please provide current and new passcodes.");
      return;
    }
    if (newP.length < 6) {
      this.showToast("New passcode must be at least 6 characters.");
      return;
    }
    if (newP !== confP) {
      this.showToast("Passcodes do not match.");
      return;
    }

    try {
      const res = await fetch('/api/auth/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_password: oldP, new_password: newP })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Passcode changed successfully");
        const modal = document.getElementById('modal-user-settings');
        if (modal) modal.classList.remove('active');
      } else {
        this.showToast(data.detail || "Passcode update rejected");
      }
    } catch (_) {
      this.showToast("Network error updating passcode");
    }
  },

  // ---------------- In-Lobby Settings & Parameter Audit ----------------
  async openLobbySettingsAuditForGame(gameId) {
    try {
      const res = await fetch(`/api/games/${gameId}`);
      if (res.ok) {
        const state = await res.json();
        this.activeGameId = gameId;
        this.gameState = state;
        this.openLobbySettingsAuditModal();
      } else {
        this.showToast("Failed to load match parameters");
      }
    } catch (_) {
      this.showToast("Network error fetching match settings");
    }
  },

  openLobbySettingsAuditModal() {
    if (!this.gameState || !this.gameState.config) return;
    const cfg = this.gameState.config;
    const modal = document.getElementById('modal-lobby-settings-audit');
    const isAdmin = this.currentUser && this.currentUser.is_admin;

    const adminBanner = document.getElementById('lobby-admin-banner');
    const adminSaveBtn = document.getElementById('lobby-admin-save-btn-container');
    if (adminBanner) adminBanner.style.display = isAdmin ? 'block' : 'none';
    if (adminSaveBtn) adminSaveBtn.style.display = isAdmin ? 'flex' : 'none';

    const isGameActive = this.gameState && this.gameState.status === 'ACTIVE';
    const btnEndGame = document.getElementById('btn-admin-end-game');
    if (btnEndGame) {
      btnEndGame.style.display = (isAdmin && isGameActive) ? 'inline-block' : 'none';
    }

    // Populate inputs
    const setVal = (id, val, isCheckbox = false) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (isCheckbox) el.checked = Boolean(val);
      else el.value = (val !== undefined && val !== null) ? val : '';
      el.disabled = !isAdmin;
    };

    setVal('edit-cfg-burst-cap', cfg.burst_cap_ap);
    setVal('edit-cfg-drop-amount', cfg.ap_drop_amount);
    setVal('edit-cfg-ap-drop-server-time', cfg.ap_drop_server_time || '22:00');
    setVal('edit-cfg-cost-give', (cfg.give_ap_cost_ap !== undefined) ? cfg.give_ap_cost_ap : 0);
    setVal('edit-cfg-cost-move', cfg.move_cost_ap);
    setVal('edit-cfg-cost-attack', cfg.attack_cost_ap);
    setVal('edit-cfg-armistice-trigger-threshold', cfg.armistice_trigger_threshold || 1.0);
    setVal('edit-cfg-conference-duration', cfg.diplomatic_conference_duration_sec || 120);
    setVal('edit-cfg-conference-transparency', cfg.conference_transparency || 'REVEAL_COUNTS_AT_END');
    setVal('edit-cfg-conference-end-condition', cfg.conference_end_condition || 'ON_ALL_VOTED');
    setVal('edit-cfg-armistice-hide-votes', cfg.armistice_hide_vote_count, true);
    setVal('edit-cfg-rogue-bonus', cfg.rogue_bonus_ap || 3);
    setVal('edit-cfg-multi-disagree-bonus', cfg.multi_disagree_bonus_ap || 2);
    setVal('edit-cfg-win-armistice', cfg.win_cond_armistice, true);
    setVal('edit-cfg-fog-of-war', cfg.fog_of_war_stats, true);
    setVal('edit-cfg-allow-late-join', cfg.allow_late_join, true);
    setVal('edit-cfg-map-voting-enabled', cfg.map_voting_enabled, true);
    setVal('edit-cfg-map-voting-duration', cfg.map_voting_duration_minutes || 10);

    // Populate consequence selects
    let singleList = [];
    let multiList = [];
    try {
      singleList = typeof cfg.conference_single_disagree_outcomes === 'string' ? JSON.parse(cfg.conference_single_disagree_outcomes) : (cfg.conference_single_disagree_outcomes || []);
    } catch (_) { singleList = ["GRANT_AP"]; }
    try {
      multiList = typeof cfg.conference_multi_disagree_outcomes === 'string' ? JSON.parse(cfg.conference_multi_disagree_outcomes) : (cfg.conference_multi_disagree_outcomes || []);
    } catch (_) { multiList = ["RESUME_COMBAT"]; }

    const targetSingle = singleList[0] || 'GRANT_AP';
    const singleRadios = document.querySelectorAll('input[name="edit-cfg-single-outcome"]');
    singleRadios.forEach(r => {
      r.checked = (r.value === targetSingle);
      r.disabled = !isAdmin;
    });
    const activeSingle = document.querySelector('input[name="edit-cfg-single-outcome"]:checked');
    this.handleEditSingleOutcomeChange(activeSingle);

    const targetMulti = multiList[0] || 'RESUME_COMBAT';
    const multiRadios = document.querySelectorAll('input[name="edit-cfg-multi-outcome"]');
    multiRadios.forEach(r => {
      r.checked = (r.value === targetMulti);
      r.disabled = !isAdmin;
    });
    const activeMulti = document.querySelector('input[name="edit-cfg-multi-outcome"]:checked');
    this.handleEditMultiOutcomeChange(activeMulti);

    const isHostOrAdmin = this.currentUser && (this.currentUser.id === this.gameState.created_by || this.currentUser.is_admin);
    const joinRequestsSec = document.getElementById('lobby-join-requests-section');
    if (joinRequestsSec) {
      joinRequestsSec.style.display = isHostOrAdmin ? 'block' : 'none';
      if (isHostOrAdmin) {
        this.loadLobbyJoinRequests(this.activeGameId);
      }
    }

    this.loadMatchAuditLogs();
    if (modal) modal.classList.add('active');
  },

  async loadMatchAuditLogs() {
    if (!this.activeGameId) return;
    const tbody = document.getElementById('match-audit-tbody');
    if (!tbody) return;

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/audit`);
      if (res.ok) {
        const data = await res.json();
        tbody.innerHTML = '';
        if (!data.audit_logs || data.audit_logs.length === 0) {
          tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 14px;">${I18N.t('audit_no_logs')}</td></tr>`;
          return;
        }
        data.audit_logs.forEach(log => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td style="font-family: var(--font-mono); font-size: 0.8rem;">${(log.timestamp || '').substring(0, 19).replace('T', ' ')}</td>
            <td style="font-weight: bold; color: var(--primary-cyan); font-size: 0.82rem;">${log.param_key}</td>
            <td style="color: var(--text-muted); font-size: 0.82rem;">${log.old_value}</td>
            <td style="color: var(--matrix-mint); font-size: 0.82rem; font-weight: bold;">${log.new_value}</td>
            <td style="font-size: 0.82rem;">@${log.admin_username}</td>
          `;
          tbody.appendChild(tr);
        });
      }
    } catch (_) {}
  },

  async saveLobbyConfigModifications() {
    if (!this.activeGameId || !this.currentUser || !this.currentUser.is_admin) return;

    const singleOutcome = document.querySelector('input[name="edit-cfg-single-outcome"]:checked')?.value || 'GRANT_AP';
    const multiOutcome = document.querySelector('input[name="edit-cfg-multi-outcome"]:checked')?.value || 'RESUME_COMBAT';

    const updates = {
      burst_cap_ap: parseInt(document.getElementById('edit-cfg-burst-cap')?.value || '3', 10),
      ap_drop_amount: parseInt(document.getElementById('edit-cfg-drop-amount')?.value || '1', 10),
      ap_drop_server_time: document.getElementById('edit-cfg-ap-drop-server-time')?.value || '22:00',
      give_ap_cost_ap: parseInt(document.getElementById('edit-cfg-cost-give')?.value || '0', 10),
      move_cost_ap: parseInt(document.getElementById('edit-cfg-cost-move')?.value || '1', 10),
      attack_cost_ap: parseInt(document.getElementById('edit-cfg-cost-attack')?.value || '1', 10),
      armistice_trigger_threshold: parseFloat(document.getElementById('edit-cfg-armistice-trigger-threshold')?.value || '1.0'),
      diplomatic_conference_duration_sec: parseInt(document.getElementById('edit-cfg-conference-duration')?.value || '120', 10),
      conference_transparency: document.getElementById('edit-cfg-conference-transparency')?.value || 'REVEAL_COUNTS_AT_END',
      conference_end_condition: document.getElementById('edit-cfg-conference-end-condition')?.value || 'ON_ALL_VOTED',
      armistice_hide_vote_count: document.getElementById('edit-cfg-armistice-hide-votes')?.checked ? 1 : 0,
      conference_single_disagree_outcomes: JSON.stringify([singleOutcome]),
      conference_multi_disagree_outcomes: JSON.stringify([multiOutcome]),
      rogue_bonus_ap: parseInt(document.getElementById('edit-cfg-rogue-bonus')?.value || '3', 10),
      multi_disagree_bonus_ap: parseInt(document.getElementById('edit-cfg-multi-disagree-bonus')?.value || '2', 10),
      win_cond_armistice: document.getElementById('edit-cfg-win-armistice')?.checked ? 1 : 0,
      win_cond_rogue: 1,
      fog_of_war_stats: document.getElementById('edit-cfg-fog-of-war')?.checked ? 1 : 0,
      allow_late_join: document.getElementById('edit-cfg-allow-late-join')?.checked ? 1 : 0,
      map_voting_enabled: document.getElementById('edit-cfg-map-voting-enabled')?.checked ? 1 : 0,
      map_voting_duration_minutes: parseInt(document.getElementById('edit-cfg-map-voting-duration')?.value || '10', 10)
    };

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Match parameters updated and recorded in audit log");
        await this.loadActiveGameState();
        await this.loadMatchAuditLogs();
      } else {
        this.showToast(data.detail || "Parameter modification rejected");
      }
    } catch (_) {
      this.showToast("Network error updating parameters");
    }
  },

  async deleteGame(gameId) {
    if (!confirm(I18N.t('confirm_delete_game') || I18N.t('staging_confirm_delete'))) return;
    try {
      const res = await fetch(`/api/games/${gameId}`, { method: 'DELETE' });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Lobby deleted successfully");
        if (localStorage.getItem('alliance_last_game_id') === gameId) {
          localStorage.removeItem('alliance_last_game_id');
        }
        if (this.activeGameId === gameId) {
          this.activeGameId = null;
          this.gameState = null;
          this.showView('lobbies-view');
        } else {
          await this.loadLobbies();
        }
      } else {
        this.showToast(data.detail || "Failed to delete lobby");
      }
    } catch (_) {
      this.showToast("Network error deleting lobby");
    }
  },

  async permanentlyDeleteActiveGame() {
    if (!this.activeGameId) return;
    if (!confirm(I18N.t('confirm_delete_game'))) return;
    const gameId = this.activeGameId;
    try {
      const res = await fetch(`/api/games/${gameId}`, { method: 'DELETE' });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Match erased forever from database");
        const modal = document.getElementById('modal-lobby-settings-audit');
        if (modal) modal.classList.remove('active');
        if (localStorage.getItem('alliance_last_game_id') === gameId) {
          localStorage.removeItem('alliance_last_game_id');
        }
        this.activeGameId = null;
        this.gameState = null;
        await this.loadLobbies();
        this.showView('lobbies-view');
      } else {
        this.showToast(data.detail || "Failed to erase match");
      }
    } catch (_) {
      this.showToast("Network error erasing match");
    }
  },

  async deleteActiveGame() {
    if (!this.activeGameId) return;
    await this.permanentlyDeleteActiveGame();
  },

  async terminateActiveGame() {
    if (!this.activeGameId) return;
    if (!confirm(I18N.t('confirm_end_game'))) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/terminate`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Match has been terminated prematurely by administrator.");
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to terminate match");
      }
    } catch (_) {
      this.showToast("Network error terminating match");
    }
  },

  async awardPlaytestAp(amount = 1) {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/playtest/award-ap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Awarded +${amount} AP to all living players`);
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to award playtest AP");
      }
    } catch (_) {
      this.showToast("Network error executing playtest award");
    }
  },

  async executePlaytestBotCommand() {
    if (!this.activeGameId) return;
    const botSelect = document.getElementById('playtest-bot-select');
    const actionSelect = document.getElementById('playtest-bot-action-type');
    const botId = botSelect ? botSelect.value : null;
    const actionType = actionSelect ? actionSelect.value : null;

    if (!botId || !actionType) {
      this.showToast("Please select bot and action");
      return;
    }

    const botPlayer = this.gameState.players.find(p => p.id === botId);
    if (!botPlayer || !botPlayer.is_alive) {
      this.showToast("Selected bot is eliminated");
      return;
    }

    let command = {};
    if (actionType === 'MOVE_N') {
      command = { action_type: 'MOVE', to_x: botPlayer.x, to_y: Math.max(1, botPlayer.y - 1) };
    } else if (actionType === 'MOVE_S') {
      command = { action_type: 'MOVE', to_x: botPlayer.x, to_y: Math.min(this.gameState.board_height, botPlayer.y + 1) };
    } else if (actionType === 'MOVE_E') {
      command = { action_type: 'MOVE', to_x: Math.min(this.gameState.board_width, botPlayer.x + 1), to_y: botPlayer.y };
    } else if (actionType === 'MOVE_W') {
      command = { action_type: 'MOVE', to_x: Math.max(1, botPlayer.x - 1), to_y: botPlayer.y };
    } else if (actionType === 'UPGRADE_RANGE') {
      command = { action_type: 'UPGRADE_RANGE' };
    } else if (actionType === 'VOTE_CONFERENCE') {
      command = { action_type: 'VOTE_CONFERENCE' };
    } else if (actionType === 'CONFERENCE_AGREE') {
      command = { action_type: 'CONFERENCE_AGREE' };
    } else if (actionType === 'CONFERENCE_REJECT') {
      command = { action_type: 'CONFERENCE_REJECT' };
    }

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/playtest/control-bot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bot_player_id: botId,
          actions: [command],
          action_batch: { actions: [command] }
        })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Command executed for bot @${botPlayer.username}`);
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Bot command rejected");
      }
    } catch (_) {
      this.showToast("Network error executing bot command");
    }
  },

  async downloadBackup() {
    try {
      const res = await fetch('/api/admin/backup');
      if (res.ok) {
        const data = await res.json();
        const jsonStr = JSON.stringify(data, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `alliance_grid_backup_${new Date().toISOString().substring(0, 19).replace(/[:T]/g, '_')}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        this.showToast("Database backup downloaded successfully");
      } else {
        this.showToast("Failed to generate backup");
      }
    } catch (_) {
      this.showToast("Network error during backup export");
    }
  },

  handleRestoreFileChange(input) {
    const filenameSpan = document.getElementById('admin-restore-filename');
    if (!filenameSpan) return;
    if (input && input.files && input.files.length > 0) {
      filenameSpan.textContent = input.files[0].name;
      filenameSpan.style.color = 'var(--primary-cyan)';
    } else {
      filenameSpan.textContent = I18N.t('admin_no_file_chosen');
      filenameSpan.style.color = 'var(--text-muted)';
    }
  },

  async restoreBackupFile() {
    const fileInput = document.getElementById('admin-restore-file');
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
      this.showToast("Please choose a backup JSON file to restore");
      return;
    }
    const file = fileInput.files[0];
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const jsonContent = JSON.parse(e.target.result);
        const res = await fetch('/api/admin/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(jsonContent)
        });
        const data = await res.json();
        if (res.ok) {
          this.showToast("Database restored successfully from backup!");
          await this.loadAdminData();
          await this.loadLobbies();
        } else {
          this.showToast(data.detail || "Restore failed");
        }
      } catch (err) {
        this.showToast("Failed to parse or restore backup JSON");
      }
    };
    reader.readAsText(file);
  },


  async fetchReplayState(seq) {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/replay?seq=${seq}`);
      if (res.ok) {
        const historicalState = await res.json();
        if (this.gameState) {
          historicalState.self_player_id = this.gameState.self_player_id;
          historicalState.board_width = historicalState.board_width || this.gameState.board_width;
          historicalState.board_height = historicalState.board_height || this.gameState.board_height;
          historicalState.current_shrink_level = historicalState.current_shrink_level !== undefined ? historicalState.current_shrink_level : this.gameState.current_shrink_level;
        }
        this.tacticalBoard.setState(historicalState);
      }
    } catch (_) {}
  },

  connectWebSocket(gameId) {
    if (this.websocket) this.websocket.close();

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/game/${gameId}`;
    this.websocket = new WebSocket(wsUrl);

    this.websocket.onopen = () => {
      setInterval(() => {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
          this.websocket.send(JSON.stringify({ type: 'PING' }));
        }
      }, 30000);
    };

    this.websocket.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'LOBBY_DELETED') {
          if (this.activeGameId === msg.game_id || !msg.game_id) {
            this.showToast("Match lobby was permanently erased by administrator.");
            this.activeGameId = null;
            this.gameState = null;
            localStorage.removeItem('alliance_last_game_id');
            this.showView('lobbies-view');
            await this.loadLobbies();
          }
        } else if (msg.type === 'BOARD_UPDATE' || msg.type === 'AP_DROP' || msg.type === 'GAME_STARTED' || msg.type === 'PLAYER_JOINED' || msg.type === 'PLAYER_REMOVED' || msg.type === 'GAME_ENDED') {
          this.loadActiveGameState();
        } else if (msg.type === 'CHAT') {
          this.showToast(`[CHAT] @${msg.username}: ${msg.message}`);
        } else if (msg.type === 'NEW_NOTIFICATION') {
          const notif = msg.notification;
          if (notif) {
            const title = (I18N.currentLang === 'ru' && notif.title_ru) ? notif.title_ru : notif.title_en;
            const message = (I18N.currentLang === 'ru' && notif.message_ru) ? notif.message_ru : notif.message_en;
            this.showToast(`🔔 ${title}: ${message}`);
            this.sendSelfContainedWebNotification(title, message, '');
            this.loadNotifications();
          }
        }
      } catch (e) {}
    };
  },

  // ---------------- Notifications & Alerts Modal ----------------
  _seenNotifIds: new Set(),

  async loadNotifications() {
    try {
      const res = await fetch(`/api/notifications?lang=${I18N.currentLang}`);
      if (res.ok) {
        const data = await res.json();
        const badge = document.getElementById('notif-count-badge');
        if (badge) {
          const unreadCount = data.unread_count || 0;
          badge.textContent = unreadCount;
          badge.style.display = (unreadCount > 0) ? 'inline-block' : 'none';
        }

        // Pop up new unread notifications as toasts
        if (data.notifications && data.notifications.length > 0) {
          const unreadItems = data.notifications.filter(n => !n.is_read);
          unreadItems.forEach(n => {
            if (!this._seenNotifIds.has(n.id)) {
              this._seenNotifIds.add(n.id);
              const lobbyTag = n.game_name ? `[${n.game_name}] ` : '';
              this.showToast(`🔔 ${lobbyTag}${n.title}: ${n.message}`);
              this.sendSelfContainedWebNotification(n.title, n.message, n.game_name);
            }
          });
        }
      }
    } catch (e) {}
  },

  async openNotificationsModal() {
    const modal = document.getElementById('modal-notifications');
    const list = document.getElementById('notifications-modal-list');
    if (!modal || !list) return;

    modal.classList.add('active');
    list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 16px;">Loading alerts...</div>`;

    try {
      const res = await fetch(`/api/notifications?lang=${I18N.currentLang}`);
      if (res.ok) {
        const data = await res.json();
        list.innerHTML = '';
        if (!data.notifications || data.notifications.length === 0) {
          list.innerHTML = `<div style="color: var(--text-muted); text-align: center; padding: 20px;">${I18N.t('notifications_empty')}</div>`;
          return;
        }

        data.notifications.forEach(n => {
          const item = document.createElement('div');
          item.className = 'acrylic-card';
          item.style.padding = '10px 14px';
          item.style.marginBottom = '6px';
          const lobbyBadge = n.game_name ? `<span style="display: inline-block; background: rgba(0,240,255,0.15); border: 1px solid var(--primary-cyan); color: var(--primary-cyan); border-radius: 4px; padding: 1px 6px; font-size: 0.72rem; margin-right: 6px;">${n.game_name}</span>` : '';
          item.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; font-weight: bold; color: var(--primary-cyan);">
              <div>${lobbyBadge}<span>${n.title}</span></div>
              <span style="font-family: var(--font-mono); color: var(--text-muted);">${(n.created_at || '').substring(11, 19)}</span>
            </div>
            <div style="font-size: 0.85rem; color: var(--text-primary); margin-top: 4px;">
              ${n.message}
            </div>
          `;
          list.appendChild(item);
        });
      }
    } catch (_) {}
  },

  async enableBrowserWebNotifications(silent = false) {
    if (!("Notification" in window)) {
      if (!silent) this.showToast(I18N.t('notif_unsupported') || "Browser notifications are not supported in this browser.");
      this.updateBrowserNotifStatus();
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      if (permission === 'granted') {
        localStorage.removeItem('alliance_device_disabled');
        await this.saveAlertPreference('browser_push', true);
        await this.registerDevicePushSubscription();
        if (!silent) this.showToast(I18N.t('notif_enabled_toast') || "Notifications enabled on this device!");
      } else if (permission === 'denied') {
        if (!silent) this.showToast(I18N.t('notif_blocked_toast') || "Notifications were blocked in browser settings.");
        await this.saveAlertPreference('browser_push', false);
      }
      this.updateBrowserNotifStatus();
      await this.loadPushDevicesList();
    } catch (e) {
      if (!silent) this.showToast("Error requesting notification permission: " + e.message);
    }
  },

  async disableDeviceNotifications() {
    const btn = document.getElementById('btn-disable-device-notifs');
    if (btn) btn.disabled = true;
    try {
      localStorage.setItem('alliance_device_disabled', 'true');
      await this.saveAlertPreference('browser_push', false);
      if ('serviceWorker' in navigator) {
        try {
          const reg = await navigator.serviceWorker.ready;
          if ('pushManager' in reg) {
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
              await sub.unsubscribe();
            }
          }
        } catch (_) {}
      }
      const devToken = localStorage.getItem('alliance_device_endpoint');
      if (devToken) {
        await fetch(`/api/push/subscriptions/${encodeURIComponent(devToken)}`, { method: 'DELETE' });
        localStorage.removeItem('alliance_device_endpoint');
      }
      this.showToast(I18N.t('device_disabled_success') || "Notifications disabled on this device.");
      this.updateBrowserNotifStatus();
      await this.loadPushDevicesList();
    } catch (e) {
      this.showToast("Error disabling notifications: " + e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  async disableBrowserWebNotifications() {
    return this.disableDeviceNotifications();
  },

  async disableAllDevicesNotifications() {
    const btn = document.getElementById('btn-disable-all-devices-notifs');
    if (btn) btn.disabled = true;
    try {
      localStorage.setItem('alliance_device_disabled', 'true');
      const res = await fetch('/api/push/subscriptions/revoke-all', {
        method: 'POST'
      });
      if ('serviceWorker' in navigator) {
        try {
          const reg = await navigator.serviceWorker.ready;
          if ('pushManager' in reg) {
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
              await sub.unsubscribe();
            }
          }
        } catch (_) {}
      }
      localStorage.removeItem('alliance_device_endpoint');
      await this.saveAlertPreference('browser_push', false);
      if (res.ok) {
        this.showToast(I18N.t('all_devices_revoked_success') || "Notifications disabled across all devices.");
      } else {
        this.showToast(I18N.t('err_revoke_devices') || "Failed to revoke devices.");
      }
      this.updateBrowserNotifStatus();
      await this.loadPushDevicesList();
    } catch (_) {
      this.showToast(I18N.t('err_network') || "Network error revoking devices.");
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  async revokeAllOtherPushDevices() {
    return this.disableAllDevicesNotifications();
  },

  async deleteAccount() {
    const promptMsg = I18N.t('confirm_delete_account') || "Are you sure you want to permanently delete your operative account? This action cannot be undone.";
    if (!confirm(promptMsg)) return;
    try {
      const res = await fetch('/api/auth/account', { method: 'DELETE' });
      if (res.ok) {
        this.showToast(I18N.t('account_deleted') || "Account successfully deleted.");
        this.closeAllModals();
        localStorage.removeItem('alliance_device_endpoint');
        localStorage.removeItem('alliance_last_game_id');
        this.currentUser = null;
        this.updateAuthUI();
        this.showView('auth-view');
      } else {
        const data = await res.json().catch(() => ({}));
        this.showToast(data.detail || "Failed to delete account.");
      }
    } catch (e) {
      this.showToast("Error deleting account: " + e.message);
    }
  },

  async loadPushDevicesList() {
    const listEl = document.getElementById('notif-devices-list');
    if (!listEl) return;
    try {
      const res = await fetch('/api/push/subscriptions');
      if (res.ok) {
        const data = await res.json();
        const subs = data.subscriptions || [];
        listEl.innerHTML = '';
        if (subs.length === 0) {
          listEl.innerHTML = `<div style="color: var(--text-muted); font-size: 0.75rem;">${I18N.t('notif_devices_empty')}</div>`;
          return;
        }
        const currentEndpoint = localStorage.getItem('alliance_device_endpoint') || '';
        subs.forEach(s => {
          const row = document.createElement('div');
          row.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 6px 8px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 4px; font-size: 0.75rem;';

          let deviceName = I18N.t('notif_browser_client');
          if (s.endpoint.startsWith('device://')) {
            const rawDev = s.endpoint.replace('device://', '').split('_')[0];
            deviceName = `${I18N.t('notif_device_prefix')} ${rawDev}`;
          } else {
            try {
              const u = new URL(s.endpoint);
              deviceName = `Push: ${u.hostname}`;
            } catch (_) {
              deviceName = s.endpoint.slice(0, 24) + '...';
            }
          }
          const isThisDevice = s.endpoint === currentEndpoint;
          const dateStr = (s.created_at || '').substring(0, 16).replace('T', ' ');

          row.innerHTML = `
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 70%;">
              <div style="display: flex; align-items: center; gap: 4px;">
                <span style="font-weight: 600; color: var(--primary-cyan);">${deviceName}</span>
                ${isThisDevice ? `<span class="badge-tag user" style="font-size: 0.65rem;">${I18N.t('notif_this_device')}</span>` : ''}
              </div>
              <div style="font-size: 0.68rem; color: var(--text-muted); font-family: var(--font-mono); margin-top: 2px;">${dateStr}</div>
            </div>
            <button type="button" class="btn-outline" style="padding: 2px 8px; font-size: 0.7rem; border-color: #EF4444; color: #EF4444;" onclick="AllianceApp.revokePushDevice('${s.id}')">
              ${I18N.t('btn_revoke_device')}
            </button>
          `;
          listEl.appendChild(row);
        });
      }
    } catch (_) {
      listEl.innerHTML = `<div style="color: var(--text-muted); font-size: 0.75rem;">${I18N.t('notif_devices_error')}</div>`;
    }
  },

  async revokePushDevice(subId) {
    if (!subId) return;
    try {
      const res = await fetch(`/api/push/subscriptions/${encodeURIComponent(subId)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        this.showToast(I18N.t('notif_device_revoked') || "Notification device revoked.");
        await this.loadPushDevicesList();
        this.updateBrowserNotifStatus();
      } else {
        this.showToast(I18N.t('err_revoke_device') || "Failed to revoke device.");
      }
    } catch (_) {
      this.showToast(I18N.t('err_network') || "Network error revoking device.");
    }
  },

  async revokeAllOtherPushDevices() {
    try {
      const res = await fetch('/api/push/subscriptions/revoke-all', {
        method: 'POST'
      });
      if (res.ok) {
        this.showToast("All notification devices revoked.");
        await this.loadPushDevicesList();
      } else {
        this.showToast("Failed to revoke devices.");
      }
    } catch (_) {
      this.showToast("Network error revoking devices.");
    }
  },

  checkFirstLoginNotificationPrompt() {
    if (!this.currentUser) return;
    if (!("Notification" in window)) return;

    if (Notification.permission === 'granted') {
      this.registerDevicePushSubscription();
      return;
    }
    if (Notification.permission === 'denied') {
      return;
    }

    const promptKey = 'alliance_push_prompted_v8_' + this.currentUser.id;
    if (localStorage.getItem(promptKey)) return;

    // First login on this device: Prompt user to enable notifications
    setTimeout(() => {
      const modal = document.getElementById('modal-notif-prompt');
      if (modal) modal.classList.add('active');
    }, 500);
  },

  async acceptNotificationPrompt() {
    const modal = document.getElementById('modal-notif-prompt');
    if (modal) modal.classList.remove('active');
    if (this.currentUser) {
      localStorage.setItem('alliance_push_prompted_v8_' + this.currentUser.id, 'granted');
    }
    await this.enableBrowserWebNotifications();
  },

  dismissNotificationPrompt() {
    const modal = document.getElementById('modal-notif-prompt');
    if (modal) modal.classList.remove('active');
    if (this.currentUser) {
      localStorage.setItem('alliance_push_prompted_v8_' + this.currentUser.id, 'dismissed');
    }
  },

  async registerDevicePushSubscription() {
    if (!this.currentUser) return;
    try {
      if ('serviceWorker' in navigator) {
        // Race ready promise with a 600ms timeout so we never hang if worker registration is slow or in restricted sandbox
        const regPromise = navigator.serviceWorker.ready;
        const timeoutPromise = new Promise(resolve => setTimeout(() => resolve(null), 600));
        const reg = await Promise.race([regPromise, timeoutPromise]);
        if (reg && 'pushManager' in reg) {
          try {
            const keyRes = await fetch('/api/push/key');
            if (keyRes.ok) {
              const keyData = await keyRes.json();
              const pubKey = keyData.vapid_public_key;
              if (pubKey) {
                const appServerKey = this.urlBase64ToUint8Array(pubKey);
                let sub = await reg.pushManager.getSubscription();
                if (!sub) {
                  sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: appServerKey
                  });
                }
                if (sub) {
                  const subJson = sub.toJSON();
                  localStorage.setItem('alliance_device_endpoint', sub.endpoint);
                  localStorage.removeItem('alliance_device_disabled');
                  await fetch('/api/push/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      endpoint: sub.endpoint,
                      keys: {
                        p256dh: subJson.keys?.p256dh || 'p256dh',
                        auth: subJson.keys?.auth || 'auth'
                      }
                    })
                  });
                  return;
                }
              }
            }
          } catch (_) {}
        }
      }

      // Self-contained device identifier endpoint fallback for offline/sandbox multi-device support
      let devToken = localStorage.getItem('alliance_device_endpoint');
      if (!devToken) {
        devToken = 'device://' + (navigator.userAgent.replace(/[^a-zA-Z0-9]/g, '').slice(0, 20) || 'dev') + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
        localStorage.setItem('alliance_device_endpoint', devToken);
      }
      localStorage.removeItem('alliance_device_disabled');
      await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: devToken,
          keys: { p256dh: 'self_contained', auth: 'self_contained' }
        })
      });
    } catch (_) {}
  },

  urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
      .replace(/\-/g, '+')
      .replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  },

  updateBrowserNotifStatus() {
    const el = document.getElementById('browser-notif-status');
    const enableBtn = document.getElementById('btn-request-browser-notifs');
    const disableBtn = document.getElementById('btn-disable-device-notifs');
    if (!el) return;
    if (!("Notification" in window)) {
      el.textContent = I18N.t('notif_status_unsupported') || "Status: Not supported in this browser.";
      el.style.color = "var(--text-muted)";
      if (enableBtn) {
        enableBtn.disabled = true;
        enableBtn.style.display = 'inline-flex';
      }
      if (disableBtn) disableBtn.style.display = 'none';
      return;
    }
    const perm = Notification.permission;
    const isExplicitlyDisabled = localStorage.getItem('alliance_device_disabled') === 'true';
    const hasEndpoint = !!localStorage.getItem('alliance_device_endpoint');
    const isActive = perm === 'granted' && !isExplicitlyDisabled && hasEndpoint;

    if (isActive) {
      el.textContent = I18N.t('notif_status_active') || "Status: Active on this device.";
      el.style.color = "var(--matrix-mint)";
      if (enableBtn) enableBtn.style.display = 'none';
      if (disableBtn) {
        disableBtn.style.display = 'inline-flex';
        disableBtn.disabled = false;
      }
    } else if (perm === 'denied') {
      el.textContent = I18N.t('notif_status_blocked') || "Status: Blocked in browser settings.";
      el.style.color = "var(--laser-crimson)";
      if (enableBtn) {
        enableBtn.style.display = 'inline-flex';
        enableBtn.disabled = true;
      }
      if (disableBtn) disableBtn.style.display = 'none';
    } else {
      el.textContent = I18N.t('notif_status_disabled') || "Status: Disabled on this device.";
      el.style.color = "var(--energy-amber)";
      if (enableBtn) {
        enableBtn.style.display = 'inline-flex';
        enableBtn.disabled = false;
      }
      if (disableBtn) disableBtn.style.display = 'none';
    }
  },

  async testBrowserNotification() {
    if (!("Notification" in window)) {
      this.showToast(I18N.t('notif_status_unsupported') || "Browser notifications not supported.");
      return;
    }
    const btn = document.getElementById('btn-test-notif');
    if (btn) btn.disabled = true;

    try {
      if (Notification.permission !== 'granted') {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
          this.showToast(I18N.t('notif_blocked_toast') || "Please allow notifications first.");
          this.updateBrowserNotifStatus();
          return;
        }
      }

      // Register or sync push subscription for this device
      localStorage.removeItem('alliance_device_disabled');
      await this.registerDevicePushSubscription();
      this.updateBrowserNotifStatus();

      // Dispatch backend test push notification to all active devices
      let testSentToBackend = false;
      try {
        const res = await fetch('/api/push/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: "Test Alert",
            body: "Alliance Grid notifications are functioning properly!",
            lobby_name: "Alliance HQ"
          })
        });
        if (res.ok) {
          const resData = await res.json();
          testSentToBackend = true;
          const count = resData.devices_notified || 1;
          const msg = (I18N.t('notif_test_sent_count') || "Test alert sent to {count} device(s).").replace('{count}', count);
          this.showToast(msg);
        }
      } catch (_) {}

      // Direct local notification for immediate feedback on current device
      this.sendSelfContainedWebNotification(
        "Test Alert",
        "Alliance Grid notifications are functioning properly on this device!",
        "Alliance HQ"
      );

      if (!testSentToBackend) {
        this.showToast(I18N.t('notif_test_sent') || "Test notification triggered.");
      }

      await this.loadPushDevicesList();
    } catch (e) {
      this.showToast("Test notification error: " + e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  sendSelfContainedWebNotification(title, body, lobbyName) {
    if (!("Notification" in window) || Notification.permission !== 'granted') return;
    try {
      const formattedTitle = lobbyName ? `[${lobbyName}] ${title}` : title;
      const options = {
        body: body,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        tag: 'alliance-alert-' + Date.now(),
        renotify: true
      };
      if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.ready.then(reg => {
          reg.showNotification(formattedTitle, options);
        }).catch(() => {
          new Notification(formattedTitle, options);
        });
      } else {
        new Notification(formattedTitle, options);
      }
    } catch (_) {}
  },

  async loadAlertPreferences() {
    try {
      const res = await fetch('/api/user/alert-preferences');
      if (res.ok) {
        const prefs = await res.json();
        ['eliminations', 'conference', 'moves', 'transfers'].forEach(key => {
          const el = document.getElementById(`alert-pref-${key}`);
          if (el) el.checked = !!prefs[key];
        });
      }
    } catch (_) {}
  },

  async saveAlertPreference(key, value) {
    try {
      await fetch('/api/user/alert-preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value })
      });
    } catch (_) {}
  },

  async markAllNotificationsRead() {
    try {
      await fetch('/api/notifications/read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notification_ids: null })
      });
      const badge = document.getElementById('notif-count-badge');
      if (badge) badge.style.display = 'none';
      const modal = document.getElementById('modal-notifications');
      if (modal) modal.classList.remove('active');
      this.showToast("All system alerts marked as read.");
    } catch (_) {}
  },

  // ---------------- Official Rules & Changelog ----------------
  async loadOfficialRules() {
    try {
      const res = await fetch(`/api/rules?lang=${I18N.currentLang}`);
      if (res.ok) {
        const data = await res.json();
        // Server rulebook text can sync dynamically if desired
      }
    } catch (_) {}
  },

  async loadAuditLogs() {
    const tbody = document.getElementById('audit-log-tbody');
    if (!tbody) return;
    const gameId = this.activeGameId || 'global';
    try {
      const res = await fetch(`/api/games/${gameId}/audit`);
      if (res.ok) {
        const data = await res.json();
        tbody.innerHTML = '';
        if (!data.audit_logs || data.audit_logs.length === 0) {
          tbody.innerHTML = `<tr><td colspan="5" style="padding: 20px; text-align: center; color: var(--text-muted);">No parameter modifications recorded.</td></tr>`;
          return;
        }
        data.audit_logs.forEach(log => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td style="padding: 10px; font-family: var(--font-mono);">${log.timestamp.substring(0, 19).replace('T', ' ')}</td>
            <td style="padding: 10px; font-weight: bold; color: var(--primary-cyan);">${log.param_key}</td>
            <td style="padding: 10px; color: var(--text-muted);">${log.old_value}</td>
            <td style="padding: 10px; color: var(--matrix-mint);">${log.new_value}</td>
            <td style="padding: 10px;">@${log.admin_username}</td>
          `;
          tbody.appendChild(tr);
        });
      }
    } catch (_) {}
  },

  // ---------------- Comprehensive Admin Governance ----------------
  async loadAdminData() {
    if (!this.currentUser || !this.currentUser.is_admin) return;

    // Load Settings
    try {
      const res = await fetch('/api/admin/settings');
      if (res.ok) {
        this.adminSettings = await res.json();
        const regStatusText = document.getElementById('admin-reg-status-text');
        if (regStatusText) {
          regStatusText.textContent = this.adminSettings.registrations_enabled ? I18N.t('admin_reg_status_open') : I18N.t('admin_reg_status_closed');
          regStatusText.style.color = this.adminSettings.registrations_enabled ? 'var(--matrix-mint)' : 'var(--accent-magenta)';
        }
      }
    } catch (_) {}

    // Load Invites
    try {
      const res = await fetch('/api/admin/invites');
      if (res.ok) {
        const data = await res.json();
        const tbody = document.getElementById('admin-invites-tbody');
        if (tbody) {
          tbody.innerHTML = '';
          if (!data.invites || data.invites.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 16px;">${I18N.t('admin_no_invites')}</td></tr>`;
          } else {
            data.invites.forEach(inv => {
              const tr = document.createElement('tr');
              const exp = inv.expires_at ? inv.expires_at.substring(0, 10) : 'Never';
              tr.innerHTML = `
                <td style="font-family: var(--font-mono); font-weight: bold; color: var(--primary-cyan);">${inv.token}</td>
                <td>@${inv.created_by}</td>
                <td style="font-family: var(--font-mono); font-size: 0.8rem;">${exp}</td>
                <td><span class="range-badge">${I18N.t('admin_uses_left', { count: inv.uses_left })}</span></td>
                <td>
                  <button class="btn-outline" style="padding: 4px 8px; font-size: 0.75rem;" onclick="AllianceApp.copyInviteUrl('${inv.token}')">${I18N.t('btn_copy_link')}</button>
                  <button class="btn-outline" style="padding: 4px 8px; font-size: 0.75rem; border-color: var(--accent-magenta); color: var(--accent-magenta);" onclick="AllianceApp.deleteInviteToken('${inv.token}')">${I18N.t('btn_revoke')}</button>
                </td>
              `;
              tbody.appendChild(tr);
            });
          }
        }
      }
    } catch (_) {}

    // Load Users
    try {
      const res = await fetch('/api/admin/users');
      if (res.ok) {
        const data = await res.json();
        const tbody = document.getElementById('admin-users-tbody');
        if (tbody) {
          tbody.innerHTML = '';
          data.users.forEach(u => {
            const tr = document.createElement('tr');
            const roleBadge = u.is_admin ? `<span class="badge-tag admin">${I18N.t('admin_role_admin')}</span>` : `<span class="badge-tag user">${I18N.t('admin_role_player')}</span>`;
            const isSelf = this.currentUser && this.currentUser.id === u.id;
            tr.innerHTML = `
              <td><strong>@${u.username}</strong></td>
              <td>${I18N.getFlagEmoji(u.national_flag)}</td>
              <td>${roleBadge}</td>
              <td>${u.games_count}</td>
              <td style="font-family: var(--font-mono); font-size: 0.8rem;">${(u.created_at || '').substring(0, 10)}</td>
              <td>
                ${!isSelf ? `
                  <button class="btn-outline" style="padding: 4px 8px; font-size: 0.75rem;" onclick="AllianceApp.toggleUserAdmin('${u.id}', ${u.is_admin})">
                    ${u.is_admin ? I18N.t('btn_demote_admin') : I18N.t('btn_make_admin')}
                  </button>
                  <button class="btn-outline" style="padding: 4px 8px; font-size: 0.75rem; border-color: var(--accent-magenta); color: var(--accent-magenta);" onclick="AllianceApp.deleteUser('${u.id}', '${u.username}')">
                    ${I18N.t('btn_delete')}
                  </button>
                ` : `<span style="color: var(--text-muted); font-size: 0.8rem;">${I18N.t('admin_current_user')}</span>`}
              </td>
            `;
            tbody.appendChild(tr);
          });
        }
      }
    } catch (_) {}
  },

  async toggleAdminRegistrations() {
    const current = (this.adminSettings && this.adminSettings.registrations_enabled);
    try {
      const res = await fetch('/api/admin/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ registrations_enabled: !current })
      });
      if (res.ok) {
        this.showToast(`Registration policy updated: ${!current ? 'ENABLED' : 'DISABLED'}`);
        await this.loadAdminData();
      }
    } catch (_) {}
  },

  async createInviteToken() {
    const codeInput = document.getElementById('admin-invite-code');
    const usesInput = document.getElementById('admin-invite-uses');
    const token = codeInput ? codeInput.value.trim() : '';
    const uses = parseInt(usesInput ? usesInput.value : '1', 10);

    try {
      const res = await fetch('/api/admin/invites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token || undefined, uses })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Invite token created: ${data.token}`);
        if (codeInput) codeInput.value = '';
        await this.loadAdminData();
      } else {
        this.showToast(data.detail || "Failed to create invite");
      }
    } catch (_) {}
  },

  async deleteInviteToken(token) {
    try {
      await fetch(`/api/admin/invites/${token}`, { method: 'DELETE' });
      this.showToast(`Invite revoked: ${token}`);
      await this.loadAdminData();
    } catch (_) {}
  },

  copyInviteUrl(token) {
    const url = `${window.location.origin}/?invite=${encodeURIComponent(token)}`;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        this.showToast(`Invite link copied to clipboard: ${token}`);
      });
    } else {
      prompt("Copy invite link:", url);
    }
  },

  async toggleUserAdmin(userId, currentAdmin) {
    try {
      const res = await fetch(`/api/admin/users/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_admin: !currentAdmin })
      });
      if (res.ok) {
        this.showToast("User permissions updated");
        await this.loadAdminData();
      }
    } catch (_) {}
  },

  async deleteUser(userId, username) {
    if (!confirm(I18N.t('admin_confirm_delete_user', { username: username }))) return;
    try {
      const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
      if (res.ok) {
        this.showToast(`User @${username} deleted`);
        await this.loadAdminData();
      } else {
        const d = await res.json();
        this.showToast(d.detail || "Deletion failed");
      }
    } catch (_) {}
  },

  // ---------------- UI Utilities ----------------
  _lastToasts: {},
  _actionErrors: {},

  showActionError(errKey, msg) {
    const now = Date.now();
    const entry = this._actionErrors[errKey] || { count: 0, lastTime: 0 };
    if (now - entry.lastTime > 3500) {
      entry.count = 1;
    } else {
      entry.count++;
    }
    entry.lastTime = now;
    this._actionErrors[errKey] = entry;

    // Only show toast if user attempts the illegal action twice in a short span
    if (entry.count >= 2) {
      this.showToast(msg);
    }
  },

  showToast(msg) {
    if (!msg) return;
    let text = msg;
    if (typeof msg === 'object') {
      if (Array.isArray(msg)) {
        text = msg.map(item => (typeof item === 'object' ? (item.msg || item.message || JSON.stringify(item)) : String(item))).join('; ');
      } else if (msg.detail) {
        text = typeof msg.detail === 'object'
          ? (Array.isArray(msg.detail) ? msg.detail.map(d => d.msg || d.message || JSON.stringify(d)).join('; ') : JSON.stringify(msg.detail))
          : String(msg.detail);
      } else if (msg.message) {
        text = msg.message;
      } else {
        text = JSON.stringify(msg);
      }
    } else {
      text = String(msg);
    }

    const container = document.getElementById('toast-container');
    if (!container) return;

    const now = Date.now();
    if (this._lastToasts[text] && (now - this._lastToasts[text] < 2500)) {
      return; // Debounce identical messages within 2.5s
    }
    this._lastToasts[text] = now;

    // Cap visible toasts to 2 max to eliminate visual spam
    while (container.children.length >= 2) {
      container.removeChild(container.firstChild);
    }

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = text;
    container.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.remove();
    }, 3500);
  },

  onLanguageChange(lang) {
    const langBtn = document.getElementById('lang-toggle-btn');
    if (langBtn) langBtn.textContent = lang.toUpperCase();
    this.loadNotifications();
    this.updateVitalsUI();
    if (this.radialDock) this.radialDock.renderButtons();
  },

  async openRecreateLobbyModal(sourceGameId) {
    const targetGameId = sourceGameId || this.activeGameId || (this.gameState ? this.gameState.id : null);
    if (!targetGameId) {
      this.showToast("No match selected for recreation");
      return;
    }
    this._recreateSourceGameId = targetGameId;
    let game = this.gameState;
    if (!game || game.id !== targetGameId) {
      try {
        const res = await fetch(`/api/games/${targetGameId}`);
        if (res.ok) game = await res.json();
      } catch (_) {}
    }
    if (!game) {
      this.showToast("Unable to load lobby details");
      return;
    }

    const cfg = game.config || {};
    const setVal = (id, val, isCheckbox = false) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (isCheckbox) el.checked = Boolean(val);
      else el.value = (val !== undefined && val !== null) ? val : '';
    };

    setVal('recreate-cfg-name', `${game.name || 'Match'} (Rematch)`);
    setVal('recreate-cfg-width', cfg.grid_width || 15);
    setVal('recreate-cfg-height', cfg.grid_height || 15);
    setVal('recreate-cfg-first-ap-days', (cfg.first_ap_drop_days !== undefined) ? cfg.first_ap_drop_days : 1);
    setVal('recreate-cfg-drop-amount', cfg.ap_drop_amount || 1);
    setVal('recreate-cfg-ap-drop-server-time', cfg.ap_drop_server_time || '22:00');
    setVal('recreate-cfg-burst-cap', cfg.burst_cap_ap || 3);
    setVal('recreate-cfg-starting-hp', cfg.starting_hp || 3);
    setVal('recreate-cfg-starting-ap', (cfg.starting_ap !== undefined) ? cfg.starting_ap : 0);
    setVal('recreate-cfg-starting-range', cfg.starting_range || 1);
    setVal('recreate-cfg-allow-late-join', cfg.allow_late_join, true);
    setVal('recreate-cfg-map-voting-enabled', cfg.map_voting_enabled, true);
    setVal('recreate-cfg-map-voting-duration', cfg.map_voting_duration_minutes || 10);

    const modal = document.getElementById('modal-recreate-lobby');
    if (modal) modal.classList.add('active');
  },

  async submitRecreateLobby() {
    if (!this._recreateSourceGameId) {
      this.showToast("Missing source match reference");
      return;
    }
    const name = document.getElementById('recreate-cfg-name')?.value?.trim();
    if (!name) {
      this.showToast("Please provide a name for the recreated lobby");
      return;
    }

    const gridWidth = parseInt(document.getElementById('recreate-cfg-width')?.value || '15', 10);
    const gridHeight = parseInt(document.getElementById('recreate-cfg-height')?.value || '15', 10);
    const firstApDropDays = parseInt(document.getElementById('recreate-cfg-first-ap-days')?.value || '1', 10);
    const dropAmount = parseInt(document.getElementById('recreate-cfg-drop-amount')?.value || '1', 10);
    const apDropServerTime = document.getElementById('recreate-cfg-ap-drop-server-time')?.value || '22:00';
    const burstCap = parseInt(document.getElementById('recreate-cfg-burst-cap')?.value || '3', 10);
    const startingHp = parseInt(document.getElementById('recreate-cfg-starting-hp')?.value || '3', 10);
    const startingAp = parseInt(document.getElementById('recreate-cfg-starting-ap')?.value || '0', 10);
    const startingRange = parseInt(document.getElementById('recreate-cfg-starting-range')?.value || '1', 10);
    const allowLateJoin = document.getElementById('recreate-cfg-allow-late-join')?.checked || false;
    const mapVotingEnabled = document.getElementById('recreate-cfg-map-voting-enabled')?.checked || false;
    const mapVotingDuration = parseInt(document.getElementById('recreate-cfg-map-voting-duration')?.value || '10', 10);

    const payload = {
      name,
      is_playtest: Boolean(this.gameState?.is_playtest),
      config: {
        grid_width: gridWidth,
        grid_height: gridHeight,
        burst_cap_ap: burstCap,
        ap_drop_amount: dropAmount,
        starting_hp: startingHp,
        starting_ap: startingAp,
        starting_range: startingRange,
        first_ap_drop_days: firstApDropDays,
        ap_drop_server_time: apDropServerTime,
        allow_late_join: allowLateJoin,
        map_voting_enabled: mapVotingEnabled,
        map_voting_duration_minutes: mapVotingDuration
      }
    };

    try {
      const res = await fetch(`/api/games/${this._recreateSourceGameId}/recreate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Lobby recreated! ${data.players_transferred_count} players transferred.`);
        const modal = document.getElementById('modal-recreate-lobby');
        if (modal) modal.classList.remove('active');
        const auditModal = document.getElementById('modal-lobby-settings-audit');
        if (auditModal) auditModal.classList.remove('active');
        this.activeGameId = data.game_id;
        localStorage.setItem('alliance_last_game_id', data.game_id);
        this.showView('tactical-view');
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to recreate lobby");
      }
    } catch (_) {
      this.showToast("Network error recreating lobby");
    }
  },

  async requestLateJoin(gameId) {
    if (!gameId) return;
    const conf = confirm(I18N.t('confirm_late_join') || "Request late entry into this active match? Host approval is required.");
    if (!conf) return;
    try {
      const res = await fetch(`/api/games/${gameId}/join-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(I18N.t('join_request_submitted') || "Late join request submitted to lobby host for approval.");
        await this.loadLobbies();
      } else {
        this.showToast(data.detail || "Failed to submit join request");
      }
    } catch (_) {
      this.showToast("Network error submitting join request");
    }
  },

  async loadLobbyJoinRequests(gameId) {
    const listEl = document.getElementById('lobby-join-requests-list');
    if (!listEl || !gameId) return;
    try {
      const res = await fetch(`/api/games/${gameId}/join-requests`);
      if (res.ok) {
        const data = await res.json();
        listEl.innerHTML = '';
        if (!data.requests || data.requests.length === 0) {
          listEl.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8rem; padding: 6px 0;" data-i18n="no_pending_requests">No pending late join requests.</div>`;
          return;
        }
        data.requests.forEach(req => {
          const item = document.createElement('div');
          item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.3); border: 1px solid var(--border-subtle); padding: 8px 12px; border-radius: var(--radius-sm);';
          const isPending = req.status === 'PENDING';
          const statusColor = req.status === 'APPROVED' ? 'var(--matrix-mint)' : (req.status === 'DENIED' ? '#EF4444' : 'var(--energy-amber)');

          item.innerHTML = `
            <div>
              <div style="font-weight: 600; font-size: 0.85rem; color: var(--text-primary);">@${req.username}</div>
              <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">${(req.requested_at || '').substring(0, 16).replace('T', ' ')} Server</div>
            </div>
            <div style="display: flex; gap: 6px; align-items: center;">
              <span class="badge-tag" style="border-color: ${statusColor}; color: ${statusColor};">${req.status}</span>
              ${isPending ? `
                <button class="btn-cyan" style="padding: 3px 8px; font-size: 0.75rem;" onclick="AllianceApp.approveLateJoin('${gameId}', '${req.id}')">${I18N.t('btn_approve_join') || 'Approve'}</button>
                <button class="btn-outline" style="padding: 3px 8px; font-size: 0.75rem; border-color: #EF4444; color: #EF4444;" onclick="AllianceApp.denyLateJoin('${gameId}', '${req.id}')">${I18N.t('btn_deny_join') || 'Deny'}</button>
              ` : ''}
            </div>
          `;
          listEl.appendChild(item);
        });
      }
    } catch (_) {}
  },

  async approveLateJoin(gameId, reqId) {
    try {
      const res = await fetch(`/api/games/${gameId}/join-requests/${reqId}/approve`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Approved late join for @${data.username}! Operative enlisted.`);
        await this.loadActiveGameState();
        await this.loadLobbyJoinRequests(gameId);
      } else {
        this.showToast(data.detail || "Approval failed");
      }
    } catch (_) {
      this.showToast("Network error approving request");
    }
  },

  async denyLateJoin(gameId, reqId) {
    try {
      const res = await fetch(`/api/games/${gameId}/join-requests/${reqId}/deny`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Denied late join request for @${data.username}.`);
        await this.loadLobbyJoinRequests(gameId);
      } else {
        this.showToast(data.detail || "Denial failed");
      }
    } catch (_) {
      this.showToast("Network error denying request");
    }
  },

  async renderMapVotingView() {
    if (!this.activeGameId || !this.gameState || this.gameState.status !== 'MAP_VOTING') return;
    const nameEl = document.getElementById('map-voting-game-name');
    if (nameEl) nameEl.textContent = this.gameState.name || 'Deployment Map Election';

    const isHostOrAdmin = this.currentUser && (this.currentUser.id === this.gameState.created_by || this.currentUser.is_admin);
    const btnEarly = document.getElementById('btn-map-vote-resolve-early');
    if (btnEarly) btnEarly.style.display = isHostOrAdmin ? 'inline-block' : 'none';

    await this.loadMapVotingData();
  },

  async loadMapVotingData() {
    if (!this.activeGameId) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/map-voting`);
      if (!res.ok) return;
      const data = await res.json();
      this.mapVotingData = data;

      // Update timer countdown
      const cdSpan = document.getElementById('map-voting-countdown');
      if (cdSpan && data.map_voting_ends_at) {
        const endMs = new Date(data.map_voting_ends_at).getTime();
        const diffMs = Math.max(0, endMs - Date.now());
        const mins = Math.floor(diffMs / 60000);
        const secs = Math.floor((diffMs % 60000) / 1000);
        cdSpan.textContent = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
      }

      // Update progress
      const progEl = document.getElementById('map-voting-progress-text');
      if (progEl) {
        progEl.textContent = `${data.voted_count || 0} / ${data.total_players || 0} players voted`;
      }

      // Populate candidate cards
      const grid = document.getElementById('map-voting-options-grid');
      if (grid && data.options) {
        grid.innerHTML = '';
        data.options.forEach(opt => {
          const card = document.createElement('div');
          card.className = 'acrylic-card map-candidate-card';
          card.innerHTML = `
            <div class="map-candidate-header">
              <span class="map-candidate-title">${opt.label}</span>
              <span class="badge-tag user">#${opt.option_idx}</span>
            </div>
            <div id="map-preview-canvas-${opt.option_idx}" class="map-preview-canvas">
            </div>
            <div class="map-candidate-meta">
              <span>Placements: ${(opt.placements || []).length}</span>
              <span style="color: var(--matrix-mint); font-family: var(--font-mono); font-weight: bold;">GRID</span>
            </div>
          `;
          grid.appendChild(card);
          const previewContainer = card.querySelector(`#map-preview-canvas-${opt.option_idx}`);
          if (previewContainer) {
            this.renderMapOptionPreview(opt, previewContainer);
          }
        });
      }

      // Pre-select user votes if already voted
      const statusText = document.getElementById('map-vote-status-text');
      if (data.user_vote && Array.isArray(data.user_vote) && data.user_vote.length === 4) {
        const [r1, r2, r3, r4] = data.user_vote;
        const s1 = document.getElementById('vote-rank-1');
        const s2 = document.getElementById('vote-rank-2');
        const s3 = document.getElementById('vote-rank-3');
        const s4 = document.getElementById('vote-rank-4');
        if (s1) s1.value = r1;
        if (s2) s2.value = r2;
        if (s3) s3.value = r3;
        if (s4) s4.value = r4;
        if (statusText) {
          statusText.textContent = `✓ Current ballot registered: [${data.user_vote.join(' > ')}]`;
        }
      } else {
        if (statusText) statusText.textContent = '';
      }
    } catch (_) {}
  },

  renderMapOptionPreview(opt, container) {
    if (!container || !opt || !opt.placements) return;
    const placements = opt.placements;
    const width = this.gameState?.config?.grid_width || 15;
    const height = this.gameState?.config?.grid_height || 15;

    // Render SVG preview
    const svgNs = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("viewBox", `0 0 ${width * 10} ${height * 10}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("style", "width: 100%; height: 100%; display: block;");

    // Grid background
    const bg = document.createElementNS(svgNs, "rect");
    bg.setAttribute("width", `${width * 10}`);
    bg.setAttribute("height", `${height * 10}`);
    bg.setAttribute("fill", "#050b14");
    svg.appendChild(bg);

    // Subtle grid lines
    for (let x = 0; x <= width; x++) {
      const line = document.createElementNS(svgNs, "line");
      line.setAttribute("x1", `${x * 10}`);
      line.setAttribute("y1", "0");
      line.setAttribute("x2", `${x * 10}`);
      line.setAttribute("y2", `${height * 10}`);
      line.setAttribute("stroke", "rgba(0, 240, 255, 0.08)");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
    }
    for (let y = 0; y <= height; y++) {
      const line = document.createElementNS(svgNs, "line");
      line.setAttribute("x1", "0");
      line.setAttribute("y1", `${y * 10}`);
      line.setAttribute("x2", `${width * 10}`);
      line.setAttribute("y2", `${y * 10}`);
      line.setAttribute("stroke", "rgba(0, 240, 255, 0.08)");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
    }

    // Players placement dots
    placements.forEach(p => {
      const circle = document.createElementNS(svgNs, "circle");
      circle.setAttribute("cx", `${p.x * 10 + 5}`);
      circle.setAttribute("cy", `${p.y * 10 + 5}`);
      circle.setAttribute("r", "3.2");
      circle.setAttribute("fill", "#00F0FF");
      circle.setAttribute("stroke", "#10B981");
      circle.setAttribute("stroke-width", "1");
      svg.appendChild(circle);
    });

    container.innerHTML = '';
    container.appendChild(svg);
  },

  async submitMapVote() {
    if (!this.activeGameId) return;
    const r1 = parseInt(document.getElementById('vote-rank-1')?.value || '1', 10);
    const r2 = parseInt(document.getElementById('vote-rank-2')?.value || '2', 10);
    const r3 = parseInt(document.getElementById('vote-rank-3')?.value || '3', 10);
    const r4 = parseInt(document.getElementById('vote-rank-4')?.value || '4', 10);

    const rankings = [r1, r2, r3, r4];
    const unique = new Set(rankings);
    if (unique.size !== 4) {
      this.showToast("Each preference rank (1st to 4th) must be a unique option.");
      return;
    }

    try {
      const res = await fetch(`/api/games/${this.activeGameId}/map-vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rankings })
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast("Ranked choice ballot cast successfully!");
        await this.loadMapVotingData();
      } else {
        this.showToast(data.detail || "Failed to submit ballot");
      }
    } catch (_) {
      this.showToast("Network error submitting ballot");
    }
  },

  async resolveMapVoteEarly() {
    if (!this.activeGameId) return;
    const conf = confirm(I18N.t('confirm_resolve_map_vote') || "Tally ranked-choice votes now and initiate deployment?");
    if (!conf) return;
    try {
      const res = await fetch(`/api/games/${this.activeGameId}/map-voting/resolve`, {
        method: 'POST'
      });
      const data = await res.json();
      if (res.ok) {
        this.showToast(`Map voting resolved: Option ${data.winning_option_idx} selected! Deployment active.`);
        await this.loadActiveGameState();
      } else {
        this.showToast(data.detail || "Failed to resolve voting");
      }
    } catch (_) {
      this.showToast("Network error resolving voting");
    }
  },

  async initPushNotifications() {
    if ('serviceWorker' in navigator) {
      try {
        await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      } catch (e) {
        try {
          await navigator.serviceWorker.register('/static/sw.js');
        } catch (_) {}
      }
    }
  }
};

window.addEventListener('DOMContentLoaded', () => AllianceApp.init());
