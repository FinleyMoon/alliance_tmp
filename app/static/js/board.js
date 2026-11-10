/**
 * Tactical Matrix Board Renderer (HTML5 Canvas)
 * Supports pinch-to-zoom, pan, range projector, and ghost trails.
 */
class TacticalBoard {
  constructor(canvasId, onTileSelect) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.onTileSelect = onTileSelect;

    this.boardWidth = 15;
    this.boardHeight = 15;
    this.shrinkLevel = 0;
    this.players = [];
    this.selfPlayerId = null;
    this.selectedTile = null; // {x, y}
    this.ghostTrails = [];
    this.casualtySites = [];

    // Camera transform
    this.scale = 1.0;
    this.panX = 0;
    this.panY = 0;
    this.tileSize = 48;

    this.isDragging = false;
    this.dragStart = { x: 0, y: 0 };
    this.dashOffset = 0;
    this.avatarCache = new Map();
    this.isViewLocked = false;
    this.batchPreview = [];

    this.initEvents();
    this.resize();
    this.startAnimationLoop();
  }

  toggleViewLock() {
    this.isViewLocked = !this.isViewLocked;
    return this.isViewLocked;
  }

  resize() {
    if (!this.canvas || !this.canvas.parentElement) return;
    const rect = this.canvas.parentElement.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.floor(rect.width * dpr);
    this.canvas.height = Math.floor(rect.height * dpr);
    this.canvas.style.width = `${rect.width}px`;
    this.canvas.style.height = `${rect.height}px`;

    this.centerBoard();
  }

  centerBoard() {
    if (!this.canvas || !this.canvas.parentElement) return;
    const rect = this.canvas.parentElement.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const boardPxW = this.boardWidth * this.tileSize;
    const boardPxH = this.boardHeight * this.tileSize;
    const padding = 32;
    const availW = Math.max(100, rect.width - padding);
    const availH = Math.max(100, rect.height - padding);

    let calculatedScale = Math.min(availW / boardPxW, availH / boardPxH, 1.2);
    if (!isFinite(calculatedScale) || calculatedScale <= 0) {
      calculatedScale = 1.0;
    }
    this.scale = calculatedScale;

    this.panX = Math.floor((rect.width - boardPxW * this.scale) / 2);
    this.panY = Math.floor((rect.height - boardPxH * this.scale) / 2);
    if (!isFinite(this.panX)) this.panX = 20;
    if (!isFinite(this.panY)) this.panY = 20;
  }

  setState(gameState, diffData = null) {
    if (!gameState) return;
    this.boardWidth = gameState.board_width || this.boardWidth || 15;
    this.boardHeight = gameState.board_height || this.boardHeight || 15;
    this.shrinkLevel = gameState.current_shrink_level || 0;
    this.players = gameState.players || [];
    if (gameState.self_player_id !== undefined) {
      this.selfPlayerId = gameState.self_player_id;
    }

    if (diffData) {
      this.ghostTrails = diffData.ghost_trails || [];
      this.casualtySites = diffData.casualty_sites || [];
    }

    if (this.canvas.width <= 0 || !isFinite(this.panX) || this.panX === 0) {
      this.resize();
    }
    this.render();
  }

  initEvents() {
    window.addEventListener('resize', () => this.resize());

    let lastTapTime = 0;
    let lastTapPos = { x: 0, y: 0 };
    let initialPinchDist = null;
    let initialPinchScale = 1.0;
    let touchMoved = false;

    // Mouse Pan & Drag
    this.canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return; // Only primary button for drag
      this.dragStart = { x: e.clientX - this.panX, y: e.clientY - this.panY, downX: e.clientX, downY: e.clientY };
      if (!this.isViewLocked) {
        this.isDragging = true;
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (this.isDragging && !this.isViewLocked && this.dragStart) {
        this.panX = e.clientX - this.dragStart.x;
        this.panY = e.clientY - this.dragStart.y;
      }
    });

    window.addEventListener('mouseup', (e) => {
      if (e.button !== 0) return;
      if (this.dragStart) {
        const dist = Math.hypot(e.clientX - this.dragStart.downX, e.clientY - this.dragStart.downY);
        this.isDragging = false;
        if (dist < 8) {
          const now = Date.now();
          const distToLast = Math.hypot(e.clientX - lastTapPos.x, e.clientY - lastTapPos.y);
          if (now - lastTapTime < 320 && distToLast < 20) {
            lastTapTime = 0;
            this.handleDoubleClick(e.clientX, e.clientY);
          } else {
            lastTapTime = now;
            lastTapPos = { x: e.clientX, y: e.clientY };
            this.handleClick(e.clientX, e.clientY);
          }
        }
      }
    });

    // Native Double Click
    this.canvas.addEventListener('dblclick', (e) => {
      e.preventDefault();
      this.handleDoubleClick(e.clientX, e.clientY);
    });

    // Right-Click Context Menu
    this.canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      this.handleDoubleClick(e.clientX, e.clientY, true);
    });

    // Mouse Wheel Zoom
    this.canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (this.isViewLocked) return;

      const rect = this.canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const zoomFactor = e.deltaY < 0 ? 1.15 : 0.88;
      const newScale = Math.min(Math.max(this.scale * zoomFactor, 0.35), 4.0);

      // Centered on mouse pointer
      this.panX = mouseX - (mouseX - this.panX) * (newScale / this.scale);
      this.panY = mouseY - (mouseY - this.panY) * (newScale / this.scale);
      this.scale = newScale;
      this.render();
    }, { passive: false });

    // Touch Support for Mobile (Tap, Double Tap, Pan, Pinch-to-Zoom)
    this.canvas.addEventListener('touchstart', (e) => {
      touchMoved = false;
      if (e.touches.length === 1) {
        const t = e.touches[0];
        this.dragStart = { x: t.clientX - this.panX, y: t.clientY - this.panY, downX: t.clientX, downY: t.clientY, time: Date.now() };
        if (!this.isViewLocked) {
          this.isDragging = true;
        }
      } else if (e.touches.length === 2 && !this.isViewLocked) {
        this.isDragging = false;
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        initialPinchDist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
        initialPinchScale = this.scale;
      }
    }, { passive: true });

    this.canvas.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1 && this.isDragging && !this.isViewLocked && this.dragStart) {
        const t = e.touches[0];
        const dist = Math.hypot(t.clientX - this.dragStart.downX, t.clientY - this.dragStart.downY);
        if (dist > 6) touchMoved = true;
        this.panX = t.clientX - this.dragStart.x;
        this.panY = t.clientY - this.dragStart.y;
        e.preventDefault();
      } else if (e.touches.length === 2 && initialPinchDist && !this.isViewLocked) {
        touchMoved = true;
        e.preventDefault();
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        const curDist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
        const rect = this.canvas.getBoundingClientRect();
        const midX = (t1.clientX + t2.clientX) / 2 - rect.left;
        const midY = (t1.clientY + t2.clientY) / 2 - rect.top;

        const factor = curDist / initialPinchDist;
        const newScale = Math.min(Math.max(initialPinchScale * factor, 0.35), 4.0);

        this.panX = midX - (midX - this.panX) * (newScale / this.scale);
        this.panY = midY - (midY - this.panY) * (newScale / this.scale);
        this.scale = newScale;
        this.render();
      }
    }, { passive: false });

    this.canvas.addEventListener('touchend', (e) => {
      if (e.touches.length === 0) {
        this.isDragging = false;
        initialPinchDist = null;
        if (!touchMoved && this.dragStart) {
          const now = Date.now();
          const tX = this.dragStart.downX;
          const tY = this.dragStart.downY;
          const distToLast = Math.hypot(tX - lastTapPos.x, tY - lastTapPos.y);

          if (now - lastTapTime < 350 && distToLast < 30) {
            lastTapTime = 0;
            this.handleDoubleClick(tX, tY);
          } else {
            lastTapTime = now;
            lastTapPos = { x: tX, y: tY };
            this.handleClick(tX, tY);
          }
        }
      }
    }, { passive: true });
  }

  handleClick(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const localX = (clientX - rect.left - this.panX) / this.scale;
    const localY = (clientY - rect.top - this.panY) / this.scale;

    const tileX = Math.floor(localX / this.tileSize) + 1;
    const tileY = Math.floor(localY / this.tileSize) + 1;

    if (tileX >= 1 && tileX <= this.boardWidth && tileY >= 1 && tileY <= this.boardHeight) {
      this.selectedTile = { x: tileX, y: tileY };
      if (navigator.vibrate) navigator.vibrate(15);
      if (this.onTileSelect) {
        const playerAtTile = this.players.find(p => p.x === tileX && p.y === tileY && p.is_alive);
        this.onTileSelect({
          tileX,
          tileY,
          player: playerAtTile,
          clientX,
          clientY,
          isDoubleClick: false
        });
      }
    }
  }

  handleDoubleClick(clientX, clientY, isRightClick = false) {
    const rect = this.canvas.getBoundingClientRect();
    const localX = (clientX - rect.left - this.panX) / this.scale;
    const localY = (clientY - rect.top - this.panY) / this.scale;

    const tileX = Math.floor(localX / this.tileSize) + 1;
    const tileY = Math.floor(localY / this.tileSize) + 1;

    if (tileX >= 1 && tileX <= this.boardWidth && tileY >= 1 && tileY <= this.boardHeight) {
      this.selectedTile = { x: tileX, y: tileY };
      if (navigator.vibrate) navigator.vibrate(30);
      if (this.onTileSelect) {
        const playerAtTile = this.players.find(p => p.x === tileX && p.y === tileY && p.is_alive);
        this.onTileSelect({
          tileX,
          tileY,
          player: playerAtTile,
          clientX,
          clientY,
          isDoubleClick: true,
          isRightClick: isRightClick
        });
      }
    }
  }

  startAnimationLoop() {
    const loop = () => {
      this.dashOffset = (this.dashOffset + 0.3) % 12;
      this.render();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  render() {
    if (!this.canvas || !this.ctx) return;
    if (this.canvas.width <= 0 || this.canvas.height <= 0 || !isFinite(this.panX) || !isFinite(this.scale)) {
      this.resize();
    }
    const dpr = window.devicePixelRatio || 1;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    this.ctx.save();
    this.ctx.scale(dpr, dpr);
    this.ctx.translate(this.panX, this.panY);
    this.ctx.scale(this.scale, this.scale);

    this.drawGrid();
    this.drawRangeOverlay();
    this.drawGhostTrails();
    this.drawBatchPreview();
    this.drawCasualtySites();
    this.drawPlayers();
    this.drawSelectionReticle();

    this.ctx.restore();
  }

  drawGrid() {
    const minX = 1 + this.shrinkLevel;
    const maxX = this.boardWidth - this.shrinkLevel;
    const minY = 1 + this.shrinkLevel;
    const maxY = this.boardHeight - this.shrinkLevel;

    // Draw active zone background
    this.ctx.fillStyle = 'rgba(17, 24, 39, 0.5)';
    this.ctx.fillRect(
      (minX - 1) * this.tileSize,
      (minY - 1) * this.tileSize,
      (maxX - minX + 1) * this.tileSize,
      (maxY - minY + 1) * this.tileSize
    );

    // Crosshairs at intersections
    this.ctx.strokeStyle = '#1E293B';
    this.ctx.lineWidth = 1;
    for (let x = 1; x <= this.boardWidth; x++) {
      for (let y = 1; y <= this.boardHeight; y++) {
        const px = (x - 1) * this.tileSize;
        const py = (y - 1) * this.tileSize;

        // Subtle crosshair '+'
        this.ctx.beginPath();
        this.ctx.moveTo(px - 3, py);
        this.ctx.lineTo(px + 3, py);
        this.ctx.moveTo(px, py - 3);
        this.ctx.lineTo(px, py + 3);
        this.ctx.stroke();
      }
    }

    // Grid outer border
    this.ctx.strokeStyle = '#334155';
    this.ctx.strokeRect(0, 0, this.boardWidth * this.tileSize, this.boardHeight * this.tileSize);

    // Shrunk danger zone hatching
    if (this.shrinkLevel > 0) {
      this.ctx.strokeStyle = 'rgba(244, 63, 94, 0.6)';
      this.ctx.lineWidth = 2;
      this.ctx.strokeRect(
        (minX - 1) * this.tileSize,
        (minY - 1) * this.tileSize,
        (maxX - minX + 1) * this.tileSize,
        (maxY - minY + 1) * this.tileSize
      );
    }
  }

  drawRangeOverlay() {
    // Project range overlay if self player or selected living player
    const targetPlayer = this.players.find(p => p.id === this.selfPlayerId && p.is_alive);
    if (!targetPlayer) return;

    const r = targetPlayer.range_level ? targetPlayer.range_level + 1 : 2;
    const x0 = targetPlayer.x;
    const y0 = targetPlayer.y;

    this.ctx.fillStyle = 'rgba(0, 240, 255, 0.08)';
    this.ctx.strokeStyle = '#00F0FF';
    this.ctx.lineWidth = 1.5;
    this.ctx.setLineDash([6, 6]);
    this.ctx.lineDashOffset = -this.dashOffset;

    // Draw range footprint tiles
    for (let x = 1; x <= this.boardWidth; x++) {
      for (let y = 1; y <= this.boardHeight; y++) {
        const dx = Math.abs(x - x0);
        const dy = Math.abs(y - y0);
        const inCore = Math.max(dx, dy) <= r;
        const inSpike = (dx === r + 1 && dy === 0) || (dy === r + 1 && dx === 0);

        if ((inCore || inSpike) && !(x === x0 && y === y0)) {
          const px = (x - 1) * this.tileSize;
          const py = (y - 1) * this.tileSize;
          this.ctx.fillRect(px + 2, py + 2, this.tileSize - 4, this.tileSize - 4);
        }
      }
    }
    this.ctx.setLineDash([]);
  }

  drawGhostTrails() {
    this.ghostTrails.forEach(trail => {
      const fromPx = (trail.from_x - 0.5) * this.tileSize;
      const fromPy = (trail.from_y - 0.5) * this.tileSize;
      const toPx = (trail.to_x - 0.5) * this.tileSize;
      const toPy = (trail.to_y - 0.5) * this.tileSize;

      this.ctx.strokeStyle = 'rgba(0, 240, 255, 0.4)';
      this.ctx.lineWidth = 2;
      this.ctx.setLineDash([4, 4]);

      this.ctx.beginPath();
      this.ctx.moveTo(fromPx, fromPy);
      this.ctx.lineTo(toPx, toPy);
      this.ctx.stroke();

      // Arrowhead
      const angle = Math.atan2(toPy - fromPy, toPx - fromPx);
      this.ctx.fillStyle = 'rgba(0, 240, 255, 0.6)';
      this.ctx.beginPath();
      this.ctx.moveTo(toPx, toPy);
      this.ctx.lineTo(toPx - 8 * Math.cos(angle - Math.PI / 6), toPy - 8 * Math.sin(angle - Math.PI / 6));
      this.ctx.lineTo(toPx - 8 * Math.cos(angle + Math.PI / 6), toPy - 8 * Math.sin(angle + Math.PI / 6));
      this.ctx.fill();
    });
    this.ctx.setLineDash([]);
  }

  setBatchPreview(batch) {
    this.batchPreview = Array.isArray(batch) ? batch : [];
    this.render();
  }

  drawBatchPreview() {
    if (!this.batchPreview || this.batchPreview.length === 0) return;
    const selfPlayer = this.players.find(p => p.id === this.selfPlayerId && p.is_alive);
    if (!selfPlayer) return;

    let curX = selfPlayer.x;
    let curY = selfPlayer.y;
    const path = [{ x: curX, y: curY }];
    const stepMarkers = [];
    const attackTargets = [];
    const giftTargets = [];

    this.batchPreview.forEach((cmd, idx) => {
      const stepNum = idx + 1;
      if (cmd.action_type === 'MOVE') {
        const toX = (cmd.to_x !== undefined) ? cmd.to_x : (cmd.target_x !== undefined ? cmd.target_x : curX);
        const toY = (cmd.to_y !== undefined) ? cmd.to_y : (cmd.target_y !== undefined ? cmd.target_y : curY);
        curX = toX;
        curY = toY;
        path.push({ x: curX, y: curY });
        stepMarkers.push({ x: curX, y: curY, stepNum, action_type: 'MOVE' });
      } else if (cmd.action_type === 'ATTACK') {
        let tx = cmd.target_x;
        let ty = cmd.target_y;
        if (cmd.target_player_id) {
          const tp = this.players.find(p => p.id === cmd.target_player_id);
          if (tp) { tx = tp.x; ty = tp.y; }
        }
        if (tx !== undefined && ty !== undefined) {
          attackTargets.push({ x: tx, y: ty, stepNum, target_player_id: cmd.target_player_id });
        }
      } else if (cmd.action_type === 'GIVE_AP') {
        let tx = cmd.target_x;
        let ty = cmd.target_y;
        if (cmd.target_player_id) {
          const tp = this.players.find(p => p.id === cmd.target_player_id);
          if (tp) { tx = tp.x; ty = tp.y; }
        }
        if (tx !== undefined && ty !== undefined) {
          giftTargets.push({ x: tx, y: ty, stepNum, target_player_id: cmd.target_player_id });
        }
      }
    });

    // 1. Draw trajectory polyline if there are movements
    if (path.length > 1) {
      this.ctx.save();
      this.ctx.strokeStyle = '#00F0FF';
      this.ctx.lineWidth = 3;
      this.ctx.lineCap = 'round';
      this.ctx.lineJoin = 'round';
      this.ctx.shadowColor = '#00F0FF';
      this.ctx.shadowBlur = 8;
      this.ctx.setLineDash([8, 5]);
      this.ctx.lineDashOffset = -this.dashOffset;

      this.ctx.beginPath();
      path.forEach((pt, i) => {
        const px = (pt.x - 0.5) * this.tileSize;
        const py = (pt.y - 0.5) * this.tileSize;
        if (i === 0) this.ctx.moveTo(px, py);
        else this.ctx.lineTo(px, py);
      });
      this.ctx.stroke();
      this.ctx.restore();

      // Draw intermediate step badges
      stepMarkers.forEach(m => {
        const cx = (m.x - 0.5) * this.tileSize;
        const cy = (m.y - 0.5) * this.tileSize;
        const badgeR = Math.max(9, Math.floor(this.tileSize * 0.18));

        this.ctx.save();
        this.ctx.fillStyle = '#00F0FF';
        this.ctx.shadowColor = 'rgba(0, 240, 255, 0.6)';
        this.ctx.shadowBlur = 6;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, badgeR, 0, Math.PI * 2);
        this.ctx.fill();

        this.ctx.fillStyle = '#0B0F17';
        this.ctx.font = `bold ${Math.floor(badgeR * 1.2)}px Inter, sans-serif`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText(String(m.stepNum), cx, cy);
        this.ctx.restore();
      });

      // Draw Ghost Destination Token at final position
      if (curX !== selfPlayer.x || curY !== selfPlayer.y) {
        const destCx = (curX - 0.5) * this.tileSize;
        const destCy = (curY - 0.5) * this.tileSize;
        const ghostR = Math.floor(this.tileSize * 0.36);

        this.ctx.save();
        this.ctx.fillStyle = 'rgba(0, 240, 255, 0.22)';
        this.ctx.strokeStyle = '#00F0FF';
        this.ctx.lineWidth = 2;
        this.ctx.setLineDash([5, 3]);
        this.ctx.lineDashOffset = this.dashOffset;

        this.ctx.beginPath();
        this.ctx.arc(destCx, destCy, ghostR, 0, Math.PI * 2);
        this.ctx.fill();
        this.ctx.stroke();

        // Ghost text badge
        this.ctx.fillStyle = '#00F0FF';
        this.ctx.font = `bold ${Math.floor(this.tileSize * 0.18)}px Inter, sans-serif`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText('DEST', destCx, destCy);
        this.ctx.restore();
      }
    }

    // 2. Draw Attack Reticles
    attackTargets.forEach(t => {
      const cx = (t.x - 0.5) * this.tileSize;
      const cy = (t.y - 0.5) * this.tileSize;
      const r = Math.floor(this.tileSize * 0.44);

      this.ctx.save();
      this.ctx.strokeStyle = '#EF4444';
      this.ctx.lineWidth = 2;
      this.ctx.setLineDash([4, 4]);
      this.ctx.lineDashOffset = -this.dashOffset;

      this.ctx.beginPath();
      this.ctx.arc(cx, cy, r, 0, Math.PI * 2);
      this.ctx.stroke();

      // Crosshair ticks
      this.ctx.setLineDash([]);
      this.ctx.beginPath();
      this.ctx.moveTo(cx - r - 4, cy); this.ctx.lineTo(cx - r + 4, cy);
      this.ctx.moveTo(cx + r - 4, cy); this.ctx.lineTo(cx + r + 4, cy);
      this.ctx.moveTo(cx, cy - r - 4); this.ctx.lineTo(cx, cy - r + 4);
      this.ctx.moveTo(cx, cy + r - 4); this.ctx.lineTo(cx, cy + r + 4);
      this.ctx.stroke();

      this.ctx.fillStyle = '#EF4444';
      this.ctx.font = `bold ${Math.floor(this.tileSize * 0.2)}px Inter, sans-serif`;
      this.ctx.textAlign = 'center';
      this.ctx.textBaseline = 'alphabetic';
      this.ctx.fillText(`⚔️ #${t.stepNum}`, cx, cy - r - 2);
      this.ctx.restore();
    });

    // 3. Draw Gift AP Target Markers
    giftTargets.forEach(t => {
      const cx = (t.x - 0.5) * this.tileSize;
      const cy = (t.y - 0.5) * this.tileSize;
      const r = Math.floor(this.tileSize * 0.42);

      this.ctx.save();
      this.ctx.strokeStyle = '#F59E0B';
      this.ctx.fillStyle = 'rgba(245, 158, 11, 0.15)';
      this.ctx.lineWidth = 2;
      this.ctx.setLineDash([4, 4]);
      this.ctx.lineDashOffset = this.dashOffset;

      this.ctx.beginPath();
      this.ctx.arc(cx, cy, r, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.stroke();

      this.ctx.fillStyle = '#F59E0B';
      this.ctx.font = `bold ${Math.floor(this.tileSize * 0.2)}px Inter, sans-serif`;
      this.ctx.textAlign = 'center';
      this.ctx.textBaseline = 'alphabetic';
      this.ctx.fillText(`🎁 #${t.stepNum}`, cx, cy - r - 2);
      this.ctx.restore();
    });
  }

  drawCasualtySites() {
    this.casualtySites.forEach(site => {
      const px = (site.x - 1) * this.tileSize;
      const py = (site.y - 1) * this.tileSize;

      this.ctx.fillStyle = 'rgba(244, 63, 94, 0.15)';
      this.ctx.fillRect(px + 4, py + 4, this.tileSize - 8, this.tileSize - 8);

      // Stylized digital glitch marker
      this.ctx.strokeStyle = '#F43F5E';
      this.ctx.lineWidth = 1.5;
      this.ctx.strokeRect(px + 10, py + 10, this.tileSize - 20, this.tileSize - 20);
    });
  }

  drawPlayers() {
    this.players.forEach(p => {
      if (!p.is_alive) return;

      const cx = (p.x - 0.5) * this.tileSize;
      const cy = (p.y - 0.5) * this.tileSize;
      const size = this.tileSize * 0.76;
      const r = size / 2;

      // Status ring
      this.ctx.lineWidth = 2.5;
      if (p.is_self) {
        this.ctx.strokeStyle = '#00F0FF';
        this.ctx.shadowColor = 'rgba(0, 240, 255, 0.6)';
        this.ctx.shadowBlur = 8;
      } else if (p.armistice_vote) {
        this.ctx.strokeStyle = '#10B981';
        this.ctx.shadowColor = 'rgba(16, 185, 129, 0.5)';
        this.ctx.shadowBlur = 6;
      } else {
        this.ctx.strokeStyle = '#94A3B8';
        this.ctx.shadowBlur = 0;
      }

      // Continuous curvature squircle
      this.ctx.fillStyle = '#111827';
      this.ctx.beginPath();
      this.ctx.roundRect(cx - r, cy - r, size, size, 10);
      this.ctx.fill();
      this.ctx.stroke();
      this.ctx.shadowBlur = 0;

      // Render custom avatar if present
      let avatarDrawn = false;
      if (p.avatar_path) {
        if (!this.avatarCache.has(p.avatar_path)) {
          const img = new Image();
          img.src = p.avatar_path;
          this.avatarCache.set(p.avatar_path, img);
        }
        const img = this.avatarCache.get(p.avatar_path);
        if (img && img.complete && img.naturalWidth > 0) {
          this.ctx.save();
          this.ctx.beginPath();
          this.ctx.roundRect(cx - r + 2, cy - r + 2, size - 4, size - 4, 8);
          this.ctx.clip();
          this.ctx.drawImage(img, cx - r + 2, cy - r + 2, size - 4, size - 4);
          this.ctx.restore();
          avatarDrawn = true;
        }
      }

      if (!avatarDrawn) {
        // Draw the Unicode Flag Emoji
        const rawFlag = p.national_flag || '🇺🇳';
        const flagChar = (window.AllianceApp && window.AllianceApp.codeToEmoji) ? window.AllianceApp.codeToEmoji(rawFlag) : rawFlag;
        this.ctx.fillStyle = '#FFFFFF';
        this.ctx.font = `${Math.floor(this.tileSize * 0.44)}px 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText(flagChar, cx, cy);
      }

      // Mini username label below badge
      this.ctx.font = `600 ${Math.floor(this.tileSize * 0.2)}px 'Inter', sans-serif`;
      this.ctx.fillStyle = p.is_self ? '#00F0FF' : '#CBD5E1';
      this.ctx.textAlign = 'center';
      this.ctx.textBaseline = 'alphabetic';
      this.ctx.fillText(p.username.substring(0, 6), cx, cy + r + 10);
    });
  }

  drawSelectionReticle() {
    if (!this.selectedTile) return;
    const px = (this.selectedTile.x - 1) * this.tileSize;
    const py = (this.selectedTile.y - 1) * this.tileSize;

    this.ctx.strokeStyle = '#00F0FF';
    this.ctx.lineWidth = 2;
    this.ctx.strokeRect(px + 1, py + 1, this.tileSize - 2, this.tileSize - 2);
  }

  resetView() {
    this.panX = 20;
    this.panY = 20;
    this.scale = 1.0;
    this.resize();
    this.render();
  }
}

