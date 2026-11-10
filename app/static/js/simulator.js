/**
 * Interactive Rulebook Sandbox Simulator
 * Mini-grid sandbox for testing movement, range expansion, and burst scenarios.
 * Grid dynamically expands as interaction range is upgraded.
 */
class RuleSimulator {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.player = { x: 4, y: 4, rangeLevel: 1 };
    this.dummyTargets = [];
    this.updateGridGeometry();
    this.initEvents();
    this.render();
  }

  updateGridGeometry() {
    // Range tier L requires radius R = L + 1, and cardinal spikes at distance L + 2.
    // Minimum grid size to contain full footprint = 2 * (L + 2) + 1 = 2L + 5.
    this.gridSize = 2 * this.player.rangeLevel + 5;
    // Scale tile size so the board comfortably fits in the panel while expanding
    const containerWidth = 320;
    this.tileSize = Math.max(24, Math.floor(containerWidth / this.gridSize));

    this.canvas.width = this.gridSize * this.tileSize;
    this.canvas.height = this.gridSize * this.tileSize;

    const center = Math.floor(this.gridSize / 2) + 1;
    this.player.x = center;
    this.player.y = center;

    const r = this.player.rangeLevel + 1;
    this.dummyTargets = [
      { id: 'dummy_1', x: Math.max(1, center - 2), y: Math.max(1, center - 1) },
      { id: 'dummy_2', x: Math.min(this.gridSize, center + r), y: center },
      { id: 'dummy_3', x: center, y: Math.min(this.gridSize, center + r + 1) }
    ];
  }

  initEvents() {
    this.canvas.addEventListener('click', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / rect.width;
      const scaleY = this.canvas.height / rect.height;
      const clientX = (e.clientX - rect.left) * scaleX;
      const clientY = (e.clientY - rect.top) * scaleY;

      const tx = Math.floor(clientX / this.tileSize) + 1;
      const ty = Math.floor(clientY / this.tileSize) + 1;

      // If orthogonal adjacent, move player
      if (Math.abs(tx - this.player.x) + Math.abs(ty - this.player.y) === 1) {
        this.player.x = tx;
        this.player.y = ty;
        this.render();
      }
    });
  }

  upgradeRange() {
    this.player.rangeLevel = Math.min(this.player.rangeLevel + 1, 5);
    this.updateGridGeometry();
    this.render();
  }

  reset() {
    this.player.rangeLevel = 1;
    this.updateGridGeometry();
    this.render();
  }

  render() {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Draw grid
    this.ctx.strokeStyle = '#1E293B';
    this.ctx.lineWidth = 1;
    for (let x = 1; x <= this.gridSize; x++) {
      for (let y = 1; y <= this.gridSize; y++) {
        const px = (x - 1) * this.tileSize;
        const py = (y - 1) * this.tileSize;
        this.ctx.strokeRect(px, py, this.tileSize, this.tileSize);
      }
    }

    // Draw range projection (Core square + Cardinal Spikes)
    const r = this.player.rangeLevel + 1;
    this.ctx.fillStyle = 'rgba(0, 240, 255, 0.14)';
    this.ctx.strokeStyle = '#00F0FF';
    this.ctx.lineWidth = 1;

    for (let x = 1; x <= this.gridSize; x++) {
      for (let y = 1; y <= this.gridSize; y++) {
        const dx = Math.abs(x - this.player.x);
        const dy = Math.abs(y - this.player.y);
        const inCore = Math.max(dx, dy) <= r;
        const inSpike = (dx === r + 1 && dy === 0) || (dy === r + 1 && dx === 0);

        if ((inCore || inSpike) && !(x === this.player.x && y === this.player.y)) {
          const px = (x - 1) * this.tileSize;
          const py = (y - 1) * this.tileSize;
          this.ctx.fillRect(px + 1, py + 1, this.tileSize - 2, this.tileSize - 2);
        }
      }
    }

    // Draw Dummies (Red Tokens)
    this.dummyTargets.forEach(d => {
      const cx = (d.x - 0.5) * this.tileSize;
      const cy = (d.y - 0.5) * this.tileSize;
      const radius = Math.floor(this.tileSize * 0.32);
      this.ctx.fillStyle = '#F43F5E';
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      this.ctx.fill();
    });

    // Draw Player Unit (Cyan Squircle)
    const px = (this.player.x - 0.5) * this.tileSize;
    const py = (this.player.y - 0.5) * this.tileSize;
    const unitSize = Math.floor(this.tileSize * 0.72);
    const halfUnit = Math.floor(unitSize / 2);
    this.ctx.fillStyle = '#00F0FF';
    this.ctx.shadowColor = 'rgba(0, 240, 255, 0.5)';
    this.ctx.shadowBlur = 6;
    this.ctx.beginPath();
    this.ctx.roundRect(px - halfUnit, py - halfUnit, unitSize, unitSize, Math.max(3, Math.floor(unitSize / 5)));
    this.ctx.fill();
    this.ctx.shadowBlur = 0;
  }
}
